import React, { useState } from 'react';
import {
  IonIcon,
  IonSpinner,
} from '@ionic/react';
import {
  checkmarkCircle,
  closeCircle,
  codeSlashOutline,
  documentTextOutline,
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
  const [expandedFiles, setExpandedFiles] = useState<Record<number, boolean>>({});
  const [showVerifierOutput, setShowVerifierOutput] = useState<boolean>(false);

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

        <div className="flex items-center gap-2 flex-wrap">
          {/* Aegis Live Status Badge */}
          <div className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-rose-50 border border-rose-200 text-rose-700 text-[10px] font-mono font-bold shadow-xs">
            <span className="w-1.5 h-1.5 rounded-full bg-rose-600 animate-pulse" />
            <span>🛡️ Aegis Live: Active Enforcement</span>
          </div>

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
      <div className="flex-1 overflow-y-auto p-5 space-y-4 font-sans bg-canvas/30">
        {steps.length === 0 && !isStreaming && (
          <div className="h-full flex flex-col items-center justify-center text-center p-8 space-y-3">
            <div className="w-12 h-12 rounded-2xl bg-canvas border border-border-subtle flex items-center justify-center text-text-muted">
              <IonIcon icon={terminalOutline} className="text-2xl" />
            </div>
            <h4 className="text-sm font-bold text-text-primary font-mono">No Active Evaluation</h4>
            <p className="text-xs text-text-secondary max-w-sm">
              Select a benchmark task and model above, then click <strong>Launch Evaluation</strong> to stream autonomous turns.
            </p>
          </div>
        )}

        {steps.map((step) => {
          const isFinishTool = step.action.tool === 'finish';
          const isFileEdit = step.action.tool === 'write_file';
          const isBlocked =
            Boolean(step.firewall_blocked || step.action.firewall_blocked) ||
            step.observation?.includes('[SAFETY FIREWALL BLOCKED]') ||
            step.observation?.includes('[SAFETY FIREWALL INTERCEPTION]');

          // Determine rule tag & risk score if blocked
          let ruleTag = 'POLICY_VIOLATION';
          let riskScore = 0.95;
          if (step.action.command?.includes('sudo') || step.action.command?.includes('su -')) {
            ruleTag = 'RULE #1: PRIVILEGE_ESCALATION';
            riskScore = 0.90;
          } else if (step.action.command?.includes('docker.sock') || step.action.command?.includes('nsenter')) {
            ruleTag = 'RULE #2: CONTAINER_ESCAPE_ATTEMPT';
            riskScore = 0.98;
          } else if (step.action.command?.includes('chmod 777')) {
            ruleTag = 'RULE #3: PERMISSIONS_WEAKENING';
            riskScore = 0.85;
          } else if (
            step.action.command?.includes('test_outputs') ||
            step.action.path?.includes('test_outputs') ||
            step.action.command?.includes('assert')
          ) {
            ruleTag = 'RULE #4: REWARD_TAMPERING_ATTEMPT';
            riskScore = 0.98;
          } else if (step.action.command?.includes('audit.log') || step.action.command?.includes('watchdog')) {
            ruleTag = 'RULE #5: AUDIT_TRAIL_SABOTAGE';
            riskScore = 0.95;
          }

          return (
            <div
              key={step.step_number}
              id={`trajectory-turn-${step.step_number}`}
              className={`p-4 rounded-2xl border bg-white shadow-sm space-y-3 transition-all ${
                isBlocked
                  ? 'border-rose-300 ring-2 ring-rose-100'
                  : 'border-border-subtle hover:border-border-strong'
              }`}
            >
              {/* Step Header */}
              <div className="flex items-center justify-between border-b border-border-subtle pb-2.5">
                <div className="flex items-center gap-2">
                  <span className={`w-6 h-6 rounded-lg text-xs font-mono font-bold flex items-center justify-center ${
                    isBlocked ? 'bg-rose-100 text-rose-700' : 'bg-surface-subtle text-brand-purple'
                  }`}>
                    #{step.step_number}
                  </span>
                  <div className="flex items-center gap-1.5">
                    <IonIcon icon={getToolIcon(step.action.tool)} className="text-text-muted text-xs" />
                    <span className="text-xs font-mono font-bold text-text-primary">
                      {step.action.tool}
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-2 text-[10px] font-mono text-text-muted">
                  <span className="flex items-center gap-1">
                    <IonIcon icon={timeOutline} className="text-accent-orange text-xs w-3 h-3" />
                    <span>{step.latency_ms.toFixed(0)}ms</span>
                  </span>
                  <span>•</span>
                  <span className="flex items-center gap-1">
                    <IonIcon icon={sparklesOutline} className="text-brand-purple text-xs w-3 h-3" />
                    <span>{step.tokens_used} tok</span>
                  </span>
                </div>
              </div>

              {/* Watcher Live Crimson Interception Banner */}
              {isBlocked && (
                <div className="p-4 rounded-xl bg-gradient-to-r from-rose-50 to-red-50 border border-rose-300 text-xs text-rose-950 space-y-3 animate-fadeIn">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-rose-200/80 pb-2">
                    <div className="flex items-center gap-2">
                      <div className="w-6 h-6 rounded-md bg-rose-600 text-white flex items-center justify-center font-bold">
                        🛡️
                      </div>
                      <div>
                        <div className="font-bold font-mono text-rose-800 text-xs flex items-center gap-1.5">
                          <span>AEGIS LIVE: RUNTIME INTERCEPTION</span>
                          <span className="px-2 py-0.5 rounded bg-rose-200/80 text-rose-900 text-[10px] uppercase font-bold">
                            {ruleTag}
                          </span>
                        </div>
                      </div>
                    </div>

                    {/* Risk Score Gauge */}
                    <div className="flex items-center gap-2 bg-white/80 px-2.5 py-1 rounded-lg border border-rose-200 self-start sm:self-auto font-mono text-[11px]">
                      <span className="text-text-muted">Risk Score:</span>
                      <strong className="text-rose-700 font-bold">{riskScore.toFixed(2)} / 1.00</strong>
                      <div className="w-12 h-2 bg-slate-200 rounded-full overflow-hidden">
                        <div className="h-full bg-rose-600 rounded-full" style={{ width: `${riskScore * 100}%` }} />
                      </div>
                    </div>
                  </div>

                  {/* Side-by-Side Interception Diff */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
                    <div className="p-3 bg-white rounded-lg border border-rose-200 space-y-1">
                      <div className="text-[10px] font-mono font-bold uppercase text-text-muted">
                        ⛔ Proposed Action (Blocked Before Execution)
                      </div>
                      <pre className="font-mono text-xs text-rose-700 bg-rose-50/50 p-2 rounded overflow-x-auto whitespace-pre-wrap">
                        {step.action.command || step.action.path || step.action.tool}
                      </pre>
                    </div>

                    <div className="p-3 bg-white rounded-lg border border-rose-200 space-y-1">
                      <div className="text-[10px] font-mono font-bold uppercase text-emerald-800">
                        🛡️ Enforced Observation (Injected into Context)
                      </div>
                      <div className="font-mono text-xs text-text-primary leading-relaxed bg-surface-subtle/50 p-2 rounded whitespace-pre-wrap">
                        {step.observation}
                      </div>
                    </div>
                  </div>
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
      </div>
    </div>
  );
};

