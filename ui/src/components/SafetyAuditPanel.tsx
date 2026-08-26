import React from 'react';
import { AlertOctagon, CheckCircle2, Eye, ShieldAlert, Sparkles } from 'lucide-react';
import { JudgeVerdict } from '../types';

interface SafetyAuditPanelProps {
  verdicts?: JudgeVerdict[];
}

export const SafetyAuditPanel: React.FC<SafetyAuditPanelProps> = ({ verdicts }) => {
  if (!verdicts || verdicts.length === 0) {
    return null;
  }

  const getMetricIcon = (name: string) => {
    switch (name) {
      case 'plan_adherence':
        return <Sparkles className="w-4 h-4 text-sky-400" />;
      case 'hallucination_detection':
        return <Eye className="w-4 h-4 text-amber-400" />;
      case 'reward_tampering':
        return <ShieldAlert className="w-4 h-4 text-rose-400" />;
      default:
        return <AlertOctagon className="w-4 h-4 text-slate-400" />;
    }
  };

  const getMetricTitle = (name: string) => {
    switch (name) {
      case 'plan_adherence':
        return 'Plan Adherence';
      case 'hallucination_detection':
        return 'Hallucination & Error Filter';
      case 'reward_tampering':
        return 'Reward Tampering Defense';
      default:
        return name;
    }
  };

  return (
    <div className="p-5 rounded-2xl bg-surface border border-border space-y-4 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-indigo-400" />
          <h3 className="text-sm font-bold text-white tracking-tight">AI Safety & Alignment Audits</h3>
        </div>
        <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
          LLM-as-a-Judge
        </span>
      </div>

      {/* Vertical Stack (1 Card per row for spacious, legible layout) */}
      <div className="flex flex-col space-y-3">
        {verdicts.map((v) => {
          const isPassed = v.passed;
          const pct = Math.round(v.score * 100);

          return (
            <div
              key={v.metric_name}
              className={`p-4 rounded-xl border space-y-2.5 transition-all ${
                isPassed
                  ? 'bg-surface-elevated/70 border-border/80'
                  : 'bg-rose-500/10 border-rose-500/30'
              }`}
            >
              {/* Header with Title & Badge */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 font-semibold text-xs text-slate-200">
                  {getMetricIcon(v.metric_name)}
                  <span>{getMetricTitle(v.metric_name)}</span>
                </div>

                {isPassed ? (
                  <span className="inline-flex items-center gap-1 text-[11px] font-bold font-mono px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/25">
                    <CheckCircle2 className="w-3 h-3" /> {pct}%
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-[11px] font-bold font-mono px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-400 border border-rose-500/30 animate-pulse">
                    <AlertOctagon className="w-3 h-3" /> FLAGGED ({pct}%)
                  </span>
                )}
              </div>

              {/* Score Progress Bar */}
              <div className="w-full bg-black/40 h-1.5 rounded-full overflow-hidden border border-border/40">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    isPassed ? 'bg-emerald-400' : 'bg-rose-500'
                  }`}
                  style={{ width: `${pct}%` }}
                />
              </div>

              {/* Reasoning Description */}
              <p className="text-xs text-slate-300 leading-relaxed bg-black/30 p-2.5 rounded-lg border border-border/40 font-mono text-[11px]">
                {v.reasoning}
              </p>

              {/* Flagged Turns Badge if any */}
              {v.flagged_steps && v.flagged_steps.length > 0 && (
                <div className="text-[11px] font-mono text-rose-400 font-semibold flex items-center gap-1">
                  <AlertOctagon className="w-3.5 h-3.5" />
                  <span>Flagged Turn(s): {v.flagged_steps.join(', ')}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
