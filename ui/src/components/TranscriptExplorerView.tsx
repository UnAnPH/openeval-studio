import React, { useEffect, useState } from 'react';
import {
  Search,
  Shield,
  Trash2,
  Upload,
} from 'lucide-react';
import { TranscriptCardView, TranscriptMessage } from './TranscriptCardView';
import { WatcherSession } from '../types';

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
}

interface ReactiveMetrics {
  total_sessions: number;
  evaluated_sessions: number;
  passed_sessions: number;
  failed_sessions: number;
  raw_pass_rate: number;
  effective_pass_rate: number;
  human_overrides_count: number;
  behavioral_tag_counts: Record<string, number>;
}

export const TranscriptExplorerView: React.FC = () => {
  const [sessions, setSessions] = useState<WatcherSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [fragmentMatches, setFragmentMatches] = useState<FragmentMatch[]>([]);
  const [, setReactiveMetrics] = useState<ReactiveMetrics | null>(null);

  // Annotation state
  const [humanVerdict, setHumanVerdict] = useState<'PASS' | 'FAIL' | null>(null);
  const [annotationNotes, setAnnotationNotes] = useState('');
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [isSavingAnnotation, setIsSavingAnnotation] = useState(false);
  const [annotationSuccess, setAnnotationSuccess] = useState(false);
  const [annotatorId, setAnnotatorId] = useState(() => {
    try {
      return localStorage.getItem('openeval_annotator_id') || 'eval-reviewer';
    } catch {
      return 'eval-reviewer';
    }
  });

  // Ingest state
  const [showIngestModal, setShowIngestModal] = useState(false);
  const [ingestFilePath, setIngestFilePath] = useState('');
  const [isIngesting, setIsIngesting] = useState(false);
  const [ingestStatus, setIngestStatus] = useState<string | null>(null);

  // Fetch sessions and reactive metrics
  const fetchSessionsAndMetrics = async () => {
    try {
      const res = await fetch('/api/v1/watcher/sessions').catch(() => null);
      if (res && res.ok) {
        const data: WatcherSession[] = await res.json();
        if (Array.isArray(data)) {
          setSessions(data);
          if (!selectedSessionId && data.length > 0) {
            setSelectedSessionId(data[0].session_id);
          }
        }
      }

      const mRes = await fetch('/api/v1/metrics/reactive').catch(() => null);
      if (mRes && mRes.ok) {
        const mData: ReactiveMetrics = await mRes.json();
        setReactiveMetrics(mData);
      }
    } catch {
      // offline fallback
    }
  };

  useEffect(() => {
    fetchSessionsAndMetrics();
  }, []);

  const selectedSession = sessions.find((s) => s.session_id === selectedSessionId);

  // Set annotation state when selected session changes
  useEffect(() => {
    if (selectedSession) {
      setHumanVerdict(selectedSession.human_verdict_override || (selectedSession.passed ? 'PASS' : selectedSession.passed === false ? 'FAIL' : null));
      setAnnotationNotes(selectedSession.human_notes || '');
      const tags = selectedSession.audit_overrides ? Object.keys(selectedSession.audit_overrides) : [];
      setActiveTags(tags);
    }
  }, [selectedSessionId]);

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
      });
      if (res.ok) {
        const data: FragmentMatch[] = await res.json();
        setFragmentMatches(data);
      }
    } catch (err) {
      console.error('Fragment search failed:', err);
    } finally {
      setIsSearching(false);
    }
  };

  // Handle Collaborative Annotation Save
  const handleSaveAnnotation = async () => {
    if (!selectedSessionId) return;
    setIsSavingAnnotation(true);
    try {
      const res = await fetch(`/api/v1/sessions/${selectedSessionId}/annotate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          human_verdict: humanVerdict,
          notes: annotationNotes,
          tags: activeTags,
          annotator_id: annotatorId,
          reviewer: annotatorId,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.reactive_metrics) {
          setReactiveMetrics(data.reactive_metrics);
        }
        setAnnotationSuccess(true);
        setTimeout(() => setAnnotationSuccess(false), 2500);
        fetchSessionsAndMetrics();
      }
    } catch (err) {
      console.error('Annotation failed:', err);
    } finally {
      setIsSavingAnnotation(false);
    }
  };

  // Handle Ingest
  const handleIngestFile = async () => {
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
        fetchSessionsAndMetrics();
        setTimeout(() => setShowIngestModal(false), 1500);
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

  // Handle Purge/Reset Sessions
  const handleClearSessions = async () => {
    try {
      await fetch('/api/v1/watcher/sessions/clear', { method: 'POST' });
      setSessions([]);
      setSelectedSessionId(null);
      setFragmentMatches([]);
      fetchSessionsAndMetrics();
    } catch (err) {
      console.error('Failed to clear sessions:', err);
    }
  };

  // Format session messages into TranscriptMessages
  const transcriptMessages: TranscriptMessage[] = [];
  if (selectedSession && selectedSession.trajectory) {
    if (selectedSession.trajectory.messages && selectedSession.trajectory.messages.length > 0) {
      const allToolCalls = (selectedSession.trajectory.tool_calls || []).map((tc) => ({
        tool_id: tc.tool_id,
        tool_name: tc.tool_name,
        arguments: tc.arguments,
        raw_input: tc.raw_input || undefined,
      }));

      const allToolResults = (selectedSession.trajectory.tool_results && selectedSession.trajectory.tool_results.length > 0)
        ? selectedSession.trajectory.tool_results.map((tr) => ({
            tool_id: tr.tool_id,
            tool_name: tr.tool_name,
            stdout: tr.stdout,
            stderr: tr.stderr,
            exit_code: tr.exit_code,
            duration_ms: tr.duration_ms,
            is_error: tr.is_error,
          }))
        : (selectedSession.trajectory.reviews || []).map((r) => ({
            tool_id: r.id,
            tool_name: r.tool_name,
            stdout: r.diff ? `Applied Diff:\n${r.diff}` : `Verdict: ${r.decision}\nExplanation: ${r.explanation || 'Approved operation.'}`,
            exit_code: r.decision === 'block' ? 1 : 0,
            duration_ms: r.latency_ms,
            is_error: r.decision === 'block',
          }));

      const assistantIndices = selectedSession.trajectory.messages
        .map((m, idx) => (m.role === 'assistant' ? idx : -1))
        .filter((idx) => idx !== -1);

      selectedSession.trajectory.messages.forEach((m, idx) => {
        const isAssistant = m.role === 'assistant';
        const isTargetAssistant = isAssistant && (
          assistantIndices.length <= 1 ||
          idx === assistantIndices[assistantIndices.length - 1]
        );

        transcriptMessages.push({
          id: `m-${idx}`,
          role: m.role as 'developer' | 'system' | 'user' | 'assistant',
          content: m.content,
          thinking: m.thinking,
          tool_calls: isTargetAssistant ? allToolCalls : undefined,
          tool_results: isTargetAssistant ? allToolResults : undefined,
        });
      });
    } else if (selectedSession.trajectory.reviews && selectedSession.trajectory.reviews.length > 0) {
      // Synthesize high-readability cards from intercepted reviews
      selectedSession.trajectory.reviews.forEach((r, idx) => {
        transcriptMessages.push({
          id: r.id || `rev-${idx}`,
          role: 'assistant',
          content: r.explanation ? `Decision [${r.decision.toUpperCase()}]: ${r.explanation}` : `Tool Operation: ${r.tool_name}`,
          thinking: r.rule_name
            ? `Evaluated under rule: ${r.rule_name}. Policy stage: ${r.stage}. Severity score: ${r.score}/10.`
            : `Policy stage: ${r.stage}. Severity score: ${r.score}/10. Latency: ${Math.round(r.latency_ms)}ms.`,
          tool_calls: [
            {
              tool_id: r.id,
              tool_name: r.tool_name,
              arguments: { input: r.tool_input },
              raw_input: r.tool_input,
            },
          ],
          tool_results: [
            {
              tool_id: r.id,
              tool_name: r.tool_name,
              stdout: r.diff ? `Proposed Diff:\n${r.diff}` : `Verdict: ${r.decision}`,
              exit_code: r.decision === 'block' ? 1 : 0,
              duration_ms: r.latency_ms,
              is_error: r.decision === 'block',
            },
          ],
        });
      });
    }
  }

  return (
    <div className="flex flex-col h-full bg-[#fcfcfd] text-[#1e2029] font-sans p-6 space-y-4">
      {/* 1. Card Block Header */}
      <div className="bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm flex items-center justify-between shrink-0">
        <h1 className="text-base font-bold text-slate-900 tracking-tight flex items-center gap-2.5">Transcript Explorer</h1>

        <div className="flex items-center gap-2.5">
          {/* Ingest Button */}
          <button
            onClick={() => setShowIngestModal(true)}
            className="flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-semibold bg-slate-900 text-white rounded-xl hover:bg-black transition-all shadow-xs cursor-pointer"
          >
            <Upload className="w-3.5 h-3.5" />
            Ingest Agent Log
          </button>

          {/* Clear All Sessions Button */}
          {sessions.length > 0 && (
            <button
              onClick={handleClearSessions}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50 border border-rose-200 rounded-xl transition-all cursor-pointer"
              title="Purge all sessions and reset to empty state"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Two-Stage Search Input Bar */}
      <div className="px-6 py-3 bg-white border-b border-[#e5e7eb] shrink-0">
        <form onSubmit={handleSearch} className="flex items-center gap-2 max-w-4xl">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-[#9ca3af]" />
            <input
              type="text"
              placeholder="Search transcript fragments, covert scheming thoughts, or tool commands..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-3 py-1.5 text-xs bg-[#f9fafb] border border-[#e5e7eb] rounded-lg focus:outline-none focus:ring-1 focus:ring-[#6366f1] font-mono"
            />
          </div>
          <button
            type="submit"
            disabled={isSearching}
            className="px-4 py-1.5 text-xs font-medium bg-[#4f46e5] text-white rounded-lg hover:bg-[#4338ca] transition-colors cursor-pointer"
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
              className="px-3 py-1.5 text-xs text-slate-500 hover:text-slate-800"
            >
              Clear
            </button>
          )}
        </form>

        {/* Fragment Search Results Preview */}
        {fragmentMatches.length > 0 && (
          <div className="mt-3 p-3 bg-[#f5f3ff] rounded-xl border border-indigo-100 space-y-2">
            <div className="text-[11px] font-mono font-bold text-indigo-900 flex items-center justify-between">
              <span>{fragmentMatches.length} High-Signal Fragments Extracted:</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {fragmentMatches.map((m, i) => (
                <div
                  key={i}
                  onClick={() => setSelectedSessionId(m.session_id)}
                  className="p-2.5 bg-white rounded-lg border border-indigo-100 shadow-2xs hover:border-indigo-300 cursor-pointer transition-all"
                >
                  <div className="flex items-center justify-between text-[10px] font-mono text-slate-500 mb-1">
                    <span className="font-bold text-indigo-700">{m.project_or_task}</span>
                    <span>Turn #{m.turn_start}</span>
                  </div>
                  <div className="text-xs text-slate-800 font-mono line-clamp-2 mb-1">
                    "{m.matched_span}"
                  </div>
                  <div className="text-[10px] text-slate-500 italic">
                    💡 {m.explanation}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Main Two-Column View */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left Column: Sessions List */}
        <div className="w-72 border-r border-[#e5e7eb] bg-white flex flex-col shrink-0">
          <div className="p-3 border-b border-[#e5e7eb] text-xs font-mono text-[#6b7280]">
            Sessions ({sessions.length})
          </div>
          <div className="flex-1 overflow-y-auto divide-y divide-[#f3f4f6]">
            {sessions.length === 0 ? (
              <div className="p-8 text-center text-xs text-[#9ca3af] font-mono">
                No sessions available. Ingest a log or run an agent.
              </div>
            ) : (
              sessions.map((s) => {
                const isSelected = s.session_id === selectedSessionId;
                return (
                  <div
                    key={s.session_id}
                    onClick={() => setSelectedSessionId(s.session_id)}
                    className={`p-3 cursor-pointer transition-all ${
                      isSelected
                        ? 'bg-[#f5f3ff] border-l-4 border-l-[#6366f1]'
                        : 'hover:bg-[#f9fafb]'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-bold text-[#111827] truncate" title={s.title || s.project_name || s.task_id}>
                        {s.title || s.project_name || s.task_id}
                      </span>
                      <span
                        className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded ${
                          s.passed === true
                            ? 'bg-emerald-50 text-emerald-700'
                            : s.passed === false
                            ? 'bg-rose-50 text-rose-700'
                            : 'bg-slate-100 text-slate-600'
                        }`}
                      >
                        {s.human_verdict_override || (s.passed ? 'PASS' : s.passed === false ? 'FAIL' : 'UNRATED')}
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-[10px] text-[#9ca3af] font-mono">
                      <span className="truncate max-w-[120px]">{s.model}</span>
                      <span>{s.agent_type}</span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right Column: Active Session Transcript & Collaborative Annotation Bar */}
        <div className="flex-1 flex flex-col overflow-hidden bg-[#fcfcfd]">
          {selectedSession ? (
            <>
              {/* Collaborative Annotation Toolbar */}
              <div className="px-6 py-3 bg-white border-b border-[#e5e7eb] flex items-center justify-between shrink-0 gap-4">
                {/* Verdict Override Buttons */}
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-slate-500 font-semibold">Verdict:</span>
                  <button
                    onClick={() => setHumanVerdict('PASS')}
                    className={`px-3 py-1 text-xs font-mono font-bold rounded-lg transition-all ${
                      humanVerdict === 'PASS'
                        ? 'bg-emerald-600 text-white shadow-xs'
                        : 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                    }`}
                  >
                    PASS
                  </button>
                  <button
                    onClick={() => setHumanVerdict('FAIL')}
                    className={`px-3 py-1 text-xs font-mono font-bold rounded-lg transition-all ${
                      humanVerdict === 'FAIL'
                        ? 'bg-rose-600 text-white shadow-xs'
                        : 'bg-rose-50 text-rose-700 hover:bg-rose-100'
                    }`}
                  >
                    FAIL
                  </button>
                </div>

                {/* Behavioral Tags */}
                <div className="flex items-center gap-1.5 text-xs font-mono">
                  {['@scheming', '@eval_awareness', '@unfaithful_cot', '@credential_leak'].map((tag) => {
                    const isSelected = activeTags.includes(tag);
                    return (
                      <button
                        key={tag}
                        onClick={() => {
                          setActiveTags((prev) =>
                            isSelected ? prev.filter((t) => t !== tag) : [...prev, tag]
                          );
                        }}
                        className={`px-2 py-0.5 rounded text-[10px] font-mono transition-all ${
                          isSelected
                            ? 'bg-indigo-600 text-white font-bold'
                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                        }`}
                      >
                        {tag}
                      </button>
                    );
                  })}
                </div>

                {/* Notes & Save Button */}
                <div className="flex items-center gap-2 flex-1 max-w-md">
                  <input
                    type="text"
                    placeholder="Annotator id"
                    value={annotatorId}
                    onChange={(e) => {
                      const v = e.target.value;
                      setAnnotatorId(v);
                      try {
                        localStorage.setItem('openeval_annotator_id', v);
                      } catch {
                        /* ignore */
                      }
                    }}
                    className="w-28 px-2 py-1 text-xs bg-[#f9fafb] border border-[#e5e7eb] rounded-lg font-mono"
                    title="Shared annotator id for collaborative review"
                  />
                  <input
                    type="text"
                    placeholder="Review notes..."
                    value={annotationNotes}
                    onChange={(e) => setAnnotationNotes(e.target.value)}
                    className="flex-1 px-3 py-1 text-xs bg-[#f9fafb] border border-[#e5e7eb] rounded-lg font-mono"
                  />
                  <button
                    onClick={handleSaveAnnotation}
                    disabled={isSavingAnnotation}
                    className="px-3 py-1 text-xs font-mono font-bold bg-[#111827] text-white rounded-lg hover:bg-black transition-colors"
                  >
                    {isSavingAnnotation ? 'Saving...' : annotationSuccess ? 'Saved!' : 'Save'}
                  </button>
                </div>
              </div>

              {/* High-Readability Transcript Cards */}
              <div className="flex-1 overflow-y-auto p-6">
                <TranscriptCardView
                  messages={transcriptMessages}
                  highlightQuery={searchQuery}
                  onAddTag={(_msgId, tag) => {
                    setActiveTags((prev) => (prev.includes(tag) ? prev : [...prev, tag]));
                  }}
                />
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-12 text-center text-xs text-[#9ca3af] font-mono">
              <Shield className="w-10 h-10 text-slate-300 mb-2 stroke-1" />
              <span className="text-sm font-semibold text-slate-600">No Session Selected</span>
              <p className="text-slate-400 mt-1 max-w-sm">
                Select a session on the left or search transcript fragments using the LLM query box.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Ingest Modal */}
      {showIngestModal && (
        <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full border border-[#e5e7eb] shadow-2xl p-6 space-y-4">
            <h3 className="text-sm font-bold text-[#111827]">Universal Coding Agent Ingestion</h3>
            <p className="text-xs text-slate-600">
              Enter an absolute path to a Claude Code session (<code className="font-mono text-indigo-600">.jsonl</code>) or Cursor session (<code className="font-mono text-indigo-600">.json</code>):
            </p>
            <input
              type="text"
              placeholder="/Users/.../.claude/sessions/session.jsonl"
              value={ingestFilePath}
              onChange={(e) => setIngestFilePath(e.target.value)}
              className="w-full px-3 py-2 text-xs bg-[#f9fafb] border border-[#e5e7eb] rounded-lg font-mono focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
            {ingestStatus && (
              <div className={`text-xs font-mono ${ingestStatus.startsWith('Error') ? 'text-rose-600' : 'text-emerald-600'}`}>
                {ingestStatus}
              </div>
            )}
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                onClick={() => setShowIngestModal(false)}
                className="px-3 py-1.5 text-xs text-slate-600 hover:text-slate-900"
              >
                Cancel
              </button>
              <button
                onClick={handleIngestFile}
                disabled={isIngesting}
                className="px-4 py-1.5 text-xs font-medium bg-[#4f46e5] text-white rounded-lg hover:bg-[#4338ca]"
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
