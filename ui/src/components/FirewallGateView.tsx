import React, { useState } from 'react';
import { IonIcon } from '@ionic/react';
import {
  timeOutline,
  chevronDownOutline,
  chevronUpOutline,
  copyOutline,
  checkmarkOutline,
  checkmarkCircleOutline,
  terminalOutline,
} from 'ionicons/icons';
import { FindingRecord, RunRecord, WatcherConfig, WatcherVerdict } from '../types';

interface FirewallGateViewProps {
  /** @deprecated Session browse lives under Safety → Sessions; kept for App API compat. */
  runs?: RunRecord[];
  /** @deprecated Session browse lives under Safety → Sessions; kept for App API compat. */
  findings?: FindingRecord[];
  /** @deprecated Session browse lives under Safety → Sessions; kept for App API compat. */
  discoveredSessions?: unknown[];
  watcherConfig?: WatcherConfig;
  liveInterceptions?: WatcherVerdict[];
  isDemoSeed?: boolean;
  onSelectIncident?: (finding: FindingRecord) => void;
  onUpdateWatcherConfig?: (config: Partial<WatcherConfig>) => void;
  onNavigateToSessions?: () => void;
}

const PAGE_SIZE = 25;

/** Normalize gate decision vocab (deny/block/reject → blocked, else closed). */
export type LiveStreamBucket = 'all' | 'blocked' | 'closed';

export function classifyLiveDecision(
  verdictOrDecision?: WatcherVerdict | string | null
): Exclude<LiveStreamBucket, 'all'> {
  if (verdictOrDecision && typeof verdictOrDecision === 'object') {
    const v = verdictOrDecision;
    if (v.human_override !== 'allow' && v.resolution_status !== 'human_approved') {
      const d = String(v.decision || '').toLowerCase();
      if (d === 'deny' || d === 'block' || d === 'reject') return 'blocked';
    }
    return 'closed';
  }
  const d = String(verdictOrDecision || '').toLowerCase();
  if (d === 'deny' || d === 'block' || d === 'reject') return 'blocked';
  return 'closed';
}

export function getActionTitle(v: WatcherVerdict): string {
  let text = (v.action_preview || '').trim();
  let toolPrefix = '';
  const colonIdx = text.indexOf(': ');
  if (colonIdx > 0 && colonIdx < 35) {
    toolPrefix = text.slice(0, colonIdx + 2);
    text = text.slice(colonIdx + 2).trim();
  }

  const candidateJson =
    text.startsWith('{') || text.startsWith('[')
      ? text
      : text.includes('{') && text.includes('}')
      ? text.slice(text.indexOf('{'), text.lastIndexOf('}') + 1)
      : null;

  if (candidateJson) {
    try {
      const parsed = JSON.parse(candidateJson);
      if (parsed.toolAction) return parsed.toolAction;
      if (parsed.toolSummary) return parsed.toolSummary;
      if (parsed.CommandLine) return `${toolPrefix}${parsed.CommandLine.split('\n')[0].trim()}`;
      if (parsed.command) return `${toolPrefix}${parsed.command.split('\n')[0].trim()}`;
      if (parsed.Description) return parsed.Description.split('\n')[0].trim();
      if (parsed.AbsolutePath) {
        const parts = parsed.AbsolutePath.split('/');
        return `${toolPrefix || 'view_file: '}${parts[parts.length - 1]}`;
      }
      if (parsed.TargetFile) {
        const parts = parsed.TargetFile.split('/');
        return `${toolPrefix || 'edit: '}${parts[parts.length - 1]}`;
      }
      if (parsed.path || parsed.FilePath) {
        return `${toolPrefix}File: ${parsed.path || parsed.FilePath}`;
      }
    } catch {
      // Fall through to plain text
    }
  }

  const firstLine = (text || v.action_preview || '').split('\n')[0].trim();
  return (toolPrefix && !firstLine.startsWith(toolPrefix) ? `${toolPrefix}${firstLine}` : firstLine) || 'Agent Action';
}

export const FirewallGateView: React.FC<FirewallGateViewProps> = ({
  watcherConfig: propConfig,
  liveInterceptions = [],
  discoveredSessions: _discoveredSessions = [],
  isDemoSeed,
  onUpdateWatcherConfig,
  onNavigateToSessions,
}) => {
  // Control owns mode + live stream; deep session browse lives under Safety → Sessions.
  const [streamFilter, setStreamFilter] = useState<LiveStreamBucket>('all');
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [expandedLiveIdxs, setExpandedLiveIdxs] = useState<Set<number>>(new Set());
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  /*
  const [isResolving, setIsResolving] = useState<boolean>(false);

  const handleAllowAll = async () => {
    try {
      setIsResolving(true);
      await fetch('/api/v1/watcher/reviews/resolve-all', { method: 'POST' });
    } catch (err) {
      console.error('Failed to resolve all reviews:', err);
    } finally {
      setIsResolving(false);
    }
  };
  */

  const handleResolveSingle = async (v: WatcherVerdict) => {
    try {
      await fetch('/api/v1/watcher/gate/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: v.session_id,
          review_id: v.review_id,
          resolution: 'human_approved',
          note: 'Approved manually from Sentinel Firewall Gate',
        }),
      });
    } catch (err) {
      console.error('Failed to resolve review:', err);
    }
  };

  const toggleExpandLive = (idx: number) => {
    setExpandedLiveIdxs((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const handleCopyText = (text: string, key: string, e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const toggleExpandAll = (totalCount: number) => {
    if (expandedLiveIdxs.size === totalCount) {
      setExpandedLiveIdxs(new Set());
    } else {
      setExpandedLiveIdxs(new Set(Array.from({ length: totalCount }, (_, i) => i)));
    }
  };

  const [config, setConfig] = useState<WatcherConfig>(
    propConfig || {
      mode: 'enforce',
      deny_threshold: 0.8,
      flag_threshold: 0.4,
      fail_open: true,
      fallback_decision: 'allow',
      timeout_sec: 0.8,
    }
  );

  React.useEffect(() => {
    if (propConfig) {
      setConfig(propConfig);
    }
  }, [propConfig]);

  const handleModeChange = (mode: 'enforce' | 'observe' | 'paused') => {
    const updated = { ...config, mode };
    setConfig(updated);
    if (onUpdateWatcherConfig) {
      onUpdateWatcherConfig(updated);
    }
  };

  const formatEventTime = (timestamp?: string) => {
    if (!timestamp) {
      return {
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        relative: 'Just now',
      };
    }
    try {
      const d = new Date(timestamp);
      if (isNaN(d.getTime())) {
        return {
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          relative: 'Just now',
        };
      }
      const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      const diffSec = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
      let relStr = 'Just now';
      if (diffSec < 5) relStr = 'Just now';
      else if (diffSec < 60) relStr = `${diffSec}s ago`;
      else if (diffSec < 3600) relStr = `${Math.floor(diffSec / 60)}m ago`;
      else if (diffSec < 86400) relStr = `${Math.floor(diffSec / 3600)}h ago`;
      else relStr = `${Math.floor(diffSec / 86400)}d ago`;

      return { time: timeStr, relative: relStr };
    } catch {
      return {
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        relative: 'Just now',
      };
    }
  };

  // Canonical Control universe: live interception events from SSE (not findings/runs/sessions).

  const totalAllCount = liveInterceptions.length;
  const totalBlockedCount = liveInterceptions.filter((v) => classifyLiveDecision(v) === 'blocked').length;
  const totalClosedCount = liveInterceptions.filter((v) => classifyLiveDecision(v) === 'closed').length;

  const filteredLive =
    streamFilter === 'all'
      ? liveInterceptions
      : liveInterceptions.filter((v) => classifyLiveDecision(v) === streamFilter);

  const totalItems = filteredLive.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / PAGE_SIZE));
  const displayedLive = filteredLive.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const setFilter = (next: LiveStreamBucket) => {
    setStreamFilter(next);
    setCurrentPage(1);
    setExpandedLiveIdxs(new Set());
  };

  const filterPillClass = (bucket: LiveStreamBucket, accent?: string) => {
    const selected = streamFilter === bucket;
    if (selected) {
      return 'bg-white text-slate-900 shadow-xs font-bold';
    }
    if (accent && bucket === 'blocked' && totalBlockedCount > 0) {
      return 'text-rose-600 font-bold hover:text-rose-800';
    }
    return 'text-slate-600 hover:text-slate-900 font-medium';
  };

  return (
    <div className="flex flex-col h-full bg-[#fcfcfd] text-[#1e2029] font-sans p-6 space-y-5 overflow-y-auto">
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

      {/* 1. Card Header */}
      <div className="bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm flex items-center justify-between shrink-0">
        <h1 className="text-base font-bold text-slate-900 tracking-tight flex items-center gap-2.5">
          Control
        </h1>

        <div className="inline-flex items-center gap-1.5 rounded-full bg-slate-100/90 border border-slate-200/80 px-2 py-1.5">
          <span className="pl-1.5 pr-1 text-[11px] font-semibold text-slate-600 whitespace-nowrap">
            Mode :
          </span>
          <div className="inline-flex items-center gap-0.5 rounded-full bg-white/80 p-0.5 border border-slate-200/70">
            <button
              type="button"
              onClick={() => handleModeChange('enforce')}
              className={`px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wide transition-colors ${
                config.mode === 'enforce'
                  ? 'bg-rose-600 text-white shadow-xs'
                  : 'text-slate-500 hover:text-slate-800 hover:bg-slate-50'
              }`}
            >
              Enforce
            </button>
            <button
              type="button"
              onClick={() => handleModeChange('observe')}
              className={`px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wide transition-colors ${
                config.mode === 'observe'
                  ? 'bg-brand-purple text-white shadow-xs'
                  : 'text-slate-500 hover:text-slate-800 hover:bg-slate-50'
              }`}
            >
              Observe
            </button>
            <button
              type="button"
              onClick={() => handleModeChange('paused')}
              className={`px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wide transition-colors ${
                config.mode === 'paused'
                  ? 'bg-dark-base text-white shadow-xs'
                  : 'text-slate-500 hover:text-slate-800 hover:bg-slate-50'
              }`}
            >
              Paused
            </button>
          </div>
        </div>
      </div>

      {/* Live telemetry — session browse lives under Safety → Sessions */}
      <div className="bg-white rounded-2xl border border-border-subtle shadow-sm overflow-hidden space-y-0">
        {/* Decision filter pills + Open Sessions on one toolbar row */}
        <div className="p-3 px-4 border-b border-border-subtle bg-white flex items-center justify-between gap-3">
          <div className="grid grid-cols-3 bg-slate-100 p-1 rounded-xl gap-1 text-center max-w-md flex-1 min-w-0">
            <button
              type="button"
              onClick={() => setFilter('all')}
              className={`py-1.5 px-1 rounded-lg transition-all cursor-pointer flex flex-col items-center justify-center ${filterPillClass('all')}`}
            >
              <span className="text-[10px] uppercase tracking-wider font-bold">All</span>
              <span className="font-mono text-xs font-bold tabular-nums">{totalAllCount}</span>
            </button>
            <button
              type="button"
              onClick={() => setFilter('blocked')}
              className={`py-1.5 px-1 rounded-lg transition-all cursor-pointer flex flex-col items-center justify-center ${filterPillClass('blocked', 'rose')}`}
            >
              <span className="text-[10px] uppercase tracking-wider font-bold">Blocked</span>
              <span className={`font-mono text-xs font-bold tabular-nums ${totalBlockedCount > 0 ? 'text-rose-600' : ''}`}>
                {totalBlockedCount}
              </span>
            </button>
            <button
              type="button"
              onClick={() => setFilter('closed')}
              className={`py-1.5 px-1 rounded-lg transition-all cursor-pointer flex flex-col items-center justify-center ${filterPillClass('closed')}`}
            >
              <span className="text-[10px] uppercase tracking-wider font-bold">Closed</span>
              <span className="font-mono text-xs font-bold tabular-nums">{totalClosedCount}</span>
            </button>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {/* {totalBlockedCount > 0 && (
              <button
                type="button"
                onClick={handleAllowAll}
                disabled={isResolving}
                className="px-3 py-1.5 rounded-xl text-xs font-bold bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 cursor-pointer transition-colors flex items-center gap-1.5 shadow-2xs"
                title="Mark all existing blocked commands as operator-allowed for clarity"
              >
                <span>{isResolving ? 'Allowing...' : '👤 Allow All for Clarity'}</span>
              </button>
            )} */}
            {onNavigateToSessions && (
              <button
                type="button"
                onClick={onNavigateToSessions}
                className="px-3.5 py-1.5 rounded-xl text-xs font-bold bg-white text-text-primary border border-border-subtle hover:bg-canvas cursor-pointer shrink-0"
              >
                Open Sessions →
              </button>
            )}
          </div>
        </div>

        {/* Live Telemetry Stream */}
        <div>
          <div className="p-3 px-5 border-b border-border-subtle bg-canvas/30 flex items-center justify-end text-xs">
            <button
              type="button"
              onClick={() => toggleExpandAll(displayedLive.length)}
              className="px-2.5 py-1 rounded-lg bg-white border border-border-subtle hover:bg-canvas text-text-primary text-[11px] font-bold transition-all shadow-2xs flex items-center gap-1 cursor-pointer"
            >
              <IonIcon
                icon={
                  expandedLiveIdxs.size === displayedLive.length && displayedLive.length > 0
                    ? chevronUpOutline
                    : chevronDownOutline
                }
                className="text-xs"
              />
              <span>
                {expandedLiveIdxs.size === displayedLive.length && displayedLive.length > 0
                  ? 'Collapse All'
                  : 'Expand All'}
              </span>
            </button>
          </div>

          <div className="divide-y divide-border-subtle max-h-[600px] overflow-y-auto">
            {displayedLive.length === 0 ? (
              <div className="p-12 text-center text-xs text-text-muted">
                {totalAllCount === 0
                  ? 'No tool interceptions yet. Install Antigravity/Cursor hooks, set Control → Enforce, then run a blocked command (e.g. git push --force). Or open Sessions to browse ingested agent logs.'
                  : `No ${streamFilter} events in the live stream.`}
              </div>
            ) : (
              displayedLive.map((v, vIdx) => {
                const isExpanded = expandedLiveIdxs.has(vIdx);
                const timeInfo = formatEventTime(v.timestamp);
                const bucket = classifyLiveDecision(v);
                return (
                  <div key={`${v.timestamp}-${vIdx}-${v.action_preview.slice(0, 24)}`} className="hover:bg-canvas/40 transition-colors">
                    <div
                      onClick={() => toggleExpandLive(vIdx)}
                      className="p-3.5 px-5 flex items-center justify-between text-xs cursor-pointer select-none"
                    >
                      <div className="flex items-center gap-3 min-w-0 flex-1 mr-3">
                        <button
                          type="button"
                          className="text-text-muted hover:text-text-primary transition-colors flex-shrink-0"
                          title={isExpanded ? 'Collapse' : 'Expand full command'}
                        >
                          <IonIcon icon={isExpanded ? chevronUpOutline : chevronDownOutline} className="text-sm" />
                        </button>

                        <div className="flex items-center gap-1.5 font-mono text-[11px] text-text-muted flex-shrink-0 bg-canvas px-2.5 py-1 rounded-md border border-border-subtle">
                          <IonIcon icon={timeOutline} className="text-xs text-text-muted" />
                          <span className="font-semibold text-text-primary">{timeInfo.time}</span>
                          <span className="text-[10px] text-text-muted">({timeInfo.relative})</span>
                        </div>

                        {(v.human_override === 'allow' || v.resolution_status === 'human_approved') &&
                        (Boolean(v.rule_violation_tag) ||
                          (typeof v.risk_score === 'number' && v.risk_score >= 0.5) ||
                          /lockout|violation|blocked|escalat|operator/i.test(v.reason || '')) ? (
                          <span className="px-2 py-0.5 rounded font-mono font-bold text-[10px] uppercase flex-shrink-0 bg-indigo-600 text-white shadow-2xs">
                            👤 Allowed (Human)
                          </span>
                        ) : (
                          <span
                            className={`px-2 py-0.5 rounded font-mono font-bold text-[10px] uppercase flex-shrink-0 ${
                              bucket === 'blocked'
                                ? 'bg-rose-600 text-white shadow-2xs'
                                : 'bg-emerald-600 text-white shadow-2xs'
                            }`}
                          >
                            {bucket === 'blocked' ? '🛑 Denied' : '✓ Allowed'}
                          </span>
                        )}

                        <span className="font-mono text-text-secondary flex-shrink-0">
                          {v.agent_id === 'antigravity'
                            ? '🤖 Antigravity'
                            : v.agent_id === 'cursor'
                            ? '🖱️ Cursor'
                            : v.agent_id === 'claude_code' || v.agent_id === 'claude-code'
                            ? '⚡ Claude Code'
                            : `⚡ ${v.agent_id || 'Agent'}`}
                        </span>

                        <span
                          className="font-mono font-semibold text-text-primary min-w-0 flex-1 truncate"
                          title={v.full_command || v.action_preview}
                        >
                          {getActionTitle(v)}
                        </span>
                      </div>
                    </div>

                    {isExpanded && (
                      <div className="px-5 pb-4 pt-1 bg-surface-subtle/30 border-t border-border-subtle/60 space-y-3 animate-fadeIn">
                        <div className="space-y-1.5">
                          <div className="flex items-center justify-between text-[11px] font-mono font-bold text-text-secondary uppercase">
                            <div className="flex items-center gap-1.5">
                              <IonIcon icon={terminalOutline} className="text-xs text-brand-purple" />
                              <span>Complete Unclipped Action / Command Payload</span>
                            </div>
                            <button
                              type="button"
                              onClick={(e) => handleCopyText(v.full_command || v.action_preview, `cmd-${vIdx}`, e)}
                              className="px-2.5 py-0.5 rounded bg-white hover:bg-canvas text-text-primary border border-border-subtle flex items-center gap-1 text-[10px] font-mono cursor-pointer transition-all shadow-2xs"
                            >
                              <IonIcon
                                icon={copiedKey === `cmd-${vIdx}` ? checkmarkOutline : copyOutline}
                                className="text-xs text-brand-purple"
                              />
                              <span>{copiedKey === `cmd-${vIdx}` ? 'Copied!' : 'Copy Command'}</span>
                            </button>
                          </div>
                          <pre className="p-3.5 rounded-xl bg-[#14121F] text-slate-200 font-mono text-xs overflow-x-auto whitespace-pre-wrap leading-relaxed border border-border-subtle shadow-inner select-text">
                            {v.full_command || v.action_preview}
                          </pre>
                        </div>

                        <div className="space-y-1.5">
                          <div className="flex items-center justify-between text-[11px] font-mono font-bold text-text-secondary uppercase">
                            <div className="flex items-center gap-1.5">
                              <IonIcon icon={checkmarkCircleOutline} className="text-xs text-emerald-600" />
                              <span>Command Execution Result / Output</span>
                            </div>
                            {v.tool_result && (
                              <button
                                type="button"
                                onClick={(e) => handleCopyText(v.tool_result || '', `res-${vIdx}`, e)}
                                className="px-2.5 py-0.5 rounded bg-white hover:bg-canvas text-text-primary border border-border-subtle flex items-center gap-1 text-[10px] font-mono cursor-pointer transition-all shadow-2xs"
                              >
                                <IonIcon
                                  icon={copiedKey === `res-${vIdx}` ? checkmarkOutline : copyOutline}
                                  className="text-xs text-emerald-600"
                                />
                                <span>{copiedKey === `res-${vIdx}` ? 'Copied!' : 'Copy Output'}</span>
                              </button>
                            )}
                          </div>
                          <pre className="p-3.5 rounded-xl bg-[#0b1329] text-emerald-300 font-mono text-xs overflow-x-auto max-h-72 overflow-y-auto whitespace-pre-wrap leading-relaxed border border-border-subtle shadow-inner select-text">
                            {v.tool_result || (bucket === 'blocked' ? 'Execution blocked by OpenEval runtime gate.' : 'Awaiting execution output from agent transcript...')}
                          </pre>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs font-mono">
                          <div className="p-2.5 rounded-lg bg-white border border-border-subtle shadow-2xs">
                            <div className="text-[10px] text-text-muted uppercase">Security Decision</div>
                            <div className="font-bold text-text-primary mt-0.5 flex items-center gap-1.5">
                              <span
                                className={`w-2 h-2 rounded-full ${
                                  bucket === 'blocked'
                                    ? 'bg-rose-500'
                                    : 'bg-emerald-500'
                                }`}
                              />
                              <span className="capitalize">{v.decision}</span>
                              {v.shadow_decision && (
                                <span className="text-[10px] text-text-muted font-normal">
                                  (Shadow: {v.shadow_decision})
                                </span>
                              )}
                            </div>
                          </div>

                          <div className="p-2.5 rounded-lg bg-white border border-border-subtle shadow-2xs">
                            <div className="text-[10px] text-text-muted uppercase">Verification Stage</div>
                            <div className="font-bold text-text-primary mt-0.5">{v.stage}</div>
                          </div>

                          <div className="p-2.5 rounded-lg bg-white border border-border-subtle shadow-2xs">
                            <div className="text-[10px] text-text-muted uppercase">Analysis Latency</div>
                            <div className="font-bold text-text-primary mt-0.5">{Math.round(v.latency_ms)} ms</div>
                          </div>
                        </div>

                        {v.reason && (
                          <div className="p-3 rounded-lg bg-white border border-border-subtle text-xs text-text-secondary leading-relaxed font-sans">
                            <strong className="font-mono text-text-primary">Policy Note: </strong>
                            {v.reason}
                          </div>
                        )}

                        {bucket === 'blocked' && !(v.human_override === 'allow' || v.resolution_status === 'human_approved') && (
                          <div className="flex items-center justify-end pt-1">
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleResolveSingle(v);
                              }}
                              className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold transition-colors shadow-2xs cursor-pointer flex items-center gap-1.5"
                            >
                              <span>👤 Mark as Operator Allowed</span>
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {totalPages > 1 && (
          <div className="pt-2 pb-4 px-4 border-t border-border-subtle bg-canvas/40 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs select-none">
            <span className="text-text-muted font-mono text-[11px]">
              Showing{' '}
              <strong className="text-text-primary">
                {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, totalItems)}
              </strong>{' '}
              of <strong className="text-text-primary">{totalItems}</strong> items
            </span>

            <div className="flex items-center gap-1.5">
              <button
                type="button"
                disabled={currentPage <= 1}
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                className="px-2.5 py-1 rounded-lg border border-border-subtle bg-white text-text-primary disabled:opacity-40 disabled:cursor-not-allowed hover:bg-canvas font-medium text-xs shadow-2xs"
              >
                &larr; Prev
              </button>

              {Array.from({ length: totalPages }, (_, i) => i + 1).map((pageNum) => (
                <button
                  key={pageNum}
                  type="button"
                  onClick={() => setCurrentPage(pageNum)}
                  className={`w-7 h-7 rounded-lg text-xs font-mono font-bold transition-colors ${
                    currentPage === pageNum
                      ? 'bg-dark-base text-white shadow-xs'
                      : 'bg-white border border-border-subtle text-text-secondary hover:text-text-primary hover:bg-canvas'
                  }`}
                >
                  {pageNum}
                </button>
              ))}

              <button
                type="button"
                disabled={currentPage >= totalPages}
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                className="px-2.5 py-1 rounded-lg border border-border-subtle bg-white text-text-primary disabled:opacity-40 disabled:cursor-not-allowed hover:bg-canvas font-medium text-xs shadow-2xs"
              >
                Next &rarr;
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
