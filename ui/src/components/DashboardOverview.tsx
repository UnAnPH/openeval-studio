import React, { useEffect, useMemo, useState } from 'react';
import { IonIcon } from '@ionic/react';
import {
  alertCircle,
  pulseOutline,
  shieldCheckmark,
  timeOutline,
} from 'ionicons/icons';
import { FindingRecord, RunRecord, TaskSummary } from '../types';

interface DashboardOverviewProps {
  runs?: RunRecord[];
  tasks?: TaskSummary[];
  findings?: FindingRecord[];
  onSelectIncident?: (finding: FindingRecord) => void;
  onNavigateToStudio?: () => void;
  onNavigateToTestCases?: () => void;
  onNavigateToRuns?: () => void;
  onNavigateToFirewall?: () => void;
}

interface RawSession {
  id: string;
  session_id: string;
  headline?: string;
  summary?: string;
  developer?: string;
  timestamp?: string;
  severity?: string;
  agent_source?: string;
  is_blocked?: boolean;
  is_escalated?: boolean;
  blocked_turn?: number | null;
  total_turns?: number;
  turns?: any[];
  // WatcherStore session shape (API uses created_at, not timestamp)
  created_at?: string;
  updated_at?: string;
  title?: string;
  agent_type?: string;
  final_summary?: string;
  status?: string;
  steps?: any[];
  total_steps?: number;
}

export const DashboardOverview: React.FC<DashboardOverviewProps> = ({
  runs,
  findings: propFindings,
  onSelectIncident,
}) => {
  const [dateFilter, setDateFilter] = useState<'today' | 'this_week' | 'last_week' | 'this_month' | 'last_month'>('today');
  const [watcherSessions, setWatcherSessions] = useState<RawSession[]>([]);

  // Fetch monitored watcher sessions (same source as Safety → Sessions)
  useEffect(() => {
    const fetchSessions = async () => {
      try {
        const res = await fetch('/api/v1/watcher/sessions');
        if (res.ok) {
          const data: RawSession[] = await res.json();
          setWatcherSessions(data);
        }
      } catch (err) {
        console.warn('Failed to fetch watcher sessions:', err);
      }
    };
    fetchSessions();
    const interval = setInterval(fetchSessions, 5000);
    return () => clearInterval(interval);
  }, []);

  // Safe timestamp parser
  const parseTimestamp = (ts?: string | null): number => {
    if (!ts) return 0;
    const t = new Date(ts).getTime();
    return isNaN(t) ? 0 : t;
  };

  const sessionObservedAt = (s: RawSession): string | undefined =>
    s.timestamp || s.created_at || s.updated_at;

  // Combine and deduplicate real sessions without inventing fake ones
  const allSessions = useMemo(() => {
    const sessionMap = new Map<string, RawSession>();

    // 1. Monitored sessions from WatcherStore (normalize API fields for Overview)
    for (const ws of watcherSessions) {
      const id = ws.session_id || ws.id;
      const observedAt = sessionObservedAt(ws);
      sessionMap.set(id, {
        ...ws,
        id: id,
        session_id: id,
        headline: ws.headline || ws.title || `Session ${id}`,
        summary: ws.summary || ws.final_summary || (ws.total_steps ? `${ws.total_steps} steps` : undefined),
        timestamp: observedAt,
        agent_source: ws.agent_source || ws.agent_type,
        developer: ws.developer || ws.agent_type || 'Agent',
        turns: ws.turns || ws.steps || [],
        total_turns: ws.total_turns ?? ws.total_steps ?? 0,
        is_blocked: ws.is_blocked ?? ws.status === 'blocked',
      });
    }

    // 2. Monitored evaluation runs
    for (const r of runs || []) {
      const id = (r as any).session_id || r.run_id;
      if (!sessionMap.has(id)) {
        sessionMap.set(id, {
          id,
          session_id: id,
          headline: `Evaluation: ${r.task_id || r.run_id}`,
          timestamp: r.created_at,
          severity: r.passed === false || (r.steps && r.steps.some((s) => s.firewall_blocked)) ? 'critical' : 'cleared',
          summary: r.final_summary || (r.steps?.length ? `${r.steps.length} turns executed` : 'Autonomous evaluation run'),
          developer: 'OpenEval Runner',
          agent_source: 'openeval_runner',
          is_blocked: r.steps ? r.steps.some((s) => s.firewall_blocked) : false,
          turns: r.steps || [],
          total_turns: r.steps?.length || 0,
        });
      }
    }

    // 3. Security findings
    for (const f of propFindings || []) {
      const id = f.session_id || f.id;
      if (sessionMap.has(id)) {
        const existing = sessionMap.get(id)!;
        sessionMap.set(id, {
          ...existing,
          headline: f.headline || existing.headline,
          summary: f.summary || existing.summary,
          severity: f.severity || existing.severity,
          is_blocked: f.severity === 'critical' || existing.is_blocked,
        });
      } else {
        sessionMap.set(id, {
          id,
          session_id: id,
          headline: f.headline,
          summary: f.summary,
          severity: f.severity,
          timestamp: f.timestamp,
          developer: f.developer || 'Security Engine',
          agent_source: f.agent_source,
          is_blocked: f.severity === 'critical',
          turns: [],
          total_turns: f.flagged_turns?.length || 1,
        });
      }
    }

    return Array.from(sessionMap.values());
  }, [watcherSessions, runs, propFindings]);

  // Dynamic Date Ranges relative to today (new Date())
  const rangeConfig = useMemo(() => {
    const now = new Date();
    const todayEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999);

    if (dateFilter === 'this_week') {
      const day = now.getDay();
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - day, 0, 0, 0, 0);
      const end = todayEnd;
      const prevEnd = new Date(start.getTime() - 1);
      const prevStart = new Date(prevEnd.getFullYear(), prevEnd.getMonth(), prevEnd.getDate() - 6, 0, 0, 0, 0);
      return { start, end, prevStart, prevEnd };
    }

    if (dateFilter === 'last_week') {
      const day = now.getDay();
      const currentWeekStart = new Date(now.getFullYear(), now.getMonth(), now.getDate() - day, 0, 0, 0, 0);
      const end = new Date(currentWeekStart.getTime() - 1);
      const start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - 6, 0, 0, 0, 0);
      const prevEnd = new Date(start.getTime() - 1);
      const prevStart = new Date(prevEnd.getFullYear(), prevEnd.getMonth(), prevEnd.getDate() - 6, 0, 0, 0, 0);
      return { start, end, prevStart, prevEnd };
    }

    if (dateFilter === 'this_month') {
      const start = new Date(now.getFullYear(), now.getMonth(), 1, 0, 0, 0, 0);
      const end = todayEnd;
      const prevStart = new Date(now.getFullYear(), now.getMonth() - 1, 1, 0, 0, 0, 0);
      const prevEnd = new Date(now.getFullYear(), now.getMonth(), 0, 23, 59, 59, 999);
      return { start, end, prevStart, prevEnd };
    }

    if (dateFilter === 'last_month') {
      const start = new Date(now.getFullYear(), now.getMonth() - 1, 1, 0, 0, 0, 0);
      const end = new Date(now.getFullYear(), now.getMonth(), 0, 23, 59, 59, 999);
      const prevStart = new Date(now.getFullYear(), now.getMonth() - 2, 1, 0, 0, 0, 0);
      const prevEnd = new Date(now.getFullYear(), now.getMonth() - 1, 0, 23, 59, 59, 999);
      return { start, end, prevStart, prevEnd };
    }

    // Default: 'today'
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
    const end = todayEnd;
    const prevStart = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1, 0, 0, 0, 0);
    const prevEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1, 23, 59, 59, 999);
    return { start, end, prevStart, prevEnd };
  }, [dateFilter]);

  const formatDate = (d: Date): string => {
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  };

  const formatShortRange = (start: Date, end: Date): string => {
    const sStr = start.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    const eStr = end.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    return `${sStr} - ${eStr}`;
  };

  const dateRangeSubtitle =
    dateFilter === 'today'
      ? formatDate(rangeConfig.start)
      : `${formatDate(rangeConfig.start)} - ${formatDate(rangeConfig.end)}`;
  const prevRangeLabel =
    dateFilter === 'today'
      ? `Yesterday (${formatShortRange(rangeConfig.prevStart, rangeConfig.prevEnd)})`
      : formatShortRange(rangeConfig.prevStart, rangeConfig.prevEnd);

  // Filter sessions in current and prior windows
  const startMs = rangeConfig.start.getTime();
  const endMs = rangeConfig.end.getTime();

  const currentSessions = allSessions.filter((s) => {
    const t = parseTimestamp(sessionObservedAt(s));
    // Include sessions with no usable timestamp in the current window so the KPI
    // matches WatcherStore totals when ingest timestamps are missing.
    if (!t) return dateFilter === 'today';
    return t >= startMs && t <= endMs;
  });

  const prevSessions = allSessions.filter((s) => {
    const t = parseTimestamp(sessionObservedAt(s));
    if (!t) return false;
    return t >= rangeConfig.prevStart.getTime() && t <= rangeConfig.prevEnd.getTime();
  });

  // Real Calculated Metrics
  const observedCount = currentSessions.length;
  const prevObservedCount = prevSessions.length;
  const deltaObserved = observedCount - prevObservedCount;

  const criticalSessions = currentSessions.filter(
    (s) => s.severity === 'critical' || s.is_blocked || (s.turns && s.turns.some((t: any) => t.is_blocked))
  );
  const criticalCount = criticalSessions.length;
  const prevCriticalCount = prevSessions.filter(
    (s) => s.severity === 'critical' || s.is_blocked || (s.turns && s.turns.some((t: any) => t.is_blocked))
  ).length;
  const deltaCritical = criticalCount - prevCriticalCount;

  const criticalRate = observedCount > 0 ? ((criticalCount / observedCount) * 100).toFixed(1) : '0.0';

  // Generate Data for Charts (Hourly for 'today', Daily for multi-day windows)
  const chartDailyData = useMemo(() => {
    if (dateFilter === 'today') {
      const points: { dateIso: string; dateLabel: string; total: number; critical: number }[] = [];
      const todayStartMs = rangeConfig.start.getTime();
      const currentHour = new Date().getHours();

      for (let h = 0; h < 24; h++) {
        const bucketStart = todayStartMs + h * 3600 * 1000;
        const bucketEnd = bucketStart + 3600 * 1000 - 1;
        const hourStr = String(h).padStart(2, '0') + ':00';
        const nextHourStr = String(h + 1).padStart(2, '0') + ':00';

        const hourSessions = currentSessions.filter((s) => {
          const t = parseTimestamp(sessionObservedAt(s));
          if (!t) return h === currentHour;
          return t >= bucketStart && t <= bucketEnd;
        });

        const hourCritical = hourSessions.filter(
          (s) =>
            s.severity === 'critical' ||
            s.is_blocked ||
            (s.turns && s.turns.some((t: any) => t.is_blocked))
        );

        points.push({
          dateIso: `${hourStr} - ${nextHourStr}`,
          dateLabel: hourStr,
          total: hourSessions.length,
          critical: hourCritical.length,
        });
      }
      return points;
    }

    const diffMs = endMs - startMs;
    const daysCount = Math.max(1, Math.round(diffMs / (24 * 60 * 60 * 1000)));
    const points: { dateIso: string; dateLabel: string; total: number; critical: number }[] = [];

    for (let i = 0; i <= daysCount; i++) {
      const cur = new Date(rangeConfig.start.getTime() + i * 24 * 60 * 60 * 1000);
      if (cur.getTime() > endMs && i > 0) break;
      const dateIso = cur.toISOString().split('T')[0];
      const dateLabel = cur.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

      const daySessions = currentSessions.filter((s) => {
        const ts = sessionObservedAt(s);
        return ts && ts.startsWith(dateIso);
      });

      const dayCritical = daySessions.filter(
        (s) => s.severity === 'critical' || s.is_blocked || (s.turns && s.turns.some((t: any) => t.is_blocked))
      );

      points.push({
        dateIso,
        dateLabel,
        total: daySessions.length,
        critical: dayCritical.length,
      });
    }
    return points;
  }, [dateFilter, currentSessions, startMs, endMs, rangeConfig.start]);

  const maxDailyTotal = Math.max(1, ...chartDailyData.map((d) => d.total));
  const maxDailyCritical = Math.max(1, ...chartDailyData.map((d) => d.critical));

  // Pick tick indices for X-axis labels (~6-8 labels)
  const tickIndices = useMemo(() => {
    if (dateFilter === 'today') {
      // 4-hour marks across the 24-hour day: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00, 23:00
      return [0, 4, 8, 12, 16, 20, 23];
    }
    const tickCount = Math.min(8, chartDailyData.length);
    return Array.from({ length: tickCount }, (_, i) =>
      Math.floor((i * (chartDailyData.length - 1)) / (tickCount - 1 || 1))
    );
  }, [dateFilter, chartDailyData.length]);

  return (
    <div className="flex-1 min-h-0 flex flex-col h-full bg-[#fcfcfd] text-[#1e2029] font-sans p-6 space-y-6 overflow-y-auto">
      {/* 1. Header Card Block */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm shrink-0">
        <div>
          <h1 className="text-base font-bold text-slate-900 tracking-tight flex items-center gap-2.5">
            Overview
          </h1>
          <p className="text-xs text-slate-500 font-medium mt-0.5">{dateRangeSubtitle}</p>
        </div>

        {/* Date Selector Tabs */}
        <div className="flex items-center bg-slate-100 p-1 rounded-xl border border-slate-200/60 text-xs font-medium overflow-x-auto">
          {[
            { id: 'today', label: 'Today' },
            { id: 'this_week', label: 'This week' },
            { id: 'last_week', label: 'Last week' },
            { id: 'this_month', label: 'This month' },
            { id: 'last_month', label: 'Last month' },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setDateFilter(tab.id as any)}
              className={`px-3 py-1.5 rounded-lg transition-all cursor-pointer whitespace-nowrap ${
                dateFilter === tab.id
                  ? 'bg-white text-slate-900 shadow-xs font-bold'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* 2. Top KPI Cards Row */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {/* Card 1: Sessions Observed */}
        <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-gray-500">Sessions Observed</span>
            <div className="w-7 h-7 rounded-lg bg-gray-100 flex items-center justify-center text-gray-500">
              <IonIcon icon={pulseOutline} className="text-sm" />
            </div>
          </div>
          <div className="my-2">
            <div className="text-3xl font-bold text-gray-900 tracking-tight">
              {observedCount.toLocaleString()}
            </div>
          </div>
          <div className="text-xs text-gray-400 font-medium">
            {deltaObserved >= 0 ? `+${deltaObserved}` : deltaObserved} vs {prevRangeLabel}
          </div>
        </div>

        {/* Card 2: Critical Sessions */}
        <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-gray-500">Critical Sessions</span>
            <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${criticalCount > 0 ? 'bg-rose-50 text-rose-600' : 'bg-gray-100 text-gray-500'}`}>
              <IonIcon icon={alertCircle} className="text-sm" />
            </div>
          </div>
          <div className="my-2">
            <div className={`text-3xl font-bold tracking-tight ${criticalCount > 0 ? 'text-rose-600' : 'text-gray-900'}`}>
              {criticalCount}
            </div>
          </div>
          <div className="text-xs text-gray-400 font-medium">
            {deltaCritical >= 0 ? `+${deltaCritical}` : deltaCritical} vs {prevRangeLabel}
          </div>
        </div>

        {/* Card 3: Critical Session Rate */}
        <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-gray-500">Critical Session Rate</span>
            <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${criticalCount > 0 ? 'bg-rose-50 text-rose-600' : 'bg-gray-100 text-gray-500'}`}>
              <IonIcon icon={alertCircle} className="text-sm" />
            </div>
          </div>
          <div className="my-2">
            <div className={`text-3xl font-bold tracking-tight ${criticalCount > 0 ? 'text-rose-600' : 'text-gray-900'}`}>
              {criticalRate}%
            </div>
          </div>
          <div className="text-xs text-gray-400 font-medium truncate">
            {criticalCount} of {observedCount} sessions · {criticalRate}%
          </div>
        </div>
      </div>

      {/* 3. Dual Daily Charts Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Chart 1: Total Sessions */}
        <div className="bg-white p-6 rounded-2xl border border-border-subtle shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-bold text-gray-900">Total Sessions</h3>
              <p className="text-xs text-gray-500 mt-0.5">
                {dateFilter === 'today'
                  ? 'Hourly active sessions today (24h breakdown)'
                  : 'Daily active sessions in the selected period'}
              </p>
            </div>
            <span className="text-xs font-mono font-bold text-teal-700 bg-teal-50 px-2 py-0.5 rounded border border-teal-200">
              {observedCount} active
            </span>
          </div>

          <div className="relative pt-4">
            {/* Chart Area */}
            <div className="h-44 flex items-end justify-between gap-1 border-b border-gray-100 pb-1">
              {chartDailyData.map((item, idx) => {
                const heightPct = item.total > 0 ? Math.max(12, Math.round((item.total / maxDailyTotal) * 100)) : 0;
                return (
                  <div
                    key={idx}
                    className="flex-1 flex flex-col items-center justify-end h-full group relative cursor-pointer"
                  >
                    {/* Tooltip */}
                    <div className="absolute -top-8 hidden group-hover:block bg-gray-900 text-white text-[10px] font-mono px-2 py-1 rounded shadow-md whitespace-nowrap z-20">
                      {item.dateIso}: {item.total} session{item.total !== 1 ? 's' : ''}
                    </div>
                    {/* Teal Bar */}
                    {item.total > 0 ? (
                      <div
                        className="w-full bg-[#0d9488] hover:bg-[#0f766e] rounded-t-sm transition-all"
                        style={{ height: `${heightPct}%` }}
                      />
                    ) : (
                      <div className="w-full h-0.5 bg-gray-100" />
                    )}
                  </div>
                );
              })}
            </div>

            {/* X-Axis Dates */}
            <div className="flex items-center justify-between text-[10px] font-mono text-gray-400 pt-2">
              {tickIndices.map((tIdx) => (
                <span key={tIdx}>{chartDailyData[tIdx]?.dateLabel || ''}</span>
              ))}
            </div>
          </div>
        </div>

        {/* Chart 2: Critical Agent Actions */}
        <div className="bg-white p-6 rounded-2xl border border-border-subtle shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-bold text-gray-900">Critical Agent Actions</h3>
              <p className="text-xs text-gray-500 mt-0.5">
                {dateFilter === 'today'
                  ? 'Critical agent actions per hour today (24h breakdown)'
                  : 'Critical agent actions per day in the selected period'}
              </p>
            </div>
            <span className={`text-xs font-mono font-bold px-2 py-0.5 rounded border ${criticalCount > 0 ? 'text-rose-700 bg-rose-50 border-rose-200' : 'text-gray-500 bg-gray-50 border-gray-200'}`}>
              {criticalCount} critical
            </span>
          </div>

          <div className="relative pt-4">
            {/* Chart Area */}
            <div className="h-44 flex items-end justify-between gap-1 border-b border-gray-100 pb-1">
              {chartDailyData.map((item, idx) => {
                const heightPct = item.critical > 0 ? Math.max(15, Math.round((item.critical / maxDailyCritical) * 100)) : 0;
                return (
                  <div
                    key={idx}
                    className="flex-1 flex flex-col items-center justify-end h-full group relative cursor-pointer"
                  >
                    {/* Tooltip */}
                    {item.critical > 0 && (
                      <div className="absolute -top-8 hidden group-hover:block bg-gray-900 text-white text-[10px] font-mono px-2 py-1 rounded shadow-md whitespace-nowrap z-20">
                        {item.dateIso}: {item.critical} critical action{item.critical !== 1 ? 's' : ''}
                      </div>
                    )}
                    {/* Coral Orange Bar */}
                    {item.critical > 0 ? (
                      <div
                        className="w-full bg-[#ea580c] hover:bg-[#c2410c] rounded-t-sm transition-all"
                        style={{ height: `${heightPct}%` }}
                      />
                    ) : (
                      <div className="w-full h-0.5 bg-gray-100" />
                    )}
                  </div>
                );
              })}
            </div>

            {/* X-Axis Dates */}
            <div className="flex items-center justify-between text-[10px] font-mono text-gray-400 pt-2">
              {tickIndices.map((tIdx) => (
                <span key={tIdx}>{chartDailyData[tIdx]?.dateLabel || ''}</span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* 4. Sessions to Review */}
      <div className="bg-white p-6 rounded-2xl border border-border-subtle shadow-sm space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-bold text-gray-900">Sessions to Review</h3>
          <span className="text-xs text-gray-500 font-medium">
            {criticalSessions.length > 0
              ? `Top ${Math.min(5, criticalSessions.length)} shown · ${criticalSessions.length} critical`
              : '0 critical · All sessions compliant'}
          </span>
        </div>

        {criticalSessions.length > 0 ? (
          <div className="space-y-3">
            {criticalSessions.slice(0, 5).map((s) => (
              <div
                key={s.id || s.session_id}
                onClick={() =>
                  onSelectIncident &&
                  onSelectIncident({
                    id: s.id,
                    session_id: s.session_id || s.id,
                    headline: s.headline || `Critical Session ${s.id}`,
                    summary: s.summary || 'Critical security incident flagged by policy engine.',
                    severity: 'critical',
                    developer: s.developer || 'Operator',
                    timestamp: s.timestamp || 'Recent',
                    dimension: 'Safety & Security',
                    agent_source: (s.agent_source as any) || 'antigravity',
                    recommended_actions: [],
                    flagged_turns: [],
                    tags: ['critical'],
                  })
                }
                className="p-4 rounded-xl border border-gray-100 hover:border-gray-300 bg-white hover:bg-gray-50/50 transition-all cursor-pointer flex items-start justify-between gap-4 group"
              >
                <div className="space-y-1 flex-1 min-w-0">
                  <h4 className="text-sm font-bold text-gray-900 group-hover:text-indigo-600 transition-colors leading-snug">
                    {s.headline || `Session ${s.session_id || s.id}`}
                  </h4>
                  <p className="text-xs text-gray-500 line-clamp-2 leading-relaxed">
                    {s.summary || 'Security policy interception or critical violation recorded.'}
                  </p>
                  <div className="text-xs text-gray-400 font-mono pt-1">
                    {s.timestamp ? new Date(s.timestamp).toLocaleString() : 'Recent'}
                  </div>
                </div>

                <div className="shrink-0 pt-0.5">
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-50 text-rose-600 border border-rose-200">
                    <span className="w-1.5 h-1.5 rounded-full bg-rose-600" />
                    <span>Critical</span>
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="space-y-4">
            <div className="p-6 text-center bg-gray-50/50 rounded-xl border border-dashed border-gray-200 space-y-1.5">
              <div className="w-7 h-7 rounded-full bg-emerald-50 text-emerald-600 flex items-center justify-center mx-auto">
                <IonIcon icon={shieldCheckmark} className="text-sm" />
              </div>
              <p className="text-xs font-bold text-gray-800">No Critical Sessions Recorded</p>
              <p className="text-[11px] text-gray-500 max-w-md mx-auto">
                All {observedCount} monitored sessions in this period executed without policy violations or blocked commands.
              </p>
            </div>

            {/* Real Observed Sessions list so user can inspect their real sessions */}
            {currentSessions.length > 0 && (
              <div className="pt-2 space-y-2">
                <div className="text-xs font-bold text-gray-700">
                  Observed Agent Sessions ({currentSessions.length})
                </div>
                <div className="space-y-2">
                  {currentSessions.slice(0, 5).map((s) => (
                    <div
                      key={s.id || s.session_id}
                      onClick={() =>
                        onSelectIncident &&
                        onSelectIncident({
                          id: s.id,
                          session_id: s.session_id || s.id,
                          headline: s.headline || `Session ${s.session_id || s.id}`,
                          summary: s.summary || `${s.turns?.length || s.total_turns || 0} turns recorded.`,
                          severity: 'low',
                          developer: s.developer || 'Operator',
                          timestamp: s.timestamp || 'Recent',
                          dimension: 'Observability',
                          agent_source: (s.agent_source as any) || 'antigravity',
                          recommended_actions: [],
                          flagged_turns: [],
                          tags: ['cleared'],
                        })
                      }
                      className="p-3.5 rounded-xl border border-gray-100 hover:border-gray-300 bg-white hover:bg-gray-50/50 transition-all cursor-pointer flex items-center justify-between gap-4 group"
                    >
                      <div className="min-w-0 flex-1">
                        <h4 className="text-xs font-bold text-gray-900 group-hover:text-indigo-600 transition-colors truncate">
                          {s.headline || `Session ${s.session_id || s.id}`}
                        </h4>
                        <div className="flex items-center gap-2 text-[11px] text-gray-400 font-mono mt-0.5">
                          <span>{s.developer || 'Agent'}</span>
                          <span>·</span>
                          <span>{s.turns?.length || s.total_turns || 0} turns</span>
                          <span>·</span>
                          <span className="flex items-center gap-1">
                            <IonIcon icon={timeOutline} className="text-[10px]" />
                            <span>{s.timestamp ? new Date(s.timestamp).toLocaleDateString() : 'Active'}</span>
                          </span>
                        </div>
                      </div>
                      <span className="shrink-0 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">
                        ● Cleared
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
