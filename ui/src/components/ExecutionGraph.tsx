import React, { useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  checkmarkCircle,
  closeCircle,
  codeSlashOutline,
  cubeOutline,
  hourglassOutline,
  shieldCheckmarkOutline,
  sparklesOutline,
  terminalOutline,
} from 'ionicons/icons';
import { RunRecord, TaskSummary } from '../types';

interface ExecutionGraphProps {
  tasks?: TaskSummary[];
  runs?: RunRecord[];
  onSelectRun?: (runId: string) => void;
  onNavigateToTrace?: (runId: string) => void;
}

export const ExecutionGraph: React.FC<ExecutionGraphProps> = ({
  tasks = [],
  runs = [],
  onNavigateToTrace,
}) => {
  const [selectedTaskId, setSelectedTaskId] = useState<string>(tasks[0]?.task_id || '');

  const selectedTask = tasks.find((t) => t.task_id === selectedTaskId) || tasks[0] || null;
  const matchingRuns = runs.filter((r) => r.task_id === (selectedTask?.task_id || ''));
  const latestRun = matchingRuns.length > 0 ? matchingRuns[0] : null;

  const planVerdict = latestRun?.audit_verdicts?.find((v) => v.metric_name === 'plan_adherence');
  const halluVerdict = latestRun?.audit_verdicts?.find((v) => v.metric_name === 'hallucination_detection');
  const rewardVerdict = latestRun?.audit_verdicts?.find((v) => v.metric_name === 'reward_tampering');

  return (
    <div className="w-full space-y-4 animate-fadeIn font-sans">
      {/* Header */}
      <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-bold text-text-primary">
            Execution & Verification Topology
          </h2>
          <p className="text-xs text-text-secondary mt-0.5 font-mono">
            End-to-end dependency pipeline from sandbox to LLM judges
          </p>
        </div>

        <select
          value={selectedTask?.task_id || ''}
          onChange={(e) => setSelectedTaskId(e.target.value)}
          className="bg-canvas border border-border-subtle rounded-xl px-3 py-2 text-xs text-text-primary font-medium focus:outline-none focus:border-brand-primary"
        >
          {tasks.map((t) => (
            <option key={t.task_id} value={t.task_id}>
              {t.task_id} ({t.difficulty || 'medium'})
            </option>
          ))}
        </select>
      </div>

      {/* 3-Tier Linear Flow Canvas */}
      <div className="bg-white p-6 rounded-2xl border border-border-subtle shadow-sm">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
          {/* Tier 1: Target Task */}
          <div className="space-y-3">
            <div className="text-[10px] font-mono font-bold tracking-wider text-text-muted uppercase">
              1. Evaluation Target
            </div>

            <div className="p-4 rounded-xl bg-canvas border border-border-subtle space-y-2.5">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-white border border-border-subtle flex items-center justify-center text-brand-purple">
                  <IonIcon icon={codeSlashOutline} className="text-base" />
                </div>
                <div>
                  <h3 className="text-xs font-bold text-text-primary font-mono">{selectedTask?.task_id || 'No task selected'}</h3>
                  <span className="text-[10px] text-text-secondary capitalize">
                    {selectedTask?.category || 'General'} · {selectedTask?.difficulty || 'Medium'}
                  </span>
                </div>
              </div>

              <p className="text-xs text-text-secondary leading-relaxed bg-white p-3 rounded-lg border border-border-subtle">
                {selectedTask?.instruction_preview || 'Execute bash tool and resolve repository defect with held-out pytest verification.'}
              </p>

              <div className="flex items-center justify-between text-[11px] font-mono pt-1 text-text-secondary">
                <span>Model: {latestRun?.model || '—'}</span>
                <span className={`font-bold ${latestRun?.passed ? 'text-emerald-700' : latestRun?.passed === false ? 'text-rose-700' : 'text-text-muted'}`}>
                  Reward: {latestRun && latestRun.reward !== null ? `${latestRun.reward.toFixed(1)}/1.0` : '—'}
                </span>
              </div>
            </div>
          </div>

          {/* Tier 2: Execution & Verifier */}
          <div className="space-y-3">
            <div className="text-[10px] font-mono font-bold tracking-wider text-text-muted uppercase">
              2. Sandbox Execution & Trace
            </div>

            <div className="space-y-2.5">
              {/* Docker Sandbox */}
              <div className="p-3.5 rounded-xl bg-canvas border border-border-subtle flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <IonIcon icon={cubeOutline} className="text-brand-purple text-base" />
                  <div>
                    <h4 className="text-xs font-bold text-text-primary">Docker Sandbox</h4>
                    <p className="text-[10px] text-text-secondary font-mono">net:none · isolated container</p>
                  </div>
                </div>
                <span className="text-[10px] font-mono font-bold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded">
                  {latestRun ? 'Completed' : 'Ready'}
                </span>
              </div>

              {/* ReAct Loop */}
              <div
                onClick={() => latestRun && onNavigateToTrace?.(latestRun.run_id)}
                className={`p-3.5 rounded-xl bg-canvas border border-border-subtle flex items-center justify-between transition-colors ${
                  latestRun ? 'cursor-pointer hover:border-brand-purple' : 'opacity-75'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <IonIcon icon={terminalOutline} className="text-accent-orange text-base" />
                  <div>
                    <h4 className="text-xs font-bold text-text-primary">Solver Trajectory</h4>
                    <p className="text-[10px] text-text-secondary font-mono">
                      {latestRun ? `${latestRun.total_steps} turns · ${latestRun.total_duration_sec.toFixed(1)}s` : 'No execution trace'}
                    </p>
                  </div>
                </div>
                {latestRun && <span className="text-[10px] font-mono text-brand-purple font-medium">Inspect &rarr;</span>}
              </div>

              {/* Pytest Verifier */}
              <div
                onClick={() => latestRun && onNavigateToTrace?.(latestRun.run_id)}
                className={`p-3.5 rounded-xl bg-canvas border border-border-subtle flex items-center justify-between transition-colors ${
                  latestRun ? 'cursor-pointer hover:border-brand-purple' : 'opacity-75'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <IonIcon
                    icon={latestRun?.passed ? checkmarkCircle : latestRun?.passed === false ? closeCircle : hourglassOutline}
                    className={`text-base ${latestRun?.passed ? 'text-status-cleared' : latestRun?.passed === false ? 'text-risk-high' : 'text-text-muted'}`}
                  />
                  <div>
                    <h4 className="text-xs font-bold text-text-primary">Held-Out Verifier Trace</h4>
                    <p className="text-[10px] text-text-secondary font-mono">
                      {latestRun?.passed ? 'All assertions passed' : latestRun?.passed === false ? 'Assertion failed' : 'Awaiting execution'}
                    </p>
                  </div>
                </div>
                <span
                  className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded ${
                    latestRun?.passed
                      ? 'text-emerald-700 bg-emerald-50'
                      : latestRun?.passed === false
                      ? 'text-rose-700 bg-rose-50'
                      : 'text-text-muted bg-canvas'
                  }`}
                >
                  {latestRun?.passed ? 'PASS' : latestRun?.passed === false ? 'FAIL' : 'PENDING'}
                </span>
              </div>
            </div>
          </div>

          {/* Tier 3: LLM Judge Audits */}
          <div className="space-y-3">
            <div className="text-[10px] font-mono font-bold tracking-wider text-text-muted uppercase">
              3. AI Safety Judges
            </div>

            <div className="space-y-2.5">
              <div className="p-3.5 rounded-xl bg-canvas border border-border-subtle flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <IonIcon icon={sparklesOutline} className="text-brand-purple" />
                  <span className="text-xs font-bold text-text-primary">Plan Adherence</span>
                </div>
                <span className="text-xs font-mono font-bold text-text-primary">
                  {planVerdict ? `${Math.round(planVerdict.score * 100)}%` : '—'}
                </span>
              </div>

              <div className="p-3.5 rounded-xl bg-canvas border border-border-subtle flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <IonIcon icon={shieldCheckmarkOutline} className="text-status-cleared" />
                  <span className="text-xs font-bold text-text-primary">Hallucination Filter</span>
                </div>
                <span className="text-xs font-mono font-bold text-text-primary">
                  {halluVerdict ? `${Math.round(halluVerdict.score * 100)}%` : '—'}
                </span>
              </div>

              <div className="p-3.5 rounded-xl bg-canvas border border-border-subtle flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <IonIcon icon={shieldCheckmarkOutline} className="text-status-cleared" />
                  <span className="text-xs font-bold text-text-primary">Reward Tampering</span>
                </div>
                <span className="text-xs font-mono font-bold text-text-primary">
                  {rewardVerdict ? `${Math.round(rewardVerdict.score * 100)}%` : '—'}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
