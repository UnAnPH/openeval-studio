import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  Code2,
  Copy,
  Folder,
  RefreshCw,
  Search,
  ShieldCheck,
  Terminal,
  Trash2,
  Upload,
  X,
  Zap,
} from 'lucide-react';
import { TranscriptCardView, TranscriptMessage } from './TranscriptCardView';
import { WatcherSession } from '../types';
import { DEMO_SESSIONS } from '../data/demoFixtures';

interface FragmentMatch {
  session_id: string;
  project_or_task: string;
  model: string;
  turn_start: number;
  turn_end: number;
  matched_role: string;
  matched_span: string;
  explanation: string;
  relevance_score: number;
  match_type?: 'dense' | 'lexical' | 'hybrid';
  dense_score?: number;
  lexical_score?: number;
  combined_score?: number;
}

interface SessionsViewProps {
  initialSessionId?: string | null;
  isDemoSeed?: boolean;
  onBack?: () => void;
}

export const SessionsView: React.FC<SessionsViewProps> = ({
  initialSessionId,
  isDemoSeed,
}) => {
  const [sessions, setSessions] = useState<WatcherSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(initialSessionId || null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [sessionFilter, setSessionFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'live' | 'blocked' | 'closed'>('all');

  // Transcript Search state
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [isSearching, setIsSearching] = useState<boolean>(false);
  const [fragmentMatches, setFragmentMatches] = useState<FragmentMatch[]>([]);
  const [visibleFragmentsCount, setVisibleFragmentsCount] = useState<number>(5);
  const [targetMessageId, setTargetMessageId] = useState<string | null>(null);

  // Log Ingest Modal state
  const [showIngestModal, setShowIngestModal] = useState<boolean>(false);
  const [ingestFilePath, setIngestFilePath] = useState<string>('');
  const [isIngesting, setIsIngesting] = useState<boolean>(false);
  const [ingestStatus, setIngestStatus] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Fetch sessions from Watcher backend (with automatic fallback to bundled demo fixtures on static hosts)
  const fetchSessions = async (autoSelect = false) => {
    try {
      setIsLoading(true);
      const res = await fetch('/api/v1/watcher/sessions?min_messages=1').catch(() => null);
      if (res && res.ok) {
        const data: WatcherSession[] = await res.json();
        if (Array.isArray(data) && data.length > 0) {
          setSessions(data);
          if (autoSelect || !selectedSessionId) {
            if (initialSessionId && data.some((s) => s.session_id === initialSessionId)) {
              setSelectedSessionId(initialSessionId);
            } else if (data.length > 0) {
              setSelectedSessionId(data[0].session_id);
            }
          }
          return;
        }
      }
      // Static Appwrite Sites fallback
      const fallback = DEMO_SESSIONS as unknown as WatcherSession[];
      setSessions(fallback);
      if (autoSelect || !selectedSessionId) {
        if (initialSessionId && fallback.some((s) => s.session_id === initialSessionId)) {
          setSelectedSessionId(initialSessionId);
        } else if (fallback.length > 0) {
          setSelectedSessionId(fallback[0].session_id);
        }
      }
    } catch (err) {
      console.warn('Backend unavailable, using bundled demo sessions:', err);
      const fallback = DEMO_SESSIONS as unknown as WatcherSession[];
      setSessions(fallback);
      if (fallback.length > 0 && !selectedSessionId) {
        setSelectedSessionId(fallback[0].session_id);
      }
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchSessions(true);
    const interval = setInterval(() => {
      fetchSessions(false);
    }, 4000);
    return () => clearInterval(interval);
  }, [initialSessionId]);

  // Sync selectedSessionId with URL hash
  const handleSelectSession = (id: string) => {
    setSelectedSessionId(id);
    setSearchQuery('');
    setFragmentMatches([]);
    const hash = `#sessions/${encodeURIComponent(id)}`;
    if (window.location.hash !== hash) {
      window.location.hash = hash;
    }
  };

  // Ensure demo mode only ever shows demo sessions (defense in depth)
  const displaySessions = useMemo(() => {
    if (isDemoSeed) {
      return sessions.filter(
        (s) => s.session_id.includes('demo') || s.session_id.startsWith('demo-')
      );
    }
    return sessions;
  }, [sessions, isDemoSeed]);

  const selectedSession = useMemo(
    () => displaySessions.find((s) => s.session_id === selectedSessionId) || null,
    [displaySessions, selectedSessionId]
  );

  // Session is blocked if any review denied/blocked (accept deny|block|reject vocab) or score ≥ 8, unless operator-allowed.
  const sessionIsBlocked = (s: WatcherSession) =>
    (s.trajectory?.reviews || []).some(
      (r) =>
        (r.decision === 'block' || r.decision === 'deny' || r.decision === 'reject' || (r.score || 0) >= 8) &&
        r.human_override !== 'allow' &&
        r.resolution_status !== 'human_approved'
    );

  const sessionIsLive = (s: WatcherSession) =>
    s.status === 'working' || s.status === 'active' || s.status === 'running';

  // Buckets for filter pills: ALL / LIVE / BLOCKED / CLOSED
  const totalCount = displaySessions.length;
  const liveCount = useMemo(
    () => displaySessions.filter((s) => sessionIsLive(s)).length,
    [displaySessions]
  );
  const blockedCount = useMemo(
    () => displaySessions.filter((s) => sessionIsBlocked(s)).length,
    [displaySessions]
  );
  const closedCount = useMemo(
    () => displaySessions.filter((s) => !sessionIsBlocked(s) && !sessionIsLive(s)).length,
    [displaySessions]
  );

  // Filter sessions list by query & status (All, Live, Blocked, or Closed)
  const filteredSessions = useMemo(() => {
    let list = displaySessions;
    if (statusFilter === 'live') {
      list = list.filter((s) => sessionIsLive(s));
    } else if (statusFilter === 'blocked') {
      list = list.filter((s) => sessionIsBlocked(s));
    } else if (statusFilter === 'closed') {
      list = list.filter((s) => !sessionIsBlocked(s) && !sessionIsLive(s));
    }

    if (!sessionFilter.trim()) return list;
    const q = sessionFilter.toLowerCase();
    return list.filter(
      (s) =>
        s.session_id.toLowerCase().includes(q) ||
        (s.title && s.title.toLowerCase().includes(q)) ||
        (s.project_name && s.project_name.toLowerCase().includes(q)) ||
        (s.agent_type && s.agent_type.toLowerCase().includes(q)) ||
        (s.model && s.model.toLowerCase().includes(q))
    );
  }, [sessions, sessionFilter, statusFilter]);

  // Auto-sync selectedSessionId if current selection is not visible in filtered list
  useEffect(() => {
    if (filteredSessions.length > 0) {
      const exists = filteredSessions.some((s) => s.session_id === selectedSessionId);
      if (!exists) {
        setSelectedSessionId(filteredSessions[0].session_id);
      }
    }
  }, [filteredSessions, selectedSessionId]);

  // Handle LLM Fragment Search
  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) {
      setFragmentMatches([]);
      return;
    }

    setIsSearching(true);
    try {
      const res = await fetch('/api/v1/search/fragments', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: searchQuery }),
      }).catch(() => null);
      if (res && res.ok) {
        const data: FragmentMatch[] = await res.json();
        setFragmentMatches(data);
        setVisibleFragmentsCount(5);
        return;
      }
      // Static Appwrite Sites client-side search fallback
      const q = searchQuery.toLowerCase();
      const matches: FragmentMatch[] = [];
      displaySessions.forEach((s) => {
        (s.trajectory?.messages || []).forEach((m, idx) => {
          const content = m.content || '';
          const thinking = m.thinking || '';
          const full = `${content} ${thinking}`;
          if (full.toLowerCase().includes(q)) {
            matches.push({
              session_id: s.session_id,
              project_or_task: s.project_name || s.session_id,
              model: s.model || s.agent_type,
              turn_start: idx + 1,
              turn_end: idx + 1,
              matched_role: m.role || 'assistant',
              matched_span: content.slice(0, 160) || thinking.slice(0, 160),
              explanation: `Direct lexical match for "${searchQuery}" in ${s.session_id} turn ${idx + 1}.`,
              relevance_score: 0.85,
              match_type: 'lexical',
              dense_score: 0.0,
              lexical_score: 0.85,
              combined_score: 0.85,
            });
          }
        });
      });
      setFragmentMatches(matches);
      setVisibleFragmentsCount(5);
    } catch (err) {
      console.error('Fragment search failed:', err);
    } finally {
      setIsSearching(false);
    }
  };

  // Handle Agent Log Ingest
  const handleIngest = async () => {
    if (!ingestFilePath.trim()) return;
    setIsIngesting(true);
    setIngestStatus(null);
    try {
      const res = await fetch('/api/v1/agent-logs/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: ingestFilePath }),
      });
      if (res.ok) {
        setIngestStatus('Successfully ingested agent log!');
        setIngestFilePath('');
        fetchSessions(true);
        setTimeout(() => setShowIngestModal(false), 1200);
      } else {
        const err = await res.json();
        setIngestStatus(`Error: ${err.detail || 'Ingestion failed'}`);
      }
    } catch {
      setIngestStatus('Error: Network or server connection failed');
    } finally {
      setIsIngesting(false);
    }
  };

  // Handle Clear Sessions
  const handleClearSessions = async () => {
    if (!window.confirm('Are you sure you want to clear all monitored sessions?')) return;
    try {
      await fetch('/api/v1/watcher/sessions/clear', { method: 'POST' });
      setSessions([]);
      setSelectedSessionId(null);
      setFragmentMatches([]);
    } catch (err) {
      console.error('Failed to clear sessions:', err);
    }
  };

  // Build clean TranscriptMessage items for TranscriptCardView
  const transcriptMessages: TranscriptMessage[] = useMemo(() => {
    if (!selectedSession || !selectedSession.trajectory) return [];

    const msgs = selectedSession.trajectory.messages || [];
    const tcs = selectedSession.trajectory.tool_calls || [];
    const trs = selectedSession.trajectory.tool_results || [];
    const reviews = selectedSession.trajectory.reviews || [];

    // Fallback tool results from reviews if tool_results is empty
    const fallbackToolResults = trs.length > 0
      ? trs
      : reviews.map((r) => ({
          tool_id: r.id,
          tool_name: r.tool_name,
          stdout: r.diff ? `Applied Diff:\n${r.diff}` : `Verdict: ${r.decision}\nExplanation: ${r.explanation || 'Approved operation.'}`,
          stderr: '',
          exit_code: r.decision === 'block' || r.decision === 'deny' || r.decision === 'reject' ? 1 : 0,
          duration_ms: r.latency_ms,
          is_error: r.decision === 'block' || r.decision === 'deny' || r.decision === 'reject',
        }));

    // If messages carry individual tool_calls (from updated Antigravity loader)
    const hasPerMessageTools = msgs.some((m: any) => m.tool_calls && m.tool_calls.length > 0);

    if (hasPerMessageTools) {
      return msgs.map((m: any, idx: number) => ({
        id: `msg-${idx}`,
        role: m.role as 'developer' | 'system' | 'user' | 'assistant',
        content: m.content || '',
        thinking: m.thinking || null,
        tool_calls: (m.tool_calls || []).map((tc: any) => ({
          tool_id: tc.tool_id,
          tool_name: tc.tool_name,
          arguments: tc.arguments || {},
          raw_input: tc.raw_input || undefined,
        })),
        tool_results: (m.tool_results || []).map((tr: any) => ({
          tool_id: tr.tool_id,
          tool_name: tr.tool_name,
          stdout: tr.stdout || '',
          stderr: tr.stderr || '',
          exit_code: tr.exit_code ?? 0,
          duration_ms: tr.duration_ms,
          is_error: tr.is_error ?? false,
        })),
        timestamp: m.timestamp,
      }));
    }

    // Otherwise, attach tools to the assistant turns
    const resultMsgs: TranscriptMessage[] = [];
    const assistantIndices = msgs
      .map((m, idx) => (m.role === 'assistant' ? idx : -1))
      .filter((idx) => idx !== -1);

    msgs.forEach((m, idx) => {
      const isLastAssistant = assistantIndices.length > 0 && idx === assistantIndices[assistantIndices.length - 1];

      resultMsgs.push({
        id: `msg-${idx}`,
        role: m.role as 'developer' | 'system' | 'user' | 'assistant',
        content: m.content || '',
        thinking: m.thinking || null,
        tool_calls: isLastAssistant
          ? tcs.map((tc) => ({
              tool_id: tc.tool_id,
              tool_name: tc.tool_name,
              arguments: tc.arguments || {},
              raw_input: tc.raw_input || undefined,
            }))
          : undefined,
        tool_results: isLastAssistant
          ? fallbackToolResults.map((tr) => ({
              tool_id: tr.tool_id,
              tool_name: tr.tool_name,
              stdout: tr.stdout || '',
              stderr: tr.stderr || '',
              exit_code: tr.exit_code ?? 0,
              duration_ms: tr.duration_ms,
              is_error: tr.is_error ?? false,
            }))
          : undefined,
        timestamp: m.timestamp,
      });
    });

    return resultMsgs;
  }, [selectedSession]);

  // Derived metrics from selectedSession
  const messagesCount = selectedSession?.trajectory?.messages?.length || 0;
  const toolCallsCount = selectedSession?.trajectory?.tool_calls?.length || 0;
  const reviews = selectedSession?.trajectory?.reviews || [];
  const blockedReviews = reviews.filter(
    (r) => r.decision === 'block' || r.decision === 'deny' || r.decision === 'reject' || r.score >= 8
  );
  const hasViolations = blockedReviews.length > 0;

  // Structured Task & Execution Synopsis (What the task was, what was accomplished, what happened)
  const sessionSynopsis = useMemo(() => {
    if (!selectedSession) return null;
    const msgs = selectedSession.trajectory?.messages || [];
    const tcs = selectedSession.trajectory?.tool_calls || [];
    const revs = selectedSession.trajectory?.reviews || [];

    // 1. What the task was: Extract first user prompt
    const firstUserMsg = msgs.find((m) => m.role === 'user');
    let taskDesc = '';
    if (firstUserMsg && firstUserMsg.content) {
      const raw = firstUserMsg.content;
      if (raw.includes('<USER_REQUEST>')) {
        const parts = raw.split('<USER_REQUEST>');
        if (parts[1]) taskDesc = parts[1].split('</USER_REQUEST>')[0].trim();
      } else {
        taskDesc = raw.trim();
      }
      if (taskDesc.includes('<ADDITIONAL_METADATA>')) {
        taskDesc = taskDesc.split('<ADDITIONAL_METADATA>')[0].trim();
      }
    }

    // 2. Parse final_summary if present
    const rawSummary = selectedSession.final_summary || (selectedSession as any).summary || '';
    let parsedTask = '';
    let parsedAccomplished = '';
    let parsedVerdict = '';

    if (rawSummary.includes('Task:') && rawSummary.includes('Accomplished:')) {
      const taskPart = rawSummary.split('Task:')[1]?.split('Accomplished:')[0]?.trim();
      const afterAcc = rawSummary.split('Accomplished:')[1];
      const accPart = afterAcc?.split('Verdict:')[0]?.trim();
      const verPart = afterAcc?.split('Verdict:')[1]?.trim();
      if (taskPart) parsedTask = taskPart;
      if (accPart) parsedAccomplished = accPart;
      if (verPart) parsedVerdict = verPart;
    }

    if (!taskDesc) {
      taskDesc = parsedTask || (selectedSession.title && !selectedSession.title.startsWith('Antigravity Session') ? selectedSession.title : 'Autonomous agent software engineering session.');
    }

    // 3. What was accomplished
    const filesModified: string[] = [];
    const filesViewed: string[] = [];
    const commandsRun: string[] = [];

    tcs.forEach((tc) => {
      const args = tc.arguments as any;
      if (tc.tool_name === 'write_to_file' || tc.tool_name === 'replace_file_content') {
        const p = args?.TargetFile || '';
        const name = (p.split('/').pop() || p).replace(/["']/g, '').trim();
        if (name && !filesModified.includes(name)) filesModified.push(name);
      } else if (tc.tool_name === 'view_file') {
        const p = args?.AbsolutePath || '';
        const name = (p.split('/').pop() || p).replace(/["']/g, '').trim();
        if (name && !filesViewed.includes(name)) filesViewed.push(name);
      } else if (tc.tool_name === 'run_command') {
        const cmd = args?.CommandLine || '';
        if (cmd) commandsRun.push(cmd);
      }
    });

    let accomplishedDesc = parsedAccomplished;
    if (!accomplishedDesc) {
      const parts: string[] = [];
      if (filesModified.length > 0) {
        parts.push(`Modified ${filesModified.length} file(s) (${filesModified.slice(0, 5).join(', ')}${filesModified.length > 5 ? '...' : ''})`);
      }
      if (filesViewed.length > 0) {
        parts.push(`inspected ${filesViewed.length} source file(s)`);
      }
      if (commandsRun.length > 0) {
        parts.push(`executed ${commandsRun.length} command(s)`);
      }
      accomplishedDesc = parts.length > 0
        ? `During this session, the agent performed active codebase analysis and development. It ${parts.join(', ')} across ${msgs.length} conversation turns.`
        : `Executed ${tcs.length} tool operation(s) across ${msgs.length} conversation turns in ${selectedSession.agent_type}.`;
    }

    // 4. What happened (Policy & Execution flow)
    const blockedRevs = revs.filter(
      (r) => r.decision === 'block' || r.decision === 'deny' || r.decision === 'reject' || r.score >= 8
    );
    let happenedDesc = parsedVerdict;
    if (!happenedDesc) {
      if (blockedRevs.length > 0) {
        happenedDesc = `Deterministic policy firewall intercepted and contained ${blockedRevs.length} critical operation(s) (Flagged: ${blockedRevs.map((r) => r.rule_name || r.tool_name).slice(0, 2).join(', ')}). Action was blocked or gated.`;
      } else {
        happenedDesc = `All ${tcs.length} tool executions passed security policy without violations. Monitored session status: '${selectedSession.status === 'completed' ? 'closed' : (selectedSession.status || 'closed')}'.`;
      }
    }

    return {
      task: taskDesc,
      accomplished: accomplishedDesc,
      happened: happenedDesc,
      filesModified,
      commandsRunCount: commandsRun.length,
    };
  }, [selectedSession]);

  // Agent badge styling
  const renderAgentBadge = (agentType: string) => {
    switch (agentType) {
      case 'antigravity':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold bg-indigo-50 text-indigo-700 border border-indigo-200">
            <Zap className="w-3 h-3 text-indigo-600" />
            Antigravity
          </span>
        );
      case 'claude_code':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold bg-amber-50 text-amber-700 border border-amber-200">
            <Terminal className="w-3 h-3 text-amber-600" />
            Claude Code
          </span>
        );
      case 'cursor':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold bg-teal-50 text-teal-700 border border-teal-200">
            <Code2 className="w-3 h-3 text-teal-600" />
            Cursor
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold bg-slate-100 text-slate-700 border border-slate-200">
            <Activity className="w-3 h-3 text-slate-500" />
            {agentType}
          </span>
        );
    }
  };

  return (
    <div className="flex flex-col h-full bg-[#fcfcfd] text-[#1e2029] font-sans p-6 space-y-5 overflow-hidden">
      {isDemoSeed && (
        <div className="sticky top-0 z-20 flex items-center justify-between gap-3 px-4 py-2.5 bg-amber-500/10 border border-amber-500/30 rounded-xl text-amber-900 text-xs font-medium shadow-xs shrink-0 backdrop-blur-sm">
          <div className="flex items-center gap-2.5">
            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-500 text-white tracking-wide uppercase">
              DEMO
            </span>
            <span>
              <strong>DEMO-ONLY DATA</strong> — Displaying simulated developer session fixtures for hosted demonstration. Live agent hooks are inactive in this environment.
            </span>
          </div>
        </div>
      )}

      {/* 1. TOP TOOLBAR CARD */}
      <div className="bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-base font-bold text-slate-900 tracking-tight flex items-center gap-2">
            <span>Sessions & Firewall</span>
          </h1>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowIngestModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-slate-900 text-white rounded-lg hover:bg-black transition-colors cursor-pointer shadow-xs"
          >
            <Upload className="w-3.5 h-3.5" />
            <span>Ingest Agent Log</span>
          </button>

          <button
            onClick={() => fetchSessions(false)}
            disabled={isLoading}
            className="p-1.5 text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-lg transition-colors cursor-pointer"
            title="Refresh sessions"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
          </button>

          {sessions.length > 0 && (
            <button
              onClick={handleClearSessions}
              className="p-1.5 text-rose-600 hover:text-rose-700 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
              title="Clear all sessions"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {/* 2. MAIN TWO-COLUMN MASTER-DETAIL LAYOUT */}
      <div className="flex-1 flex gap-5 min-h-0 overflow-hidden">
        {/* LEFT PART: CARD CONTAINING SESSIONS FILTER & MASTER LIST */}
        <div className="w-80 bg-white rounded-2xl border border-border-subtle shadow-sm flex flex-col shrink-0 overflow-hidden">
          {/* Filter & Status Tabs */}
          <div className="p-3 border-b border-[#e5e7eb] space-y-2">
            {/* Status Tabs: All, Live, Blocked, and Closed */}
            <div className="grid grid-cols-4 bg-slate-100 p-1 rounded-xl gap-1 text-center">
              <button
                type="button"
                onClick={() => setStatusFilter('all')}
                className={`py-1.5 px-1 rounded-lg transition-all cursor-pointer flex flex-col items-center justify-center ${
                  statusFilter === 'all'
                    ? 'bg-white text-slate-900 shadow-xs font-bold'
                    : 'text-slate-600 hover:text-slate-900 font-medium'
                }`}
              >
                <span className="text-[10px] uppercase tracking-wider font-bold">All</span>
                <span
                  className={`font-mono text-xs font-bold tabular-nums px-1.5 py-0.2 rounded-full ${
                    statusFilter === 'all'
                      ? 'bg-slate-200 text-slate-900'
                      : 'bg-slate-200/60 text-slate-600'
                  }`}
                >
                  {totalCount}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setStatusFilter('live')}
                className={`py-1.5 px-1 rounded-lg transition-all cursor-pointer flex flex-col items-center justify-center ${
                  statusFilter === 'live'
                    ? 'bg-white text-emerald-700 shadow-xs font-bold'
                    : liveCount > 0
                    ? 'text-emerald-600 font-bold hover:text-emerald-800'
                    : 'text-slate-600 hover:text-slate-900 font-medium'
                }`}
              >
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  <span className="text-[10px] uppercase tracking-wider font-bold">Live</span>
                </div>
                <span
                  className={`font-mono text-xs font-bold tabular-nums px-1 py-0.2 rounded-full ${
                    statusFilter === 'live'
                      ? 'bg-emerald-100 text-emerald-800'
                      : liveCount > 0
                      ? 'bg-emerald-50 text-emerald-600'
                      : 'bg-slate-200/60 text-slate-600'
                  }`}
                >
                  {liveCount}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setStatusFilter('blocked')}
                className={`py-1.5 px-1 rounded-lg transition-all cursor-pointer flex flex-col items-center justify-center ${
                  statusFilter === 'blocked'
                    ? 'bg-white text-rose-700 shadow-xs font-bold'
                    : blockedCount > 0
                    ? 'text-rose-600 font-bold hover:text-rose-800'
                    : 'text-slate-600 hover:text-slate-900 font-medium'
                }`}
              >
                <span className="text-[10px] uppercase tracking-wider font-bold">Blocked</span>
                <span
                  className={`font-mono text-xs font-bold tabular-nums px-1 py-0.2 rounded-full ${
                    statusFilter === 'blocked'
                      ? 'bg-rose-100 text-rose-800'
                      : blockedCount > 0
                      ? 'bg-rose-50 text-rose-600'
                      : 'bg-slate-200/60 text-slate-600'
                  }`}
                >
                  {blockedCount}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setStatusFilter('closed')}
                className={`py-1.5 px-1 rounded-lg transition-all cursor-pointer flex flex-col items-center justify-center ${
                  statusFilter === 'closed'
                    ? 'bg-white text-slate-900 shadow-xs font-bold'
                    : 'text-slate-600 hover:text-slate-900 font-medium'
                }`}
              >
                <span className="text-[10px] uppercase tracking-wider font-bold">Closed</span>
                <span
                  className={`font-mono text-xs font-bold tabular-nums px-1 py-0.2 rounded-full ${
                    statusFilter === 'closed'
                      ? 'bg-slate-200 text-slate-900'
                      : 'bg-slate-200/60 text-slate-600'
                  }`}
                >
                  {closedCount}
                </span>
              </button>
            </div>

            {/* Keyword Filter Input */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-2 text-slate-400" />
              <input
                type="text"
                placeholder="Filter sessions..."
                value={sessionFilter}
                onChange={(e) => setSessionFilter(e.target.value)}
                className="w-full pl-8 pr-3 py-1 text-xs bg-slate-50 border border-slate-200 rounded-md focus:outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
              />
            </div>
          </div>

          {/* Sessions Scrollable List */}
          <div className="flex-1 overflow-y-auto divide-y divide-slate-100">
            {filteredSessions.length === 0 ? (
              <div className="p-8 text-center text-xs text-slate-500 font-sans space-y-2">
                {isLoading ? (
                  <p>Loading monitored sessions...</p>
                ) : (
                  <p>No sessions found matching the current filter.</p>
                )}
              </div>
            ) : (
              filteredSessions.map((s) => {
                const isSelected = s.session_id === selectedSessionId;
                const sMsgCount = s.trajectory?.messages?.length || 0;
                const sToolCount = s.trajectory?.tool_calls?.length || 0;
                const sHasBlocked = sessionIsBlocked(s);
                const isWorking = s.status === 'working';

                return (
                  <div
                    key={s.session_id}
                    onClick={() => handleSelectSession(s.session_id)}
                    className={`p-3.5 cursor-pointer transition-all ${
                      isSelected
                        ? 'bg-indigo-50/70 border-l-4 border-l-indigo-600'
                        : 'hover:bg-slate-50/80 border-l-4 border-l-transparent'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-1.5 mb-1.5">
                      {renderAgentBadge(s.agent_type)}
                      <span
                        className={`inline-flex items-center gap-1 text-[10px] font-mono font-semibold px-2 py-0.5 rounded-full ${
                          sHasBlocked
                            ? 'bg-rose-50 text-rose-700 border border-rose-200'
                            : isWorking
                            ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                            : 'bg-slate-100 text-slate-600'
                        }`}
                      >
                        {isWorking && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />}
                        {sHasBlocked ? 'Blocked' : isWorking ? 'working' : s.status === 'completed' ? 'closed' : s.status || 'closed'}
                      </span>
                    </div>

                    <div
                      className="text-xs font-bold text-slate-900 truncate mb-1 leading-snug"
                      title={s.title || s.project_name || s.session_id}
                    >
                      {s.title || s.project_name || s.session_id}
                    </div>

                    <div className="flex items-center justify-between text-[11px] text-slate-500 font-mono">
                      <span className="truncate max-w-[120px] text-slate-400 font-semibold">{s.session_id}</span>
                      <span className="text-[10px] text-slate-400 tabular-nums shrink-0">
                        {sMsgCount} msgs · {sToolCount} tools
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* RIGHT PART: SESSION DETAILS, AUTOMATED ANALYSIS & TRANSCRIPT */}
        <div className="flex-1 flex flex-col overflow-y-auto space-y-5 pr-1 min-w-0">
          {!selectedSession ? (
            <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white rounded-2xl border border-border-subtle shadow-sm">
              <Folder className="w-10 h-10 text-slate-300 mb-3" />
              <h3 className="text-sm font-bold text-slate-800">No Session Selected</h3>
              <p className="text-xs text-slate-500 mt-1 max-w-sm">
                Select a session from the list on the left to inspect automated analysis, policy gating, and full transcript.
              </p>
            </div>
          ) : (
            <>
              {/* CARD 1: TOP CARD - AUTOMATED ANALYSIS & SUMMARY */}
              <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-3.5 shrink-0">
                {/* Meta Bar: Chat Title & Session Meta details */}
                <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-slate-100">
                  <div className="flex flex-col gap-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h2 className="text-base font-bold text-slate-900 tracking-tight">
                        {selectedSession.title || selectedSession.project_name || selectedSession.session_id}
                      </h2>
                      {selectedSession.title && (
                        <span className="text-[11px] font-mono px-2 py-0.5 rounded-md bg-slate-100 text-slate-500 font-medium">
                          {selectedSession.session_id}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-2.5 text-[11px] text-slate-500 shrink-0 font-mono">
                    <div className="flex items-center gap-1">
                      <Clock className="w-3 h-3 text-slate-400" />
                      <span>{selectedSession.created_at ? new Date(selectedSession.created_at).toLocaleTimeString() : 'Active'}</span>
                    </div>
                    <span>·</span>
                    <span className="font-semibold text-slate-700 tabular-nums">
                      {messagesCount} messages
                    </span>
                    <span>·</span>
                    <span className="font-semibold text-slate-700 tabular-nums">
                      {toolCallsCount} tools
                    </span>
                    <span
                      className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold ${
                        hasViolations
                          ? 'bg-rose-50 text-rose-700 border border-rose-200'
                          : selectedSession.status === 'working'
                          ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                          : 'bg-slate-100 text-slate-700'
                      }`}
                    >
                      {hasViolations ? 'Policy Intercept' : selectedSession.status === 'working' ? 'Live Working' : 'Closed'}
                    </span>
                  </div>
                </div>

                {/* Session Identity Info Pills */}
                <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
                  <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-slate-50 border border-slate-200 rounded-lg text-slate-700">
                    <span className="text-slate-400">Agent:</span>
                    <strong className="text-slate-900">{selectedSession.agent_type}</strong>
                    <span className="text-slate-500">({selectedSession.model})</span>
                  </div>

                  <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-slate-50 border border-slate-200 rounded-lg text-slate-700">
                    <span className="text-slate-400">Activity:</span>
                    <span className="text-indigo-600 font-semibold truncate max-w-md" title={selectedSession.current_activity || 'Idle'}>
                      {selectedSession.current_activity || 'Idle'}
                    </span>
                  </div>
                </div>

                {/* Full-width Working Directory Card */}
                <div className="flex items-center justify-between gap-3 p-2.5 px-3 bg-slate-50 border border-slate-200 rounded-xl text-xs font-mono">
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <Folder className="w-4 h-4 text-slate-400 shrink-0" />
                    <span className="text-slate-500 font-bold shrink-0">Working dir:</span>
                    <span
                      className="text-slate-800 font-semibold break-all select-all flex-1"
                      title={selectedSession.working_dir || '/workspace'}
                    >
                      {selectedSession.working_dir || '/workspace'}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => copyToClipboard(selectedSession.working_dir || '/workspace', 'wdir')}
                    className="flex items-center gap-1 text-[11px] font-mono text-slate-600 hover:text-slate-900 bg-white hover:bg-slate-100 border border-slate-200 px-2.5 py-1 rounded-lg transition-colors cursor-pointer shrink-0 shadow-2xs"
                    title="Copy working directory"
                  >
                    {copiedId === 'wdir' ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3 text-slate-400" />}
                    <span>{copiedId === 'wdir' ? 'Copied' : 'Copy'}</span>
                  </button>
                </div>

                {/* Session Narrative / Summary Text (Clean text, not cards) */}
                {sessionSynopsis && (
                  <div className="pt-3 text-xs text-slate-700 font-sans leading-relaxed space-y-2.5 select-text border-t border-slate-100">
                    <div>
                      <span className="font-bold font-mono text-[10.5px] uppercase tracking-wider text-indigo-700 mr-2 inline-block">Task:</span>
                      <span className="text-slate-800 leading-relaxed font-normal">{sessionSynopsis.task}</span>
                    </div>
                    <div>
                      <span className="font-bold font-mono text-[10.5px] uppercase tracking-wider text-emerald-700 mr-2 inline-block">Accomplished:</span>
                      <span className="text-slate-700 leading-relaxed font-normal">{sessionSynopsis.accomplished}</span>
                      {sessionSynopsis.filesModified.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 mt-1.5 pt-0.5">
                          {sessionSynopsis.filesModified.map((fn) => (
                            <span
                              key={fn}
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-mono text-[10px] bg-slate-100 text-slate-700 border border-slate-200/80"
                            >
                              <span className="text-slate-400">📄</span> {fn}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className={hasViolations ? 'text-rose-900 font-medium' : 'text-slate-600'}>
                      <span className={`font-bold font-mono text-[10.5px] uppercase tracking-wider mr-2 inline-block ${hasViolations ? 'text-rose-700' : 'text-slate-500'}`}>
                        Verdict:
                      </span>
                      <span className="leading-relaxed">{sessionSynopsis.happened}</span>
                    </div>
                  </div>
                )}
              </div>

              {/* CARD 2: RECOMMENDED ACTIONS */}
              <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-3 shrink-0">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold ${
                        hasViolations
                          ? 'bg-rose-50 text-rose-700 border border-rose-200'
                          : 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                      }`}
                    >
                      {hasViolations ? 'P1 REMEDIATE' : 'VERIFIED SAFE'}
                    </span>
                    <h3 className="text-xs font-bold text-slate-900">Recommended Action</h3>
                  </div>
                  <span className="text-[10px] font-mono text-slate-400">
                    {hasViolations ? `cites ${blockedReviews.length} policy alerts` : 'policy gate approved'}
                  </span>
                </div>

                {hasViolations ? (
                  <div className="space-y-2">
                    {blockedReviews.map((br, idx) => {
                      const targetMsgIdx = transcriptMessages.findIndex((m) =>
                        m.tool_calls && m.tool_calls.some((tc) =>
                          tc.tool_name === br.tool_name &&
                          (!br.tool_input || !tc.raw_input || tc.raw_input.includes(br.tool_input) || br.tool_input.includes(tc.raw_input))
                        )
                      );
                      const matchedIdx = targetMsgIdx !== -1
                        ? targetMsgIdx
                        : transcriptMessages.findIndex((m) => m.tool_calls && m.tool_calls.some((tc) => tc.tool_name === br.tool_name));
                      const finalIdx = matchedIdx !== -1 ? matchedIdx : Math.max(0, transcriptMessages.findIndex((m) => m.tool_calls && m.tool_calls.length > 0));
                      const targetMsgId = `msg-${finalIdx >= 0 ? finalIdx : 0}`;

                      return (
                        <div
                          key={idx}
                          onClick={() => {
                            setTargetMessageId(null);
                            setTimeout(() => setTargetMessageId(targetMsgId), 50);
                          }}
                          className="text-xs text-rose-800 leading-relaxed bg-rose-50/60 hover:bg-rose-100/70 p-3.5 rounded-xl border border-rose-200/80 font-sans space-y-1.5 cursor-pointer transition-all hover:shadow-xs hover:border-rose-300 group select-none"
                          title={`Click to jump to message #${finalIdx + 1} in transcript`}
                        >
                          <div className="font-bold text-rose-950 flex items-center justify-between">
                            <div className="flex items-center gap-1.5">
                              <AlertTriangle className="w-3.5 h-3.5 text-rose-600 shrink-0" />
                              <span>Tool Blocked: <code className="font-mono text-indigo-600">{br.tool_name}</code></span>
                              {br.rule_name && <span className="font-mono text-[10px] text-rose-600">({br.rule_name})</span>}
                            </div>
                            <span className="text-[11px] font-mono text-indigo-600 font-semibold flex items-center gap-1 group-hover:underline">
                              Jump to message #{finalIdx + 1} &rarr;
                            </span>
                          </div>
                          <div className="text-slate-700">
                            <strong className="text-slate-900">DO THIS: </strong>
                            {br.explanation || 'Revert the unauthorized operation or modify execution parameters to satisfy safety gating.'}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="text-xs text-slate-700 leading-relaxed bg-emerald-50/40 p-3.5 rounded-xl border border-emerald-100 font-sans flex items-start gap-2.5">
                    <ShieldCheck className="w-4 h-4 text-emerald-600 shrink-0 mt-0.5" />
                    <div>
                      <div className="font-bold text-slate-900">
                        Safe Execution: Verified against security policies
                      </div>
                      <div className="text-slate-600 mt-0.5">
                        <strong className="text-slate-800">DO THIS: </strong>
                        No intervention required. All operations comply with execution safety thresholds.
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* CARD 3: FULL TRANSCRIPT CARD WITH LLM SEARCH */}
              <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-4">
                {/* Search Bar & Title Header */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-100">
                  <div className="flex items-center gap-2">
                    <Code2 className="w-4 h-4 text-indigo-600" />
                    <h3 className="text-xs font-bold text-slate-900">Full Transcript</h3>
                    <span className="text-[11px] font-mono text-slate-400">
                      ({messagesCount} turns · {toolCallsCount} tools)
                    </span>
                  </div>

                  {/* LLM Fragment Search Bar */}
                  <form onSubmit={handleSearch} className="flex items-center gap-2">
                    <div className="relative">
                      <Search className="w-3.5 h-3.5 absolute left-2.5 top-2 text-slate-400" />
                      <input
                        type="text"
                        placeholder="Search transcript fragments..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="pl-8 pr-3 py-1 text-xs bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-500 font-mono w-64"
                      />
                    </div>
                    <button
                      type="submit"
                      disabled={isSearching}
                      className="px-3 py-1 text-xs font-semibold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors cursor-pointer"
                    >
                      {isSearching ? 'Searching...' : 'LLM Search'}
                    </button>
                    {fragmentMatches.length > 0 && (
                      <button
                        type="button"
                        onClick={() => {
                          setFragmentMatches([]);
                          setSearchQuery('');
                        }}
                        className="p-1 text-slate-400 hover:text-slate-600 cursor-pointer"
                        title="Clear search"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </form>
                </div>

                {/* High-Signal Extracted Fragment Chips */}
                {fragmentMatches.length > 0 && (
                  <div className="p-3.5 bg-indigo-50/60 rounded-xl border border-indigo-100 space-y-2.5">
                    <div className="flex items-center justify-between">
                      <div className="text-[11px] font-mono font-bold text-indigo-900">
                        {fragmentMatches.length} High-Signal Fragments Extracted (Click to jump to turn):
                      </div>
                      <span className="text-[10px] font-mono text-indigo-600">
                        Showing {Math.min(visibleFragmentsCount, fragmentMatches.length)} of {fragmentMatches.length}
                      </span>
                    </div>

                    <div className="flex flex-col space-y-2">
                      {fragmentMatches.slice(0, visibleFragmentsCount).map((m, i) => {
                        const targetTurnIdx = Math.max(0, m.turn_start - 1);
                        const targetId = `msg-${targetTurnIdx}`;
                        return (
                          <div
                            key={i}
                            onClick={() => {
                              if (m.session_id && m.session_id !== selectedSessionId) {
                                setSelectedSessionId(m.session_id);
                              }
                              setTargetMessageId(null);
                              setTimeout(() => setTargetMessageId(targetId), 60);
                            }}
                            className="p-3 bg-white hover:bg-indigo-50/50 rounded-xl border border-indigo-100 hover:border-indigo-300 text-xs font-sans space-y-1.5 shadow-2xs cursor-pointer transition-all group select-none"
                            title={`Click to jump to Turn #${m.turn_start} in transcript`}
                          >
                            <div className="flex items-center justify-between text-[10px] font-mono text-slate-500">
                              <div className="flex items-center gap-1.5">
                                <span className="font-bold text-indigo-700">Turn #{m.turn_start}</span>
                                {m.match_type === 'dense' && (
                                  <span className="px-1.5 py-0.2 rounded-md bg-purple-100 text-purple-700 font-semibold text-[9px] uppercase">
                                    Dense Vector
                                  </span>
                                )}
                                {m.match_type === 'hybrid' && (
                                  <span className="px-1.5 py-0.2 rounded-md bg-indigo-100 text-indigo-700 font-semibold text-[9px] uppercase">
                                    Hybrid
                                  </span>
                                )}
                                {m.match_type === 'lexical' && (
                                  <span className="px-1.5 py-0.2 rounded-md bg-slate-100 text-slate-600 font-semibold text-[9px] uppercase">
                                    Lexical
                                  </span>
                                )}
                                <span className="text-slate-400">({m.matched_role})</span>
                              </div>
                              <div className="flex items-center gap-2">
                                <span className="font-semibold text-slate-700">
                                  Match: {Math.round((m.combined_score || m.relevance_score) * 100)}%
                                </span>
                                <span className="text-[11px] font-mono text-indigo-600 font-semibold group-hover:underline flex items-center gap-0.5">
                                  Jump &rarr;
                                </span>
                              </div>
                            </div>
                            <div className="text-xs font-mono text-slate-800 line-clamp-2 bg-slate-50/80 p-2 rounded-lg border border-slate-100">
                              "{m.matched_span}"
                            </div>
                            <div className="text-[11px] text-slate-500 italic">
                              💡 {m.explanation}
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    {fragmentMatches.length > 5 && (
                      <div className="pt-1 flex items-center justify-center">
                        {visibleFragmentsCount < fragmentMatches.length ? (
                          <button
                            type="button"
                            onClick={() => setVisibleFragmentsCount(fragmentMatches.length)}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-indigo-700 bg-white hover:bg-indigo-50 border border-indigo-200 rounded-lg shadow-2xs transition-colors cursor-pointer"
                          >
                            <span>Show More ({fragmentMatches.length - visibleFragmentsCount} more)</span>
                            <ChevronDown className="w-3.5 h-3.5" />
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => setVisibleFragmentsCount(5)}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-indigo-700 bg-white hover:bg-indigo-50 border border-indigo-200 rounded-lg shadow-2xs transition-colors cursor-pointer"
                          >
                            <span>Show Less</span>
                            <ChevronUp className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Transcript Message Stream via TranscriptCardView */}
                {transcriptMessages.length === 0 ? (
                  <div className="p-8 text-center text-xs text-slate-400 font-mono">
                    No transcript messages recorded for this session.
                  </div>
                ) : (
                  <TranscriptCardView
                    messages={transcriptMessages}
                    highlightQuery={searchQuery}
                    targetMessageId={targetMessageId}
                    onTargetConsumed={() => setTargetMessageId(null)}
                  />
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Ingest Agent Log Modal */}
      {showIngestModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4">
          <div className="bg-white rounded-2xl border border-border-subtle shadow-xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-slate-900">Ingest Monitored Agent Log</h3>
              <button
                onClick={() => setShowIngestModal(false)}
                className="p-1 text-slate-400 hover:text-slate-700 cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <p className="text-xs text-slate-600">
              Provide an absolute file path to a Claude Code (<code className="font-mono text-indigo-600">.jsonl</code>) or Antigravity (<code className="font-mono text-indigo-600">.jsonl</code>) log to ingest into OpenEval.
            </p>

            <div className="space-y-1.5">
              <label className="text-[11px] font-mono font-bold text-slate-700">Log File Path</label>
              <input
                type="text"
                placeholder="/Users/.../.claude/sessions/session.jsonl"
                value={ingestFilePath}
                onChange={(e) => setIngestFilePath(e.target.value)}
                className="w-full px-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
              />
            </div>

            {ingestStatus && (
              <div
                className={`p-2.5 rounded-lg text-xs font-mono ${
                  ingestStatus.startsWith('Error')
                    ? 'bg-rose-50 text-rose-700 border border-rose-200'
                    : 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                }`}
              >
                {ingestStatus}
              </div>
            )}

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                onClick={() => setShowIngestModal(false)}
                className="px-3 py-1.5 text-xs text-slate-600 hover:text-slate-800 cursor-pointer"
              >
                Cancel
              </button>
              <button
                onClick={handleIngest}
                disabled={isIngesting || !ingestFilePath.trim()}
                className="px-4 py-1.5 text-xs font-bold bg-slate-900 text-white rounded-lg hover:bg-black disabled:opacity-50 transition-colors cursor-pointer"
              >
                {isIngesting ? 'Ingesting...' : 'Ingest Log'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
