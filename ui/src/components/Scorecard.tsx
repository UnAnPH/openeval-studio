import React from 'react';
import { AlertCircle, CheckCircle2, Clock, DollarSign, HelpCircle, Sparkles, XCircle } from 'lucide-react';
import { RunRecord } from '../types';

interface ScorecardProps {
  run: RunRecord | null;
}

export const Scorecard: React.FC<ScorecardProps> = ({ run }) => {
  if (!run) {
    return (
      <div className="p-6 rounded-2xl bg-surface border border-border text-center text-slate-500">
        <HelpCircle className="w-8 h-8 mx-auto mb-2 opacity-50 text-slate-400" />
        <p className="text-sm font-medium">No evaluation scorecard yet.</p>
        <p className="text-xs text-slate-600 mt-1">Run an evaluation to see verifier results & costs.</p>
      </div>
    );
  }

  const isPassed = run.passed === true;
  const isFailed = run.passed === false;
  const isRunning = run.status === 'running' || run.status === 'pending';

  return (
    <div className="p-5 rounded-2xl bg-surface border border-border space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Evaluation Scorecard
        </h3>
        <span className="text-xs font-mono text-slate-500">Run ID: {run.run_id}</span>
      </div>

      {/* Primary Reward Banner */}
      <div
        className={`p-4 rounded-xl border flex items-center justify-between ${
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
          {isPassed && <CheckCircle2 className="w-6 h-6 text-emerald-400" />}
          {isFailed && <XCircle className="w-6 h-6 text-rose-400" />}
          {isRunning && <Clock className="w-6 h-6 text-sky-400 animate-spin" />}

          <div>
            <div className="font-bold text-base">
              {isPassed ? 'VERIFICATION PASSED' : isFailed ? 'VERIFICATION FAILED' : isRunning ? 'EVALUATION IN PROGRESS' : run.status.toUpperCase()}
            </div>
            <div className="text-xs opacity-80">
              {isPassed
                ? 'All held-out pytest assertions satisfied in sandbox.'
                : isFailed
                ? 'Verifier test suite did not pass.'
                : 'Agent is exploring & patching files.'}
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
            <Clock className="w-3 h-3 text-slate-400" /> Duration
          </div>
          <div className="text-sm font-bold font-mono text-slate-100">{run.total_duration_sec}s</div>
        </div>

        <div className="p-3 rounded-xl bg-surface-elevated/60 border border-border/80 text-center">
          <div className="text-[11px] text-slate-400 mb-1 flex items-center justify-center gap-1">
            <Sparkles className="w-3 h-3 text-accent" /> Tokens
          </div>
          <div className="text-sm font-bold font-mono text-slate-100">{run.total_tokens.toLocaleString()}</div>
        </div>

        <div className="p-3 rounded-xl bg-surface-elevated/60 border border-border/80 text-center">
          <div className="text-[11px] text-slate-400 mb-1 flex items-center justify-center gap-1">
            <DollarSign className="w-3 h-3 text-emerald-400" /> Est. Cost
          </div>
          <div className="text-sm font-bold font-mono text-emerald-400">${run.estimated_cost_usd.toFixed(5)}</div>
        </div>

        <div className="p-3 rounded-xl bg-surface-elevated/60 border border-border/80 text-center">
          <div className="text-[11px] text-slate-400 mb-1">ReAct Steps</div>
          <div className="text-sm font-bold font-mono text-slate-100">{run.total_steps} turns</div>
        </div>
      </div>

      {/* Failure explanation */}
      {run.failure_reason && (
        <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300 space-y-1">
          <div className="flex items-center gap-1.5 font-semibold">
            <AlertCircle className="w-3.5 h-3.5 text-rose-400" />
            Verifier Diagnosis Note
          </div>
          <p className="font-mono text-[11px] bg-black/40 p-2 rounded text-rose-200">{run.failure_reason}</p>
        </div>
      )}

      {/* Final Summary */}
      {run.final_summary && (
        <div className="p-3.5 rounded-xl bg-surface-elevated border border-border text-xs space-y-1">
          <div className="text-slate-400 font-semibold">Agent Final Summary</div>
          <p className="text-slate-200 leading-relaxed">{run.final_summary}</p>
        </div>
      )}
    </div>
  );
};
