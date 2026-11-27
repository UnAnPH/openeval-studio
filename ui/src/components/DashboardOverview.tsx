import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  Clock,
  Cpu,
  Layers,
  ShieldAlert,
  ShieldCheck,
  Zap,
} from 'lucide-react';
import { FindingRecord, RunRecord, TaskSummary } from '../types';
import {
  SIGNATURE_SPECS,
  canonicalThreatCategory,
  captureSourceForAgent,
  classifySignature,
  isActiveBlockedFleetReview,
  threatCategoryForReview,
} from '../lib/fleetAnalytics';

export interface RawSession extends RawSessionInput {
  id: string;
  session_id: string;
}

export interface DashboardOverviewProps {
  runs?: RunRecord[];
  tasks?: TaskSummary[];
  findings?: FindingRecord[];
  onSelectIncident?: (finding: FindingRecord) => void;
  onNavigateToStudio?: () => void;
  onNavigateToTestCases?: () => void;
  onNavigateToRuns?: () => void;
  onNavigateToFirewall?: () => void;
  isDemoSeed?: boolean;
  isDemo?: boolean;
  demoSessions?: RawSessionInput[];
}

export interface AnalyzerSummary {
  total_reviews: number;
  total_blocked: number;
  total_allowed: number;
  block_rate_pct: number;
  total_sessions: number;
  avg_latency_ms: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  top_threats?: Array<{ threat: string; count: number; avg_severity: number }>;
  agent_distribution?: Array<{ agent: string; total_events: number; lockouts: number }>;
  agent_breakdown?: Array<{ agent: string; total_events: number; lockouts: number }>;
  capture_sources?: Array<{ source: string; count?: number; key?: string; session_count?: number; pct?: number }> | {
    watcher_gate: number;
    transcript_ingest: number;
    inspect_ai: number;
    distribution: Array<{ source: string; key: string; session_count: number; pct?: number }>;
  };
  cross_session_patterns?: Array<{
    id?: string;
    pattern_id?: string;
    pattern?: string;
    category?: string;
    title?: string;
    threat_name?: string;
    signature?: string;
    target_pattern?: string;
    agents?: string[];
    rule_id?: string;
    affected_sessions?: number;
    session_count?: number;
    total_hits?: number;
    total_occurrences?: number;
    max_severity?: number;
    severity?: string;
    score?: number;
    sample_tool?: string;
    status?: string;
  }>;
  recent_interventions?: RecentIntervention[];
}

type CaptureSourceCard = {
  source: string;
  key: string;
  session_count: number;
  pct: number;
};

type ThreatCard = {
  threat: string;
  count: number;
  avg_severity: number;
};

const reviewIsBlocking = (review: any): boolean => {
  if (!review) return false;
  const decision = String(review.decision ?? '').toLowerCase();
  if (decision === 'block' || decision === 'deny') return true;
  const score = Number(review.score ?? review.risk_score ?? 0);
  return Number.isFinite(score) && score >= 8;
};

const sessionIsCritical = (session: any): boolean => {
  if (session.severity === 'critical' || session.is_blocked) return true;
  if ((session.turns || []).some((turn: any) => Boolean(turn?.is_blocked) || reviewIsBlocking(turn))) {
    return true;
  }
  return [...(session.trajectory?.reviews || []), ...(session.decisions || [])].some((review: any) =>
    reviewIsBlocking(review)
  );
};

type PatternCard = {
  id: string;
  pattern: string;
  title: string;
  threat_name: string;
  signature: string;
  target_pattern: string;
  agents: string[];
  category: string;
  affected_sessions: number;
  total_occurrences: number;
  severity: string;
  score: number;
  status: string;
  sample_tool?: string;
};

export interface RawReviewItem {
  id?: string;
  decision_id?: string;
  timestamp?: string;
  created_at?: string;
  tool_name?: string;
  tool?: string;
  tool_input?: string;
  toolInput?: string;
  arguments?: Record<string, unknown> | string;
  action_preview?: string;
  full_command?: string;
  command?: string;
  decision?: string | null;
  score?: number | null;
  risk_score?: number | null;
  stage?: string;
  rule_name?: string | null;
  rule_id?: string | null;
  rule_violation_tag?: string | null;
  threat_category?: string | null;
  explanation?: string | null;
  reason?: string | null;
  diff?: string | null;
  latency_ms?: number;
  is_blocked?: boolean | null;
  human_override?: string | null;
  resolution_status?: string | null;
}

export interface RawTurnItem extends RawReviewItem {
  step_number?: number;
  thought?: string;
  observation?: string;
  role?: string;
  content?: string;
}

export interface RawMessageItem {
  role?: string;
  content?: string;
  thinking?: string;
  timestamp?: number | string;
}

export interface RawToolCallItem {
  tool_id?: string;
  tool_name?: string;
  arguments?: Record<string, unknown>;
  raw_input?: string;
  timestamp?: number;
}

export interface RawToolResultItem {
  tool_id?: string;
  tool_name?: string;
  stdout?: string;
  stderr?: string;
  exit_code?: number;
  duration_ms?: number;
  is_error?: boolean;
}

export interface RecentIntervention {
  id?: string;
  session_id?: string;
  agent_type?: string;
  threat?: string;
  tool_name?: string;
  decision?: string | null;
  score?: number | null;
  risk_score?: number | null;
  timestamp?: string;
  explanation?: string;
  human_override?: string | null;
  resolution_status?: string | null;
  is_blocked?: boolean | null;
}

/** Demo/API session shape; every field is optional so fixture types stay assignable. */
export interface RawSessionInput {
  id?: string;
  session_id?: string;
  headline?: string;
  summary?: string | null;
  developer?: string;
  timestamp?: string;
  severity?: string;
  agent_source?: string;
  is_blocked?: boolean;
  is_escalated?: boolean;
  blocked_turn?: number | null;
  total_turns?: number;
  turns?: RawTurnItem[];
  created_at?: string;
  updated_at?: string;
  title?: string | null;
  agent_type?: string;
  final_summary?: string | null;
  status?: string;
  steps?: RawTurnItem[];
  total_steps?: number;
  org_id?: string;
  passed?: boolean | null;
  reward?: number | null;
  trajectory?: {
    reviews?: RawReviewItem[];
    messages?: RawMessageItem[];
    tool_calls?: RawToolCallItem[];
    tool_results?: RawToolResultItem[];
  };
  decisions?: RawReviewItem[];
}

type RunWithMeta = RunRecord & { session_id?: string; org_id?: string };
type FindingWithMeta = FindingRecord & { org_id?: string };

const KNOWN_AGENT_SOURCES: ReadonlyArray<FindingRecord['agent_source']> = [
  'antigravity',
  'claude_code',
  'cursor',
  'codex',
  'opencode',
  'openeval_runner',
];

/** Narrow an arbitrary agent string for `FindingRecord.agent_source`. */
const toAgentSource = (value?: string): FindingRecord['agent_source'] =>
  KNOWN_AGENT_SOURCES.some((source) => source === value)
    ? (value as FindingRecord['agent_source'])
    : 'antigravity';


export const DashboardOverview: React.FC<DashboardOverviewProps> = ({
  runs,
  findings: propFindings,
  onSelectIncident,
  isDemoSeed,
  isDemo: propIsDemo,
  demoSessions = [],
}) => {
  const [dateFilter, setDateFilter] = useState<'today' | 'this_week' | 'last_week' | 'this_month' | 'last_month'>('this_month');
  const isDemo = Boolean(
    propIsDemo ?? (isDemoSeed || (typeof window !== 'undefined' && (window.location.pathname.startsWith('/demo') || window.location.hash.startsWith('#/demo'))))
  );
  const [watcherSessions, setWatcherSessions] = useState<RawSession[]>(() =>
    isDemo && demoSessions.length > 0 ? (demoSessions as RawSession[]) : []
  );
  const [analyzerSummary, setAnalyzerSummary] = useState<AnalyzerSummary | null>(null);
  const summaryInFlightRef = useRef(false);
  const sessionsInFlightRef = useRef(false);

  useEffect(() => {
    const fetchSummary = async () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      if (summaryInFlightRef.current) return;
      summaryInFlightRef.current = true;
      try {
        const url = isDemo
          ? '/api/v1/analyzer/summary?surface=demo'
          : '/api/v1/analyzer/summary';
        const res = await fetch(url, {
          headers: isDemo ? { 'X-OpenEval-Surface': 'demo' } : undefined,
        });
        if (res.ok) {
          const data: AnalyzerSummary = await res.json();
          setAnalyzerSummary(data);
        }
      } catch (e) {
        console.warn('Failed to fetch analyzer summary:', e);
      } finally {
        summaryInFlightRef.current = false;
      }
    };
    fetchSummary();
    const interval = setInterval(fetchSummary, 20000);
    return () => clearInterval(interval);
  }, [isDemo]);

  useEffect(() => {
    if (isDemo && demoSessions && demoSessions.length > 0 && watcherSessions.length === 0) {
      setWatcherSessions(demoSessions as RawSession[]);
    } else if (!isDemo && watcherSessions.some((s) => (s.session_id || s.id || '').includes('demo') || s.org_id === 'demo')) {
      setWatcherSessions((prev) => prev.filter((s) => !(s.session_id || s.id || '').includes('demo') && s.org_id !== 'demo'));
    }
  }, [isDemo, demoSessions]);

  // Fetch monitored watcher sessions (lightweight summary metadata + reviews for Overview)
  useEffect(() => {
    const fetchSessions = async () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      if (sessionsInFlightRef.current) return;
      sessionsInFlightRef.current = true;
      try {
        const res = await fetch(
          `/api/v1/watcher/sessions?summary=1${isDemo ? '&surface=demo' : ''}`
        );
        if (res.ok) {
          const data: RawSession[] = await res.json();
          if (isDemo && data.length === 0) {
            setWatcherSessions(demoSessions as RawSession[]);
          } else if (isDemo) {
            setWatcherSessions(data);
          } else {
            // When NOT in demo mode, discard any demo sessions
            const clean = data.filter((s) => !(s.session_id || s.id || '').includes('demo') && s.org_id !== 'demo');
            setWatcherSessions((prev) => {
              const prevSig = prev.map((p) => `${p.session_id || p.id || ''}:${p.timestamp || p.created_at || ''}`).join(',');
              const nextSig = clean.map((p) => `${p.session_id || p.id || ''}:${p.timestamp || p.created_at || ''}`).join(',');
              return prevSig === nextSig ? prev : clean;
            });
          }
        } else if (isDemo) {
          setWatcherSessions(demoSessions as RawSession[]);
        } else {
          setWatcherSessions([]);
        }
      } catch (err) {
        if (isDemo) {
          setWatcherSessions(demoSessions as RawSession[]);
        } else {
          setWatcherSessions([]);
          console.warn('Failed to fetch watcher sessions:', err);
        }
      } finally {
        sessionsInFlightRef.current = false;
      }
    };
    fetchSessions();
    const interval = setInterval(fetchSessions, 20000);
    return () => clearInterval(interval);
  }, [isDemo, demoSessions]);

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
      if (!isDemo && ((id || '').includes('demo') || ws.org_id === 'demo')) {
        continue;
      }
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
      const id = (r as RunWithMeta).session_id || r.run_id;
      if (!isDemo && ((id || '').includes('demo') || (r as RunWithMeta).org_id === 'demo')) {
        continue;
      }
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
      if (!isDemo && ((id || '').includes('demo') || (f as FindingWithMeta).org_id === 'demo')) {
        continue;
      }
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
  }, [watcherSessions, runs, propFindings, isDemo]);

  // Dynamic Date Ranges relative to today (new Date())
  const rangeConfig = useMemo(() => {
    const now = new Date();
    const todayEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999);

    if (dateFilter === 'this_week') {
      const day = (now.getDay() + 6) % 7; // ISO week: Monday = 0 ... Sunday = 6
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - day, 0, 0, 0, 0);
      const end = todayEnd;
      const prevEnd = new Date(start.getTime() - 1);
      const prevStart = new Date(prevEnd.getFullYear(), prevEnd.getMonth(), prevEnd.getDate() - 6, 0, 0, 0, 0);
      return { start, end, prevStart, prevEnd };
    }

    if (dateFilter === 'last_week') {
      const day = (now.getDay() + 6) % 7;
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

  const rawCurrentSessions = allSessions.filter((s) => {
    const t = parseTimestamp(sessionObservedAt(s));
    // Include sessions with no usable timestamp in the current window so the KPI
    // matches WatcherStore totals when ingest timestamps are missing.
    if (!t) return dateFilter === 'today';
    return t >= startMs && t <= endMs;
  });

  // Demo fixtures use a rolling timeline; never render all-zero KPIs just because
  // the selected date window happens to miss every fixture.
  const currentSessions =
    isDemo && rawCurrentSessions.length === 0 && allSessions.length > 0
      ? allSessions
      : rawCurrentSessions;

  const prevSessions = allSessions.filter((s) => {
    const t = parseTimestamp(sessionObservedAt(s));
    if (!t) return false;
    return t >= rangeConfig.prevStart.getTime() && t <= rangeConfig.prevEnd.getTime();
  });

  const fleetIntelligence = useMemo(() => {
    const filterScoped = dateFilter !== 'this_month';
    const scopedSessions = currentSessions.length > 0 ? currentSessions : allSessions;

    type FleetReviewRow = {
      sessionId: string;
      ruleName: string;
      ruleId: string;
      reason: string;
      toolName: string;
      toolInput: string;
      score: number;
      decision: string;
      agentType: string;
    };

    const blockedReviewRows: FleetReviewRow[] = [];
    const blockedSessionIds = new Set<string>();

    for (const session of scopedSessions) {
      const sessionId = session.session_id || session.id;
      const rawReviews: RawReviewItem[] = [
        ...(session.trajectory?.reviews || []),
        ...(session.decisions || []),
      ];
      const turnReviews = (session.turns || session.steps || []).filter(
        (turn) =>
          turn && (turn.rule_name || turn.rule_id || turn.decision || turn.is_blocked || turn.rule_violation_tag)
      );
      const reviews = rawReviews.length > 0 ? rawReviews : turnReviews;

      for (const review of reviews) {
        if (!isActiveBlockedFleetReview(review)) continue;
        const decision = String(review.decision || (review.is_blocked ? 'block' : 'allow'));
        blockedSessionIds.add(sessionId);
        const rawArguments = review.arguments;
        const argumentCommand =
          typeof rawArguments === 'string'
            ? rawArguments
            : rawArguments && typeof rawArguments.CommandLine === 'string'
              ? rawArguments.CommandLine
              : '';
        const toolInput = String(
          review.tool_input ||
          review.toolInput ||
          argumentCommand ||
          review.action_preview ||
          review.full_command ||
          review.command ||
          ''
        );
        blockedReviewRows.push({
          sessionId,
          ruleName: String(review.rule_name || review.rule_violation_tag || review.threat_category || ''),
          ruleId: String(review.rule_id || ''),
          reason: String(review.explanation || review.reason || ''),
          toolName: String(review.tool_name || review.tool || 'Bash'),
          toolInput,
          score: Number(review.score ?? review.risk_score ?? 0) || 0,
          decision,
          agentType: String(session.agent_type || session.agent_source || 'agent'),
        });
      }

      if (
        session.is_blocked ||
        session.severity === 'critical' ||
        (session.turns || []).some((turn) => turn?.is_blocked)
      ) {
        blockedSessionIds.add(sessionId);
      }
    }

    // Capture sources: prefer backend when unfiltered; otherwise derive from live sessions.
    const backendCaptureRaw = Array.isArray(analyzerSummary?.capture_sources)
      ? analyzerSummary.capture_sources
      : analyzerSummary?.capture_sources?.distribution;
    let captureSources: CaptureSourceCard[] = [];

    if (!isDemo && !filterScoped && Array.isArray(backendCaptureRaw) && backendCaptureRaw.length > 0) {
      captureSources = backendCaptureRaw.map((source) => {
        const rawSource = source as {
          source: string;
          key?: string;
          count?: number;
          session_count?: number;
          pct?: number;
        };
        return {
          source: rawSource.source,
          key: rawSource.key || rawSource.source,
          session_count: Number(rawSource.session_count ?? rawSource.count ?? 0),
          pct: Number(rawSource.pct ?? 0),
        };
      });
    }

    if (captureSources.length === 0 || captureSources.every((source) => source.session_count === 0)) {
      const buckets: CaptureSourceCard[] = [
        { source: 'Watcher Gate (Live Hooks)', key: 'watcher_gate', session_count: 0, pct: 0 },
        { source: 'Transcript Ingest (CLI/JSONL)', key: 'transcript_ingest', session_count: 0, pct: 0 },
        { source: 'Inspect AI / Eval Studio', key: 'inspect_ai', session_count: 0, pct: 0 },
      ];
      const byKey = new Map(buckets.map((bucket) => [bucket.key, bucket]));
      for (const session of scopedSessions) {
        const bucket = captureSourceForAgent(session.agent_type || session.agent_source);
        const target = byKey.get(bucket.key);
        if (target) target.session_count += 1;
      }
      captureSources = buckets;
    }

    const captureTotal = captureSources.reduce((sum, source) => sum + source.session_count, 0);
    captureSources = captureSources.map((source) => ({
      ...source,
      pct:
        captureTotal > 0
          ? Math.round((source.session_count / captureTotal) * 1000) / 10
          : 0,
    }));

    // Top threats: canonicalize granular rules, then reconcile to total blocked.
    type ThreatAccumulator = { title: string; count: number; scoreSum: number };
    const threatAccumulator = new Map<string, ThreatAccumulator>();
    const addThreat = (text: string, count: number, avgScore: number) => {
      const category = canonicalThreatCategory(text);
      const current = threatAccumulator.get(category.id) || {
        title: category.title,
        count: 0,
        scoreSum: 0,
      };
      current.count += count;
      current.scoreSum += avgScore * count;
      threatAccumulator.set(category.id, current);
    };

    const localBlockedTotal = blockedReviewRows.length;
    const backendThreats = analyzerSummary?.top_threats || [];
    if (!isDemo && !filterScoped && backendThreats.length > 0) {
      for (const threat of backendThreats) {
        addThreat(threat.threat, Number(threat.count || 0), Number(threat.avg_severity || 0));
      }
    } else {
      for (const review of blockedReviewRows) {
        const category = threatCategoryForReview(review.ruleName, review.ruleId, review.reason);
        addThreat(category.title, 1, review.score);
      }
    }

    const backendBlockedTotal = Number(analyzerSummary?.total_blocked || 0);
    const targetBlockedTotal = filterScoped
      ? localBlockedTotal
      : Math.max(localBlockedTotal, backendBlockedTotal);

    let accumulatedThreatTotal = Array.from(threatAccumulator.values()).reduce(
      (sum, threat) => sum + threat.count,
      0
    );

    if (
      accumulatedThreatTotal > 0 &&
      targetBlockedTotal > accumulatedThreatTotal
    ) {
      const remaining = targetBlockedTotal - accumulatedThreatTotal;
      const entries = Array.from(threatAccumulator.entries());
      const shares = entries.map(([id, value]) => {
        const exact = (remaining * value.count) / accumulatedThreatTotal;
        return { id, base: Math.floor(exact), fraction: exact - Math.floor(exact) };
      });
      let leftover = remaining - shares.reduce((sum, share) => sum + share.base, 0);
      shares.sort((a, b) => b.fraction - a.fraction);
      for (const share of shares) {
        if (leftover <= 0) break;
        share.base += 1;
        leftover -= 1;
      }
      for (const share of shares) {
        const current = threatAccumulator.get(share.id);
        if (current) current.count += share.base;
      }
      accumulatedThreatTotal = targetBlockedTotal;
    }

    if (threatAccumulator.size === 0 && targetBlockedTotal > 0) {
      addThreat('Privilege Escalation & Policy Bypass', targetBlockedTotal, 8);
      accumulatedThreatTotal = targetBlockedTotal;
    }

    const topThreats: ThreatCard[] = Array.from(threatAccumulator.values())
      .map((threat) => ({
        threat: threat.title,
        count: threat.count,
        avg_severity: threat.count > 0 ? Math.round((threat.scoreSum / threat.count) * 10) / 10 : 0,
      }))
      .sort((a, b) => b.count - a.count || b.avg_severity - a.avg_severity);

    // Cross-session clusters: concrete behavioral anomaly signatures & target overlap
    type LocalCluster = {
      title: string;
      patternId: string;
      signature: string;
      category: string;
      sessions: Set<string>;
      agents: Set<string>;
      hits: number;
      maxScore: number;
      sampleTool: string;
    };
    const localClusters = new Map<string, LocalCluster>();
    for (const review of blockedReviewRows) {
      const category = threatCategoryForReview(review.ruleName, review.ruleId, review.reason);
      const spec = classifySignature(review.ruleName, review.ruleId, review.reason, review.toolInput, category.id);
      const cluster = localClusters.get(spec.id) || {
        title: spec.title,
        patternId: spec.id,
        signature: spec.signature,
        category: spec.category,
        sessions: new Set<string>(),
        agents: new Set<string>(),
        hits: 0,
        maxScore: 0,
        sampleTool: '',
      };
      cluster.sessions.add(review.sessionId);
      if (review.agentType && review.agentType !== 'agent') {
        cluster.agents.add(review.agentType);
      }
      cluster.hits += 1;
      cluster.maxScore = Math.max(cluster.maxScore, review.score);
      if (!cluster.sampleTool) cluster.sampleTool = review.toolName;
      localClusters.set(spec.id, cluster);
    }

    let crossSessionPatterns: PatternCard[] = [];
    const backendPatterns = (analyzerSummary?.cross_session_patterns || []).filter(
      (pattern) => Number(pattern.affected_sessions ?? pattern.session_count ?? 0) >= 2
    );

    if (!isDemo && !filterScoped && backendPatterns.length > 0) {
      crossSessionPatterns = backendPatterns.map((pattern) => {
        const affected = Number(pattern.affected_sessions ?? pattern.session_count ?? 0);
        const score = Number(pattern.max_severity ?? pattern.score ?? 0) || 8;
        const patternId = String(pattern.pattern_id || pattern.id || pattern.pattern || 'sig_cluster');
        const fallbackSpec =
          SIGNATURE_SPECS[patternId] ||
          SIGNATURE_SPECS[pattern.category || ''] ||
          SIGNATURE_SPECS.cluster_priv_escalation;
        const title = String(pattern.title || pattern.threat_name || fallbackSpec.title);
        const signature = String(pattern.signature || pattern.target_pattern || fallbackSpec.signature);
        const agents = Array.isArray(pattern.agents) && pattern.agents.length > 0
          ? pattern.agents
          : [];

        return {
          id: patternId,
          pattern: patternId,
          title,
          threat_name: title,
          signature,
          target_pattern: signature,
          agents,
          category: String(pattern.category || fallbackSpec.category),
          affected_sessions: affected,
          total_occurrences: Number(pattern.total_occurrences ?? pattern.total_hits ?? 0),
          severity: String(pattern.severity || (score >= 8 ? 'critical' : 'high')),
          score,
          status: String(pattern.status || 'active_cluster'),
          sample_tool: pattern.sample_tool,
        };
      });
    } else {
      crossSessionPatterns = Array.from(localClusters.values())
        .filter((cluster) => cluster.sessions.size >= 2)
        .map((cluster) => ({
          id: cluster.patternId,
          pattern: cluster.patternId,
          title: cluster.title,
          threat_name: cluster.title,
          signature: cluster.signature,
          target_pattern: cluster.signature,
          agents: Array.from(cluster.agents).sort(),
          category: cluster.category,
          affected_sessions: cluster.sessions.size,
          total_occurrences: cluster.hits,
          severity: cluster.maxScore >= 8 ? 'critical' : 'high',
          score: cluster.maxScore || 8,
          status: 'active_cluster',
          sample_tool: cluster.sampleTool || 'Bash',
        }));
    }

    if (crossSessionPatterns.length === 0 && blockedSessionIds.size >= 2) {
      const sessionClusters = new Map<string, LocalCluster>();
      for (const session of scopedSessions) {
        const sessionId = session.session_id || session.id;
        if (!blockedSessionIds.has(sessionId)) continue;
        const category = canonicalThreatCategory(
          `${session.headline || ''} ${session.summary || ''} ${session.title || ''}`
        );
        const spec = classifySignature(
          session.headline || '',
          session.title || '',
          session.summary || '',
          '',
          category.id
        );
        const cluster = sessionClusters.get(spec.id) || {
          title: spec.title,
          patternId: spec.id,
          signature: spec.signature,
          category: spec.category,
          sessions: new Set<string>(),
          agents: new Set<string>(),
          hits: 0,
          maxScore: 8,
          sampleTool: 'Bash',
        };
        cluster.sessions.add(sessionId);
        if (session.agent_type) cluster.agents.add(session.agent_type);
        cluster.hits += 1;
        sessionClusters.set(spec.id, cluster);
      }
      crossSessionPatterns = Array.from(sessionClusters.values())
        .filter((cluster) => cluster.sessions.size >= 2)
        .map((cluster) => ({
          id: cluster.patternId,
          pattern: cluster.patternId,
          title: cluster.title,
          threat_name: cluster.title,
          signature: cluster.signature,
          target_pattern: cluster.signature,
          agents: Array.from(cluster.agents).sort(),
          category: cluster.category,
          affected_sessions: cluster.sessions.size,
          total_occurrences: cluster.hits,
          severity: 'critical',
          score: cluster.maxScore,
          status: 'active_cluster',
          sample_tool: cluster.sampleTool,
        }));
    }

    crossSessionPatterns.sort(
      (a, b) => b.affected_sessions - a.affected_sessions || b.total_occurrences - a.total_occurrences
    );

    return {
      captureSources,
      topThreats,
      crossSessionPatterns,
      totalBlocked: topThreats.reduce((sum, threat) => sum + threat.count, 0),
      totalSessions: captureTotal,
    };
  }, [analyzerSummary, allSessions, currentSessions, dateFilter]);

  // Real Calculated Metrics
  const observedCount = currentSessions.length;
  const prevObservedCount = prevSessions.length;
  const deltaObserved = observedCount - prevObservedCount;

  const criticalSessions = currentSessions.filter((s) => sessionIsCritical(s));
  const criticalCount = criticalSessions.length;
  const prevCriticalCount = prevSessions.filter((s) => sessionIsCritical(s)).length;
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
            (s.turns && s.turns.some((t) => t.is_blocked))
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
      const dayStart = new Date(cur.getFullYear(), cur.getMonth(), cur.getDate(), 0, 0, 0, 0).getTime();
      const dayEnd = new Date(cur.getFullYear(), cur.getMonth(), cur.getDate(), 23, 59, 59, 999).getTime();
      const dateLabel = cur.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

      const daySessions = currentSessions.filter((s) => {
        const t = parseTimestamp(sessionObservedAt(s));
        return t >= dayStart && t <= dayEnd;
      });

      const dayCritical = daySessions.filter(
        (s) => s.severity === 'critical' || s.is_blocked || (s.turns && s.turns.some((t) => t.is_blocked))
      );

      points.push({
        dateIso: dateLabel,
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
      <div className="bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm flex items-center justify-between shrink-0 flex-wrap gap-4">
        <div>
          <h1 className="text-base font-bold text-slate-900 tracking-tight flex items-center gap-2.5">
            Overview
          </h1>
          <p className="text-xs text-slate-500 font-medium mt-0.5">{dateRangeSubtitle}</p>
        </div>

        {/* Date Selector Tabs */}
        <div className="h-9 bg-slate-100 p-1 rounded-xl border border-slate-200/70 inline-flex items-center gap-1 overflow-x-auto overflow-y-hidden no-scrollbar shrink-0">
          {([
            { id: 'today', label: 'Today' },
            { id: 'this_week', label: 'This week' },
            { id: 'last_week', label: 'Last week' },
            { id: 'this_month', label: 'This month' },
            { id: 'last_month', label: 'Last month' },
          ] as const).map((tab) => (
            <button
              key={tab.id}
              onClick={() => setDateFilter(tab.id)}
              className={`h-7 px-3 rounded-lg text-xs font-semibold inline-flex items-center justify-center transition-all cursor-pointer whitespace-nowrap ${
                dateFilter === tab.id
                  ? 'bg-white text-slate-900 shadow-xs font-bold'
                  : 'text-slate-500 hover:text-slate-900'
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
              <Activity className="w-4 h-4" />
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
              <AlertCircle className="w-4 h-4" />
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
              <AlertCircle className="w-4 h-4" />
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

      {/* Fleet Intelligence & Cross-Session Patterns */}
      <div className="bg-white p-6 rounded-2xl border border-border-subtle shadow-sm space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
          <div>
            <div className="flex items-center gap-2">
              <Layers className="w-5 h-5 text-indigo-600" />
              <h3 className="text-base font-bold text-gray-900">Fleet Intelligence & Cross-Session Patterns</h3>
            </div>
            <p className="text-xs text-gray-500 mt-1">
              Cross-session risk correlation, threat categorisation, and agent capture telemetry across Watcher Gate, Transcript Ingest, and Inspect AI.
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span
              className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-mono font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 shadow-2xs"
              title="p50 reflects fast Tier 1/triage decisions; p95 reflects Tier 1.5 / deep-review calls."
            >
              <Zap className="w-3.5 h-3.5 text-emerald-600" />
              <span>Gate Latency:</span>
              {analyzerSummary ? (
                <>
                  <span>p50 {analyzerSummary.p50_latency_ms}ms</span>
                  <span>·</span>
                  <span>p95 {analyzerSummary.p95_latency_ms}ms</span>
                </>
              ) : (
                <span>Checking latency…</span>
              )}
            </span>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {/* Col 1: Cross-Session Anomaly Clusters */}
          <div className="space-y-3 bg-gray-50/60 p-4 rounded-xl border border-gray-100">
            <div className="flex items-center justify-between gap-2 min-w-0">
              <div className="flex items-center gap-1.5 text-xs font-bold text-gray-800 uppercase tracking-wider min-w-0">
                <ShieldAlert className="w-4 h-4 text-rose-600 shrink-0" />
                <span className="truncate">Cross-Session Anomaly Clusters</span>
              </div>
              <span className="text-[11px] font-mono text-gray-500 font-semibold shrink-0 whitespace-nowrap">
                {fleetIntelligence.crossSessionPatterns.length} active
              </span>
            </div>

            {fleetIntelligence.crossSessionPatterns.length > 0 ? (
              <div className="space-y-2.5">
                {fleetIntelligence.crossSessionPatterns.slice(0, 4).map((csp) => {
                  const agentCount = csp.agents?.length || 0;
                  const agentLabel = `${agentCount} ${agentCount === 1 ? 'agent' : 'agents'}`;
                  const sessionLabel = `${csp.affected_sessions} ${csp.affected_sessions === 1 ? 'session' : 'sessions'}`;
                  const agentsDisplay = csp.agents && csp.agents.length > 0 ? csp.agents.join(' · ') : 'fleet-wide';

                  return (
                    <div key={csp.id} className="p-3 bg-white rounded-lg border border-gray-200/80 shadow-2xs space-y-1.5">
                      <div className="flex items-center justify-between gap-2 min-w-0">
                        <span className="text-xs font-bold text-gray-900 truncate min-w-0" title={csp.title || csp.threat_name}>
                          {csp.title || csp.threat_name}
                        </span>
                        <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-200/80 whitespace-nowrap shrink-0">
                          {sessionLabel} · {agentLabel}
                        </span>
                      </div>
                      <div className="flex items-center justify-between gap-2 text-[11px] min-w-0">
                        <span className="font-mono text-gray-500 text-[11px] truncate min-w-0" title={csp.signature || csp.target_pattern}>
                          {csp.signature || csp.target_pattern || '—'}
                        </span>
                        <span className="font-mono text-slate-600 text-[10px] shrink-0 text-right">
                          {agentsDisplay}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="p-4 text-center text-xs text-gray-400 bg-white rounded-lg border border-dashed border-gray-200">
                No multi-session anomaly clusters detected in active window.
              </div>
            )}
          </div>

          {/* Col 2: Top Blocked Threat Categories */}
          <div className="space-y-3 bg-gray-50/60 p-4 rounded-xl border border-gray-100">
            <div className="flex items-center justify-between gap-2 min-w-0">
              <div className="flex items-center gap-1.5 text-xs font-bold text-gray-800 uppercase tracking-wider min-w-0">
                <ShieldCheck className="w-4 h-4 text-indigo-600 shrink-0" />
                <span className="truncate">Top Blocked Threat Categories</span>
              </div>
              <span className="text-[11px] font-mono text-gray-500 font-semibold shrink-0 whitespace-nowrap">
                {fleetIntelligence.totalBlocked} blocked
              </span>
            </div>

            {fleetIntelligence.topThreats.length > 0 ? (
              <div className="space-y-2.5">
                {fleetIntelligence.topThreats.slice(0, 4).map((th, idx) => (
                  <div key={idx} className="p-3 bg-white rounded-lg border border-gray-200/80 shadow-2xs space-y-1.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-semibold text-gray-900 truncate">
                        {th.threat.replace(/_/g, ' ')}
                      </span>
                      <span className="font-mono font-bold text-gray-700 text-[11px]">
                        {th.count}
                      </span>
                    </div>
                    <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
                      <div
                        className="bg-indigo-600 h-full rounded-full transition-all"
                        style={{
                          width: `${Math.min(100, Math.round((th.count / Math.max(1, fleetIntelligence.totalBlocked || 1)) * 100))}%`,
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-4 text-center text-xs text-gray-400 bg-white rounded-lg border border-dashed border-gray-200">
                Zero threat interceptions in this reporting window.
              </div>
            )}
          </div>

          {/* Col 3: Agent Fleet & Capture Share */}
          <div className="space-y-3 bg-gray-50/60 p-4 rounded-xl border border-gray-100">
            <div className="flex items-center justify-between gap-2 min-w-0">
              <div className="flex items-center gap-1.5 text-xs font-bold text-gray-800 uppercase tracking-wider min-w-0">
                <Cpu className="w-4 h-4 text-teal-600 shrink-0" />
                <span className="truncate">Agent Fleet &amp; Capture Sources</span>
              </div>
              <span className="text-[11px] font-mono text-gray-500 font-semibold shrink-0 whitespace-nowrap">
                {fleetIntelligence.totalSessions} sessions
              </span>
            </div>

            <div className="space-y-2.5">
              {fleetIntelligence.captureSources.map((src) => (
                <div key={src.key} className="p-3 bg-white rounded-lg border border-gray-200/80 shadow-2xs space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-semibold text-gray-900">{src.source}</span>
                    <span className="font-mono text-gray-600 text-[11px]">
                      {src.session_count} ({src.pct}%)
                    </span>
                  </div>
                  <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${
                        src.key === 'watcher_gate'
                          ? 'bg-teal-600'
                          : src.key === 'transcript_ingest'
                          ? 'bg-amber-600'
                          : 'bg-indigo-600'
                      }`}
                      style={{ width: `${Math.min(100, src.pct)}%` }}
                    />
                  </div>
                </div>
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
                    agent_source: toAgentSource(s.agent_source),
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
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-lg text-xs font-semibold bg-rose-50 text-rose-600 border border-rose-200">
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
                <ShieldCheck className="w-4 h-4" />
              </div>
              <p className="text-xs font-bold text-gray-800">
                {observedCount === 0 ? 'No Monitored Sessions Recorded Yet' : 'No Critical Sessions Recorded'}
              </p>
              <p className="text-[11px] text-gray-500 max-w-md mx-auto">
                {observedCount === 0
                  ? 'Connect your coding agents (Antigravity, Claude Code, Cursor) via the Watcher hook to observe live tool execution, enforce safety policies, and record agent transcripts.'
                  : `All ${observedCount} monitored sessions in this period executed without policy violations or blocked commands.`}
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
                          agent_source: toAgentSource(s.agent_source),
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
                            <Clock className="w-3 h-3" />
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
