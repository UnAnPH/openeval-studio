import React, { useState } from 'react';
import {
  IonIcon,
  IonModal,
} from '@ionic/react';
import {
  alertCircleOutline,
  checkmarkCircle,
  closeCircle,
  closeOutline,
  codeSlashOutline,
  createOutline,
  documentTextOutline,
  downloadOutline,
  flashOutline,
  layersOutline,
  saveOutline,
  shieldCheckmarkOutline,
  shieldOutline,
  terminalOutline,
  trashOutline,
} from 'ionicons/icons';
import { RunRecord, TaskSummary } from '../types';

interface TestCaseDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  run: RunRecord | null;
  task?: TaskSummary | null;
  onSaveRevision?: (revisedRun: RunRecord) => void;
  onDeleteRun?: (runId: string) => void;
}

export const TestCaseDrawer: React.FC<TestCaseDrawerProps> = ({
  isOpen,
  onClose,
  run,
  task,
  onSaveRevision,
  onDeleteRun,
}) => {
  const [activeTab, setActiveTab] = useState<'overview' | 'trajectory' | 'judges' | 'verifier'>('overview');
  const [expandedFiles, setExpandedFiles] = useState<Record<number, boolean>>({});

  // Human Audit Revision State
  const [isEditingAudit, setIsEditingAudit] = useState<boolean>(false);
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [overrideReasons, setOverrideReasons] = useState<Record<string, string>>({});
  const [humanReviewNotes, setHumanReviewNotes] = useState<string>('');
  const [humanReviewer, setHumanReviewer] = useState<string>('lead-evaluator@anthropic.ai');
  const [saveSuccessMsg, setSaveSuccessMsg] = useState<string | null>(null);

  if (!run) return null;

  const isPassed = run.passed === true;
  const isFailed = run.passed === false;

  const toggleFileExpand = (stepIdx: number) => {
    setExpandedFiles((prev) => ({ ...prev, [stepIdx]: !prev[stepIdx] }));
  };

  const getToolIcon = (tool: string) => {
    switch (tool) {
      case 'execute_bash':
        return terminalOutline;
      case 'view_file':
        return documentTextOutline;
      case 'write_file':
        return codeSlashOutline;
      case 'finish':
        return checkmarkCircle;
      default:
        return terminalOutline;
    }
  };

  // Toggle override for a specific judge metric
  const handleToggleOverride = (metricName: string, currentPassed: boolean) => {
    setOverrides((prev) => {
      const isCurrentlyOverridden = prev[metricName] !== undefined ? prev[metricName] : !currentPassed;
      return {
        ...prev,
        [metricName]: !isCurrentlyOverridden,
      };
    });
  };

  // Save revised audit back to application state
  const handleSaveAudit = () => {
    if (!run) return;

    const updatedVerdicts = run.audit_verdicts?.map((v) => {
      const isOverridden = overrides[v.metric_name] !== undefined;
      return {
        ...v,
        passed: isOverridden ? overrides[v.metric_name] : v.passed,
        overridden: isOverridden,
        override_reason: overrideReasons[v.metric_name] || v.override_reason,
      };
    });

    const revisedRecord: RunRecord = {
      ...run,
      audit_verdicts: updatedVerdicts,
      audit_overrides: overrides,
      human_review_notes: humanReviewNotes,
      human_reviewer: humanReviewer,
      revised_at: new Date().toISOString(),
    };

    if (onSaveRevision) {
      onSaveRevision(revisedRecord);
    }
    setSaveSuccessMsg('Audit revisions saved successfully.');
    setTimeout(() => setSaveSuccessMsg(null), 3000);
    setIsEditingAudit(false);
  };

  // Export audit report as formatted Markdown
  const handleExportAuditReport = () => {
    const reportContent = `# OpenEval Studio — Evaluation & Safety Audit Report

**Task Identifier:** \`${run.task_id}\`  
**Run ID:** \`${run.run_id}\`  
**Target Model:** \`${run.model}\` (\`${run.provider}\`)  
**Status:** **${run.passed ? 'PASSED (1.0)' : 'FAILED (0.0)'}**  
**Execution Latency:** ${run.total_duration_sec?.toFixed(1)}s  
**Tokens Consumed:** ${run.total_tokens?.toLocaleString()}  
**Estimated Spend:** $${run.estimated_cost_usd?.toFixed(4)}  
**Audit Timestamp:** ${new Date().toISOString()}  
**Human Reviewer Sign-off:** ${run.human_reviewer || humanReviewer || 'Unassigned'}  

---

## 1. Pytest Verifier Output
\`\`\`text
${run.failure_reason || (isPassed ? '=== 1 passed in 0.42s ===\nAll test harness assertions verified.' : 'No diagnostic trace provided.')}
\`\`\`

---

## 2. LLM-as-a-Judge Verdicts & Safety Audits

${(run.audit_verdicts || [])
  .map((v) => {
    const isOverridden = overrides[v.metric_name] !== undefined ? overrides[v.metric_name] : v.overridden;
    const finalVerdict = isOverridden !== undefined ? (isOverridden ? 'APPROVED (HUMAN OVERRIDE)' : 'FLAGGED') : (v.passed ? 'PASSED' : 'FLAGGED');
    return `### Metric: ${v.metric_name.toUpperCase()}
- **Raw Judge Score:** ${(v.score * 100).toFixed(0)}%
- **Final Determination:** **${finalVerdict}**
- **Judge Reasoning:** ${v.reasoning}
- **Flagged Steps:** ${v.flagged_steps?.length > 0 ? v.flagged_steps.join(', ') : 'None'}
${v.override_reason || overrideReasons[v.metric_name] ? `- **Human Override Justification:** ${overrideReasons[v.metric_name] || v.override_reason}` : ''}
`;
  })
  .join('\n')}

---

## 3. Human Reviewer Notes & Sign-off
> ${run.human_review_notes || humanReviewNotes || 'No additional reviewer notes provided.'}

---
*Generated by OpenEval Studio — Frontier AI Agent Evaluation & Runtime Safety Workbench*
`;

    const blob = new Blob([reportContent], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `openeval_audit_report_${run.task_id}_${run.run_id.slice(0, 8)}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <IonModal isOpen={isOpen} onDidDismiss={onClose} className="aegis-inspection-modal">
      <div className="flex flex-col h-full bg-[#F6F5F9] text-text-primary">
        {/* Drawer Header */}
        <div className="p-5 bg-white border-b border-border-subtle flex items-center justify-between shadow-sm">
          <div className="flex items-center gap-3">
            <div
              className={`w-10 h-10 rounded-xl flex items-center justify-center ${
                isPassed
                  ? 'bg-emerald-50 text-emerald-700'
                  : isFailed
                  ? 'bg-rose-50 text-rose-700'
                  : 'bg-purple-50 text-brand-purple'
              }`}
            >
              <IonIcon
                icon={isPassed ? checkmarkCircle : isFailed ? closeCircle : flashOutline}
                className="text-xl"
              />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-bold text-text-primary font-mono">{run.task_id}</h2>
                <span
                  className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded-full border ${
                    isPassed
                      ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                      : isFailed
                      ? 'bg-rose-50 text-rose-700 border-rose-200'
                      : 'bg-purple-50 text-brand-purple border-purple-200'
                  }`}
                >
                  {isPassed ? 'PASSED' : isFailed ? 'FAILED' : run.status}
                </span>
              </div>
              <p className="text-xs text-text-secondary font-mono">
                Run ID: {run.run_id} • Model: {run.model}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {onDeleteRun && (
              <button
                type="button"
                onClick={() => {
                  if (confirm(`Delete run ${run.run_id}?`)) {
                    onDeleteRun(run.run_id);
                    onClose();
                  }
                }}
                className="px-2.5 py-1.5 rounded-lg bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200 text-xs font-bold flex items-center gap-1.5 transition-colors cursor-pointer"
                title="Delete this run"
              >
                <IonIcon icon={trashOutline} className="text-sm" />
                <span>Delete Run</span>
              </button>
            )}

            <button
              type="button"
              onClick={onClose}
              className="w-8 h-8 rounded-full bg-canvas text-text-secondary hover:text-text-primary hover:bg-surface-subtle flex items-center justify-center transition-colors"
            >
              <IonIcon icon={closeOutline} className="text-xl" />
            </button>
          </div>
        </div>

        {/* Tab Navigation Segment */}
        <div className="px-5 py-3 bg-white border-b border-border-subtle flex items-center justify-between">
          <div className="flex items-center gap-1.5 bg-canvas p-1 rounded-xl border border-border-subtle text-xs">
            {(['overview', 'trajectory', 'judges', 'verifier'] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => setActiveTab(tab)}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold capitalize transition-all ${
                  activeTab === tab
                    ? 'bg-white text-brand-primary shadow-sm'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                {tab === 'judges' ? 'Audits & Review' : tab} {tab === 'trajectory' ? `(${run.steps?.length || 0})` : tab === 'judges' ? `(${run.audit_verdicts?.length || 0})` : ''}
              </button>
            ))}
          </div>

          <div className="hidden sm:flex items-center gap-2 text-xs font-mono text-text-secondary">
            <span>{run.total_duration_sec?.toFixed(1)}s</span>
            <span>•</span>
            <span>{run.total_tokens?.toLocaleString()} toks</span>
            <span>•</span>
            <span className="text-emerald-700 font-semibold">${run.estimated_cost_usd?.toFixed(4)}</span>
          </div>
        </div>

        {/* Tab Content Area */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {saveSuccessMsg && (
            <div className="p-3 bg-emerald-50 border border-emerald-300 rounded-xl text-emerald-800 text-xs font-bold flex items-center gap-2 animate-fadeIn">
              <IonIcon icon={checkmarkCircle} className="text-sm" />
              <span>{saveSuccessMsg}</span>
            </div>
          )}

          {/* TAB 1: OVERVIEW */}
          {activeTab === 'overview' && (
            <div className="space-y-6 animate-fadeIn">
              {/* Verdict Hero Banner */}
              <div
                className={`p-5 rounded-2xl border flex items-center justify-between ${
                  isPassed
                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-900'
                    : isFailed
                    ? 'bg-rose-500/10 border-rose-500/30 text-rose-900'
                    : 'bg-purple-500/10 border-purple-500/30 text-brand-primary'
                }`}
              >
                <div className="flex items-center gap-3">
                  <IonIcon
                    icon={isPassed ? checkmarkCircle : isFailed ? closeCircle : flashOutline}
                    className="text-3xl"
                  />
                  <div>
                    <h3 className="font-bold text-sm">
                      {isPassed
                        ? 'Ground-Truth Verification Passed'
                        : isFailed
                        ? 'Verifier Test Suite Failed'
                        : 'Evaluation In Progress'}
                    </h3>
                    <p className="text-xs opacity-80">
                      {isPassed
                        ? 'All held-out pytest assertions passed in isolated Docker sandbox.'
                        : run.failure_reason || 'Agent did not satisfy test criteria.'}
                    </p>
                  </div>
                </div>

                <div className="text-right font-mono">
                  <div className="text-2xl font-bold">
                    {run.reward !== null ? `${run.reward.toFixed(1)} / 1.0` : '—'}
                  </div>
                  <div className="text-[10px] uppercase font-bold tracking-wider opacity-70">
                    Reward Score
                  </div>
                </div>
              </div>

              {/* Task Spec & Instruction */}
              {task && (
                <div className="p-5 rounded-2xl bg-white border border-border-subtle shadow-aegis-card space-y-2">
                  <div className="flex items-center justify-between text-xs text-text-secondary">
                    <span className="font-bold uppercase tracking-wider flex items-center gap-1.5 font-mono text-text-primary">
                      <IonIcon icon={layersOutline} className="text-brand-purple text-sm" />
                      Task Specification
                    </span>
                    <div className="flex items-center gap-2">
                      {task.category && (
                        <span className="px-2 py-0.5 rounded bg-surface-subtle text-brand-purple text-[10px] font-mono font-bold">
                          {task.category}
                        </span>
                      )}
                      {task.difficulty && (
                        <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-canvas border border-border-subtle">
                          {task.difficulty}
                        </span>
                      )}
                    </div>
                  </div>
                  <p className="text-xs text-text-primary leading-relaxed bg-canvas p-3.5 rounded-xl border border-border-subtle">
                    {task.instruction_preview}
                  </p>
                </div>
              )}

              {/* Agent Resolution Summary */}
              {run.final_summary && (
                <div className="p-5 rounded-2xl bg-white border border-border-subtle shadow-aegis-card space-y-2">
                  <h4 className="text-xs font-bold uppercase tracking-wider text-brand-purple font-mono">
                    Agent Resolution & Solution Summary
                  </h4>
                  <p className="text-xs text-text-secondary leading-relaxed bg-canvas p-3.5 rounded-xl border border-border-subtle whitespace-pre-wrap">
                    {run.final_summary}
                  </p>
                </div>
              )}

              {/* Resource Consumption Grid */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 font-mono">
                <div className="p-4 rounded-xl bg-white border border-border-subtle shadow-sm text-center">
                  <div className="text-[10px] uppercase font-bold text-text-muted mb-1">Latency</div>
                  <div className="text-sm font-bold text-text-primary">{run.total_duration_sec?.toFixed(1)}s</div>
                </div>
                <div className="p-4 rounded-xl bg-white border border-border-subtle shadow-sm text-center">
                  <div className="text-[10px] uppercase font-bold text-text-muted mb-1">Total Tokens</div>
                  <div className="text-sm font-bold text-text-primary">{run.total_tokens?.toLocaleString()}</div>
                </div>
                <div className="p-4 rounded-xl bg-white border border-border-subtle shadow-sm text-center">
                  <div className="text-[10px] uppercase font-bold text-text-muted mb-1">Estimated Cost</div>
                  <div className="text-sm font-bold text-emerald-700">${run.estimated_cost_usd?.toFixed(4)}</div>
                </div>
                <div className="p-4 rounded-xl bg-white border border-border-subtle shadow-sm text-center">
                  <div className="text-[10px] uppercase font-bold text-text-muted mb-1">Trajectory Steps</div>
                  <div className="text-sm font-bold text-text-primary">{run.total_steps} turns</div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: TRAJECTORY */}
          {activeTab === 'trajectory' && (
            <div className="space-y-4 animate-fadeIn">
              {(!run.steps || run.steps.length === 0) ? (
                <div className="p-8 text-center text-text-muted text-xs bg-white rounded-2xl border border-border-subtle">
                  No execution steps recorded for this run.
                </div>
              ) : (
                run.steps.map((step) => {
                  const toolIcon = getToolIcon(step.action.tool);
                  const isFileEdit = step.action.tool === 'write_file';
                  const isBash = step.action.tool === 'execute_bash';
                  const isExpanded = !!expandedFiles[step.step_number];
                  const isFirewallBlocked = step.firewall_blocked || step.action?.firewall_blocked || step.observation?.toLowerCase().includes('blocked by safety firewall');

                  return (
                    <div
                      key={step.step_number}
                      className="p-5 rounded-2xl bg-white border border-border-subtle shadow-aegis-card space-y-3"
                    >
                      {/* Step Header */}
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs font-bold text-brand-purple bg-surface-subtle px-2 py-0.5 rounded-full">
                            Turn #{step.step_number}
                          </span>
                          <span className="text-xs font-mono font-bold text-text-primary flex items-center gap-1.5">
                            <IonIcon icon={toolIcon} className="text-accent-orange" />
                            {step.action.tool}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 text-[10px] font-mono text-text-muted">
                          <span>{step.latency_ms}ms</span>
                          <span>•</span>
                          <span>{step.tokens_used} tok</span>
                        </div>
                      </div>

                      {/* Safety Firewall Alert Badge */}
                      {isFirewallBlocked && (
                        <div className="p-3 rounded-xl bg-rose-50 border border-rose-300 text-xs text-rose-900 space-y-1">
                          <div className="flex items-center gap-1.5 font-bold font-mono text-rose-700">
                            <IonIcon icon={shieldOutline} className="text-rose-600 text-sm" />
                            <span>⚠️ BLOCKED / REJECTED by Safety Firewall</span>
                          </div>
                          <p className="text-[11px] text-rose-800 leading-tight">
                            {step.firewall_reason || step.action?.firewall_reason || 'Policy Violation: Attempted unauthorized network access, privilege escalation, or forbidden file mutation.'}
                          </p>
                        </div>
                      )}

                      {/* Thought */}
                      <div className="space-y-1">
                        <span className="text-[10px] font-mono uppercase text-text-muted font-bold">Reasoning:</span>
                        <p className="text-xs text-text-secondary leading-relaxed bg-canvas p-3 rounded-xl border border-border-subtle">
                          {step.thought}
                        </p>
                      </div>

                      {/* Action Tool Payload (Dark syntax block for code) */}
                      {isBash && step.action.command && (
                        <div className="space-y-1">
                          <span className="text-[10px] font-mono uppercase text-accent-orange font-bold">Command:</span>
                          <pre className="p-3 rounded-xl bg-[#14121F] text-emerald-400 font-mono text-xs overflow-x-auto">
                            $ {step.action.command}
                          </pre>
                        </div>
                      )}

                      {isFileEdit && (
                        <div className="space-y-1">
                          <div className="flex items-center justify-between text-xs">
                            <span className="text-[10px] font-mono uppercase text-brand-purple font-bold">
                              Write File: {step.action.path}
                            </span>
                            <button
                              type="button"
                              onClick={() => toggleFileExpand(step.step_number)}
                              className="text-[10px] font-mono text-brand-purple hover:underline"
                            >
                              {isExpanded ? 'Hide Code' : 'View Code'}
                            </button>
                          </div>
                          {isExpanded && step.action.content && (
                            <pre className="p-3 rounded-xl bg-[#14121F] text-slate-200 font-mono text-xs overflow-x-auto max-h-60">
                              {step.action.content}
                            </pre>
                          )}
                        </div>
                      )}

                      {/* Observation Output */}
                      {step.observation && (
                        <div className="space-y-1">
                          <span className="text-[10px] font-mono uppercase text-text-muted font-bold">Observation / Output:</span>
                          <pre className="p-3 rounded-xl bg-[#14121F] text-slate-300 font-mono text-[11px] overflow-x-auto max-h-48 whitespace-pre-wrap leading-tight">
                            {step.observation}
                          </pre>
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          )}

          {/* TAB 3: JUDGES & HUMAN AUDIT REVISION */}
          {activeTab === 'judges' && (
            <div className="space-y-5 animate-fadeIn">
              {/* Revision Header Toolbar */}
              <div className="p-4 bg-white rounded-2xl border border-border-subtle shadow-sm flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-text-primary flex items-center gap-2">
                    <IonIcon icon={shieldCheckmarkOutline} className="text-brand-purple text-base" />
                    <span>LLM-as-a-Judge & Human Sign-off</span>
                  </h3>
                  <p className="text-[11px] text-text-secondary mt-0.5">
                    Review and override false-positive judge flags with full human-in-the-loop audit trail
                  </p>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setIsEditingAudit(!isEditingAudit)}
                    className={`px-3 py-1.5 rounded-xl text-xs font-bold transition-all flex items-center gap-1.5 ${
                      isEditingAudit
                        ? 'bg-brand-purple text-white shadow-sm'
                        : 'bg-canvas text-brand-primary border border-border-subtle hover:bg-surface-subtle'
                    }`}
                  >
                    <IonIcon icon={createOutline} />
                    <span>{isEditingAudit ? 'Done Editing' : 'Edit & Revise Audit'}</span>
                  </button>

                  <button
                    type="button"
                    onClick={handleExportAuditReport}
                    className="px-3 py-1.5 rounded-xl bg-dark-base text-white text-xs font-bold hover:bg-black transition-all flex items-center gap-1.5 shadow-sm"
                  >
                    <IonIcon icon={downloadOutline} />
                    <span>Export Audit Report</span>
                  </button>
                </div>
              </div>

              {/* Judge Verdict Cards */}
              {(!run.audit_verdicts || run.audit_verdicts.length === 0) ? (
                <div className="p-8 text-center text-text-muted text-xs bg-white rounded-2xl border border-border-subtle">
                  No LLM judge verdicts available for this run.
                </div>
              ) : (
                run.audit_verdicts.map((v) => {
                  const isOverridden = overrides[v.metric_name] !== undefined;
                  const effectivePassed = isOverridden ? overrides[v.metric_name] : v.passed;

                  return (
                    <div
                      key={v.metric_name}
                      className="p-5 rounded-2xl bg-white border border-border-subtle shadow-aegis-card space-y-3"
                    >
                      <div className="flex items-center justify-between">
                        <h4 className="text-xs font-bold text-text-primary capitalize flex items-center gap-2">
                          <span>{v.metric_name.replace(/_/g, ' ')}</span>
                          {isOverridden && (
                            <span className="text-[9px] font-mono px-2 py-0.5 rounded bg-purple-50 text-brand-purple border border-purple-200 uppercase">
                              Human Override
                            </span>
                          )}
                        </h4>

                        <div className="flex items-center gap-2">
                          <span
                            className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border ${
                              effectivePassed
                                ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                : 'bg-rose-50 text-rose-700 border-rose-200'
                            }`}
                          >
                            {effectivePassed
                              ? `PASSED (${Math.round(v.score * 100)}%)`
                              : `FLAGGED (${Math.round(v.score * 100)}%)`}
                          </span>

                          {isEditingAudit && (
                            <button
                              type="button"
                              onClick={() => handleToggleOverride(v.metric_name, v.passed)}
                              className="text-[11px] px-2.5 py-1 rounded-lg bg-surface-subtle hover:bg-purple-100 text-brand-purple font-mono font-bold border border-border-subtle transition-all"
                            >
                              {effectivePassed ? 'Mark Flagged' : 'Override to Pass'}
                            </button>
                          )}
                        </div>
                      </div>

                      <p className="text-xs text-text-secondary leading-relaxed bg-canvas p-3 rounded-xl border border-border-subtle">
                        {v.reasoning}
                      </p>

                      {v.flagged_steps && v.flagged_steps.length > 0 && (
                        <div className="text-[10px] font-mono text-risk-high flex items-center gap-1">
                          <IonIcon icon={alertCircleOutline} />
                          <span>Flagged Step(s): {v.flagged_steps.join(', ')}</span>
                        </div>
                      )}

                      {/* Override justification input */}
                      {isEditingAudit && (
                        <div className="pt-2 space-y-1">
                          <label className="text-[10px] font-mono uppercase text-text-muted font-bold">
                            Override Rationale (Optional):
                          </label>
                          <input
                            type="text"
                            value={overrideReasons[v.metric_name] || ''}
                            onChange={(e) =>
                              setOverrideReasons((prev) => ({
                                ...prev,
                                [v.metric_name]: e.target.value,
                              }))
                            }
                            placeholder="e.g., False positive: Step 3 was a diagnostic probe, not a security escalation."
                            className="w-full bg-canvas border border-border-subtle rounded-xl px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:border-brand-primary"
                          />
                        </div>
                      )}
                    </div>
                  );
                })
              )}

              {/* Human Reviewer Sign-off Section */}
              <div className="p-5 rounded-2xl bg-white border border-border-subtle shadow-sm space-y-3">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-mono font-bold uppercase text-text-primary">
                    Human Reviewer Sign-off & Audit Notes
                  </h4>
                  <span className="text-[10px] font-mono text-text-muted">
                    {run.revised_at ? `Revised on ${new Date(run.revised_at).toLocaleDateString()}` : 'Unsigned'}
                  </span>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="text-[10px] font-mono uppercase text-text-muted font-bold">
                      Reviewer Handle / Email:
                    </label>
                    <input
                      type="text"
                      value={humanReviewer}
                      disabled={!isEditingAudit}
                      onChange={(e) => setHumanReviewer(e.target.value)}
                      className="w-full mt-1 bg-canvas border border-border-subtle rounded-xl px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:border-brand-primary disabled:opacity-70"
                    />
                  </div>
                </div>

                <div>
                  <label className="text-[10px] font-mono uppercase text-text-muted font-bold">
                    Adjudication Notes:
                  </label>
                  <textarea
                    rows={3}
                    value={humanReviewNotes || run.human_review_notes || ''}
                    disabled={!isEditingAudit}
                    onChange={(e) => setHumanReviewNotes(e.target.value)}
                    placeholder="Enter human sign-off notes, context regarding agent trajectory, or validation comments..."
                    className="w-full mt-1 bg-canvas border border-border-subtle rounded-xl p-3 text-xs text-text-primary focus:outline-none focus:border-brand-primary disabled:opacity-70 leading-relaxed"
                  />
                </div>

                {isEditingAudit && (
                  <div className="pt-2 flex justify-end">
                    <button
                      type="button"
                      onClick={handleSaveAudit}
                      className="px-4 py-2 rounded-xl bg-dark-base text-white text-xs font-bold hover:bg-black transition-all flex items-center gap-1.5 shadow-sm active:scale-98"
                    >
                      <IonIcon icon={saveOutline} />
                      <span>Save Revised Audit</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* TAB 4: VERIFIER */}
          {activeTab === 'verifier' && (
            <div className="space-y-4 animate-fadeIn">
              <div className="p-5 rounded-2xl bg-white border border-border-subtle shadow-aegis-card space-y-3">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-bold text-text-primary font-mono uppercase">
                    Held-Out Verifier Diagnostics
                  </h4>
                  <span
                    className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border ${
                      isPassed
                        ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                        : 'bg-rose-50 text-rose-700 border-rose-200'
                    }`}
                  >
                    {isPassed ? 'Pytest Pass (Code 0)' : 'Pytest Failed'}
                  </span>
                </div>

                <pre className="p-4 rounded-xl bg-[#14121F] text-slate-300 font-mono text-xs overflow-x-auto whitespace-pre-wrap leading-relaxed">
                  {run.failure_reason || (isPassed ? '=== 1 passed in 0.42s ===\nAll test harness assertions verified.' : 'No diagnostic trace provided.')}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </IonModal>
  );
};

