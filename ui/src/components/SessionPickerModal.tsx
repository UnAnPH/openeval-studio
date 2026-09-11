import React, { useMemo, useState } from 'react';
import {
  Search,
  X,
  Clock,
  Terminal,
  MessageSquare,
  Zap,
  Activity,
  AlertTriangle,
} from 'lucide-react';
import { WatcherSession } from '../types';

export interface SessionPickerModalProps {
  isOpen: boolean;
  title?: string;
  selectedSessionId: string;
  sessions: WatcherSession[];
  onSelect: (sessionId: string) => void;
  onClose: () => void;
}

export const sessionIsBlocked = (s: WatcherSession): boolean =>
  (s.trajectory?.reviews || []).some(
    (r) =>
      (r.decision === 'block' || r.decision === 'deny' || r.decision === 'reject' || (r.score || 0) >= 8) &&
      r.human_override !== 'allow' &&
      r.resolution_status !== 'human_approved'
  );

export const SessionPickerModal: React.FC<SessionPickerModalProps> = ({
  isOpen,
  title = 'Select Monitored Agent Session',
  selectedSessionId,
  sessions,
  onSelect,
  onClose,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [agentFilter, setAgentFilter] = useState('all');
  const [modelFilter, setModelFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState<'all' | 'blocked' | 'closed'>('all');
  const [sortBy, setSortBy] = useState<'newest' | 'oldest' | 'messages' | 'tools' | 'duration'>('newest');

  // Unique agents & models
  const uniqueAgents = useMemo(() => {
    const agents = Array.from(new Set(sessions.map((s) => s.agent_type).filter(Boolean)));
    return agents.sort();
  }, [sessions]);

  const uniqueModels = useMemo(() => {
    const models = Array.from(new Set(sessions.map((s) => s.model).filter(Boolean)));
    return models.sort();
  }, [sessions]);

  // Filtered & sorted sessions
  const filteredSessions = useMemo(() => {
    return sessions
      .filter((s) => {
        // Exclude fake/empty sessions with 0 messages
        if ((s.trajectory?.messages?.length || 0) === 0) return false;

        const isBlocked = sessionIsBlocked(s);

        // Status filter
        if (statusFilter === 'blocked' && !isBlocked) return false;
        if (statusFilter === 'closed' && isBlocked) return false;

        // Agent filter
        if (agentFilter !== 'all' && s.agent_type !== agentFilter) return false;

        // Model filter
        if (modelFilter !== 'all' && s.model !== modelFilter) return false;

        // Search query
        if (searchQuery.trim()) {
          const q = searchQuery.toLowerCase();
          const matchId = s.session_id.toLowerCase().includes(q);
          const matchTitle = (s.title || '').toLowerCase().includes(q);
          const matchProject = (s.project_name || '').toLowerCase().includes(q);
          const matchAgent = (s.agent_type || '').toLowerCase().includes(q);
          const matchModel = (s.model || '').toLowerCase().includes(q);
          const matchSummary = (s.final_summary || '').toLowerCase().includes(q);
          const matchReason = (s.failure_reason || '').toLowerCase().includes(q);

          if (!matchId && !matchTitle && !matchProject && !matchAgent && !matchModel && !matchSummary && !matchReason) {
            return false;
          }
        }
        return true;
      })
      .sort((a, b) => {
        if (sortBy === 'newest') {
          return new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime();
        }
        if (sortBy === 'oldest') {
          return new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime();
        }
        if (sortBy === 'messages') {
          const countA = a.trajectory?.messages?.length || 0;
          const countB = b.trajectory?.messages?.length || 0;
          return countB - countA;
        }
        if (sortBy === 'tools') {
          const countA = a.trajectory?.tool_calls?.length || 0;
          const countB = b.trajectory?.tool_calls?.length || 0;
          return countB - countA;
        }
        if (sortBy === 'duration') {
          return (b.total_duration_sec || 0) - (a.total_duration_sec || 0);
        }
        return 0;
      });
  }, [sessions, searchQuery, agentFilter, modelFilter, statusFilter, sortBy]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs animate-fadeIn font-sans">
      <div className="bg-white w-full max-w-4xl rounded-2xl shadow-2xl border border-border-subtle flex flex-col max-h-[85vh] overflow-hidden">
        {/* 1. Modal Header */}
        <div className="p-5 border-b border-border-subtle flex items-center justify-between bg-canvas/40">
          <div className="flex items-center gap-3">
            <div className="w-3.5 h-3.5 rounded-full bg-indigo-600" />
            <div>
              <h3 className="text-base font-bold text-text-primary">{title}</h3>
              <p className="text-xs text-text-secondary">
                Filter and select from {sessions.length.toLocaleString()} monitored agent sessions
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-xl bg-white border border-border-subtle hover:bg-canvas text-text-secondary flex items-center justify-center transition-colors cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* 2. Search & Filters Bar */}
        <div className="p-4 border-b border-border-subtle bg-white space-y-3">
          {/* Main Search Input */}
          <div className="relative">
            <input
              type="text"
              autoFocus
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by Session ID, Agent, Model, Project, or Title..."
              className="w-full bg-canvas border border-border-subtle rounded-xl pl-9 pr-8 py-2 text-xs text-text-primary focus:outline-none focus:border-indigo-500 placeholder:text-text-muted font-mono"
            />
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary p-0.5 cursor-pointer"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Faceted Filter Controls */}
          <div className="flex items-center justify-between gap-3 flex-wrap text-xs">
            <div className="flex items-center gap-2 flex-wrap">
              {/* Agent Filter */}
              <select
                value={agentFilter}
                onChange={(e) => setAgentFilter(e.target.value)}
                className="bg-canvas border border-border-subtle rounded-lg px-2.5 py-1 text-xs text-text-primary font-medium focus:outline-none focus:border-indigo-500"
              >
                <option value="all">All Agents ({uniqueAgents.length})</option>
                {uniqueAgents.map((agent) => (
                  <option key={agent} value={agent}>
                    Agent: {agent}
                  </option>
                ))}
              </select>

              {/* Model Filter */}
              <select
                value={modelFilter}
                onChange={(e) => setModelFilter(e.target.value)}
                className="bg-canvas border border-border-subtle rounded-lg px-2.5 py-1 text-xs text-text-primary font-medium focus:outline-none focus:border-indigo-500"
              >
                <option value="all">All Models ({uniqueModels.length})</option>
                {uniqueModels.map((m) => (
                  <option key={m} value={m}>
                    Model: {m}
                  </option>
                ))}
              </select>

              {/* Status Segmented Buttons */}
              <div className="flex items-center bg-canvas p-0.5 rounded-lg border border-border-subtle text-[11px] font-bold">
                <button
                  type="button"
                  onClick={() => setStatusFilter('all')}
                  className={`px-2.5 py-0.5 rounded-md transition-colors cursor-pointer ${
                    statusFilter === 'all'
                      ? 'bg-white text-text-primary shadow-2xs'
                      : 'text-text-muted hover:text-text-primary'
                  }`}
                >
                  All
                </button>
                <button
                  type="button"
                  onClick={() => setStatusFilter('blocked')}
                  className={`px-2.5 py-0.5 rounded-md transition-colors cursor-pointer ${
                    statusFilter === 'blocked'
                      ? 'bg-rose-600 text-white shadow-2xs'
                      : 'text-text-muted hover:text-rose-600'
                  }`}
                >
                  Blocked
                </button>
                <button
                  type="button"
                  onClick={() => setStatusFilter('closed')}
                  className={`px-2.5 py-0.5 rounded-md transition-colors cursor-pointer ${
                    statusFilter === 'closed'
                      ? 'bg-slate-900 text-white shadow-2xs'
                      : 'text-text-muted hover:text-text-primary'
                  }`}
                >
                  Closed
                </button>
              </div>
            </div>

            {/* Sort Dropdown */}
            <div className="flex items-center gap-1.5 text-[11px] text-text-muted font-mono">
              <span>Sort:</span>
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as any)}
                className="bg-canvas border border-border-subtle rounded-lg px-2 py-1 text-[11px] text-text-primary font-medium focus:outline-none"
              >
                <option value="newest">Newest First</option>
                <option value="oldest">Oldest First</option>
                <option value="messages">Most Messages</option>
                <option value="tools">Most Tool Calls</option>
                <option value="duration">Longest Duration</option>
              </select>
            </div>
          </div>
        </div>

        {/* 3. Scrollable List of Session Cards */}
        <div className="flex-1 overflow-y-auto divide-y divide-border-subtle p-3 space-y-2">
          {filteredSessions.length === 0 ? (
            <div className="p-12 text-center text-xs text-text-muted space-y-2">
              <AlertTriangle className="w-8 h-8 text-amber-500 mx-auto mb-1" />
              <div className="font-bold text-text-primary">No Monitored Sessions Match Your Filter</div>
              <p>Try clearing your search query or adjusting your filters.</p>
            </div>
          ) : (
            filteredSessions.map((s) => {
              const isSelected = s.session_id === selectedSessionId;
              const isBlocked = sessionIsBlocked(s);
              const isActive = s.status === 'working' || s.status === 'active' || s.status === 'running';
              const msgsCount = s.trajectory?.messages?.length || 0;
              const toolsCount = s.trajectory?.tool_calls?.length || 0;
              const dateStr = s.created_at
                ? new Date(s.created_at).toLocaleDateString(undefined, {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                  })
                : 'Recent';

              return (
                <div
                  key={s.session_id}
                  onClick={() => {
                    onSelect(s.session_id);
                    onClose();
                  }}
                  className={`p-3.5 rounded-xl transition-all cursor-pointer flex flex-col sm:flex-row sm:items-center justify-between gap-3 ${
                    isSelected
                      ? 'bg-indigo-50/50 border-2 border-indigo-600 shadow-2xs'
                      : 'hover:bg-canvas/60 border border-border-subtle/80 bg-white'
                  }`}
                >
                  <div className="space-y-1.5 flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      {/* Status Badge */}
                      <span
                        className={`px-2 py-0.5 rounded-full text-[10px] font-bold font-mono uppercase tracking-wider ${
                          isBlocked
                            ? 'bg-rose-500/10 text-rose-700 border border-rose-500/20'
                            : isActive
                            ? 'bg-emerald-500/10 text-emerald-700 border border-emerald-500/20'
                            : 'bg-slate-100 text-slate-700 border border-slate-200'
                        }`}
                      >
                        {isBlocked ? '🛑 Blocked' : isActive ? '🟢 Working' : '✓ Closed'}
                      </span>

                      {/* Agent Badge */}
                      <span className="px-2 py-0.5 rounded-md bg-dark-base text-white text-[11px] font-mono font-semibold">
                        {s.agent_type === 'antigravity'
                          ? '🤖 Antigravity'
                          : s.agent_type === 'claude_code'
                          ? '⚡ Claude Code'
                          : s.agent_type === 'cursor'
                          ? '🖱️ Cursor'
                          : s.agent_type || 'Agent'}
                      </span>

                      {/* Model Badge */}
                      {s.model && (
                        <span className="px-2 py-0.5 rounded-md bg-surface-subtle text-text-primary text-[11px] font-mono">
                          {s.model}
                        </span>
                      )}

                      {/* Session ID */}
                      <span className="text-[10px] font-mono text-text-muted">
                        id: <strong className="text-text-secondary">{s.session_id.slice(0, 16)}</strong>
                      </span>
                    </div>

                    {/* Title / Summary */}
                    <div className="text-xs font-bold text-text-primary line-clamp-1">
                      {s.title || s.project_name || s.session_id}
                    </div>
                    <p className="text-[11px] text-text-secondary line-clamp-1 font-sans">
                      {s.final_summary ||
                        (isBlocked
                          ? 'Safety policy violation intercepted by Watcher Firewall.'
                          : 'Monitored agent workflow with recorded execution trace.')}
                    </p>

                    {/* Meta Stats */}
                    <div className="flex items-center gap-3 text-[11px] text-text-muted font-mono flex-wrap">
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {dateStr}
                      </span>
                      <span>·</span>
                      <span className="flex items-center gap-1">
                        <MessageSquare className="w-3 h-3" />
                        {msgsCount} msgs
                      </span>
                      <span>·</span>
                      <span className="flex items-center gap-1">
                        <Terminal className="w-3 h-3" />
                        {toolsCount} tools
                      </span>
                      {s.total_duration_sec > 0 && (
                        <>
                          <span>·</span>
                          <span className="flex items-center gap-1">
                            <Activity className="w-3 h-3" />
                            {Math.round(s.total_duration_sec)}s
                          </span>
                        </>
                      )}
                      {s.total_tokens > 0 && (
                        <>
                          <span>·</span>
                          <span className="flex items-center gap-1">
                            <Zap className="w-3 h-3 text-amber-500" />
                            {s.total_tokens.toLocaleString()} tok
                          </span>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Selection Button */}
                  <div className="flex items-center gap-2 flex-shrink-0">
                    {isSelected ? (
                      <span className="px-3 py-1 rounded-lg bg-indigo-600 text-white text-xs font-bold font-mono shadow-xs">
                        Selected
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="px-3.5 py-1.5 rounded-lg border border-border-subtle bg-white hover:bg-canvas text-xs font-bold text-text-primary shadow-2xs transition-colors cursor-pointer"
                      >
                        Select
                      </button>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* 4. Modal Footer */}
        <div className="p-3 px-5 border-t border-border-subtle bg-canvas/40 flex items-center justify-between text-xs text-text-muted font-mono">
          <span>
            Showing <strong className="text-text-primary">{filteredSessions.length}</strong> of{' '}
            <strong className="text-text-primary">{sessions.length}</strong> sessions
          </span>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 rounded-xl bg-dark-base hover:bg-black text-white text-xs font-bold transition-colors cursor-pointer shadow-xs"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};
