import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Code,
  Copy,
  Info,
  Maximize2,
  Minimize2,
  Search,
  Shield,
  ShieldAlert,
  Terminal,
  XCircle,
} from 'lucide-react';
import {
  WatcherCommandRule,
  WatcherPolicy,
  WatcherReviewRecord,
  WatcherSession,
  WatcherToolThreshold,
} from '../types';
import {
  DEFAULT_FALLBACK_POLICY,
  DEFAULT_WATCHER_TOOL_THRESHOLDS,
  THREAT_CATEGORY_META,
  toolThresholdAgentGroup,
} from '../data/defaultWatcherData';

interface WatcherLiveViewProps {
  onSelectSession?: (sessionId: string) => void;
  /** Preferred landing tab when opened as Safety → Policy */
  initialTab?: 'rules' | 'thresholds';
}

export const WatcherLiveView: React.FC<WatcherLiveViewProps> = ({
  onSelectSession,
  initialTab = 'rules',
}) => {
  // Flight Control (live session matrix) is hidden — Sessions + Control own that job.
  const [activeTab, setActiveTab] = useState<'rules' | 'thresholds'>(
    initialTab === 'thresholds' ? 'thresholds' : 'rules'
  );
  const [sessionFilter, setSessionFilter] = useState<'active' | 'closed' | 'parked'>('active');
  const [searchQuery, setSearchQuery] = useState('');
  const [sessions, setSessions] = useState<WatcherSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<WatcherReviewRecord[]>([]);
  const [expandedDecisions, setExpandedDecisions] = useState<Record<string, boolean>>({});
  const [copiedItemId, setCopiedItemId] = useState<string | null>(null);

  const toggleDecision = (id: string) => {
    setExpandedDecisions((prev) => ({
      ...prev,
      [id]: !prev[id],
    }));
  };

  const handleCopy = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedItemId(id);
    setTimeout(() => setCopiedItemId(null), 2000);
  };
  const [policy, setPolicy] = useState<WatcherPolicy>(DEFAULT_FALLBACK_POLICY);
  const [toolThresholds, setToolThresholds] = useState<WatcherToolThreshold[]>(DEFAULT_WATCHER_TOOL_THRESHOLDS);
  const [isThresholdDirty, setIsThresholdDirty] = useState(false);
  const [isSavingThresholds, setIsSavingThresholds] = useState(false);
  const [toolSearchQuery, setToolSearchQuery] = useState('');

  const [activeResolveReview, setActiveResolveReview] = useState<WatcherReviewRecord | null>(null);
  const [resolveAction, setResolveAction] = useState<'allow_once' | 'allow_session' | 'deny' | 'cancel'>('allow_once');
  const [resolveNotes, setResolveNotes] = useState('');
  const [isResolving, setIsResolving] = useState(false);
  const [rulesCategory, setRulesCategory] = useState<'all' | 'git' | 'security' | 'fs'>('all');

  // Fetch all sessions & active policy on mount
  const fetchSessions = async () => {
    try {
      const res = await fetch('/api/v1/watcher/sessions').catch(() => null);
      if (res && res.ok) {
        const data: WatcherSession[] = await res.json();
        if (Array.isArray(data)) {
          setSessions(data);
          if (data.length > 0 && (!selectedSessionId || !data.some((s) => s.session_id === selectedSessionId))) {
            setSelectedSessionId(data[0].session_id);
          } else if (data.length === 0) {
            setSelectedSessionId(null);
          }
        }
      }
    } catch {
      // Offline fallback: keep sessions empty
    }
  };

  const fetchPolicy = async () => {
    try {
      const res = await fetch('/api/v1/watcher/policy').catch(() => null);
      if (res && res.ok) {
        const data: WatcherPolicy = await res.json();
        if (data && data.command_rules && data.command_rules.length > 0) {
          setPolicy(data);
        }
        if (data && Array.isArray(data.tool_thresholds) && data.tool_thresholds.length > 0) {
          setToolThresholds(data.tool_thresholds);
        }
      }
    } catch {
      // Offline fallback
    }
  };

  useEffect(() => {
    fetchSessions();
    fetchPolicy();
    const interval = setInterval(() => {
      fetchSessions();
    }, 4000);
    return () => clearInterval(interval);
  }, []);

  // Fetch decisions whenever selectedSessionId changes
  useEffect(() => {
    if (!selectedSessionId) {
      setDecisions([]);
      return;
    }
    const fetchDecisions = async () => {
      try {
        const res = await fetch(`/api/v1/watcher/sessions/${selectedSessionId}/decisions`).catch(() => null);
        if (res && res.ok) {
          const data: WatcherReviewRecord[] = await res.json();
          if (Array.isArray(data)) {
            setDecisions(data);
            return;
          }
        }
      } catch {
        // Fallback
      }
      const local = sessions.find((s) => s.session_id === selectedSessionId);
      if (local && local.trajectory && Array.isArray(local.trajectory.reviews)) {
        setDecisions(local.trajectory.reviews);
      } else {
        setDecisions([]);
      }
    };
    fetchDecisions();

    // SSE Subscription for live session updates
    let eventSource: EventSource | null = null;
    try {
      eventSource = new EventSource(`/api/v1/watcher/sessions/${selectedSessionId}/stream`);
      eventSource.onerror = () => {
        if (eventSource) {
          eventSource.close();
          eventSource = null;
        }
      };

      eventSource.addEventListener('review_decision', (e: MessageEvent) => {
        try {
          const newReview: WatcherReviewRecord = JSON.parse(e.data);
          setDecisions((prev) => {
            if (prev.some((r) => r.id === newReview.id)) return prev;
            return [...prev, newReview];
          });
        } catch (err) {
          console.warn('Error parsing SSE review decision:', err);
        }
      });

      eventSource.addEventListener('decision_resolved', (e: MessageEvent) => {
        try {
          const resolvedReview: WatcherReviewRecord = JSON.parse(e.data);
          setDecisions((prev) =>
            prev.map((r) => (r.id === resolvedReview.id ? resolvedReview : r))
          );
        } catch (err) {
          console.warn('Error parsing SSE resolved decision:', err);
        }
      });
    } catch {
      // Safe fallback when SSE unavailable
    }

    return () => {
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [selectedSessionId]);

  // Handle Human Oversight Decision Resolution
  const handleResolveDecision = async () => {
    if (!selectedSessionId || !activeResolveReview) return;
    setIsResolving(true);
    try {
      const res = await fetch(`/api/v1/watcher/sessions/${selectedSessionId}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          review_id: activeResolveReview.id,
          action: resolveAction,
          notes: resolveNotes,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        const updated: WatcherReviewRecord = data.review;
        setDecisions((prev) =>
          prev.map((r) => (r.id === updated.id ? updated : r))
        );
        setActiveResolveReview(null);
        setResolveNotes('');
      }
    } catch (err) {
      console.error('Failed to resolve decision:', err);
    } finally {
      setIsResolving(false);
    }
  };

  // Handle Rule Action Toggle
  const handleUpdateRuleAction = async (ruleName: string, action: WatcherCommandRule['action']) => {
    try {
      const res = await fetch('/api/v1/watcher/policy/rule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rule_name: ruleName, action }),
      });
      if (res.ok) {
        setPolicy((prev) => ({
          ...prev,
          command_rules: prev.command_rules.map((r) =>
            r.name === ruleName ? { ...r, action } : r
          ),
        }));
      }
    } catch (err) {
      console.error('Failed to update rule action:', err);
    }
  };

  const handleResetPolicy = async () => {
    try {
      const res = await fetch('/api/v1/watcher/policy/reset', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        if (data.policy) {
          setPolicy(data.policy);
          if (Array.isArray(data.policy.tool_thresholds)) {
            setToolThresholds(data.policy.tool_thresholds);
          }
          setIsThresholdDirty(false);
        } else {
          await fetchPolicy();
        }
      }
    } catch (err) {
      console.error('Failed to reset policy:', err);
    }
  };


  // =========================================================================
  // Tool Threshold Interactivity:
  // "Escalate and auto-deny can be activated together, but are off when either
  // auto-approve or always escalate is on."
  // =========================================================================
  const toggleAutoApprove = (toolName: string) => {
    setToolThresholds((prev) =>
      prev.map((t) => {
        if (t.tool_name !== toolName) return t;
        const willBeActive = !t.auto_approve;
        return {
          ...t,
          auto_approve: willBeActive,
          always_escalate: false,
          escalate_ge: willBeActive ? null : t.escalate_ge,
          auto_deny_ge: willBeActive ? null : t.auto_deny_ge,
        };
      })
    );
    setIsThresholdDirty(true);
  };

  const toggleAlwaysEscalate = (toolName: string) => {
    setToolThresholds((prev) =>
      prev.map((t) => {
        if (t.tool_name !== toolName) return t;
        const willBeActive = !t.always_escalate;
        return {
          ...t,
          always_escalate: willBeActive,
          auto_approve: false,
          escalate_ge: willBeActive ? null : t.escalate_ge,
          auto_deny_ge: willBeActive ? null : t.auto_deny_ge,
        };
      })
    );
    setIsThresholdDirty(true);
  };

  const toggleEscalate = (toolName: string) => {
    setToolThresholds((prev) =>
      prev.map((t) => {
        if (t.tool_name !== toolName) return t;
        const isCurrentlyActive = t.escalate_ge != null;
        return {
          ...t,
          escalate_ge: isCurrentlyActive ? null : 5,
          auto_approve: false,
          always_escalate: false,
        };
      })
    );
    setIsThresholdDirty(true);
  };

  const updateEscalateGte = (toolName: string, valStr: string) => {
    setToolThresholds((prev) =>
      prev.map((t) => {
        if (t.tool_name !== toolName) return t;
        if (valStr.trim() === '') {
          return { ...t, escalate_ge: null };
        }
        const parsed = Math.max(1, Math.min(10, parseInt(valStr, 10) || 1));
        return {
          ...t,
          escalate_ge: parsed,
          auto_approve: false,
          always_escalate: false,
        };
      })
    );
    setIsThresholdDirty(true);
  };

  const toggleAutoDeny = (toolName: string) => {
    setToolThresholds((prev) =>
      prev.map((t) => {
        if (t.tool_name !== toolName) return t;
        const isCurrentlyActive = t.auto_deny_ge != null;
        return {
          ...t,
          auto_deny_ge: isCurrentlyActive ? null : 8,
          auto_approve: false,
          always_escalate: false,
        };
      })
    );
    setIsThresholdDirty(true);
  };

  const updateAutoDenyGte = (toolName: string, valStr: string) => {
    setToolThresholds((prev) =>
      prev.map((t) => {
        if (t.tool_name !== toolName) return t;
        if (valStr.trim() === '') {
          return { ...t, auto_deny_ge: null };
        }
        const parsed = Math.max(1, Math.min(10, parseInt(valStr, 10) || 1));
        return {
          ...t,
          auto_deny_ge: parsed,
          auto_approve: false,
          always_escalate: false,
        };
      })
    );
    setIsThresholdDirty(true);
  };

  const handleSaveThresholds = async () => {
    setIsSavingThresholds(true);
    try {
      const res = await fetch('/api/v1/watcher/policy/thresholds', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(toolThresholds),
      });
      if (res.ok) {
        setIsThresholdDirty(false);
      }
    } catch (err) {
      console.error('Failed to save thresholds:', err);
    } finally {
      setIsSavingThresholds(false);
    }
  };

  // Filtered lists
  const filteredSessions = sessions.filter((s) => {
    const matchesFilter =
      sessionFilter === 'active'
        ? s.status === 'active' || s.status === 'working'
        : sessionFilter === 'closed'
        ? s.status === 'completed' || s.status === 'error' || s.status === 'cancelled'
        : s.status === 'parked';

    const matchesSearch =
      (s.project_name || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      (s.model || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      (s.session_id || '').toLowerCase().includes(searchQuery.toLowerCase());

    return matchesFilter && matchesSearch;
  });

  const selectedSession = sessions.find((s) => s.session_id === selectedSessionId);

  const filteredToolThresholds = toolThresholds.filter((t) =>
    t.tool_name.toLowerCase().includes(toolSearchQuery.toLowerCase())
  );

  const groupedToolThresholds = filteredToolThresholds.reduce<Record<string, WatcherToolThreshold[]>>(
    (acc, t) => {
      const group = toolThresholdAgentGroup(t.tool_name);
      if (!acc[group]) acc[group] = [];
      acc[group].push(t);
      return acc;
    },
    {}
  );
  const groupOrder = ['All Agents', 'Claude Code', 'Cursor / Antigravity', 'Codex'];
  const orderedGroups = [
    ...groupOrder.filter((g) => groupedToolThresholds[g]?.length),
    ...Object.keys(groupedToolThresholds).filter((g) => !groupOrder.includes(g)),
  ];

  return (
    <div className="flex flex-col h-full bg-[#fcfcfd] text-[#1e2029] font-sans p-6 space-y-4">
      {/* Policy header — rules + thresholds only */}
      <div className="bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-base font-bold text-slate-900 tracking-tight flex items-center gap-2.5">
            Policy
          </h1>
        </div>

        <div className="flex items-center gap-2.5">
          <div className="flex items-center bg-slate-100 p-1 rounded-xl border border-slate-200/60 text-xs font-medium">
            <button
              type="button"
              onClick={() => setActiveTab('rules')}
              className={`px-3 py-1.5 rounded-lg transition-all cursor-pointer ${
                activeTab === 'rules'
                  ? 'bg-white text-slate-900 shadow-xs font-semibold'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Command Rules
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('thresholds')}
              className={`px-3 py-1.5 rounded-lg transition-all cursor-pointer ${
                activeTab === 'thresholds'
                  ? 'bg-white text-slate-900 shadow-xs font-semibold'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Tool Thresholds
            </button>
          </div>
        </div>
      </div>

      {/* Hidden: former Flight Control live matrix — use Safety → Sessions / Control */}
      {false && (
        <div className="flex flex-1 min-h-0 gap-5 overflow-hidden">
          {/* Left Column: Active Sessions Matrix (CARD) */}
          <div className="w-80 bg-white rounded-2xl border border-border-subtle shadow-sm flex flex-col shrink-0 overflow-hidden">
            {/* Session Tabs & Search */}
            <div className="p-3 border-b border-[#e5e7eb] space-y-2">
              <div className="flex items-center bg-[#f3f4f6] p-0.5 rounded-lg text-xs font-medium">
                <button
                  onClick={() => setSessionFilter('active')}
                  className={`flex-1 py-1 text-center rounded-md transition-all ${
                    sessionFilter === 'active'
                      ? 'bg-white text-[#111827] shadow-xs font-semibold'
                      : 'text-[#6b7280]'
                  }`}
                >
                  Active ({sessions.filter((s) => s.status === 'active' || s.status === 'working').length})
                </button>
                <button
                  onClick={() => setSessionFilter('closed')}
                  className={`flex-1 py-1 text-center rounded-md transition-all ${
                    sessionFilter === 'closed'
                      ? 'bg-white text-[#111827] shadow-xs font-semibold'
                      : 'text-[#6b7280]'
                  }`}
                >
                  Closed
                </button>
                <button
                  onClick={() => setSessionFilter('parked')}
                  className={`flex-1 py-1 text-center rounded-md transition-all ${
                    sessionFilter === 'parked'
                      ? 'bg-white text-[#111827] shadow-xs font-semibold'
                      : 'text-[#6b7280]'
                  }`}
                >
                  Parked
                </button>
              </div>

              <div className="relative">
                <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-[#9ca3af]" />
                <input
                  type="text"
                  placeholder="Filter sessions..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-8 pr-3 py-1.5 text-xs bg-[#f9fafb] border border-[#e5e7eb] rounded-lg focus:outline-none focus:ring-1 focus:ring-[#6366f1]"
                />
              </div>
            </div>

            {/* Session Card List */}
            <div className="flex-1 overflow-y-auto divide-y divide-[#f3f4f6]">
              {filteredSessions.length === 0 ? (
                <div className="p-8 text-center text-xs text-[#9ca3af] font-mono flex flex-col items-center justify-center h-48">
                  <Shield className="w-8 h-8 text-gray-300 mb-2 stroke-1" />
                  <span className="font-semibold text-gray-600">No active sessions</span>
                  <span className="text-[11px] text-gray-400 mt-1">
                    Start an agent or benchmark to view live sessions.
                  </span>
                </div>
              ) : (
                filteredSessions.map((s) => {
                  const isSelected = s.session_id === selectedSessionId;
                  const isWorking = s.status === 'working';
                  return (
                    <div
                      key={s.session_id}
                      onClick={() => {
                        setSelectedSessionId(s.session_id);
                        if (onSelectSession) onSelectSession(s.session_id);
                      }}
                      className={`p-3.5 cursor-pointer transition-all ${
                        isSelected
                          ? 'bg-[#f5f3ff] border-l-4 border-l-[#6366f1]'
                          : 'hover:bg-[#f9fafb]'
                      }`}
                    >
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-xs font-bold text-[#111827] truncate" title={s.title || s.project_name || s.task_id}>
                          {s.title || s.project_name || s.task_id || 'unnamed-session'}
                        </span>
                        <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded font-semibold ${s.agent_type === 'antigravity' ? 'bg-indigo-50 text-indigo-700 border border-indigo-200' : 'text-[#6b7280]'}`}>
                          {s.agent_type === 'antigravity' ? '⚡ Antigravity' : s.agent_type}
                        </span>
                      </div>

                      <div className="flex items-center gap-1.5 text-[11px] text-[#4b5563] mb-2 font-mono">
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${
                            isWorking ? 'bg-amber-500 animate-ping' : 'bg-emerald-500'
                          }`}
                        />
                        <span className="truncate">{s.current_activity || (isWorking ? 'working...' : 'idle')}</span>
                      </div>

                      <div className="flex items-center justify-between text-[10px] text-[#9ca3af] font-mono">
                        <span className="truncate max-w-[140px]">{s.model}</span>
                        <span>{Math.round(s.total_duration_sec)}s</span>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Right Column: Resolved Decisions & Flight Control Stream (CARD) */}
          <div className="flex-1 bg-white rounded-2xl border border-border-subtle shadow-sm flex flex-col overflow-hidden">
            {/* Active Session Header Banner */}
            {selectedSession ? (
              <div className="px-6 py-3 bg-white border-b border-[#e5e7eb] flex items-center justify-between shrink-0">
                <div className="flex items-center gap-3">
                  <div className="text-xs font-mono flex items-center gap-2">
                    <span className="font-bold text-sm text-[#111827]">
                      {selectedSession?.title || selectedSession?.project_name || selectedSession?.session_id}
                    </span>
                    <span className="text-[11px] text-[#6b7280]">
                      ({selectedSession?.session_id})
                    </span>
                    <span className="text-[#d1d5db]">•</span>
                    <span className="text-[#6b7280]">Model: </span>
                    <strong className="text-[#4f46e5]">{selectedSession?.model}</strong>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-[#f3f4f6] text-[#374151]">
                    {decisions.length} Decisions Recorded
                  </span>
                  {decisions.length > 0 && (
                    <button
                      type="button"
                      onClick={() => {
                        const allExp = decisions.every((d, i) => expandedDecisions[d.id || String(i)]);
                        if (allExp) {
                          setExpandedDecisions({});
                        } else {
                          const next: Record<string, boolean> = {};
                          decisions.forEach((d, i) => {
                            next[d.id || String(i)] = true;
                          });
                          setExpandedDecisions(next);
                        }
                      }}
                      className="px-2.5 py-1 rounded-lg text-[11px] font-mono font-semibold text-slate-700 bg-slate-100 hover:bg-slate-200 transition-colors cursor-pointer flex items-center gap-1"
                    >
                      {decisions.length > 0 && decisions.every((d, i) => expandedDecisions[d.id || String(i)]) ? (
                        <>
                          <Minimize2 className="w-3 h-3 text-slate-500" />
                          <span>Collapse All</span>
                        </>
                      ) : (
                        <>
                          <Maximize2 className="w-3 h-3 text-slate-500" />
                          <span>Expand All</span>
                        </>
                      )}
                    </button>
                  )}
                  <a
                    href={`#sessions/${encodeURIComponent(selectedSession?.session_id || '')}`}
                    className="px-2.5 py-1 rounded-lg text-[11px] font-mono font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 transition-colors cursor-pointer"
                  >
                    View Full Session ↗
                  </a>
                </div>
              </div>
            ) : (
              <div className="px-6 py-3 bg-white border-b border-[#e5e7eb] text-xs font-mono text-[#6b7280]">
                No session selected
              </div>
            )}

            {/* Decisions List */}
            <div className="flex-1 overflow-y-auto p-6 space-y-3">
              {!selectedSession ? (
                <div className="flex-1 flex flex-col items-center justify-center p-12 text-center text-xs text-[#9ca3af] font-mono h-96">
                  <div className="w-12 h-12 rounded-2xl bg-indigo-50 border border-indigo-100 flex items-center justify-center mb-3">
                    <Terminal className="w-6 h-6 text-indigo-500" />
                  </div>
                  <span className="text-sm font-bold text-gray-700">Flight Control Standby</span>
                  <p className="text-xs text-gray-400 max-w-sm mt-1">
                    No session is currently selected. Connect an agent hook or select an active session on the left to monitor live tool calls.
                  </p>
                </div>
              ) : decisions.length === 0 ? (
                <div className="py-20 text-center text-xs text-[#9ca3af] font-mono">
                  No decisions recorded for this session yet. Agent actions will appear here in real time.
                </div>
              ) : (
                decisions.map((dec, idx) => {
                  const decKey = dec.id || String(idx);
                  const isExpanded = !!expandedDecisions[decKey];
                  const isBlocked = dec.decision === 'block';
                  const isWarn = dec.decision === 'warn';
                  const isEscalated = dec.decision === 'escalate';

                  // Trajectory matching
                  const trajectoryToolCalls = selectedSession?.trajectory?.tool_calls || [];
                  const trajectoryToolResults = selectedSession?.trajectory?.tool_results || [];

                  const matchedToolCall =
                    trajectoryToolCalls[idx]?.tool_name === dec.tool_name
                      ? trajectoryToolCalls[idx]
                      : trajectoryToolCalls.find(
                          (tc) =>
                            tc.tool_name === dec.tool_name &&
                            (tc.raw_input === dec.tool_input || tc.tool_id === dec.id)
                        );

                  const matchedToolResult =
                    trajectoryToolResults[idx]?.tool_name === dec.tool_name
                      ? trajectoryToolResults[idx]
                      : trajectoryToolResults.find(
                          (tr) =>
                            tr.tool_name === dec.tool_name &&
                            (matchedToolCall ? tr.tool_id === matchedToolCall.tool_id : tr.tool_id === dec.id)
                        );

                  const rawCommand =
                    (matchedToolCall?.arguments?.CommandLine as string) ||
                    (matchedToolCall?.arguments?.command as string) ||
                    (matchedToolCall?.arguments?.cmd as string) ||
                    matchedToolCall?.raw_input ||
                    dec.tool_input;

                  const displayCommand =
                    typeof rawCommand === 'string'
                      ? rawCommand.replace(/^"(.*)"$/, '$1').replace(/\\"/g, '"')
                      : JSON.stringify(rawCommand, null, 2);

                  const extraArgs = matchedToolCall?.arguments
                    ? { ...matchedToolCall.arguments }
                    : null;
                  if (extraArgs) {
                    delete extraArgs.CommandLine;
                    delete extraArgs.command;
                    delete extraArgs.cmd;
                    delete extraArgs.toolAction;
                    delete extraArgs.toolSummary;
                  }
                  const hasExtraArgs = extraArgs && Object.keys(extraArgs).length > 0;

                  return (
                    <div
                      key={decKey}
                      className={`bg-white rounded-xl border transition-all overflow-hidden shadow-xs ${
                        isBlocked
                          ? 'border-rose-300 bg-rose-50/20'
                          : isEscalated
                          ? 'border-amber-300 bg-amber-50/20'
                          : isWarn
                          ? 'border-yellow-200'
                          : 'border-[#e5e7eb]'
                      }`}
                    >
                      {/* Header Row: Clickable to toggle expand/collapse */}
                      <div
                        onClick={() => toggleDecision(decKey)}
                        className="p-3.5 flex items-center justify-between gap-3 cursor-pointer hover:bg-slate-50/70 transition-colors select-none"
                      >
                        <div className="flex items-center gap-2.5 min-w-0">
                          {/* Expand Toggle Chevron */}
                          <div className="text-slate-400 hover:text-slate-700 transition-transform">
                            {isExpanded ? (
                              <ChevronUp className="w-4 h-4 text-slate-700" />
                            ) : (
                              <ChevronDown className="w-4 h-4" />
                            )}
                          </div>

                          {/* Verdict Icon */}
                          <div className="shrink-0">
                            {isBlocked ? (
                              <XCircle className="w-4 h-4 text-rose-600" />
                            ) : isEscalated ? (
                              <ShieldAlert className="w-4 h-4 text-amber-600" />
                            ) : isWarn ? (
                              <AlertTriangle className="w-4 h-4 text-yellow-600" />
                            ) : (
                              <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                            )}
                          </div>

                          {/* Stage Pill */}
                          <span
                            className={`text-[10px] font-mono font-bold uppercase px-1.5 py-0.5 rounded shrink-0 ${
                              dec.stage === 'threshold'
                                ? 'bg-purple-100 text-purple-700'
                                : dec.stage === 'rule'
                                ? 'bg-rose-100 text-rose-700'
                                : dec.stage === 'deep_review'
                                ? 'bg-indigo-100 text-indigo-700'
                                : 'bg-blue-100 text-blue-700'
                            }`}
                          >
                            {dec.stage}
                          </span>

                          {/* Tool Name & Input Preview */}
                          <div className={`min-w-0 flex items-center gap-2 ${isExpanded ? 'flex-wrap flex-1' : 'truncate'}`}>
                            <span className="text-xs font-mono font-bold text-[#111827] shrink-0">
                              {dec.tool_name}
                            </span>
                            <span
                              className={`text-xs font-mono ${
                                isExpanded
                                  ? 'text-[#111827] font-semibold break-all whitespace-pre-wrap flex-1'
                                  : 'text-[#4b5563] truncate'
                              }`}
                              title={displayCommand}
                            >
                              {displayCommand}
                            </span>
                          </div>
                        </div>

                        <div className="flex items-center gap-2 shrink-0">
                          {/* Latency Pill */}
                          <span className="text-[10px] font-mono text-[#9ca3af]">
                            {Math.round(dec.latency_ms)}ms
                          </span>

                          {/* Rule pill if triggered */}
                          {dec.rule_name && (
                            <span
                              className={`text-[10px] font-mono px-2 py-0.5 rounded-full font-medium ${
                                isBlocked
                                  ? 'bg-rose-100 text-rose-800 border border-rose-200'
                                  : 'bg-indigo-100 text-indigo-800 border border-indigo-200'
                              }`}
                            >
                              {dec.rule_name}
                            </span>
                          )}

                          {/* Severity Score Pill */}
                          <span
                            className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full ${
                              dec.score >= 8
                                ? 'bg-rose-100 text-rose-800'
                                : dec.score >= 5
                                ? 'bg-amber-100 text-amber-800'
                                : 'bg-emerald-100 text-emerald-800'
                            }`}
                          >
                            Score {dec.score}
                          </span>

                          {/* Apollo 13 Threat Taxonomy Badge */}
                          {dec.threat_category && THREAT_CATEGORY_META[dec.threat_category] && (
                            <span
                              className={`text-[10px] font-mono px-2 py-0.5 rounded-full font-bold border ${THREAT_CATEGORY_META[dec.threat_category].bg} ${THREAT_CATEGORY_META[dec.threat_category].color} ${THREAT_CATEGORY_META[dec.threat_category].border}`}
                              title={THREAT_CATEGORY_META[dec.threat_category].label}
                            >
                              {THREAT_CATEGORY_META[dec.threat_category].shortLabel}
                            </span>
                          )}

                          {/* Override / Review Action Button */}
                          {(isBlocked || isEscalated) && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                setActiveResolveReview(dec);
                                setResolveAction('allow_once');
                              }}
                              className="px-2.5 py-1 text-xs font-medium bg-[#4f46e5] text-white rounded-lg hover:bg-[#4338ca] transition-colors shadow-xs cursor-pointer"
                            >
                              Resolve
                            </button>
                          )}

                          {/* Explicit Expand text toggle */}
                          <span className="text-[11px] font-mono text-indigo-600 hover:text-indigo-800 font-semibold pl-1">
                            {isExpanded ? 'Hide' : 'Expand'}
                          </span>
                        </div>
                      </div>

                      {/* Brief explanation subtitle if collapsed */}
                      {!isExpanded && dec.explanation && (
                        <div className="px-3.5 pb-2.5 text-[11px] text-[#4b5563] flex items-center gap-1.5">
                          <Info className="w-3.5 h-3.5 text-[#9ca3af] shrink-0" />
                          <span className="truncate">{dec.explanation}</span>
                        </div>
                      )}

                      {/* Expanded Detail Panel */}
                      {isExpanded && (
                        <div className="px-4 pb-4 pt-1 border-t border-slate-100 space-y-3.5 bg-slate-50/40">
                          {/* Explanation banner if present */}
                          {dec.explanation && (
                            <div className="text-xs text-slate-700 bg-white p-2.5 rounded-lg border border-slate-200/80 flex items-start gap-2">
                              <Info className="w-4 h-4 text-indigo-600 shrink-0 mt-0.5" />
                              <div>
                                <strong className="text-slate-900">Policy Assessment: </strong>
                                <span>{dec.explanation}</span>
                              </div>
                            </div>
                          )}

                          {/* SECTION 1: FULL COMMAND / INPUT */}
                          <div className="space-y-1.5">
                            <div className="flex items-center justify-between text-[11px] font-mono font-bold text-slate-700">
                              <div className="flex items-center gap-1.5">
                                <Code className="w-3.5 h-3.5 text-indigo-600" />
                                <span>Full Command / Input</span>
                              </div>
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleCopy(displayCommand, `cmd-${decKey}`);
                                }}
                                className="text-[11px] text-slate-500 hover:text-indigo-600 flex items-center gap-1 cursor-pointer font-mono px-2 py-0.5 rounded hover:bg-slate-200/60 transition-colors"
                              >
                                {copiedItemId === `cmd-${decKey}` ? (
                                  <>
                                    <Check className="w-3 h-3 text-emerald-600" />
                                    <span className="text-emerald-700 font-semibold">Copied</span>
                                  </>
                                ) : (
                                  <>
                                    <Copy className="w-3 h-3" />
                                    <span>Copy Command</span>
                                  </>
                                )}
                              </button>
                            </div>
                            <pre className="bg-slate-950 text-slate-100 font-mono text-xs p-3 rounded-lg overflow-x-auto max-h-56 overflow-y-auto whitespace-pre-wrap select-text leading-relaxed border border-slate-800">
                              {displayCommand}
                            </pre>

                            {/* Extra Parameters / Arguments if available */}
                            {hasExtraArgs && (
                              <div className="mt-1.5 p-2.5 bg-white rounded-lg border border-slate-200 text-xs font-mono space-y-1">
                                <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                                  Arguments / Parameters:
                                </span>
                                <pre className="text-[11px] text-slate-800 overflow-x-auto whitespace-pre-wrap">
                                  {JSON.stringify(extraArgs, null, 2)}
                                </pre>
                              </div>
                            )}
                          </div>

                          {/* SECTION 2: RESULT (OUTPUT / DIFF / POLICY VERDICT) */}
                          <div className="space-y-1.5">
                            <div className="flex items-center justify-between text-[11px] font-mono font-bold text-slate-700">
                              <div className="flex items-center gap-1.5">
                                <Terminal className="w-3.5 h-3.5 text-indigo-600" />
                                <span>Execution Result</span>
                                {matchedToolResult && (
                                  <span
                                    className={`px-1.5 py-0.2 rounded text-[9px] uppercase font-bold ${
                                      matchedToolResult.exit_code === 0 && !matchedToolResult.is_error
                                        ? 'bg-emerald-100 text-emerald-700'
                                        : 'bg-rose-100 text-rose-700'
                                    }`}
                                  >
                                    {matchedToolResult.exit_code === 0 && !matchedToolResult.is_error
                                      ? 'Exit 0 (Success)'
                                      : `Exit ${matchedToolResult.exit_code} (Error)`}
                                  </span>
                                )}
                              </div>

                              {matchedToolResult?.stdout && (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleCopy(matchedToolResult.stdout, `out-${decKey}`);
                                  }}
                                  className="text-[11px] text-slate-500 hover:text-indigo-600 flex items-center gap-1 cursor-pointer font-mono px-2 py-0.5 rounded hover:bg-slate-200/60 transition-colors"
                                >
                                  {copiedItemId === `out-${decKey}` ? (
                                    <>
                                      <Check className="w-3 h-3 text-emerald-600" />
                                      <span className="text-emerald-700 font-semibold">Copied</span>
                                    </>
                                  ) : (
                                    <>
                                      <Copy className="w-3 h-3" />
                                      <span>Copy Output</span>
                                    </>
                                  )}
                                </button>
                              )}
                            </div>

                            {/* Blocked by Policy */}
                            {isBlocked ? (
                              <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-xs font-mono text-rose-900 space-y-1">
                                <div className="flex items-center gap-1.5 font-bold">
                                  <XCircle className="w-4 h-4 text-rose-600" />
                                  <span>Execution Blocked by Safety Policy</span>
                                  {dec.rule_name && <span>({dec.rule_name})</span>}
                                </div>
                                <div className="text-[11px] text-rose-700">
                                  This command was intercepted and prevented from executing.
                                </div>
                              </div>
                            ) : matchedToolResult ? (
                              <div className="space-y-2">
                                {matchedToolResult.stdout ? (
                                  <pre className="bg-slate-900 text-slate-100 font-mono text-xs p-3 rounded-lg overflow-x-auto max-h-72 overflow-y-auto whitespace-pre-wrap select-text leading-relaxed border border-slate-800">
                                    {matchedToolResult.stdout}
                                  </pre>
                                ) : (
                                  <div className="text-xs font-mono text-slate-500 bg-white p-2.5 rounded-lg border border-slate-200">
                                    (Command executed successfully with no stdout output)
                                  </div>
                                )}

                                {matchedToolResult.stderr && (
                                  <div className="space-y-1">
                                    <div className="text-[10px] font-mono font-bold text-rose-700 uppercase">
                                      Stderr:
                                    </div>
                                    <pre className="bg-rose-950/80 text-rose-200 border border-rose-800/60 font-mono text-xs p-3 rounded-lg overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap select-text leading-relaxed">
                                      {matchedToolResult.stderr}
                                    </pre>
                                  </div>
                                )}
                              </div>
                            ) : dec.diff ? (
                              <div className="bg-[#1e2029] text-white rounded-lg p-3 font-mono text-xs overflow-x-auto border border-slate-800">
                                <div className="text-[10px] uppercase text-[#9ca3af] mb-1 font-bold tracking-wider">
                                  Proposed Syntax Diff:
                                </div>
                                {dec.diff.split('\n').map((line, i) => (
                                  <div
                                    key={i}
                                    className={`px-1 rounded ${
                                      line.startsWith('-')
                                        ? 'bg-rose-900/40 text-rose-300'
                                        : line.startsWith('+')
                                        ? 'bg-emerald-900/40 text-emerald-300'
                                        : 'text-gray-300'
                                    }`}
                                  >
                                    {line}
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <div className="text-xs font-mono text-slate-600 bg-emerald-50/50 p-2.5 rounded-lg border border-emerald-200/70 flex items-center gap-2">
                                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
                                <span>Operation allowed by policy gate. No external output captured.</span>
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}

      {/* Command Rules Manager Tab */}
      {activeTab === 'rules' && policy && (
        <div className="flex-1 overflow-y-auto p-6 max-w-6xl mx-auto w-full">
          <div className="flex items-center justify-between mb-6 gap-4 flex-wrap">
            <div>
              <h2 className="text-base font-bold text-[#111827]">
                Command Rules ({policy.command_rules.length})
              </h2>
              <p className="text-xs text-[#6b7280]">
                Pre-filter regex rules matched before any LLM triage to achieve sub-millisecond
                interception.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleResetPolicy}
                className="px-3 py-1.5 text-xs font-mono font-bold rounded-lg border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 cursor-pointer"
              >
                Reset to defaults
              </button>
            {/* Category Filter */}
            <div className="flex items-center bg-[#f3f4f6] p-1 rounded-lg border border-[#e5e7eb] text-xs">
              <button
                onClick={() => setRulesCategory('all')}
                className={`px-3 py-1 rounded-md ${
                  rulesCategory === 'all'
                    ? 'bg-white text-[#111827] font-bold shadow-xs'
                    : 'text-[#6b7280]'
                }`}
              >
                All ({policy.command_rules.length})
              </button>
              <button
                onClick={() => setRulesCategory('git')}
                className={`px-3 py-1 rounded-md ${
                  rulesCategory === 'git'
                    ? 'bg-white text-[#111827] font-bold shadow-xs'
                    : 'text-[#6b7280]'
                }`}
              >
                Git Operations
              </button>
              <button
                onClick={() => setRulesCategory('security')}
                className={`px-3 py-1 rounded-md ${
                  rulesCategory === 'security'
                    ? 'bg-white text-[#111827] font-bold shadow-xs'
                    : 'text-[#6b7280]'
                }`}
              >
                Security &amp; Secrets
              </button>
              <button
                onClick={() => setRulesCategory('fs')}
                className={`px-3 py-1 rounded-md ${
                  rulesCategory === 'fs'
                    ? 'bg-white text-[#111827] font-bold shadow-xs'
                    : 'text-[#6b7280]'
                }`}
              >
                Filesystem
              </button>
            </div>
            </div>
          </div>

          <div className="space-y-2">
            {policy.command_rules
              .filter((r) => (rulesCategory === 'all' ? true : r.category === rulesCategory))
              .map((rule) => (
                <div
                  key={rule.name}
                  className="bg-white p-3.5 rounded-xl border border-[#e5e7eb] flex items-center justify-between gap-4 shadow-xs"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-bold text-[#111827]">{rule.name}</span>
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#f3f4f6] text-[#4b5563] border border-[#e5e7eb]">
                        {rule.category}
                      </span>
                    </div>
                    <p className="text-xs text-[#6b7280] mb-1">{rule.description}</p>
                    <code className="text-[11px] font-mono text-[#4f46e5] bg-[#f5f3ff] px-2 py-0.5 rounded border border-[#ede9fe] inline-block">
                      {rule.pattern}
                    </code>
                  </div>

                  {/* Action Selector Buttons */}
                  <div className="flex items-center gap-1 bg-[#f9fafb] p-1 rounded-lg border border-[#e5e7eb] text-xs font-medium shrink-0">
                    {(['allow', 'triage', 'human', 'deny', 'off'] as const).map((act) => {
                      const isActive = rule.action === act;
                      const activeColors = {
                        allow: 'bg-emerald-600 text-white',
                        triage: 'bg-blue-600 text-white',
                        human: 'bg-amber-500 text-white',
                        deny: 'bg-rose-600 text-white',
                        off: 'bg-gray-500 text-white',
                      };
                      return (
                        <button
                          key={act}
                          onClick={() => handleUpdateRuleAction(rule.name, act)}
                          className={`px-2.5 py-1 rounded text-[11px] font-mono uppercase transition-all ${
                            isActive
                              ? activeColors[act] + ' font-bold shadow-xs'
                              : 'text-[#6b7280] hover:text-[#111827]'
                          }`}
                        >
                          {act}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Tool Thresholds Matrix Tab (Clean High-Contrast Light Theme) */}
      {activeTab === 'thresholds' && (
        <div className="flex-1 overflow-y-auto bg-[#fcfcfd] font-mono text-[#1e2029] p-6">
          <div className="max-w-5xl mx-auto w-full bg-white rounded-2xl border border-[#e5e7eb] shadow-xs p-6">
            {/* Header & Save Button */}
            <div className="mb-6 flex w-full flex-col gap-3 border-b border-[#e5e7eb] pb-4">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="font-mono text-sm font-bold text-[#111827] flex items-center gap-2">
                    <span>Tool Thresholds</span>
                    <span className="text-xs font-normal text-[#6b7280]">(scores range from 1–10)</span>
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-100 text-slate-700 border border-slate-200">
                      {filteredToolThresholds.length} tools
                    </span>
                  </div>
                  <p className="text-xs text-[#6b7280] mt-0.5">
                    Configure blocking, auto-approval, and human escalation gates per tool.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleSaveThresholds}
                    disabled={!isThresholdDirty || isSavingThresholds}
                    className={`rounded-lg px-4 py-1.5 text-xs font-mono font-bold transition-all shadow-xs ${
                      isThresholdDirty
                        ? 'bg-teal-600 hover:bg-teal-700 text-white cursor-pointer'
                        : 'bg-slate-100 text-slate-400 cursor-not-allowed border border-slate-200'
                    }`}
                  >
                    {isSavingThresholds ? 'Saving...' : isThresholdDirty ? 'Save Thresholds' : 'Saved'}
                  </button>
                </div>
              </div>

              {/* Search Bar */}
              <div className="flex items-center gap-3">
                <input
                  type="text"
                  placeholder="Search tools (e.g. bash, edit, read, ask)..."
                  value={toolSearchQuery}
                  onChange={(e) => setToolSearchQuery(e.target.value)}
                  className="w-full rounded-xl border border-[#e5e7eb] bg-[#f9fafb] px-3.5 py-2 text-xs text-[#111827] placeholder-slate-400 focus:border-teal-600 focus:bg-white focus:outline-none font-mono"
                />
              </div>
            </div>

            {/* Agent-grouped tool thresholds (Watcher parity) */}
            <div className="space-y-6">
              {orderedGroups.map((group) => (
                <div key={group}>
                  <div className="flex items-center gap-2 mb-2 px-1">
                    <span className="text-[10px] font-mono uppercase tracking-wider font-bold text-slate-500">
                      {group}
                    </span>
                    <span className="text-[10px] font-mono text-slate-400">
                      ({groupedToolThresholds[group].length})
                    </span>
                  </div>
                  <div className="space-y-1 divide-y divide-[#f3f4f6] border border-slate-100 rounded-xl overflow-hidden">
                    {groupedToolThresholds[group].map((t) => (
                <div
                  key={t.tool_name}
                  className="flex items-center justify-between py-2.5 px-3 hover:bg-[#f9fafb] transition-colors bg-white"
                >
                  {/* Left: Tool Name */}
                  <div className="w-60 min-w-0 shrink-0">
                    <span className="font-mono text-xs font-bold text-slate-900 break-all">
                      {t.tool_name}
                    </span>
                  </div>

                  {/* Right: 4 Threshold Buttons */}
                  <div className="flex shrink-0 items-center gap-2">
                    {/* 1. Auto-approve */}
                    <button
                      type="button"
                      onClick={() => toggleAutoApprove(t.tool_name)}
                      className={`rounded-lg border px-3 py-1 text-xs transition-colors font-mono cursor-pointer ${
                        t.auto_approve
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-300 font-bold shadow-2xs'
                          : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'
                      }`}
                    >
                      Auto-approve
                    </button>

                    {/* 2. Escalate >= */}
                    <div className="flex items-center">
                      <button
                        type="button"
                        onClick={() => toggleEscalate(t.tool_name)}
                        className={`rounded-l-lg border-y border-l px-3 py-1 text-xs transition-colors font-mono cursor-pointer ${
                          t.escalate_ge != null
                            ? 'bg-amber-50 text-amber-800 border-amber-300 font-bold'
                            : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'
                        }`}
                      >
                        Escalate ≥
                      </button>
                      <input
                        type="number"
                        min={1}
                        max={10}
                        placeholder="—"
                        value={t.escalate_ge ?? ''}
                        onChange={(e) => updateEscalateGte(t.tool_name, e.target.value)}
                        className={`w-11 rounded-r-lg border py-1 text-center font-mono text-xs transition-colors focus:outline-none ${
                          t.escalate_ge != null
                            ? 'border-amber-300 bg-amber-50/60 text-amber-900 font-bold focus:border-amber-500'
                            : 'border-slate-200 bg-white text-slate-400'
                        }`}
                      />
                    </div>

                    {/* 3. Auto-deny >= */}
                    <div className="flex items-center">
                      <button
                        type="button"
                        onClick={() => toggleAutoDeny(t.tool_name)}
                        className={`rounded-l-lg border-y border-l px-3 py-1 text-xs transition-colors font-mono cursor-pointer ${
                          t.auto_deny_ge != null
                            ? 'bg-rose-50 text-rose-700 border-rose-300 font-bold'
                            : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'
                        }`}
                      >
                        Auto-deny ≥
                      </button>
                      <input
                        type="number"
                        min={1}
                        max={10}
                        placeholder="—"
                        value={t.auto_deny_ge ?? ''}
                        onChange={(e) => updateAutoDenyGte(t.tool_name, e.target.value)}
                        className={`w-11 rounded-r-lg border py-1 text-center font-mono text-xs transition-colors focus:outline-none ${
                          t.auto_deny_ge != null
                            ? 'border-rose-300 bg-rose-50/60 text-rose-900 font-bold focus:border-rose-500'
                            : 'border-slate-200 bg-white text-slate-400'
                        }`}
                      />
                    </div>

                    {/* 4. Always escalate */}
                    <button
                      type="button"
                      onClick={() => toggleAlwaysEscalate(t.tool_name)}
                      className={`rounded-lg border px-3 py-1 text-xs transition-colors font-mono cursor-pointer ${
                        t.always_escalate
                          ? 'bg-purple-50 text-purple-700 border-purple-300 font-bold shadow-2xs'
                          : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'
                      }`}
                    >
                      Always escalate
                    </button>
                  </div>
                </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Interactive Resolution Fallback Dialog Modal (matches Claude Code media_1788440131757.png) */}
      {activeResolveReview && (
        <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-lg w-full border border-[#e5e7eb] shadow-2xl overflow-hidden animate-in fade-in zoom-in duration-150">
            <div className="p-5 border-b border-[#e5e7eb] bg-rose-50/50">
              <div className="flex items-center gap-2.5 text-rose-700">
                <ShieldAlert className="w-5 h-5" />
                <h3 className="text-sm font-bold font-mono">
                  {activeResolveReview.rule_name
                    ? `Denied by rule: ${activeResolveReview.rule_name}`
                    : 'Interception Alert'}
                </h3>
              </div>
              <p className="text-xs text-[#4b5563] mt-1 font-mono">
                {activeResolveReview.explanation}
              </p>
            </div>

            <div className="p-5 space-y-4">
              <div className="p-3 bg-[#f9fafb] rounded-lg border border-[#e5e7eb] text-xs font-mono">
                <span className="text-[#6b7280] block mb-1 uppercase font-bold text-[10px]">
                  Attempted Command:
                </span>
                <span className="text-rose-700 font-bold">{activeResolveReview.tool_input}</span>
              </div>

              <div>
                <label className="text-xs font-bold text-[#111827] block mb-2 font-mono">
                  What would you like to do?
                </label>
                <div className="space-y-2">
                  {[
                    {
                      id: 'allow_once' as const,
                      num: '1',
                      title: 'Allow once',
                      desc: 'Execute this single tool call, maintain future protection.',
                    },
                    {
                      id: 'allow_session' as const,
                      num: '2',
                      title: 'Allow for session',
                      desc: 'Permit this specific tool/path for remainder of active session.',
                    },
                    {
                      id: 'deny' as const,
                      num: '3',
                      title: 'Deny',
                      desc: 'Reject tool call and return error message to agent.',
                    },
                    {
                      id: 'cancel' as const,
                      num: '4',
                      title: 'Cancel operation',
                      desc: 'Abort the active agent session immediately.',
                    },
                  ].map((opt) => (
                    <label
                      key={opt.id}
                      onClick={() => setResolveAction(opt.id)}
                      className={`flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition-all ${
                        resolveAction === opt.id
                          ? 'bg-[#f5f3ff] border-[#6366f1] shadow-xs'
                          : 'bg-white border-[#e5e7eb] hover:bg-[#fafafa]'
                      }`}
                    >
                      <input
                        type="radio"
                        name="resolveAction"
                        checked={resolveAction === opt.id}
                        onChange={() => setResolveAction(opt.id)}
                        className="mt-0.5 text-[#6366f1]"
                      />
                      <div className="flex-1">
                        <div className="text-xs font-bold text-[#111827] font-mono">
                          {opt.num}. {opt.title}
                        </div>
                        <div className="text-[11px] text-[#6b7280]">{opt.desc}</div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-xs font-medium text-[#4b5563] block mb-1 font-mono">
                  Developer Notes / Justification (optional):
                </label>
                <input
                  type="text"
                  placeholder="e.g. Verified harmless local mock credential"
                  value={resolveNotes}
                  onChange={(e) => setResolveNotes(e.target.value)}
                  className="w-full px-3 py-1.5 text-xs bg-[#f9fafb] border border-[#e5e7eb] rounded-lg focus:outline-none focus:ring-1 focus:ring-[#6366f1]"
                />
              </div>
            </div>

            <div className="p-4 bg-[#f9fafb] border-t border-[#e5e7eb] flex items-center justify-end gap-2">
              <button
                onClick={() => setActiveResolveReview(null)}
                className="px-3 py-1.5 text-xs font-medium text-[#4b5563] hover:text-[#111827] transition-colors"
              >
                Dismiss
              </button>
              <button
                onClick={handleResolveDecision}
                disabled={isResolving}
                className="px-4 py-1.5 text-xs font-medium bg-[#4f46e5] text-white rounded-lg hover:bg-[#4338ca] transition-colors shadow-xs"
              >
                {isResolving ? 'Submitting...' : 'Confirm Decision'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
