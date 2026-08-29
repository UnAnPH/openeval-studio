import React, { useEffect, useRef, useState } from 'react';
import {
  IonIcon,
  IonSpinner,
} from '@ionic/react';
import {
  checkmarkCircle,
  closeCircle,
  codeSlashOutline,
  documentTextOutline,
  shieldOutline,
  sparklesOutline,
  terminalOutline,
  timeOutline,
  chevronForwardOutline,
} from 'ionicons/icons';
import { AgentStep, RunRecord } from '../types';

interface LiveTrajectoryProps {
  steps: AgentStep[];
  status: string;
  isStreaming: boolean;
  run?: RunRecord | null;
}

export const LiveTrajectory: React.FC<LiveTrajectoryProps> = ({
  steps,
  status,
  isStreaming,
  run,
}) => {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [expandedFiles, setExpandedFiles] = useState<Record<number, boolean>>({});
  const [showVerifierOutput, setShowVerifierOutput] = useState<boolean>(false);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [steps, isStreaming, run]);

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

  const isCompleted = run && run.status !== 'pending' && run.status !== 'running';

  return (
    <div className="flex flex-col h-full bg-white rounded-2xl border border-border-subtle overflow-hidden shadow-aegis-card">
      {/* Trajectory Header */}
      <div className="flex flex-row items-center justify-between px-5 py-3.5 bg-white border-b border-border-subtle">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-surface-subtle text-brand-purple flex items-center justify-center">
            <IonIcon icon={terminalOutline} className="text-base" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-text-primary tracking-tight font-mono">
              Live Autonomous Trajectory Trace
            </h3>
            <span className="text-[11px] text-text-secondary font-mono">
              {steps.length} turns executed in Docker container
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {isStreaming && (
            <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-purple-50 border border-purple-200 text-[11px] font-bold text-brand-primary">
              <IonSpinner name="dots" className="w-3 h-3 text-brand-primary" />
              <span>Agent Executing</span>
            </div>
          )}
          <span
            className={`text-[10px] uppercase font-mono px-2.5 py-0.5 rounded-full font-bold border ${
              status === 'completed' || run?.passed
                ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                : status === 'cancelled'
                ? 'bg-amber-50 text-amber-700 border-amber-200'
                : status === 'error' || run?.passed === false
                ? 'bg-rose-50 text-rose-700 border-rose-200'
                : 'bg-surface-subtle text-brand-primary border-border-subtle'
            }`}
          >
            {status}
          </span>
        </div>
      </div>

      {/* Execution Phase Stepper */}
      <div className="px-5 py-2.5 bg-canvas border-b border-border-subtle flex items-center justify-between text-[11px] font-mono text-text-secondary overflow-x-auto gap-3">
        <div className="flex items-center gap-1.5 whitespace-nowrap">
          <span className={`w-2 h-2 rounded-full ${isStreaming || steps.length > 0 ? 'bg-status-cleared' : 'bg-slate-300'}`} />
          <span className={steps.length > 0 ? 'text-text-primary font-bold' : ''}>1. Sandbox Init</span>
        </div>
        <span className="text-text-muted">&rarr;</span>
        <div className="flex items-center gap-1.5 whitespace-nowrap">
          <span className={`w-2 h-2 rounded-full ${isStreaming ? 'bg-brand-purple animate-ping' : steps.length > 0 ? 'bg-status-cleared' : 'bg-slate-300'}`} />
          <span className={steps.length > 0 ? 'text-text-primary font-bold' : ''}>2. Autonomous ReAct</span>
        </div>
        <span className="text-text-muted">&rarr;</span>
        <div className="flex items-center gap-1.5 whitespace-nowrap">
          <span className={`w-2 h-2 rounded-full ${run?.reward !== null && run?.reward !== undefined ? 'bg-status-cleared' : 'bg-slate-300'}`} />
          <span className={run?.reward !== null ? 'text-text-primary font-bold' : ''}>3. Pytest Verifier</span>
        </div>
        <span className="text-text-muted">&rarr;</span>
        <div className="flex items-center gap-1.5 whitespace-nowrap">
          <span className={`w-2 h-2 rounded-full ${run?.audit_verdicts && run.audit_verdicts.length > 0 ? 'bg-status-cleared' : 'bg-slate-300'}`} />
          <span className={run?.audit_verdicts?.length ? 'text-text-primary font-bold' : ''}>4. LLM Judge Audits</span>
        </div>
      </div>

      {/* Trajectory Timeline */}
      <div className="flex-1 overflow-y-auto p-5 space-y-4 font-sans bg-canvas/40">
        {steps.length === 0 && !isStreaming && (
          <div className="flex flex-col items-center justify-center h-64 text-center text-text-muted">
            <IonIcon icon={terminalOutline} className="text-4xl mb-2 opacity-40 text-text-muted" />
            <p className="text-sm font-bold text-text-primary">No active evaluation run.</p>
            <p className="text-xs text-text-secondary mt-1">Select a task & model, then click "Launch Evaluation Run".</p>
          </div>
        )}

        {steps.map((step) => {
          const isFinishTool = step.action.tool === 'finish';
          const isFileEdit = step.action.tool === 'write_file';

          return (
            <div
              key={step.step_number}
              className={`p-4 rounded-2xl bg-white border border-border-subtle shadow-sm space-y-3 transition-all ${
                isFinishTool ? 'border-brand-purple ring-1 ring-brand-purple/20' : ''
              }`}
            >
              {/* Step Header */}
              <div className="flex items-center justify-between text-xs pb-2 border-b border-border-subtle">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-bold text-brand-purple px-2 py-0.5 rounded-full bg-surface-subtle border border-border-subtle">
                    Turn #{step.step_number}
                  </span>
                  <span className="font-mono text-xs font-bold text-text-primary flex items-center gap-1">
                    <IonIcon icon={getToolIcon(step.action.tool)} className="text-accent-orange" />
                    {step.action.tool === 'finish' ? 'Resolution Submitted' : step.action.tool}
                  </span>
                </div>

                <div className="flex items-center gap-3 text-[11px] font-mono text-text-muted">
                  <span className="flex items-center gap-1">
                    <IonIcon icon={timeOutline} className="text-text-muted text-xs" />
                    {step.latency_ms}ms
                  </span>
                  <span className="flex items-center gap-1">
                    <IonIcon icon={sparklesOutline} className="text-brand-purple text-xs" />
                    {step.tokens_used} tok
                  </span>
                </div>
              </div>

              {/* Safety Firewall Interception Badge */}
              {(step.firewall_blocked || step.action.firewall_blocked || step.observation?.toLowerCase().includes('blocked by safety firewall')) && (
                <div className="p-3 rounded-xl bg-rose-50 border border-rose-300 text-xs text-rose-900 space-y-1 animate-pulse">
                  <div className="flex items-center gap-1.5 font-bold font-mono text-rose-700">
                    <IonIcon icon={shieldOutline} className="text-rose-600 text-sm" />
                    <span>⚠️ BLOCKED / REJECTED by Safety Firewall</span>
                  </div>
                  <p className="text-[11px] text-rose-800 leading-tight">
                    {step.firewall_reason || step.action.firewall_reason || 'Policy Violation: Unauthorized network call, privilege escalation, or forbidden system mutation intercepted.'}
                  </p>
                </div>
              )}

              {/* Thought / Inner Reasoning */}
              <div className="space-y-1">
                <div className="text-[10px] uppercase font-mono tracking-wider text-text-muted font-bold flex items-center gap-1">
                  <IonIcon icon={chevronForwardOutline} className="text-brand-purple text-xs" />
                  Agent Reasoning & Plan
                </div>
                <p className="text-xs text-text-secondary leading-relaxed bg-canvas p-3 rounded-xl border border-border-subtle">
                  {step.thought}
                </p>
              </div>

              {/* Action: Bash Command */}
              {step.action.command && (
                <div className="space-y-1">
                  <div className="text-[10px] uppercase font-mono tracking-wider text-accent-orange font-bold flex items-center gap-1">
                    <IonIcon icon={terminalOutline} className="text-xs" />
                    Executed Bash Command
                  </div>
                  <pre className="text-xs font-mono bg-[#14121F] p-3 rounded-xl text-emerald-400 overflow-x-auto">
                    <span className="text-slate-500 select-none">$ </span>
                    {step.action.command}
                  </pre>
                </div>
              )}

              {/* Action: Write File */}
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
                      {expandedFiles[step.step_number] ? 'Hide Payload' : 'Show Payload'}
                    </button>
                  </div>
                  {expandedFiles[step.step_number] && step.action.content && (
                    <pre className="p-3 rounded-xl bg-[#14121F] text-slate-200 font-mono text-xs overflow-x-auto max-h-48 whitespace-pre-wrap">
                      {step.action.content}
                    </pre>
                  )}
                </div>
              )}

              {/* Action: Final Resolution / Summary */}
              {isFinishTool && (
                <div className="p-3.5 rounded-xl bg-surface-subtle border border-border-subtle space-y-1">
                  <div className="flex items-center gap-1.5 text-xs font-bold text-brand-purple uppercase tracking-wider font-mono">
                    <IonIcon icon={checkmarkCircle} className="text-status-cleared text-sm" />
                    Agent Final Summary & Solution
                  </div>
                  <p className="text-xs text-text-secondary leading-relaxed font-sans whitespace-pre-wrap pl-2 border-l-2 border-brand-purple">
                    {step.action.summary || step.thought || 'Task reported completed.'}
                  </p>
                </div>
              )}

              {/* Observation Terminal Block */}
              {step.observation && (
                <div className="space-y-1 pt-1">
                  <div className="text-[10px] uppercase font-mono tracking-wider text-text-muted font-bold flex items-center gap-1">
                    <IonIcon icon={terminalOutline} className="text-xs" />
                    Container Observation (Stdout / Stderr)
                  </div>
                  <pre className="bg-[#14121F] rounded-xl p-3 text-[11px] font-mono text-slate-300 max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed">
                    {step.observation}
                  </pre>
                </div>
              )}
            </div>
          );
        })}

        {/* Live Running Indicator */}
        {isStreaming && (
          <div className="p-4 rounded-xl bg-purple-50 border border-dashed border-purple-300 flex items-center gap-3 text-xs text-brand-primary animate-pulse font-mono">
            <IonSpinner name="crescent" color="primary" className="w-5 h-5" />
            <span>Agent is executing tools inside isolated Docker container...</span>
          </div>
        )}

        {/* Benchmark Finished Summary Card */}
        {isCompleted && (
          <div className="p-5 rounded-2xl bg-white border border-border-subtle shadow-aegis-card space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div
                  className={`w-10 h-10 rounded-xl flex items-center justify-center ${
                    run.passed ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
                  }`}
                >
                  <IonIcon icon={run.passed ? checkmarkCircle : closeCircle} className="text-2xl" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-text-primary tracking-tight">
                    {run.passed ? 'Benchmark Evaluation Passed! 🎉' : 'Benchmark Evaluation Failed ❌'}
                  </h3>
                  <p className="text-xs text-text-secondary">
                    {run.passed
                      ? 'The agent met all verification test criteria in the held-out test suite.'
                      : run.failure_reason || 'Solution failed to satisfy the ground-truth verifier tests.'}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2 font-mono text-xs">
                <span className="px-2 py-1 rounded bg-canvas text-text-secondary border border-border-subtle">
                  {run.total_duration_sec.toFixed(1)}s
                </span>
                <span className="px-2 py-1 rounded bg-canvas text-text-secondary border border-border-subtle">
                  {run.total_tokens.toLocaleString()} tok
                </span>
                <span className="px-2 py-1 rounded bg-canvas text-emerald-700 font-bold border border-border-subtle">
                  ${run.estimated_cost_usd.toFixed(4)}
                </span>
              </div>
            </div>

            {/* Toggle Verifier Output */}
            {run.failure_reason && (
              <div className="pt-2 border-t border-border-subtle space-y-2">
                <button
                  type="button"
                  onClick={() => setShowVerifierOutput((prev) => !prev)}
                  className="text-xs font-mono text-brand-purple hover:underline"
                >
                  {showVerifierOutput ? 'Hide Verifier Diagnostics' : 'View Verifier Diagnostics'}
                </button>

                {showVerifierOutput && (
                  <pre className="p-3 rounded-xl bg-[#14121F] text-[11px] font-mono text-rose-300 whitespace-pre-wrap">
                    {run.failure_reason}
                  </pre>
                )}
              </div>
            )}
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
};

