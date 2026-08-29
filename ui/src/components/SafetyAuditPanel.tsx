import React, { useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  alertCircleOutline,
  checkmarkCircleOutline,
  chevronDownOutline,
  chevronForwardOutline,
  documentTextOutline,
  eyeOutline,
  shieldOutline,
  sparklesOutline,
} from 'ionicons/icons';
import { JudgeVerdict } from '../types';

interface SafetyAuditPanelProps {
  verdicts?: JudgeVerdict[];
}

export const SafetyAuditPanel: React.FC<SafetyAuditPanelProps> = ({ verdicts }) => {
  const [expandedReasoning, setExpandedReasoning] = useState<Record<string, boolean>>({});

  if (!verdicts || verdicts.length === 0) {
    return null;
  }

  const toggleReasoning = (name: string) => {
    setExpandedReasoning((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  const getMetricIcon = (name: string) => {
    switch (name) {
      case 'plan_adherence':
        return sparklesOutline;
      case 'hallucination_detection':
        return eyeOutline;
      case 'reward_tampering':
        return shieldOutline;
      case 'citation_grounding':
        return documentTextOutline;
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
      case 'citation_grounding':
        return 'Citation & Grounding Precision';
      default:
        return name;
    }
  };

  const getThresholdText = (name: string) => {
    switch (name) {
      case 'plan_adherence':
        return 'Threshold: ≥ 80%';
      case 'hallucination_detection':
        return 'Threshold: ≥ 80%';
      case 'reward_tampering':
        return 'Threshold: ≥ 90%';
      case 'citation_grounding':
        return 'Threshold: ≥ 85%';
      default:
        return 'Threshold: ≥ 80%';
    }
  };

  return (
    <div className="bg-white rounded-2xl border border-border-subtle shadow-aegis-card overflow-hidden">
      <div className="p-4 pb-3 border-b border-border-subtle flex items-center justify-between">
        <div className="flex items-center gap-2">
          <IonIcon icon={shieldOutline} className="text-brand-purple text-base" />
          <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-text-primary">
            AI Safety & Alignment Audits
          </h3>
        </div>
        <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full bg-surface-subtle text-brand-purple">
          LLM-as-a-Judge
        </span>
      </div>

      <div className="p-4 space-y-3">
        {verdicts.map((v) => {
          const isPassed = v.passed;
          const pct = Math.round(v.score * 100);
          const isExpanded = expandedReasoning[v.metric_name] ?? true;

          return (
            <div
              key={v.metric_name}
              className={`p-3.5 rounded-xl border space-y-2.5 transition-all ${
                isPassed
                  ? 'bg-canvas border-border-subtle'
                  : 'bg-rose-50 border-rose-200'
              }`}
            >
              {/* Header with Title & Badge */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 font-semibold text-xs text-text-primary">
                  <IonIcon
                    icon={getMetricIcon(v.metric_name)}
                    className={isPassed ? 'text-brand-purple text-sm' : 'text-risk-high text-sm'}
                  />
                  <span>{getMetricTitle(v.metric_name)}</span>
                  <span className="text-[10px] text-text-muted font-mono hidden sm:inline-block">
                    ({getThresholdText(v.metric_name)})
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <span
                    className={`text-[10px] font-bold font-mono px-2 py-0.5 rounded-full flex items-center gap-1 border ${
                      isPassed
                        ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                        : 'bg-rose-50 text-rose-700 border-rose-200'
                    }`}
                  >
                    <IonIcon icon={isPassed ? checkmarkCircleOutline : alertCircleOutline} className="text-xs" />
                    <span>{isPassed ? `${pct}%` : `FLAGGED (${pct}%)`}</span>
                  </span>

                  <button
                    type="button"
                    onClick={() => toggleReasoning(v.metric_name)}
                    className="text-text-muted hover:text-text-primary p-0.5 transition-colors"
                  >
                    <IonIcon icon={isExpanded ? chevronDownOutline : chevronForwardOutline} className="text-xs" />
                  </button>
                </div>
              </div>

              {/* Progress Bar */}
              <div className="h-1.5 w-full bg-white rounded-full overflow-hidden border border-border-subtle/50">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    isPassed ? 'bg-status-cleared' : 'bg-risk-high'
                  }`}
                  style={{ width: `${pct}%` }}
                />
              </div>

              {/* Collapsible Reasoning */}
              {isExpanded && (
                <div className="text-xs text-text-secondary leading-relaxed bg-white p-3 rounded-lg border border-border-subtle font-mono text-[11px] space-y-1.5">
                  <p className="whitespace-pre-wrap">{v.reasoning}</p>
                  {v.flagged_steps && v.flagged_steps.length > 0 && (
                    <div className="text-[10px] font-mono text-risk-high font-semibold flex items-center gap-1 pt-1 border-t border-rose-200">
                      <IonIcon icon={alertCircleOutline} className="text-xs" />
                      <span>Flagged Agent Turn(s): {v.flagged_steps.join(', ')}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};


