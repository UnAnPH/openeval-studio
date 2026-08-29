import React, { useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  checkmarkCircle,
  closeCircle,
  hourglassOutline,
  trophyOutline,
  searchOutline,
} from 'ionicons/icons';
import { RunRecord } from '../types';

interface LeaderboardProps {
  runs: RunRecord[];
  activeRunId?: string | null;
  onSelectRun: (runId: string) => void;
}

export const Leaderboard: React.FC<LeaderboardProps> = ({
  runs,
  activeRunId,
  onSelectRun,
}) => {
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [modelFilter, setModelFilter] = useState<string>('all');

  // Unique models for filter dropdown
  const uniqueModels = Array.from(new Set(runs.map((r) => r.model).filter(Boolean)));

  const filteredRuns = runs.filter((run) => {
    if (searchTerm) {
      const q = searchTerm.toLowerCase();
      const matchTask = run.task_id.toLowerCase().includes(q);
      const matchModel = run.model.toLowerCase().includes(q);
      const matchRunId = run.run_id.toLowerCase().includes(q);
      if (!matchTask && !matchModel && !matchRunId) return false;
    }

    if (statusFilter === 'passed' && run.passed !== true) return false;
    if (statusFilter === 'failed' && run.passed !== false) return false;
    if (statusFilter === 'running' && run.status !== 'running' && run.status !== 'pending') return false;

    if (modelFilter !== 'all' && run.model !== modelFilter) return false;

    return true;
  });

  return (
    <div className="bg-white rounded-2xl border border-border-subtle shadow-aegis-card overflow-hidden">
      <div className="p-4 border-b border-border-subtle space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-surface-subtle text-brand-purple flex items-center justify-center">
              <IonIcon icon={trophyOutline} className="text-base" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-text-primary tracking-tight">
                Evaluation History & Benchmark Leaderboard
              </h3>
              <p className="text-xs text-text-secondary">
                Comparative execution logs and benchmark accuracy scores
              </p>
            </div>
          </div>
          <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-canvas text-text-secondary border border-border-subtle self-start sm:self-auto">
            {filteredRuns.length} of {runs.length} Runs
          </span>
        </div>

        {/* Filter & Search Bar */}
        <div className="flex flex-col sm:flex-row items-center gap-3">
          <div className="relative flex-1 w-full">
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search by Task ID, Model, or Run ID..."
              className="w-full bg-canvas border border-border-subtle rounded-xl pl-8 pr-3 py-1.5 text-xs text-text-primary placeholder-text-muted focus:outline-none focus:border-brand-primary transition-all"
            />
            <IonIcon
              icon={searchOutline}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted text-xs pointer-events-none"
            />
          </div>

          <div className="flex items-center gap-2 w-full sm:w-auto">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="bg-canvas border border-border-subtle rounded-xl px-2.5 py-1.5 text-xs text-text-primary focus:outline-none focus:border-brand-primary cursor-pointer"
            >
              <option value="all">All Statuses</option>
              <option value="passed">Passed Only</option>
              <option value="failed">Failed Only</option>
              <option value="running">Running Only</option>
            </select>

            {uniqueModels.length > 1 && (
              <select
                value={modelFilter}
                onChange={(e) => setModelFilter(e.target.value)}
                className="bg-canvas border border-border-subtle rounded-xl px-2.5 py-1.5 text-xs text-text-primary focus:outline-none focus:border-brand-primary cursor-pointer max-w-[150px]"
              >
                <option value="all">All Models</option>
                {uniqueModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>
      </div>

      <div className="p-0">
        {filteredRuns.length === 0 ? (
          <div className="p-8 text-center text-text-muted text-xs">
            No historical runs matched your criteria.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-border-subtle text-[10px] uppercase font-bold tracking-wider text-text-secondary bg-[#F8F7FC]">
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Task ID</th>
                  <th className="py-3 px-4">Model</th>
                  <th className="py-3 px-4">Score</th>
                  <th className="py-3 px-4">Duration</th>
                  <th className="py-3 px-4">Tokens</th>
                  <th className="py-3 px-4">Cost ($)</th>
                  <th className="py-3 px-4 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle font-mono">
                {filteredRuns.map((run) => {
                  const isSelected = run.run_id === activeRunId;
                  const isPassed = run.passed === true;
                  const isFailed = run.passed === false;
                  const isRunning = run.status === 'running' || run.status === 'pending';

                  return (
                    <tr
                      key={run.run_id}
                      onClick={() => onSelectRun(run.run_id)}
                      className={`hover:bg-canvas transition-colors cursor-pointer ${
                        isSelected ? 'bg-surface-subtle' : ''
                      }`}
                    >
                      {/* Status */}
                      <td className="py-3 px-4 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          <IonIcon
                            icon={isPassed ? checkmarkCircle : isFailed ? closeCircle : hourglassOutline}
                            className={`text-sm ${
                              isPassed ? 'text-emerald-600' : isFailed ? 'text-rose-600' : 'text-brand-purple'
                            }`}
                          />
                          <span
                            className={`text-[10px] uppercase px-2 py-0.5 rounded-full font-bold border ${
                              isPassed
                                ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                : isFailed
                                ? 'bg-rose-50 text-rose-700 border-rose-200'
                                : isRunning
                                ? 'bg-purple-50 text-brand-purple border-purple-200 animate-pulse'
                                : 'bg-canvas text-text-secondary border-border-subtle'
                            }`}
                          >
                            {isPassed ? 'Passed' : isFailed ? 'Failed' : run.status}
                          </span>
                        </div>
                      </td>

                      {/* Task ID */}
                      <td className="py-3 px-4 text-text-primary font-sans font-bold">
                        {run.task_id}
                      </td>

                      {/* Model */}
                      <td className="py-3 px-4 text-text-secondary font-sans">
                        <span className="truncate max-w-[140px] block">{run.model}</span>
                      </td>

                      {/* Score / Reward */}
                      <td className="py-3 px-4">
                        <span
                          className={`font-bold ${
                            isPassed ? 'text-emerald-700' : isFailed ? 'text-rose-700' : 'text-text-primary'
                          }`}
                        >
                          {run.reward !== null ? `${run.reward.toFixed(1)}/1.0` : '—'}
                        </span>
                      </td>

                      {/* Duration */}
                      <td className="py-3 px-4 text-text-secondary whitespace-nowrap">
                        {run.total_duration_sec.toFixed(1)}s
                      </td>

                      {/* Tokens */}
                      <td className="py-3 px-4 text-text-secondary whitespace-nowrap">
                        {run.total_tokens.toLocaleString()}
                      </td>

                      {/* Cost */}
                      <td className="py-3 px-4 text-emerald-700 font-semibold whitespace-nowrap">
                        ${run.estimated_cost_usd.toFixed(4)}
                      </td>

                      {/* Action */}
                      <td className="py-3 px-4 text-right whitespace-nowrap">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onSelectRun(run.run_id);
                          }}
                          className={`text-[10px] px-2.5 py-1 rounded-full font-medium transition-all ${
                            isSelected
                              ? 'bg-dark-base text-white font-bold'
                              : 'bg-canvas text-text-secondary border border-border-subtle hover:text-text-primary'
                          }`}
                        >
                          {isSelected ? 'Viewing' : 'Inspect'}
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
    </div>
  );
};


