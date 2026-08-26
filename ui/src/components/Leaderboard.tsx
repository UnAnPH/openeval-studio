import React from 'react';
import { Award, CheckCircle2, ChevronRight, Clock, DollarSign, Sparkles, XCircle } from 'lucide-react';
import { RunRecord } from '../types';

interface LeaderboardProps {
  runs: RunRecord[];
  onSelectRun: (runId: string) => void;
  activeRunId?: string;
}

export const Leaderboard: React.FC<LeaderboardProps> = ({
  runs,
  onSelectRun,
  activeRunId,
}) => {
  return (
    <div className="p-5 rounded-2xl bg-surface border border-border space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Award className="w-4 h-4 text-primary" />
          <h3 className="text-sm font-bold text-white tracking-tight">Benchmark Runs Leaderboard</h3>
        </div>
        <span className="text-xs text-slate-400">{runs.length} Runs Recorded</span>
      </div>

      {runs.length === 0 ? (
        <div className="text-center py-8 text-xs text-slate-500">
          No historical evaluation runs recorded yet.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-border/80 text-slate-400 font-mono text-[11px] uppercase">
                <th className="py-2.5 px-3">Status</th>
                <th className="py-2.5 px-3">Task ID</th>
                <th className="py-2.5 px-3">Model</th>
                <th className="py-2.5 px-3">Reward</th>
                <th className="py-2.5 px-3">Tokens</th>
                <th className="py-2.5 px-3">Cost ($)</th>
                <th className="py-2.5 px-3">Duration</th>
                <th className="py-2.5 px-3">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40 font-mono">
              {runs.map((run) => {
                const isSelected = run.run_id === activeRunId;
                return (
                  <tr
                    key={run.run_id}
                    onClick={() => onSelectRun(run.run_id)}
                    className={`hover:bg-surface-elevated/80 transition-colors cursor-pointer ${
                      isSelected ? 'bg-sky-500/10' : ''
                    }`}
                  >
                    <td className="py-3 px-3">
                      {run.passed === true ? (
                        <span className="flex items-center gap-1 text-emerald-400 font-bold">
                          <CheckCircle2 className="w-3.5 h-3.5" /> PASS
                        </span>
                      ) : run.passed === false ? (
                        <span className="flex items-center gap-1 text-rose-400 font-bold">
                          <XCircle className="w-3.5 h-3.5" /> FAIL
                        </span>
                      ) : (
                        <span className="text-sky-400 animate-pulse font-bold">{run.status.toUpperCase()}</span>
                      )}
                    </td>
                    <td className="py-3 px-3 font-sans font-medium text-slate-200">{run.task_id}</td>
                    <td className="py-3 px-3 text-slate-300">{run.model}</td>
                    <td className="py-3 px-3 font-bold text-slate-100">
                      {run.reward !== null ? `${run.reward.toFixed(1)} / 1.0` : '—'}
                    </td>
                    <td className="py-3 px-3 text-slate-400">
                      <span className="flex items-center gap-1">
                        <Sparkles className="w-3 h-3 text-accent" />
                        {run.total_tokens.toLocaleString()}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-emerald-400 font-semibold">
                      <span className="flex items-center gap-0.5">
                        <DollarSign className="w-3 h-3" />
                        {run.estimated_cost_usd.toFixed(4)}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-slate-400">
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3 text-slate-500" />
                        {run.total_duration_sec}s
                      </span>
                    </td>
                    <td className="py-3 px-3 text-right">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectRun(run.run_id);
                        }}
                        className="p-1 rounded hover:bg-slate-700 text-slate-400 hover:text-primary transition-colors"
                      >
                        <ChevronRight className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
