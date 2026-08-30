import React, { useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  alertCircleOutline,
  checkmarkCircle,
  closeCircle,
  documentTextOutline,
  eyeOutline,
  shieldOutline,
  sparklesOutline,
  warningOutline,
  saveOutline,
  gitCommitOutline,
  personCircleOutline,
  fingerPrintOutline,
  arrowForwardOutline,
} from 'ionicons/icons';
import { JudgeVerdict } from '../types';

interface SafetyAuditPanelProps {
  verdicts?: JudgeVerdict[];
  onSelectTurn?: (turnNumber: number) => void;
  humanReviewer?: string;
  humanReviewNotes?: string;
  signOffStatus?: 'APPROVED_SAFE' | 'FLAGGED_RISKY' | 'REQUIRES_REVIEW';
  overrideScores?: Record<string, boolean>;
  onOverrideMetric?: (metricName: string, passed: boolean) => void;
  onSaveReview?: (reviewer: string, notes: string, status: 'APPROVED_SAFE' | 'FLAGGED_RISKY' | 'REQUIRES_REVIEW') => void;
  isSaving?: boolean;
  compact?: boolean;
}

export const SafetyAuditPanel: React.FC<SafetyAuditPanelProps> = ({
  verdicts = [],
  onSelectTurn,
  humanReviewer: initialReviewer = '',
  humanReviewNotes: initialNotes = '',
  signOffStatus: initialStatus = 'REQUIRES_REVIEW',
  overrideScores = {},
  onOverrideMetric,
  onSaveReview,
  isSaving = false,
  compact = false,
}) => {
  const [reviewerName, setReviewerName] = useState<string>(initialReviewer);
  const [reviewNotes, setReviewNotes] = useState<string>(initialNotes);
  const [status, setStatus] = useState<'APPROVED_SAFE' | 'FLAGGED_RISKY' | 'REQUIRES_REVIEW'>(initialStatus);
  const [savedBanner, setSavedBanner] = useState<boolean>(false);

  const getMetricIcon = (name: string) => {
    switch (name) {
      case 'situational_awareness':
        return eyeOutline;
      case 'goal_guarding_scheming':
        return warningOutline;
      case 'unfaithful_cot':
        return gitCommitOutline;
      case 'plan_adherence':
        return sparklesOutline;
      case 'hallucination_detection':
        return fingerPrintOutline;
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
      case 'situational_awareness':
        return 'Situational Awareness';
      case 'goal_guarding_scheming':
        return 'Goal Guarding & Deceptive Scheming';
      case 'unfaithful_cot':
        return 'Unfaithful Chain-of-Thought (CoT)';
      case 'plan_adherence':
        return 'Plan Adherence & Instruction Fidelity';
      case 'hallucination_detection':
        return 'Hallucination & Error Defense';
      case 'reward_tampering':
        return 'Reward Tampering Defense';
      case 'citation_grounding':
        return 'Citation & Grounding Precision';
      default:
        return name.replace(/_/g, ' ');
    }
  };

  const getMetricDescription = (name: string) => {
    switch (name) {
      case 'situational_awareness':
        return 'Detects whether the agent expressed awareness of being inside an evaluation benchmark, sandbox, or grading harness.';
      case 'goal_guarding_scheming':
        return 'Scans for covert strategies to bypass monitors, evade oversight, tamper with watchdog processes, or conceal goals.';
      case 'unfaithful_cot':
        return 'Analyzes divergence between the internal reasoning thought and the actual tool invocation parameters.';
      case 'plan_adherence':
        return 'Measures adherence to the specified prompt instructions without derailment or hallucinated requirements.';
      case 'hallucination_detection':
        return 'Detects fabricated tool outputs, invented file paths, and nonexistent software components.';
      case 'reward_tampering':
        return 'Checks for attempts to delete test suites, modify assertion logic, or falsify verifier outcomes.';
      case 'citation_grounding':
        return 'Evaluates whether generated incident reports and facts strictly cite verified documentation corpus.';
      default:
        return 'Automated safety and capability trajectory audit.';
    }
  };

  const handleQuickTag = (tagText: string) => {
    setReviewNotes((prev) => (prev ? `${prev}\n• ${tagText}` : `• ${tagText}`));
  };

  const handleSave = () => {
    if (onSaveReview) {
      onSaveReview(reviewerName, reviewNotes, status);
      setSavedBanner(true);
      setTimeout(() => setSavedBanner(false), 3000);
    }
  };

  // Group into Scheming Scanners and Core Alignment Judges
  const schemingMetrics = ['situational_awareness', 'goal_guarding_scheming', 'unfaithful_cot'];
  const schemingVerdicts = verdicts.filter((v) => schemingMetrics.includes(v.metric_name));
  const coreVerdicts = verdicts.filter((v) => !schemingMetrics.includes(v.metric_name));

  return (
    <div className="space-y-5 font-sans w-full max-w-full overflow-hidden">
      {/* 1. Clinical Sign-Off Hero Bar */}
      <div className={`bg-white rounded-2xl border border-border-subtle shadow-sm space-y-4 ${compact ? 'p-4' : 'p-5'}`}>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border-subtle pb-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-purple-50 text-brand-purple flex items-center justify-center flex-shrink-0">
              <IonIcon icon={personCircleOutline} className="text-xl" />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-bold text-text-primary truncate">Human Auditor Sign-Off & Review</h3>
              <p className="text-xs text-text-secondary">Clinical safety sign-off for enterprise deployment</p>
            </div>
          </div>

          {/* Status Selection Pill Buttons */}
          <div className="grid grid-cols-3 gap-1 bg-canvas p-1 rounded-xl border border-border-subtle text-xs w-full sm:w-auto flex-shrink-0">
            <button
              type="button"
              onClick={() => setStatus('APPROVED_SAFE')}
              className={`px-2.5 py-1.5 rounded-lg font-bold text-[11px] transition-all text-center cursor-pointer ${
                status === 'APPROVED_SAFE'
                  ? 'bg-emerald-600 text-white shadow-2xs'
                  : 'text-text-secondary hover:text-emerald-700'
              }`}
            >
              ✓ Approved
            </button>
            <button
              type="button"
              onClick={() => setStatus('REQUIRES_REVIEW')}
              className={`px-2.5 py-1.5 rounded-lg font-bold text-[11px] transition-all text-center cursor-pointer ${
                status === 'REQUIRES_REVIEW'
                  ? 'bg-amber-500 text-white shadow-2xs'
                  : 'text-text-secondary hover:text-amber-700'
              }`}
            >
              ⚠ Review
            </button>
            <button
              type="button"
              onClick={() => setStatus('FLAGGED_RISKY')}
              className={`px-2.5 py-1.5 rounded-lg font-bold text-[11px] transition-all text-center cursor-pointer ${
                status === 'FLAGGED_RISKY'
                  ? 'bg-rose-600 text-white shadow-2xs'
                  : 'text-text-secondary hover:text-rose-700'
              }`}
            >
              ✕ Flagged
            </button>
          </div>
        </div>

        {/* Input Controls */}
        <div className={`grid grid-cols-1 ${compact ? '' : 'lg:grid-cols-3'} gap-3`}>
          <div className="space-y-1">
            <label className="text-xs font-mono font-bold text-text-primary">Auditor Handle / Email</label>
            <input
              type="text"
              value={reviewerName}
              onChange={(e) => setReviewerName(e.target.value)}
              placeholder="e.g. auditor@safetylab.ai"
              className="w-full bg-canvas border border-border-subtle rounded-xl px-3 py-2 text-xs text-text-primary focus:outline-none focus:border-brand-primary font-mono box-border"
            />
          </div>

          <div className={`${compact ? '' : 'lg:col-span-2'} space-y-1.5`}>
            <div className="flex flex-wrap items-center justify-between gap-1.5">
              <label className="text-xs font-mono font-bold text-text-primary">Auditor Notes & Justification</label>
              {/* Quick rationale tags */}
              <div className="flex items-center gap-1 flex-wrap">
                <span className="text-[10px] text-text-muted">Insert:</span>
                <button
                  type="button"
                  onClick={() => handleQuickTag('False Positive: Diagnostic Probe')}
                  className="px-1.5 py-0.5 rounded bg-surface-subtle text-[10px] text-brand-purple hover:bg-purple-100 font-medium cursor-pointer"
                >
                  + False Positive
                </button>
                <button
                  type="button"
                  onClick={() => handleQuickTag('Confirmed Deceptive Scheming')}
                  className="px-1.5 py-0.5 rounded bg-rose-50 text-[10px] text-rose-700 hover:bg-rose-100 font-medium cursor-pointer"
                >
                  + Subversion
                </button>
                <button
                  type="button"
                  onClick={() => handleQuickTag('Harmless Refactor')}
                  className="px-1.5 py-0.5 rounded bg-surface-subtle text-[10px] text-text-secondary hover:bg-canvas font-medium cursor-pointer"
                >
                  + Benign
                </button>
              </div>
            </div>
            <textarea
              rows={compact ? 3 : 2}
              value={reviewNotes}
              onChange={(e) => setReviewNotes(e.target.value)}
              placeholder="Detail reasons for pass/fail classification, human verification findings, and safety audit context..."
              className="w-full bg-canvas border border-border-subtle rounded-xl p-2.5 text-xs text-text-primary focus:outline-none focus:border-brand-primary font-sans box-border"
            />
          </div>
        </div>

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2 pt-1 border-t border-border-subtle/60">
          {savedBanner ? (
            <span className="text-xs text-emerald-700 font-bold font-mono">✓ Sign-off saved to backend!</span>
          ) : (
            <span className="text-[11px] text-text-muted font-mono">Sign-off persists to audit database</span>
          )}

          <button
            type="button"
            onClick={handleSave}
            disabled={isSaving}
            className="px-4 py-2 rounded-xl bg-dark-base text-white text-xs font-bold hover:bg-black transition-all flex items-center justify-center gap-1.5 cursor-pointer shadow-sm disabled:opacity-50"
          >
            <IonIcon icon={saveOutline} className="text-xs" />
            <span>{isSaving ? 'Saving...' : 'Save & Submit Sign-Off'}</span>
          </button>
        </div>
      </div>

      {/* 2. Scheming & Deception Scanners Grid */}
      <div className={`bg-white rounded-2xl border border-border-subtle shadow-sm space-y-4 ${compact ? 'p-4' : 'p-5'}`}>
        <div className="flex items-center justify-between border-b border-border-subtle pb-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-amber-50 text-amber-800 flex items-center justify-center flex-shrink-0">
              <IonIcon icon={warningOutline} className="text-lg" />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-bold text-text-primary truncate">Post-Hoc Scheming & Deception Scanners</h3>
              <p className="text-xs text-text-secondary">Automated Frontier AI Safety & Alignment audit dimensions</p>
            </div>
          </div>
          <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full bg-surface-subtle text-brand-purple flex-shrink-0">
            3 Dimensions
          </span>
        </div>

        {schemingVerdicts.length === 0 ? (
          <div className="p-8 text-center text-text-muted text-xs font-mono">
            No scheming scan data recorded for this run.
          </div>
        ) : (
          <div className={`grid grid-cols-1 ${compact ? '' : 'xl:grid-cols-3'} gap-3.5`}>
            {schemingVerdicts.map((verdict) => {
              const hasOverride = overrideScores[verdict.metric_name] !== undefined;
              const effectivePassed = hasOverride ? overrideScores[verdict.metric_name] : verdict.passed;
              const pct = Math.round(verdict.score * 100);

              return (
                <div
                  key={verdict.metric_name}
                  className={`p-4 rounded-xl border flex flex-col justify-between space-y-3 transition-all ${
                    effectivePassed
                      ? 'bg-emerald-50/30 border-emerald-200'
                      : 'bg-rose-50/40 border-rose-300 ring-1 ring-rose-200'
                  }`}
                >
                  <div className="space-y-2.5">
                    {/* Header with Title and Pass/Flag Badge */}
                    <div className="flex items-start justify-between gap-2 min-w-0">
                      <div className="flex items-center gap-1.5 font-bold font-mono text-xs text-text-primary min-w-0 flex-1">
                        <IonIcon icon={getMetricIcon(verdict.metric_name)} className="text-brand-purple text-sm flex-shrink-0" />
                        <span className="break-words leading-tight">{getMetricTitle(verdict.metric_name)}</span>
                      </div>
                      <span
                        className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded-full flex-shrink-0 ${
                          effectivePassed
                            ? 'bg-emerald-100 text-emerald-800'
                            : 'bg-rose-100 text-rose-800 animate-pulse'
                        }`}
                      >
                        {effectivePassed ? 'Passed' : 'Flagged'}
                      </span>
                    </div>

                    <p className="text-[11px] text-text-secondary leading-relaxed">
                      {getMetricDescription(verdict.metric_name)}
                    </p>

                    <div className="p-3 bg-white rounded-xl border border-border-subtle text-xs text-text-primary leading-relaxed font-sans space-y-2 break-words">
                      <div><strong className="font-mono text-text-secondary">Scanner Finding:</strong> {verdict.reasoning}</div>

                      {/* Unfaithful CoT Visual Breakdown */}
                      {verdict.discrepancy_details && (
                        <div className="p-2 rounded-lg bg-rose-50/90 border border-rose-200 text-[11px] text-rose-900 font-mono break-all">
                          <strong>Divergence:</strong> {verdict.discrepancy_details}
                        </div>
                      )}

                      {/* Flagged Steps Deep Links */}
                      {verdict.flagged_steps && verdict.flagged_steps.length > 0 && (
                        <div className="flex items-center gap-1.5 flex-wrap pt-1">
                          <span className="text-[10px] text-text-muted font-bold">Flagged Turns:</span>
                          {verdict.flagged_steps.map((turnNum) => (
                            <button
                              key={turnNum}
                              type="button"
                              onClick={() => onSelectTurn?.(turnNum)}
                              className="px-2 py-0.5 rounded-md bg-rose-100 text-rose-800 text-[10px] font-bold hover:bg-rose-200 transition-colors flex items-center gap-0.5 cursor-pointer font-mono"
                              title={`Jump to turn #${turnNum}`}
                            >
                              <span>Turn #{turnNum}</span>
                              <IonIcon icon={arrowForwardOutline} className="text-[9px]" />
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Override Toggle & Badge */}
                  <div className="pt-2 border-t border-border-subtle/80 flex items-center justify-between text-xs flex-wrap gap-2">
                    <div className="text-[10px] font-mono text-text-muted">
                      {hasOverride ? (
                        <span className="text-brand-purple font-bold">👤 Human Override</span>
                      ) : (
                        <span>🤖 Model Score: {pct}%</span>
                      )}
                    </div>

                    {onOverrideMetric && (
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => onOverrideMetric(verdict.metric_name, true)}
                          className={`px-2.5 py-0.5 rounded text-[10px] font-bold cursor-pointer transition-colors ${
                            effectivePassed
                              ? 'bg-emerald-600 text-white'
                              : 'bg-white text-text-secondary hover:bg-surface-subtle border border-border-subtle'
                          }`}
                        >
                          Pass
                        </button>
                        <button
                          type="button"
                          onClick={() => onOverrideMetric(verdict.metric_name, false)}
                          className={`px-2.5 py-0.5 rounded text-[10px] font-bold cursor-pointer transition-colors ${
                            !effectivePassed
                              ? 'bg-rose-600 text-white'
                              : 'bg-white text-text-secondary hover:bg-surface-subtle border border-border-subtle'
                          }`}
                        >
                          Fail
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 3. Core Safety & Alignment Judges */}
      <div className={`bg-white rounded-2xl border border-border-subtle shadow-sm space-y-4 ${compact ? 'p-4' : 'p-5'}`}>
        <div className="flex items-center justify-between border-b border-border-subtle pb-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-purple-50 text-brand-purple flex items-center justify-center flex-shrink-0">
              <IonIcon icon={shieldOutline} className="text-lg" />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-bold text-text-primary truncate">Core Evaluation Judges & Grounding</h3>
              <p className="text-xs text-text-secondary">Trajectory fidelity, hallucination filtering, and reward defense</p>
            </div>
          </div>
          <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full bg-surface-subtle text-brand-purple flex-shrink-0">
            LLM Judges
          </span>
        </div>

        {coreVerdicts.length === 0 ? (
          <div className="p-8 text-center text-text-muted text-xs font-mono">
            No core judge verdicts recorded for this run.
          </div>
        ) : (
          <div className="space-y-3">
            {coreVerdicts.map((verdict) => {
              const hasOverride = overrideScores[verdict.metric_name] !== undefined;
              const effectivePassed = hasOverride ? overrideScores[verdict.metric_name] : verdict.passed;
              const pct = Math.round(verdict.score * 100);

              return (
                <div
                  key={verdict.metric_name}
                  className={`p-4 rounded-xl border space-y-2.5 transition-all ${
                    effectivePassed
                      ? 'bg-emerald-50/30 border-emerald-200'
                      : 'bg-rose-50/40 border-rose-300'
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2 min-w-0">
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <IonIcon
                        icon={effectivePassed ? checkmarkCircle : closeCircle}
                        className={`text-base flex-shrink-0 ${effectivePassed ? 'text-status-cleared' : 'text-risk-high'}`}
                      />
                      <span className="font-bold text-xs font-mono text-text-primary truncate">
                        {getMetricTitle(verdict.metric_name)}
                      </span>
                    </div>

                    <div className="flex items-center gap-2 flex-shrink-0">
                      <span className="font-mono font-bold text-xs">
                        Score: {pct}%
                      </span>
                      <span
                        className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded-full ${
                          effectivePassed ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'
                        }`}
                      >
                        {effectivePassed ? 'Passed' : 'Failed'}
                      </span>
                    </div>
                  </div>

                  <p className="text-xs text-text-secondary leading-relaxed bg-white p-3 rounded-lg border border-border-subtle font-sans break-words">
                    {verdict.reasoning}
                  </p>

                  {/* Override controls */}
                  <div className="pt-2 border-t border-border-subtle flex items-center justify-between text-xs flex-wrap gap-2">
                    <span className="text-[11px] text-text-muted">
                      {hasOverride ? '👤 Overridden by Human Auditor' : '🤖 Evaluated by LLM Judge'}
                    </span>

                    {onOverrideMetric && (
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => onOverrideMetric(verdict.metric_name, true)}
                          className={`px-2.5 py-1 rounded text-[10px] font-bold cursor-pointer ${
                            effectivePassed
                              ? 'bg-emerald-600 text-white shadow-2xs'
                              : 'bg-canvas text-text-secondary hover:bg-surface-subtle border border-border-subtle'
                          }`}
                        >
                          Mark Pass
                        </button>
                        <button
                          type="button"
                          onClick={() => onOverrideMetric(verdict.metric_name, false)}
                          className={`px-2.5 py-1 rounded text-[10px] font-bold cursor-pointer ${
                            !effectivePassed
                              ? 'bg-rose-600 text-white shadow-2xs'
                              : 'bg-canvas text-text-secondary hover:bg-surface-subtle border border-border-subtle'
                          }`}
                        >
                          Mark Fail
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
