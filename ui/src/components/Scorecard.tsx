import React from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  checkmarkCircle,
  closeCircle,
  helpCircleOutline,
  hourglassOutline,
  sparklesOutline,
  cashOutline,
  timeOutline,
  gitCommitOutline,
  shieldCheckmarkOutline,
} from 'ionicons/icons';
import { RunRecord } from '../types';

interface ScorecardProps {
  run: RunRecord | null;
}

export const Scorecard: React.FC<ScorecardProps> = ({ run }) => {
  if (!run) {
    return (
      <div className="p-6 rounded-2xl bg-white border border-border-subtle text-center text-text-muted shadow-aegis-card">
        <IonIcon icon={helpCircleOutline} className="text-3xl mx-auto mb-2 text-text-muted" />
        <p className="text-sm font-bold text-text-primary">No Evaluation Scorecard Yet</p>
        <p className="text-xs text-text-secondary mt-1">
          Select a task and model, then launch an evaluation to inspect real-time verifier results & cost accounting.
        </p>
      </div>
    );
  }

  const isPassed = run.passed === true;
  const isFailed = run.passed === false;
  const isRunning = run.status === 'running' || run.status === 'pending';

  return (
    <div className="bg-white rounded-2xl border border-border-subtle shadow-aegis-card overflow-hidden">
      <div className="p-4 pb-3 border-b border-border-subtle flex items-center justify-between">
        <div className="flex items-center gap-2">
          <IonIcon icon={shieldCheckmarkOutline} className="text-brand-purple text-base" />
          <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-text-primary">
            Evaluation Scorecard
          </h3>
        </div>
        <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-surface-subtle text-brand-purple font-bold">
          {run.run_id.slice(0, 10)}...
        </span>
      </div>

      <div className="p-4 space-y-4">
        {/* Primary Reward Banner */}
        <div
          className={`p-4 rounded-xl border flex items-center justify-between transition-all ${
            isPassed
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-800'
              : isFailed
              ? 'bg-rose-500/10 border-rose-500/30 text-rose-800'
              : isRunning
              ? 'bg-purple-500/10 border-purple-500/30 text-brand-primary'
              : 'bg-canvas border-border-subtle text-text-secondary'
          }`}
        >
          <div className="flex items-center gap-3">
            <IonIcon
              icon={isPassed ? checkmarkCircle : isFailed ? closeCircle : hourglassOutline}
              className={`text-3xl ${
                isPassed ? 'text-emerald-600' : isFailed ? 'text-rose-600' : 'text-brand-purple'
              }`}
            />

            <div>
              <div className="font-bold text-sm tracking-tight flex items-center gap-2">
                <span>
                  {isPassed
                    ? 'VERIFICATION PASSED'
                    : isFailed
                    ? 'VERIFICATION FAILED'
                    : isRunning
                    ? 'EVALUATION IN PROGRESS'
                    : run.status.toUpperCase()}
                </span>
                <span className="text-[10px] px-1.5 py-0.2 rounded bg-white border border-current font-mono font-normal">
                  Threshold: 1.0
                </span>
              </div>
              <div className="text-[11px] opacity-85 mt-0.5">
                {isPassed
                  ? 'All held-out pytest assertions satisfied in Docker sandbox.'
                  : isFailed
                  ? 'Verifier test suite did not pass.'
                  : 'Agent is executing tools in Docker container.'}
              </div>
            </div>
          </div>

          <div className="text-right">
            <div className="text-2xl font-bold font-mono">
              {run.reward !== null ? `${run.reward.toFixed(1)} / 1.0` : '— / 1.0'}
            </div>
            <div className="text-[10px] uppercase font-bold tracking-wider opacity-70">
              Reward Score
            </div>
          </div>
        </div>

        {/* Resource Metrics Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 font-mono">
          <div className="p-3 rounded-xl bg-canvas border border-border-subtle text-center">
            <div className="text-[10px] uppercase font-semibold text-text-muted mb-1 flex items-center justify-center gap-1">
              <IonIcon icon={timeOutline} className="text-text-muted text-xs" /> Duration
            </div>
            <div className="text-sm font-bold text-text-primary">{run.total_duration_sec.toFixed(1)}s</div>
          </div>

          <div className="p-3 rounded-xl bg-canvas border border-border-subtle text-center">
            <div className="text-[10px] uppercase font-semibold text-text-muted mb-1 flex items-center justify-center gap-1">
              <IonIcon icon={sparklesOutline} className="text-brand-purple text-xs" /> Tokens
            </div>
            <div className="text-sm font-bold text-text-primary">{run.total_tokens.toLocaleString()}</div>
          </div>

          <div className="p-3 rounded-xl bg-canvas border border-border-subtle text-center">
            <div className="text-[10px] uppercase font-semibold text-text-muted mb-1 flex items-center justify-center gap-1">
              <IonIcon icon={cashOutline} className="text-emerald-600 text-xs" /> Est. Cost
            </div>
            <div className="text-sm font-bold text-emerald-700">${run.estimated_cost_usd.toFixed(4)}</div>
          </div>

          <div className="p-3 rounded-xl bg-canvas border border-border-subtle text-center">
            <div className="text-[10px] uppercase font-semibold text-text-muted mb-1 flex items-center justify-center gap-1">
              <IonIcon icon={gitCommitOutline} className="text-text-muted text-xs" /> Turns
            </div>
            <div className="text-sm font-bold text-text-primary">{run.total_steps} steps</div>
          </div>
        </div>

        {/* Verifier Diagnostics & Reasoning */}
        {run.failure_reason && (
          <div className="p-3.5 rounded-xl bg-rose-50 border border-rose-200 text-xs text-rose-900 space-y-1">
            <strong className="block text-rose-700 font-semibold uppercase text-[10px] tracking-wider">
              Verifier Diagnostic Output:
            </strong>
            <p className="font-mono text-[11px] whitespace-pre-wrap leading-relaxed max-h-32 overflow-y-auto bg-white p-2 rounded border border-rose-200">
              {run.failure_reason}
            </p>
          </div>
        )}

        {run.final_summary && (
          <div className="p-3.5 rounded-xl bg-canvas border border-border-subtle text-xs text-text-secondary space-y-1">
            <strong className="block text-text-primary font-semibold uppercase text-[10px] tracking-wider">
              Agent Final Solution Summary:
            </strong>
            <p className="leading-relaxed text-xs">{run.final_summary}</p>
          </div>
        )}
      </div>
    </div>
  );
};


