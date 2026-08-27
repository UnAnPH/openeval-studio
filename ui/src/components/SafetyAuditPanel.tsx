import React from 'react';
import {
  IonBadge,
  IonCard,
  IonCardContent,
  IonCardHeader,
  IonCardTitle,
  IonIcon,
  IonProgressBar,
} from '@ionic/react';
import {
  alertCircleOutline,
  checkmarkCircleOutline,
  eyeOutline,
  shieldOutline,
  sparklesOutline,
} from 'ionicons/icons';
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
        return sparklesOutline;
      case 'hallucination_detection':
        return eyeOutline;
      case 'reward_tampering':
        return shieldOutline;
      default:
        return alertCircleOutline;
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
    <IonCard className="m-0 p-0 rounded-2xl bg-surface border border-border shadow-sm">
      <IonCardHeader className="p-4 pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <IonIcon icon={shieldOutline} className="text-indigo-400 text-base" />
            <IonCardTitle className="text-sm font-bold text-white tracking-tight">
              AI Safety & Alignment Audits
            </IonCardTitle>
          </div>
          <IonBadge color="tertiary" className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5">
            LLM-as-a-Judge
          </IonBadge>
        </div>
      </IonCardHeader>

      <IonCardContent className="p-4 pt-0 space-y-3">
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
                  <IonIcon icon={getMetricIcon(v.metric_name)} className={isPassed ? 'text-sky-400 text-sm' : 'text-rose-400 text-sm'} />
                  <span>{getMetricTitle(v.metric_name)}</span>
                </div>

                <IonBadge
                  color={isPassed ? 'success' : 'danger'}
                  className="text-[11px] font-bold font-mono px-2 py-0.5"
                >
                  <IonIcon icon={isPassed ? checkmarkCircleOutline : alertCircleOutline} className="mr-1 text-xs" />
                  {isPassed ? `${pct}%` : `FLAGGED (${pct}%)`}
                </IonBadge>
              </div>

              {/* Ionic Progress Bar */}
              <IonProgressBar
                value={v.score}
                color={isPassed ? 'success' : 'danger'}
                className="rounded-full h-1.5"
              />

              {/* Reasoning Description */}
              <p className="text-xs text-slate-300 leading-relaxed bg-black/30 p-2.5 rounded-lg border border-border/40 font-mono text-[11px]">
                {v.reasoning}
              </p>

              {/* Flagged Turns Badge if any */}
              {v.flagged_steps && v.flagged_steps.length > 0 && (
                <div className="text-[11px] font-mono text-rose-400 font-semibold flex items-center gap-1">
                  <IonIcon icon={alertCircleOutline} className="text-sm" />
                  <span>Flagged Turn(s): {v.flagged_steps.join(', ')}</span>
                </div>
              )}
            </div>
          );
        })}
      </IonCardContent>
    </IonCard>
  );
};
