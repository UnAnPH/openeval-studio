import React from 'react';
import {
  IonBadge,
  IonCard,
  IonCardContent,
  IonCardHeader,
  IonCardTitle,
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
} from 'ionicons/icons';
import { RunRecord } from '../types';

interface ScorecardProps {
  run: RunRecord | null;
}

export const Scorecard: React.FC<ScorecardProps> = ({ run }) => {
  if (!run) {
    return (
      <IonCard className="m-0 p-6 rounded-2xl bg-surface border border-border text-center text-slate-500">
        <IonIcon icon={helpCircleOutline} className="text-3xl mx-auto mb-2 opacity-50 text-slate-400" />
        <p className="text-sm font-medium">No evaluation scorecard yet.</p>
        <p className="text-xs text-slate-600 mt-1">Run an evaluation to see verifier results & costs.</p>
      </IonCard>
    );
  }

  const isPassed = run.passed === true;
  const isFailed = run.passed === false;
  const isRunning = run.status === 'running' || run.status === 'pending';

  return (
    <IonCard className="m-0 p-0 rounded-2xl bg-surface border border-border shadow-sm">
      <IonCardHeader className="p-4 pb-2">
        <div className="flex items-center justify-between">
          <IonCardTitle className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Evaluation Scorecard
          </IonCardTitle>
          <IonBadge color="medium" className="text-[11px] font-mono">
            {run.run_id}
          </IonBadge>
        </div>
      </IonCardHeader>

      <IonCardContent className="p-4 pt-0 space-y-4">
        {/* Primary Reward Banner */}
        <div
          className={`p-4 rounded-xl border flex items-center justify-between transition-all ${
            isPassed
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
              : isFailed
              ? 'bg-rose-500/10 border-rose-500/30 text-rose-400'
              : isRunning
              ? 'bg-sky-500/10 border-sky-500/30 text-sky-400 animate-pulse'
              : 'bg-slate-800 border-slate-700 text-slate-300'
          }`}
        >
          <div className="flex items-center gap-3">
            <IonIcon
              icon={isPassed ? checkmarkCircle : isFailed ? closeCircle : hourglassOutline}
              className={`text-3xl ${isPassed ? 'text-emerald-400' : isFailed ? 'text-rose-400' : 'text-sky-400 animate-spin'}`}
            />

            <div>
              <div className="font-bold text-base">
                {isPassed ? 'VERIFICATION PASSED' : isFailed ? 'VERIFICATION FAILED' : isRunning ? 'EVALUATION RUNNING' : run.status.toUpperCase()}
              </div>
              <div className="text-xs opacity-80">
                {isPassed
                  ? 'All held-out pytest assertions satisfied in sandbox.'
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
            <div className="text-[10px] uppercase font-bold tracking-wider opacity-70">Reward Score</div>
          </div>
        </div>

        {/* Metrics Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
          <div className="p-3 rounded-xl bg-surface-elevated/60 border border-border/80 text-center">
            <div className="text-[11px] text-slate-400 mb-1 flex items-center justify-center gap-1">
              <IonIcon icon={timeOutline} className="text-slate-400 text-xs" /> Duration
            </div>
            <div className="text-sm font-bold font-mono text-slate-100">{run.total_duration_sec.toFixed(1)}s</div>
          </div>

          <div className="p-3 rounded-xl bg-surface-elevated/60 border border-border/80 text-center">
            <div className="text-[11px] text-slate-400 mb-1 flex items-center justify-center gap-1">
              <IonIcon icon={sparklesOutline} className="text-sky-400 text-xs" /> Tokens
            </div>
            <div className="text-sm font-bold font-mono text-slate-100">{run.total_tokens.toLocaleString()}</div>
          </div>

          <div className="p-3 rounded-xl bg-surface-elevated/60 border border-border/80 text-center">
            <div className="text-[11px] text-slate-400 mb-1 flex items-center justify-center gap-1">
              <IonIcon icon={cashOutline} className="text-emerald-400 text-xs" /> Est. Cost
            </div>
            <div className="text-sm font-bold font-mono text-emerald-400">${run.estimated_cost_usd.toFixed(4)}</div>
          </div>

          <div className="p-3 rounded-xl bg-surface-elevated/60 border border-border/80 text-center">
            <div className="text-[11px] text-slate-400 mb-1 flex items-center justify-center gap-1">
              <IonIcon icon={gitCommitOutline} className="text-slate-400 text-xs" /> ReAct Steps
            </div>
            <div className="text-sm font-bold font-mono text-slate-100">{run.total_steps} turns</div>
          </div>
        </div>

        {/* Verifier Diagnostics & Reasoning */}
        {run.failure_reason && (
          <div className="p-3 rounded-xl bg-rose-950/30 border border-rose-500/20 text-xs text-rose-300">
            <strong className="block mb-1 text-rose-400 font-semibold">Verifier Output:</strong>
            <p className="font-mono text-[11px] whitespace-pre-wrap leading-relaxed">
              {run.failure_reason}
            </p>
          </div>
        )}

        {run.final_summary && (
          <div className="p-3 rounded-xl bg-surface-elevated/40 border border-border text-xs text-slate-300">
            <strong className="block mb-1 text-slate-200 font-semibold">Agent Resolution Summary:</strong>
            <p className="leading-relaxed">{run.final_summary}</p>
          </div>
        )}
      </IonCardContent>
    </IonCard>
  );
};
