import { Icon } from '../lib/icon';
import {
  BarChart2,
  Box,
  ExternalLink,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
} from 'lucide-react';
import React, { useCallback, useEffect, useState } from 'react';

interface InspectViewerProps {
  inspectPort?: number;
}

interface LeaderboardRow {
  model: string;
  total_runs: number;
  passed_runs: number;
  pass_rate: number;
  avg_tokens: number | null;
  avg_duration_sec: number | null;
}

interface ParetoPoint {
  model: string;
  accuracy: number;
  avg_tokens: number;
}

const formatNumber = (value: number | null | undefined): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return Math.round(value).toLocaleString();
};

const formatRate = (value: number | null | undefined): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(1)}%`;
};

export const InspectViewer: React.FC<InspectViewerProps> = ({ inspectPort = 7575 }) => {
  const inspectUrl = `http://127.0.0.1:${inspectPort}/`;

  const [leaderboard, setLeaderboard] = useState<LeaderboardRow[]>([]);
  const [paretoFrontier, setParetoFrontier] = useState<ParetoPoint[]>([]);
  const [totalEvalsSynced, setTotalEvalsSynced] = useState<number>(0);
  const [analyticsLoading, setAnalyticsLoading] = useState<boolean>(true);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);

  const fetchAnalytics = useCallback(async () => {
    setAnalyticsLoading(true);
    setAnalyticsError(null);
    try {
      const res = await fetch('/api/eval/analytics/summary');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setLeaderboard(Array.isArray(data?.leaderboard) ? data.leaderboard : []);
      setParetoFrontier(Array.isArray(data?.pareto_frontier) ? data.pareto_frontier : []);
      setTotalEvalsSynced(typeof data?.total_evals_synced === 'number' ? data.total_evals_synced : 0);
    } catch (e) {
      console.warn('Failed to load DuckDB .eval analytics:', e);
      setAnalyticsError('Unable to load DuckDB .eval analytics from the server.');
      setLeaderboard([]);
      setParetoFrontier([]);
      setTotalEvalsSynced(0);
    } finally {
      setAnalyticsLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAnalytics();
  }, [fetchAnalytics]);

  const handleOpenInspectWindow = () => {
    window.open(inspectUrl, '_blank', 'noopener,noreferrer');
  };

  return (
    <div className="m-0 p-0 flex flex-col h-full bg-surface-elevated/40 rounded-2xl border border-border overflow-hidden">
      {/* Header */}
      <div className="flex flex-row items-center justify-between p-4 sm:p-5 bg-surface border-b border-border shrink-0">
        <div className="flex items-center gap-2.5">
          <Icon icon={ShieldCheck} className="text-indigo-400 text-lg" />
          <div>
            <div className="text-base font-bold text-white tracking-tight flex items-center gap-2.5">
              UK AISI Inspect AI Visualizer
              <span className="inline-flex items-center px-2 py-0.5 rounded-lg text-[10px] uppercase font-bold tracking-wider bg-indigo-500/10 border border-indigo-500/30 text-indigo-300">
                Official Log Viewer
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2.5">
          <div className="h-9 inline-flex items-center px-3 rounded-xl text-xs bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 font-mono">
            <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 mr-1.5 animate-pulse" />
            Port {inspectPort}
          </div>
          <button
            type="button"
            onClick={handleOpenInspectWindow}
            className="h-9 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-xs inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer"
          >
            <Icon icon={ExternalLink} className="text-xs" />
            <span>Open Visualizer</span>
          </button>
        </div>
      </div>

      {/* Main Hub Content */}
      <div className="flex-1 overflow-y-auto p-6 py-8 flex flex-col items-center text-center space-y-6">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-indigo-600 to-sky-500 flex items-center justify-center shadow-xl shadow-indigo-500/20">
          <Icon icon={ShieldCheck} className="text-3xl text-white" />
        </div>

        <div className="max-w-md space-y-2">
          <h3 className="text-lg font-bold text-white tracking-tight">
            Official UK AI Safety Institute Visualizer
          </h3>
          <p className="text-xs text-slate-300 leading-relaxed">
            Inspect View runs as a dedicated server displaying sample message trees, solver actions,
            model thoughts, token costs, and verifier scorecards from your <code className="text-indigo-300">logs/*.eval</code> archives.
          </p>
        </div>

        {/* Feature Highlights Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 w-full max-w-xl text-left">
          <div className="p-3.5 rounded-xl bg-surface border border-border space-y-1">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-indigo-300">
              <Icon icon={Box} className="text-sm" />
              <span>Full Trajectory Replay</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Step through prompt turns, Docker tool calls, and observations with complete token metadata.
            </p>
          </div>

          <div className="p-3.5 rounded-xl bg-surface border border-border space-y-1">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-sky-300">
              <Icon icon={BarChart2} className="text-sm" />
              <span>Score & Metric Cards</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Inspect held-out pytest verifier outcomes, accuracy distributions, and cost accounting.
            </p>
          </div>

          <div className="p-3.5 rounded-xl bg-surface border border-border space-y-1">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-emerald-300">
              <Icon icon={Sparkles} className="text-sm" />
              <span>Direct Log Links</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Open and share deep-linked evaluation reports directly at <span className="font-mono text-emerald-400">/tasks/</span>.
            </p>
          </div>
        </div>

        {/* Primary Action Button */}
        <div className="pt-2 flex flex-col items-center gap-2">
          <button
            type="button"
            onClick={handleOpenInspectWindow}
            className="h-9 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-xs inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer"
          >
            <Icon icon={ExternalLink} className="text-xs" />
            <span>Launch Inspect Visualizer ({inspectUrl})</span>
          </button>
          <span className="text-[11px] text-slate-500 font-mono">
            CLI Command: uv run inspect view --port {inspectPort}
          </span>
        </div>

        {/* DuckDB .eval Analytics */}
        <div className="w-full max-w-3xl text-left space-y-4 pt-4 border-t border-border">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 flex-wrap">
              <Icon icon={TrendingUp} className="text-sky-300 text-sm" />
              <h4 className="text-sm font-bold text-white tracking-tight">DuckDB .eval Analytics</h4>
              <span className="inline-flex items-center px-2 py-0.5 rounded-lg text-[10px] uppercase font-bold tracking-wider bg-sky-500/10 border border-sky-500/30 text-sky-300">
                {totalEvalsSynced} model{totalEvalsSynced === 1 ? '' : 's'} synced
              </span>
            </div>
            <button
              type="button"
              onClick={() => void fetchAnalytics()}
              disabled={analyticsLoading}
              className="h-8 px-3 rounded-lg border border-border bg-surface hover:bg-surface-elevated disabled:opacity-60 disabled:cursor-not-allowed text-[11px] font-semibold text-slate-300 inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer"
            >
              <Icon icon={RefreshCw} className={`text-xs ${analyticsLoading ? 'animate-spin' : ''}`} />
              <span>Refresh</span>
            </button>
          </div>

          {/* Model Leaderboard */}
          <div className="rounded-xl bg-surface border border-border overflow-hidden">
            <div className="px-3.5 py-2.5 border-b border-border flex items-center gap-1.5">
              <Icon icon={BarChart2} className="text-sky-300 text-xs" />
              <span className="text-[11px] font-bold uppercase tracking-wider text-slate-300">
                Model Leaderboard
              </span>
            </div>

            {analyticsLoading ? (
              <div className="px-3.5 py-6 text-center text-[11px] text-slate-400">
                Loading DuckDB analytics...
              </div>
            ) : analyticsError ? (
              <div className="px-3.5 py-6 text-center text-[11px] text-rose-300">{analyticsError}</div>
            ) : leaderboard.length === 0 ? (
              <div className="px-3.5 py-6 text-center text-[11px] text-slate-400">
                No <code className="text-indigo-300">.eval</code> logs synced yet. Run an evaluation to
                populate the leaderboard.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-left text-slate-400 border-b border-border">
                      <th className="px-3.5 py-2 font-semibold">Model</th>
                      <th className="px-3 py-2 font-semibold text-right">Runs</th>
                      <th className="px-3 py-2 font-semibold text-right">Passed</th>
                      <th className="px-3 py-2 font-semibold text-right">Pass Rate</th>
                      <th className="px-3 py-2 font-semibold text-right">Avg Tokens</th>
                      <th className="px-3.5 py-2 font-semibold text-right">Avg Duration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leaderboard.map((row) => (
                      <tr key={row.model} className="border-b border-border/60 last:border-0">
                        <td className="px-3.5 py-2 font-mono text-slate-200">{row.model}</td>
                        <td className="px-3 py-2 text-right text-slate-300">{row.total_runs ?? 0}</td>
                        <td className="px-3 py-2 text-right text-slate-300">{row.passed_runs ?? 0}</td>
                        <td className="px-3 py-2 text-right font-mono text-emerald-300">
                          {formatRate(row.pass_rate)}
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-slate-300">
                          {formatNumber(row.avg_tokens)}
                        </td>
                        <td className="px-3.5 py-2 text-right font-mono text-slate-300">
                          {row.avg_duration_sec === null || row.avg_duration_sec === undefined
                            ? '—'
                            : `${Number(row.avg_duration_sec).toFixed(2)}s`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Cost-vs-Accuracy Pareto Frontier */}
          <div className="space-y-2">
            <div className="flex items-center gap-1.5">
              <Icon icon={TrendingUp} className="text-emerald-300 text-xs" />
              <span className="text-[11px] font-bold uppercase tracking-wider text-slate-300">
                Cost-vs-Accuracy Pareto Frontier
              </span>
            </div>
            {analyticsLoading ? (
              <div className="rounded-xl bg-surface border border-border px-3.5 py-4 text-center text-[11px] text-slate-400">
                Computing Pareto frontier...
              </div>
            ) : paretoFrontier.length === 0 ? (
              <div className="rounded-xl bg-surface border border-border px-3.5 py-4 text-center text-[11px] text-slate-400">
                No Pareto points yet. Synced <code className="text-indigo-300">.eval</code> logs will
                appear here.
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                {paretoFrontier.map((point) => (
                  <div
                    key={point.model}
                    className="p-3 rounded-xl bg-surface border border-border space-y-1.5"
                  >
                    <div className="text-[11px] font-mono font-semibold text-slate-200 truncate">
                      {point.model}
                    </div>
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 font-mono font-semibold">
                        {formatRate(point.accuracy)} accuracy
                      </span>
                      <span className="inline-flex items-center px-2 py-0.5 rounded-lg bg-sky-500/10 border border-sky-500/30 text-sky-300 font-mono font-semibold">
                        {formatNumber(point.avg_tokens)} tokens
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
