import React, { useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  checkmarkCircle,
  closeCircle,
  hourglassOutline,
  searchOutline,
  shieldCheckmarkOutline,
  sparklesOutline,
  timeOutline,
  cashOutline,
  arrowForwardOutline,
} from 'ionicons/icons';
import { RunRecord, TaskSummary } from '../types';

interface DashboardOverviewProps {
  runs: RunRecord[];
  tasks: TaskSummary[];
  onSelectRun?: (runId: string) => void;
  onNavigateToStudio?: () => void;
  onNavigateToTestCases?: () => void;
}

export const DashboardOverview: React.FC<DashboardOverviewProps> = ({
  runs,
  tasks,
  onSelectRun,
  onNavigateToStudio,
  onNavigateToTestCases,
}) => {
  const [searchText, setSearchText] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'passed' | 'failed' | 'safety_flagged'>('all');

  const totalRunsCount = runs.length;
  const passedRuns = runs.filter((r) => r.passed === true);
  const failedRuns = runs.filter((r) => r.passed === false);
  const passRate = totalRunsCount > 0 ? Math.round((passedRuns.length / totalRunsCount) * 100) : 0;

  const totalTokens = runs.reduce((acc, r) => acc + (r.total_tokens || 0), 0);
  const totalCost = runs.reduce((acc, r) => acc + (r.estimated_cost_usd || 0), 0);
  const avgDuration = totalRunsCount > 0
    ? (runs.reduce((acc, r) => acc + (r.total_duration_sec || 0), 0) / totalRunsCount).toFixed(1)
    : '0.0';

  // Dynamic Safety Verdict Aggregation
  const allVerdicts = runs.flatMap((r) => r.audit_verdicts || []);
  const planVerdicts = allVerdicts.filter((v) => v.metric_name === 'plan_adherence');
  const planScore = planVerdicts.length > 0
    ? Math.round((planVerdicts.reduce((acc, v) => acc + (v.score || 0), 0) / planVerdicts.length) * 100)
    : 0;

  const halluVerdicts = allVerdicts.filter((v) => v.metric_name === 'hallucination_detection');
  const halluScore = halluVerdicts.length > 0
    ? Math.round((halluVerdicts.reduce((acc, v) => acc + (v.score || 0), 0) / halluVerdicts.length) * 100)
    : 0;

  const rewardVerdicts = allVerdicts.filter((v) => v.metric_name === 'reward_tampering');
  const rewardScore = rewardVerdicts.length > 0
    ? Math.round((rewardVerdicts.reduce((acc, v) => acc + (v.score || 0), 0) / rewardVerdicts.length) * 100)
    : 0;

  const filteredRuns = runs.filter((run) => {
    if (searchText) {
      const q = searchText.toLowerCase();
      const matchId = run.task_id.toLowerCase().includes(q) || run.run_id.toLowerCase().includes(q);
      const matchModel = run.model.toLowerCase().includes(q);
      if (!matchId && !matchModel) return false;
    }
    if (statusFilter === 'passed' && run.passed !== true) return false;
    if (statusFilter === 'failed' && run.passed !== false) return false;
    if (statusFilter === 'safety_flagged') {
      const hasFailedAudit = run.audit_verdicts?.some((v) => !v.passed);
      if (!hasFailedAudit) return false;
    }
    return true;
  });

  return (
    <div className="w-full space-y-6 animate-fadeIn font-sans">
      {/* 1. Executive Metric Summary Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Pass Rate */}
        <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-1">
          <div className="flex items-center justify-between text-xs text-text-muted">
            <span className="font-medium">Accuracy / Pass Rate</span>
            <IonIcon icon={checkmarkCircle} className="text-status-cleared text-sm" />
          </div>
          <div className="text-2xl font-bold text-text-primary font-mono">{passRate}%</div>
          <div className="text-[11px] text-text-secondary font-mono">
            {passedRuns.length} of {totalRunsCount} cases verified
          </div>
        </div>

        {/* Total Runs */}
        <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-1">
          <div className="flex items-center justify-between text-xs text-text-muted">
            <span className="font-medium">Total Evaluations</span>
            <IonIcon icon={sparklesOutline} className="text-brand-purple text-sm" />
          </div>
          <div className="text-2xl font-bold text-text-primary font-mono">{totalRunsCount}</div>
          <div className="text-[11px] text-text-secondary font-mono">
            {failedRuns.length} failed · {totalRunsCount - passedRuns.length - failedRuns.length} running
          </div>
        </div>

        {/* Avg Latency */}
        <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-1">
          <div className="flex items-center justify-between text-xs text-text-muted">
            <span className="font-medium">Avg Turn Latency</span>
            <IonIcon icon={timeOutline} className="text-accent-orange text-sm" />
          </div>
          <div className="text-2xl font-bold text-text-primary font-mono">{avgDuration}s</div>
          <div className="text-[11px] text-text-secondary font-mono">
            Across autonomous ReAct loops
          </div>
        </div>

        {/* Total Spend */}
        <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-1">
          <div className="flex items-center justify-between text-xs text-text-muted">
            <span className="font-medium">Est. Spend</span>
            <IonIcon icon={cashOutline} className="text-emerald-600 text-sm" />
          </div>
          <div className="text-2xl font-bold text-emerald-700 font-mono">
            ${totalCost.toFixed(4)}
          </div>
          <div className="text-[11px] text-text-secondary font-mono">
            {totalTokens.toLocaleString()} tokens consumed
          </div>
        </div>
      </div>

      {/* 2. Benchmark & Safety Alignment Split */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left 7 Cols: Safety & Alignment Audits */}
        <div className="lg:col-span-7 bg-white p-6 rounded-2xl border border-border-subtle shadow-sm space-y-5">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-bold text-text-primary">Safety & Alignment Verifications</h3>
              <p className="text-xs text-text-secondary mt-0.5">Automated LLM-as-a-judge criteria and held-out verifiers</p>
            </div>
            <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-surface-subtle text-brand-purple font-bold">
              Autonomous Safety
            </span>
          </div>

          <div className="space-y-4">
            {/* Metric 1 */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-text-primary">Plan Adherence</span>
                <span className="font-mono font-bold text-text-primary">{planScore}%</span>
              </div>
              <div className="h-2 w-full bg-canvas rounded-full overflow-hidden border border-border-subtle/50">
                <div className="h-full bg-[#6B46C1] rounded-full transition-all duration-500" style={{ width: `${planScore}%` }} />
              </div>
            </div>

            {/* Metric 2 */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-text-primary">Hallucination & Error Defense</span>
                <span className="font-mono font-bold text-text-primary">{halluScore}%</span>
              </div>
              <div className="h-2 w-full bg-canvas rounded-full overflow-hidden border border-border-subtle/50">
                <div className="h-full bg-status-cleared rounded-full transition-all duration-500" style={{ width: `${halluScore}%` }} />
              </div>
            </div>

            {/* Metric 3 */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-text-primary">Reward Tampering Defense</span>
                <span className="font-mono font-bold text-text-primary">{rewardScore}%</span>
              </div>
              <div className="h-2 w-full bg-canvas rounded-full overflow-hidden border border-border-subtle/50">
                <div className="h-full bg-status-cleared rounded-full transition-all duration-500" style={{ width: `${rewardScore}%` }} />
              </div>
            </div>
          </div>
        </div>

        {/* Right 5 Cols: Quick Run CTA Card */}
        <div className="lg:col-span-5 bg-white p-6 rounded-2xl border border-border-subtle shadow-sm flex flex-col justify-between space-y-4">
          <div className="space-y-2">
            <div className="w-8 h-8 rounded-lg bg-surface-subtle text-brand-purple flex items-center justify-center">
              <IonIcon icon={shieldCheckmarkOutline} className="text-base" />
            </div>
            <h3 className="text-sm font-bold text-text-primary">Ready to Launch Evaluations</h3>
            <p className="text-xs text-text-secondary leading-relaxed">
              Execute agent benchmarks in isolated Docker sandboxes with automated pytest verifier assertions and LLM judge audits.
            </p>
          </div>

          <div className="space-y-2 pt-2 border-t border-border-subtle">
            <button
              type="button"
              onClick={onNavigateToStudio}
              className="w-full py-2.5 px-4 rounded-xl bg-dark-base text-white text-xs font-bold flex items-center justify-center gap-2 hover:bg-black transition-colors"
            >
              <span>Open Live Studio</span>
              <IonIcon icon={arrowForwardOutline} className="text-xs" />
            </button>
            <button
              type="button"
              onClick={onNavigateToTestCases}
              className="w-full py-2 px-4 rounded-xl bg-canvas border border-border-subtle text-text-secondary text-xs font-medium hover:text-text-primary hover:bg-surface-subtle transition-colors text-center"
            >
              View Test Cases ({tasks.length || 5})
            </button>
          </div>
        </div>
      </div>

      {/* 3. Recent Test Runs Table */}
      <div className="bg-white rounded-2xl border border-border-subtle shadow-sm overflow-hidden space-y-0">
        <div className="p-4 border-b border-border-subtle flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-text-primary">
              Recent Benchmark Evaluations
            </h3>
            <span className="text-[11px] text-text-muted font-mono">{filteredRuns.length} of {runs.length} runs shown</span>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            {/* Filter pills */}
            <div className="flex items-center gap-1 bg-canvas p-0.5 rounded-lg border border-border-subtle text-[11px]">
              {(['all', 'passed', 'failed', 'safety_flagged'] as const).map((filter) => (
                <button
                  key={filter}
                  type="button"
                  onClick={() => setStatusFilter(filter)}
                  className={`px-2 py-1 rounded font-medium capitalize transition-all ${
                    statusFilter === filter
                      ? 'bg-white text-brand-primary font-bold shadow-sm'
                      : 'text-text-secondary hover:text-text-primary'
                  }`}
                >
                  {filter.replace('_', ' ')}
                </button>
              ))}
            </div>

            {/* Quick search */}
            <div className="relative">
              <input
                type="text"
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                placeholder="Search runs..."
                className="bg-canvas border border-border-subtle rounded-lg pl-6 pr-2.5 py-1 text-[11px] text-text-primary focus:outline-none focus:border-brand-primary"
              />
              <IonIcon icon={searchOutline} className="absolute left-1.5 top-1/2 -translate-y-1/2 text-text-muted text-xs pointer-events-none" />
            </div>
          </div>
        </div>

        {filteredRuns.length === 0 ? (
          <div className="p-8 text-center text-text-muted text-xs">
            No evaluations match your search filter criteria.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-border-subtle text-[10px] uppercase font-bold tracking-wider text-text-muted bg-canvas/60">
                  <th className="py-2.5 px-4">Status</th>
                  <th className="py-2.5 px-4">Task ID</th>
                  <th className="py-2.5 px-4">Model</th>
                  <th className="py-2.5 px-4">Duration</th>
                  <th className="py-2.5 px-4">Score</th>
                  <th className="py-2.5 px-4 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle font-mono">
                {filteredRuns.slice(0, 8).map((run) => {
                  const isPassed = run.passed === true;
                  const isFailed = run.passed === false;

                  return (
                    <tr
                      key={run.run_id}
                      onClick={() => onSelectRun?.(run.run_id)}
                      className="hover:bg-canvas transition-colors cursor-pointer"
                    >
                      <td className="py-2.5 px-4 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          <IonIcon
                            icon={isPassed ? checkmarkCircle : isFailed ? closeCircle : hourglassOutline}
                            className={`text-sm ${
                              isPassed ? 'text-status-cleared' : isFailed ? 'text-risk-high' : 'text-brand-purple'
                            }`}
                          />
                          <span
                            className={`text-[10px] uppercase px-2 py-0.5 rounded font-bold ${
                              isPassed
                                ? 'bg-emerald-50 text-emerald-700'
                                : isFailed
                                ? 'bg-rose-50 text-rose-700'
                                : 'bg-purple-50 text-brand-purple'
                            }`}
                          >
                            {isPassed ? 'Passed' : isFailed ? 'Failed' : run.status}
                          </span>
                        </div>
                      </td>

                      <td className="py-2.5 px-4 text-text-primary font-sans font-medium">
                        {run.task_id}
                      </td>

                      <td className="py-2.5 px-4 text-text-secondary font-sans text-xs">
                        {run.model}
                      </td>

                      <td className="py-2.5 px-4 text-text-secondary">
                        {run.total_duration_sec.toFixed(1)}s
                      </td>

                      <td className="py-2.5 px-4 font-bold">
                        <span className={isPassed ? 'text-emerald-700' : isFailed ? 'text-rose-700' : 'text-text-primary'}>
                          {run.reward !== null ? `${run.reward.toFixed(1)}/1.0` : '—'}
                        </span>
                      </td>

                      <td className="py-2.5 px-4 text-right">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onSelectRun?.(run.run_id);
                          }}
                          className="text-[11px] text-brand-purple hover:underline font-sans font-medium"
                        >
                          Inspect Trace &rarr;
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

