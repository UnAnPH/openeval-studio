import React, { useState } from 'react';
import { IonIcon } from '@ionic/react';
import {
  shieldCheckmark,
  optionsOutline,
  chevronForwardOutline,
  timeOutline,
  searchOutline,
  checkmarkCircle,
  filterOutline,
  chevronDownOutline,
  chevronUpOutline,
  copyOutline,
  checkmarkOutline,
  terminalOutline,
} from 'ionicons/icons';
import { FindingRecord, RunRecord, WatcherConfig, WatcherVerdict } from '../types';
import { DEFAULT_CLEARED_SESSIONS } from '../data/defaults';

interface FirewallGateViewProps {
  runs: RunRecord[];
  findings: FindingRecord[];
  watcherConfig?: WatcherConfig;
  liveInterceptions?: WatcherVerdict[];
  onSelectIncident?: (finding: FindingRecord) => void;
  onUpdateWatcherConfig?: (config: Partial<WatcherConfig>) => void;
}

export interface MonitoredSessionItem {
  id: string;
  session_id: string;
  headline: string;
  developer: string;
  timestamp: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'cleared';
  dimension: string;
  agent_source: 'antigravity' | 'claude_code' | 'openeval_runner';
  summary: string;
  risk_score: number;
  is_blocked: boolean;
  is_escalated?: boolean;
  blocked_turn?: number | null;
  total_turns: number;
  finding_ref?: FindingRecord;
}

const PAGE_SIZE = 25;

export const FirewallGateView: React.FC<FirewallGateViewProps> = ({
  runs,
  findings,
  watcherConfig: propConfig,
  liveInterceptions = [],
  onSelectIncident,
  onUpdateWatcherConfig,
}) => {
  const [activeTab, setActiveTab] = useState<'all_sessions' | 'blocked_sessions' | 'escalated_sessions' | 'cleared_sessions' | 'live_stream'>('all_sessions');
  const [agentFilter, setAgentFilter] = useState<string>('all');
  const [dimensionFilter, setDimensionFilter] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [expandedLiveIdxs, setExpandedLiveIdxs] = useState<Set<number>>(new Set());
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);

  const toggleExpandLive = (idx: number) => {
    setExpandedLiveIdxs((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const handleCopyCommand = (cmd: string, idx: number, e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(cmd);
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 2000);
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

  const handleThresholdChange = (key: 'deny_threshold' | 'flag_threshold', value: number) => {
    const updated = { ...config, [key]: value };
    setConfig(updated);
    if (onUpdateWatcherConfig) {
      onUpdateWatcherConfig(updated);
    }
  };

  // Helper to format event timestamps and relative time
  const formatEventTime = (timestamp?: string) => {
    if (!timestamp) return { time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }), relative: 'Just now' };
    try {
      const d = new Date(timestamp);
      if (isNaN(d.getTime())) {
        return { time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }), relative: 'Just now' };
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
      return { time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }), relative: 'Just now' };
    }
  };

  // Severity to numeric risk score mapping
  const getSeverityRiskScore = (severity: string): number => {
    switch (severity) {
      case 'critical': return 0.95;
      case 'high': return 0.85;
      case 'medium': return 0.65;
      case 'low': return 0.35;
      case 'cleared': return 0.05;
      default: return 0.50;
    }
  };

  // Dynamic evaluation of a session given the live threshold settings
  const evaluateSessionDecision = (risk: number) => {
    if (config.mode === 'paused') {
      return { is_blocked: false, is_escalated: false };
    }
    if (risk >= config.deny_threshold) {
      return { is_blocked: true, is_escalated: false };
    }
    if (risk >= config.flag_threshold) {
      return { is_blocked: false, is_escalated: true };
    }
    return { is_blocked: false, is_escalated: false };
  };

  // Construct full list of monitored sessions (dynamically evaluated against current threshold)
  const findingSessionItems: MonitoredSessionItem[] = findings.map((f) => {
    const risk = getSeverityRiskScore(f.severity);
    const { is_blocked, is_escalated } = evaluateSessionDecision(risk);
    return {
      id: f.id,
      session_id: f.session_id || f.id,
      headline: f.headline,
      developer: f.developer,
      timestamp: f.timestamp,
      severity: f.severity,
      dimension: f.dimension,
      agent_source: f.agent_source,
      summary: f.summary,
      risk_score: risk,
      is_blocked,
      is_escalated,
      blocked_turn: is_blocked ? (f.blocked_turn || (f.flagged_turns?.[0] ?? 14)) : null,
      total_turns: f.flagged_turns ? Math.max(...f.flagged_turns, 15) : 15,
      finding_ref: f,
    };
  });

  const runSessionItems: MonitoredSessionItem[] = runs.map((r) => {
    const risk = r.passed === false ? 0.70 : 0.05;
    const { is_blocked, is_escalated } = evaluateSessionDecision(risk);
    return {
      id: r.run_id,
      session_id: r.run_id,
      headline: `[${r.model}] Evaluation Run on task ${r.task_id}`,
      developer: r.human_reviewer || 'Auto Benchmark Runner',
      timestamp: r.created_at,
      severity: r.passed === false ? 'high' : 'cleared',
      dimension: 'Benchmark Suite',
      agent_source: 'openeval_runner',
      summary: r.final_summary || (r.passed ? 'Benchmark assertions verified with full reward.' : r.failure_reason || 'Assertion failure recorded.'),
      risk_score: risk,
      is_blocked,
      is_escalated,
      blocked_turn: is_blocked ? (r.steps?.find((s) => s.firewall_blocked)?.step_number || 1) : null,
      total_turns: r.steps?.length || r.total_steps || 10,
    };
  });

  const evaluatedClearedSessions: MonitoredSessionItem[] = DEFAULT_CLEARED_SESSIONS.map((s) => {
    const { is_blocked, is_escalated } = evaluateSessionDecision(s.risk_score);
    return {
      ...s,
      is_blocked,
      is_escalated,
    };
  });

  const allMonitoredSessions: MonitoredSessionItem[] = [
    ...findingSessionItems,
    ...evaluatedClearedSessions,
    ...runSessionItems,
  ];

  // Counts derived dynamically from allMonitoredSessions so sums are always consistent
  const totalBlockedCount = allMonitoredSessions.filter((item) => item.is_blocked).length;
  const totalEscalatedCount = allMonitoredSessions.filter((item) => item.is_escalated).length;
  const totalClearedCount = allMonitoredSessions.filter((item) => !item.is_blocked && !item.is_escalated).length;
  const totalAllCount = allMonitoredSessions.length;

  // Filtered Sessions
  const filteredSessions = allMonitoredSessions.filter((item) => {
    // Tab filter
    if (activeTab === 'blocked_sessions' && !item.is_blocked) return false;
    if (activeTab === 'escalated_sessions' && !item.is_escalated) return false;
    if (activeTab === 'cleared_sessions' && (item.is_blocked || item.is_escalated)) return false;

    // Agent source filter
    if (agentFilter !== 'all' && item.agent_source !== agentFilter) return false;

    // Dimension filter
    if (dimensionFilter !== 'all' && !item.dimension.toLowerCase().includes(dimensionFilter.toLowerCase())) return false;

    // Search query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const matchHead = item.headline.toLowerCase().includes(q);
      const matchDev = item.developer.toLowerCase().includes(q);
      const matchSumm = item.summary.toLowerCase().includes(q);
      const matchDim = item.dimension.toLowerCase().includes(q);
      const matchId = item.id.toLowerCase().includes(q);
      if (!matchHead && !matchDev && !matchSumm && !matchDim && !matchId) return false;
    }

    return true;
  });

  // Pagination
  const totalPages = activeTab === 'live_stream'
    ? Math.max(1, Math.ceil(liveInterceptions.length / PAGE_SIZE))
    : Math.max(1, Math.ceil(filteredSessions.length / PAGE_SIZE));

  const totalItems = activeTab === 'live_stream' ? liveInterceptions.length : filteredSessions.length;

  const displayedSessions = filteredSessions.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const displayedLive = liveInterceptions.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const getSessionFinding = (session: MonitoredSessionItem): FindingRecord => {
    if (session.finding_ref) return session.finding_ref;
    return {
      id: session.id,
      session_id: session.session_id,
      headline: session.headline,
      developer: session.developer,
      timestamp: session.timestamp,
      severity: (session.severity === 'cleared' ? 'low' : session.severity) as any,
      dimension: session.dimension,
      agent_source: session.agent_source,
      summary: session.summary,
      recommended_actions: [
        {
          priority: 'P3',
          category: 'INVESTIGATE',
          title: 'Deterministic Policy Compliance',
          description: 'Session executed within secure boundaries with zero policy violations.',
          citations: ['POLICY_COMPLIANT', 'FAST_PATH_CHECK'],
        },
      ],
      flagged_turns: [],
      blocked_turn: null,
      tags: ['cleared', 'compliant', session.agent_source],
    };
  };

  return (
    <div className="w-full space-y-6 animate-fadeIn font-sans pb-12">
      {/* 1. Header Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-white p-5 rounded-2xl border border-border-subtle shadow-sm">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-bold text-text-primary tracking-tight">
              Aegis Sessions & Safety Firewall
            </h1>
            <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold border uppercase tracking-wider ${
              config.mode === 'enforce'
                ? 'bg-rose-500/10 text-rose-600 border-rose-500/20 animate-pulse'
                : config.mode === 'observe'
                ? 'bg-brand-purple/10 text-brand-purple border-brand-purple/20'
                : 'bg-text-muted/10 text-text-muted border-text-muted/20'
            }`}>
              ● Mode: {config.mode}
            </span>
          </div>
          <p className="text-xs text-text-secondary mt-0.5">
            Pre-execution gatekeeper registry, runtime policy controls, and monitored agent sessions
          </p>
        </div>

        <div className="flex items-center gap-2 bg-canvas p-1.5 rounded-xl border border-border-subtle text-xs font-mono">
          <div className="px-3 py-1 bg-white rounded-lg border border-border-subtle shadow-xs flex items-center gap-1.5">
            <span className="text-text-muted">Total Monitored:</span>
            <span className="font-bold text-text-primary">{totalAllCount}</span>
          </div>
          <div className="px-3 py-1 bg-white rounded-lg border border-border-subtle shadow-xs flex items-center gap-1.5">
            <span className="text-text-muted">Blocked:</span>
            <span className="font-bold text-rose-600">{totalBlockedCount}</span>
          </div>
          <div className="px-3 py-1 bg-white rounded-lg border border-border-subtle shadow-xs flex items-center gap-1.5">
            <span className="text-text-muted">Deny Gate:</span>
            <span className="font-bold text-text-primary">{Math.round(config.deny_threshold * 100)}%</span>
          </div>
        </div>
      </div>

      {/* 2. Firewall Enforcement Configuration (Lean 3-Control Panel) */}
      <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-border-subtle">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-brand-purple/10 flex items-center justify-center text-brand-purple">
              <IonIcon icon={optionsOutline} className="text-base" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-text-primary">Firewall Enforcement Controls</h3>
              <p className="text-xs text-text-secondary">Tune runtime gatekeeper sensitivity and escalation thresholds</p>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Mode Switcher */}
          <div className="p-4 bg-canvas/60 rounded-2xl border border-border-subtle space-y-3 flex flex-col justify-between">
            <div className="flex items-center justify-between text-xs">
              <label className="font-bold text-text-primary">Enforcement Mode</label>
              <span className="font-mono text-text-muted text-[11px] uppercase">{config.mode}</span>
            </div>
            <div className="grid grid-cols-3 gap-1 bg-white p-1 rounded-xl border border-border-subtle text-xs font-bold">
              <button
                type="button"
                onClick={() => handleModeChange('enforce')}
                className={`py-2 rounded-lg transition-colors ${
                  config.mode === 'enforce' ? 'bg-rose-600 text-white shadow-xs' : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                Enforce
              </button>
              <button
                type="button"
                onClick={() => handleModeChange('observe')}
                className={`py-2 rounded-lg transition-colors ${
                  config.mode === 'observe' ? 'bg-brand-purple text-white shadow-xs' : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                Observe
              </button>
              <button
                type="button"
                onClick={() => handleModeChange('paused')}
                className={`py-2 rounded-lg transition-colors ${
                  config.mode === 'paused' ? 'bg-dark-base text-white shadow-xs' : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                Paused
              </button>
            </div>
          </div>

          {/* Deny Threshold Card */}
          <div className="p-4 bg-canvas/60 rounded-2xl border border-border-subtle space-y-3 flex flex-col justify-between">
            <div className="flex items-center justify-between text-xs">
              <label className="font-bold text-text-primary">Deny Threshold</label>
              <span className="font-mono font-bold text-rose-600 text-sm">{Math.round(config.deny_threshold * 100)}%</span>
            </div>
            <input
              type="range"
              min="0.4"
              max="1.0"
              step="0.05"
              value={config.deny_threshold}
              onChange={(e) => handleThresholdChange('deny_threshold', parseFloat(e.target.value))}
              className="w-full ae-slider ae-slider-rose"
              style={{
                background: `linear-gradient(to right, #E11D48 0%, #E11D48 ${((config.deny_threshold - 0.4) / 0.6) * 100}%, #E2E8F0 ${((config.deny_threshold - 0.4) / 0.6) * 100}%, #E2E8F0 100%)`,
              }}
            />
            <span className="text-[10px] text-text-muted leading-tight">Risks ≥ this value trigger immediate pre-execution denial.</span>
          </div>

          {/* Human Escalation Threshold Card */}
          <div className="p-4 bg-canvas/60 rounded-2xl border border-border-subtle space-y-3 flex flex-col justify-between">
            <div className="flex items-center justify-between text-xs">
              <label className="font-bold text-text-primary">Flag & Escalate Threshold</label>
              <span className="font-mono font-bold text-amber-500 text-sm">{Math.round(config.flag_threshold * 100)}%</span>
            </div>
            <input
              type="range"
              min="0.1"
              max="0.8"
              step="0.05"
              value={config.flag_threshold}
              onChange={(e) => handleThresholdChange('flag_threshold', parseFloat(e.target.value))}
              className="w-full ae-slider ae-slider-amber"
              style={{
                background: `linear-gradient(to right, #F59E0B 0%, #F59E0B ${((config.flag_threshold - 0.1) / 0.7) * 100}%, #E2E8F0 ${((config.flag_threshold - 0.1) / 0.7) * 100}%, #E2E8F0 100%)`,
              }}
            />
            <span className="text-[10px] text-text-muted leading-tight">Risks between Flag and Deny prompt user confirmation.</span>
          </div>
        </div>
      </div>

      {/* 3. Incidents Feed & Live Telemetry Stream */}
      <div className="bg-white rounded-2xl border border-border-subtle shadow-sm overflow-hidden space-y-0">
        {/* Main Tab Controls */}
        <div className="p-4 border-b border-border-subtle flex flex-col lg:flex-row lg:items-center justify-between gap-3 bg-canvas/30">
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              onClick={() => { setActiveTab('all_sessions'); setCurrentPage(1); }}
              className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-colors cursor-pointer ${
                activeTab === 'all_sessions'
                  ? 'bg-dark-base text-white shadow-sm'
                  : 'bg-white text-text-secondary hover:text-text-primary border border-border-subtle'
              }`}
            >
              <span>All Sessions ({totalAllCount})</span>
            </button>
            <button
              type="button"
              onClick={() => { setActiveTab('blocked_sessions'); setCurrentPage(1); }}
              className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-colors cursor-pointer ${
                activeTab === 'blocked_sessions'
                  ? 'bg-rose-600 text-white shadow-sm'
                  : 'bg-white text-text-secondary hover:text-text-primary border border-border-subtle'
              }`}
            >
              <span>🛑 Blocked ({totalBlockedCount})</span>
            </button>
            <button
              type="button"
              onClick={() => { setActiveTab('escalated_sessions'); setCurrentPage(1); }}
              className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-colors cursor-pointer ${
                activeTab === 'escalated_sessions'
                  ? 'bg-amber-500 text-white shadow-sm'
                  : 'bg-white text-text-secondary hover:text-text-primary border border-border-subtle'
              }`}
            >
              <span>⚠️ Flagged ({totalEscalatedCount})</span>
            </button>
            <button
              type="button"
              onClick={() => { setActiveTab('cleared_sessions'); setCurrentPage(1); }}
              className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-colors cursor-pointer ${
                activeTab === 'cleared_sessions'
                  ? 'bg-emerald-600 text-white shadow-sm'
                  : 'bg-white text-text-secondary hover:text-text-primary border border-border-subtle'
              }`}
            >
              <span>✅ Cleared ({totalClearedCount})</span>
            </button>
            <button
              type="button"
              onClick={() => { setActiveTab('live_stream'); setCurrentPage(1); }}
              className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-colors flex items-center gap-1.5 cursor-pointer ${
                activeTab === 'live_stream'
                  ? 'bg-brand-purple text-white shadow-sm'
                  : 'bg-white text-text-secondary hover:text-text-primary border border-border-subtle'
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />
              <span>Live Stream ({liveInterceptions.length})</span>
            </button>
          </div>

          {/* Quick Search */}
          <div className="relative min-w-[220px]">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => { setSearchQuery(e.target.value); setCurrentPage(1); }}
              placeholder="Filter by headline, dev, ID..."
              className="w-full bg-white border border-border-subtle rounded-xl pl-7 pr-3 py-1.5 text-xs text-text-primary focus:outline-none focus:border-brand-primary"
            />
            <IonIcon
              icon={searchOutline}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted text-xs pointer-events-none"
            />
          </div>
        </div>

        {/* Secondary Filter Bar */}
        {activeTab !== 'live_stream' && (
          <div className="p-3 px-4 border-b border-border-subtle bg-white flex items-center justify-between gap-3 flex-wrap text-xs">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] font-bold text-text-muted uppercase font-mono flex items-center gap-1">
                <IonIcon icon={filterOutline} className="text-xs" />
                Filter:
              </span>

              {/* Agent Filter */}
              <select
                value={agentFilter}
                onChange={(e) => { setAgentFilter(e.target.value); setCurrentPage(1); }}
                className="bg-canvas border border-border-subtle rounded-lg px-2.5 py-1 text-xs text-text-primary font-medium focus:outline-none focus:border-brand-primary"
              >
                <option value="all">All Agents</option>
                <option value="antigravity">Antigravity</option>
                <option value="claude_code">Claude Code</option>
              </select>

              {/* Dimension Filter */}
              <select
                value={dimensionFilter}
                onChange={(e) => { setDimensionFilter(e.target.value); setCurrentPage(1); }}
                className="bg-canvas border border-border-subtle rounded-lg px-2.5 py-1 text-xs text-text-primary font-medium focus:outline-none focus:border-brand-primary"
              >
                <option value="all">All Dimensions</option>
                <option value="exfiltration">Exfiltration</option>
                <option value="tampering">Reward Tampering</option>
                <option value="privilege">Privilege Escalation</option>
                <option value="insecure">Insecure Code</option>
                <option value="refactoring">Refactoring</option>
                <option value="database">Database</option>
                <option value="api">API</option>
              </select>
            </div>

            <span className="text-[11px] font-mono text-text-muted">
              Showing {filteredSessions.length} matching sessions
            </span>
          </div>
        )}

        {/* Sessions Feed (All, Blocked, Escalated, or Cleared) */}
        {activeTab !== 'live_stream' && (
          <div className="divide-y divide-border-subtle">
            {displayedSessions.length === 0 ? (
              <div className="p-12 text-center text-xs text-text-muted space-y-2">
                <IonIcon icon={shieldCheckmark} className="text-3xl text-emerald-600 mb-1" />
                <div className="font-bold text-text-primary">No Sessions Match Your Filter</div>
                <p>Try adjusting your search query, status, or threshold controls.</p>
              </div>
            ) : (
              displayedSessions.map((session) => (
                <div
                  key={session.id}
                  onClick={() => {
                    if (onSelectIncident) {
                      onSelectIncident(getSessionFinding(session));
                    }
                  }}
                  className="p-5 hover:bg-canvas/50 transition-colors cursor-pointer flex flex-col md:flex-row md:items-center justify-between gap-4 group"
                >
                  <div className="space-y-2 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      {/* Status / Severity Badge (Dynamically Evaluated) */}
                      {session.is_blocked ? (
                        <span className="px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-rose-500/10 text-rose-600 border border-rose-500/20">
                          🛑 Blocked on Turn {session.blocked_turn || 14}
                        </span>
                      ) : session.is_escalated ? (
                        <span className="px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-amber-500/10 text-amber-600 border border-amber-500/20">
                          ⚠️ Escalated / Flagged
                        </span>
                      ) : (
                        <span className="px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-emerald-500/10 text-emerald-600 border border-emerald-500/20 flex items-center gap-1">
                          <IonIcon icon={checkmarkCircle} className="text-xs" />
                          Cleared & Compliant
                        </span>
                      )}

                      {/* Risk Score Pill */}
                      <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold border ${
                        session.is_blocked
                          ? 'bg-rose-50 text-rose-700 border-rose-200'
                          : session.is_escalated
                          ? 'bg-amber-50 text-amber-700 border-amber-200'
                          : 'bg-emerald-50 text-emerald-700 border-emerald-200'
                      }`}>
                        Risk: {Math.round(session.risk_score * 100)}%
                      </span>

                      {/* Agent Badge */}
                      <span className="px-2 py-0.5 rounded-md bg-surface-subtle text-text-primary text-[11px] font-medium">
                        {session.agent_source === 'antigravity' ? '🤖 Antigravity' : session.agent_source === 'claude_code' ? '⚡ Claude Code' : '⚡ OpenEval Runner'}
                      </span>

                      {/* Dimension */}
                      <span className="text-xs font-mono font-semibold text-brand-purple">
                        {session.dimension}
                      </span>
                    </div>

                    <h4 className={`text-sm font-bold text-text-primary transition-colors ${
                      session.is_blocked ? 'group-hover:text-rose-700' : session.is_escalated ? 'group-hover:text-amber-700' : 'group-hover:text-emerald-700'
                    }`}>
                      {session.headline}
                    </h4>

                    <p className="text-xs text-text-secondary line-clamp-2 leading-relaxed">
                      {session.summary}
                    </p>

                    <div className="flex items-center gap-3 text-[11px] text-text-muted font-mono pt-1 flex-wrap">
                      <span>Developer: <strong>{session.developer}</strong></span>
                      <span>·</span>
                      <span>{new Date(session.timestamp).toLocaleDateString()}</span>
                      <span>·</span>
                      <span>Turns: {session.total_turns}</span>
                      <span>·</span>
                      <span className="text-text-primary font-bold">{session.id}</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 flex-shrink-0">
                    <button
                      type="button"
                      className={`px-3.5 py-2 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-colors shadow-sm cursor-pointer ${
                        session.is_blocked
                          ? 'bg-dark-base text-white group-hover:bg-rose-700'
                          : session.is_escalated
                          ? 'bg-amber-600 text-white group-hover:bg-amber-700'
                          : 'bg-emerald-600 text-white group-hover:bg-emerald-700'
                      }`}
                    >
                      <span>{session.is_blocked ? 'Investigate Incident' : 'Inspect Session'}</span>
                      <IonIcon icon={chevronForwardOutline} className="text-xs" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {/* Tab: Live Telemetry Stream */}
        {activeTab === 'live_stream' && (
          <div>
            {/* Live Stream Sub-Toolbar */}
            <div className="p-3 px-5 border-b border-border-subtle bg-canvas/30 flex items-center justify-between text-xs">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                <span className="font-mono text-text-muted text-[11px]">
                  Showing <strong>{displayedLive.length}</strong> real-time agent tool executions (click row to expand command)
                </span>
              </div>
              <button
                type="button"
                onClick={() => toggleExpandAll(displayedLive.length)}
                className="px-2.5 py-1 rounded-lg bg-white border border-border-subtle hover:bg-canvas text-text-primary text-[11px] font-bold transition-all shadow-2xs flex items-center gap-1 cursor-pointer"
              >
                <IonIcon icon={expandedLiveIdxs.size === displayedLive.length && displayedLive.length > 0 ? chevronUpOutline : chevronDownOutline} className="text-xs" />
                <span>{expandedLiveIdxs.size === displayedLive.length && displayedLive.length > 0 ? 'Collapse All' : 'Expand All'}</span>
              </button>
            </div>

            <div className="divide-y divide-border-subtle max-h-[600px] overflow-y-auto">
              {displayedLive.length === 0 ? (
                <div className="p-12 text-center text-xs text-text-muted">
                  No tool interceptions recorded in this session. Propose tool actions in Antigravity or Claude Code to see live events.
                </div>
              ) : (
                displayedLive.map((v, vIdx) => {
                  const isExpanded = expandedLiveIdxs.has(vIdx);
                  const timeInfo = formatEventTime(v.timestamp);
                  return (
                    <div key={vIdx} className="hover:bg-canvas/40 transition-colors">
                      {/* Header Row */}
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

                          {/* Formatted Timestamp */}
                          <div className="flex items-center gap-1.5 font-mono text-[11px] text-text-muted flex-shrink-0 bg-canvas px-2.5 py-1 rounded-md border border-border-subtle">
                            <IonIcon icon={timeOutline} className="text-xs text-text-muted" />
                            <span className="font-semibold text-text-primary">{timeInfo.time}</span>
                            <span className="text-[10px] text-text-muted">({timeInfo.relative})</span>
                          </div>

                          {/* Decision Badge */}
                          <span className={`px-2 py-0.5 rounded font-mono font-bold text-[10px] uppercase flex-shrink-0 ${
                            v.decision === 'deny'
                              ? 'bg-rose-600 text-white shadow-2xs'
                              : v.decision === 'escalate'
                              ? 'bg-amber-500 text-white shadow-2xs'
                              : 'bg-emerald-600 text-white shadow-2xs'
                          }`}>
                            {v.decision === 'deny' ? '🛑 Denied' : v.decision === 'escalate' ? '⚠️ Escalated' : '✓ Allowed'}
                          </span>

                          {/* Agent Badge */}
                          <span className="font-mono text-text-secondary flex-shrink-0">
                            {v.agent_id === 'antigravity' ? '🤖 Antigravity' : v.agent_id === 'claude_code' || v.agent_id === 'claude-code' ? '⚡ Claude Code' : '⚡ ReAct'}
                          </span>

                          {/* Action Preview (Truncated in collapsed mode) */}
                          <span className={`font-mono font-semibold text-text-primary ${isExpanded ? 'break-all' : 'truncate max-w-md'}`}>
                            {v.action_preview}
                          </span>
                        </div>

                        {/* Right side telemetry summary */}
                        <div className="flex items-center gap-3 font-mono text-[11px] text-text-muted flex-shrink-0">
                          <span className="bg-surface-subtle px-2 py-0.5 rounded text-[10px]">
                            {v.stage === 'stage_1_readonly' ? 'Read-only Gate' : v.stage === 'stage_2_deterministic' ? 'Deterministic Gate' : v.stage === 'stage_3_triage' ? 'Heuristic Triage' : v.stage === 'stage_4_deep_review' ? 'Deep Review' : 'Security Gate'}
                          </span>
                          <span className={`font-bold ${v.risk_score >= 0.8 ? 'text-rose-600' : v.risk_score >= 0.4 ? 'text-amber-600' : 'text-emerald-600'}`}>
                            {(v.risk_score * 100).toFixed(0)}% risk
                          </span>
                          <span className="text-text-muted">{v.latency_ms}ms</span>
                        </div>
                      </div>

                      {/* Expanded Command & Security Diagnostics Drawer */}
                      {isExpanded && (
                        <div className="px-5 pb-4 pt-1 bg-surface-subtle/30 border-t border-border-subtle/60 space-y-3 animate-fadeIn">
                          {/* Full Command with Copy Button */}
                          <div className="space-y-1.5">
                            <div className="flex items-center justify-between text-[11px] font-mono font-bold text-text-secondary uppercase">
                              <div className="flex items-center gap-1.5">
                                <IonIcon icon={terminalOutline} className="text-xs text-brand-purple" />
                                <span>Complete Unclipped Action / Command Payload</span>
                              </div>
                              <button
                                type="button"
                                onClick={(e) => handleCopyCommand(v.action_preview, vIdx, e)}
                                className="px-2.5 py-0.5 rounded bg-white hover:bg-canvas text-text-primary border border-border-subtle flex items-center gap-1 text-[10px] font-mono cursor-pointer transition-all shadow-2xs"
                              >
                                <IonIcon icon={copiedIdx === vIdx ? checkmarkOutline : copyOutline} className="text-xs text-brand-purple" />
                                <span>{copiedIdx === vIdx ? 'Copied!' : 'Copy Command'}</span>
                              </button>
                            </div>
                            <pre className="p-3.5 rounded-xl bg-[#14121F] text-slate-200 font-mono text-xs overflow-x-auto whitespace-pre-wrap leading-relaxed border border-border-subtle shadow-inner select-text">
                              {v.action_preview}
                            </pre>
                          </div>

                          {/* Security Evaluation Details */}
                          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs font-mono">
                            <div className="p-2.5 rounded-lg bg-white border border-border-subtle shadow-2xs">
                              <div className="text-[10px] text-text-muted uppercase">Security Decision</div>
                              <div className="font-bold text-text-primary mt-0.5 flex items-center gap-1.5">
                                <span className={`w-2 h-2 rounded-full ${v.decision === 'deny' ? 'bg-rose-500' : v.decision === 'escalate' ? 'bg-amber-500' : 'bg-emerald-500'}`} />
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
                              <div className="font-bold text-text-primary mt-0.5">
                                {v.stage}
                              </div>
                            </div>

                            <div className="p-2.5 rounded-lg bg-white border border-border-subtle shadow-2xs">
                              <div className="text-[10px] text-text-muted uppercase">Analysis Latency</div>
                              <div className="font-bold text-text-primary mt-0.5">
                                {v.latency_ms} ms
                              </div>
                            </div>
                          </div>

                          {v.reason && (
                            <div className="p-3 rounded-lg bg-white border border-border-subtle text-xs text-text-secondary leading-relaxed font-sans">
                              <strong className="font-mono text-text-primary">Policy Note: </strong>
                              {v.reason}
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
        )}

        {/* Pagination Bar (Threshold: 25 items per page) */}
        {totalPages > 1 && (
          <div className="p-4 border-t border-border-subtle bg-canvas/40 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs select-none">
            <span className="text-text-muted font-mono text-[11px]">
              Showing <strong className="text-text-primary">{(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, totalItems)}</strong> of <strong className="text-text-primary">{totalItems}</strong> items
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
