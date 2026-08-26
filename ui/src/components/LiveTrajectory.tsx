import React, { useEffect, useRef, useState } from 'react';
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Coins,
  FileCode,
  FileText,
  Sparkles,
  Terminal,
  XCircle,
} from 'lucide-react';
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
        return <Terminal className="w-3.5 h-3.5 text-sky-400" />;
      case 'view_file':
        return <FileText className="w-3.5 h-3.5 text-amber-400" />;
      case 'write_file':
        return <FileCode className="w-3.5 h-3.5 text-emerald-400" />;
      case 'finish':
        return <CheckCircle2 className="w-3.5 h-3.5 text-purple-400" />;
      default:
        return <Terminal className="w-3.5 h-3.5 text-slate-400" />;
    }
  };

  const getToolBadgeColor = (tool: string) => {
    switch (tool) {
      case 'execute_bash':
        return 'bg-sky-500/10 text-sky-400 border-sky-500/30';
      case 'view_file':
        return 'bg-amber-500/10 text-amber-400 border-amber-500/30';
      case 'write_file':
        return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30';
      case 'finish':
        return 'bg-purple-500/15 text-purple-300 border-purple-500/40 shadow-sm shadow-purple-500/20';
      default:
        return 'bg-slate-800 text-slate-400 border-slate-700';
    }
  };

  const isCompleted = run && run.status !== 'pending' && run.status !== 'running';

  return (
    <div className="flex flex-col h-full bg-surface-elevated/40 rounded-2xl border border-border overflow-hidden">
      {/* Trajectory Header */}
      <div className="flex items-center justify-between px-5 py-3.5 bg-surface border-b border-border">
        <div className="flex items-center gap-2.5">
          <Bot className="w-4 h-4 text-primary" />
          <h2 className="text-sm font-bold text-white tracking-tight">ReAct Trajectory Stream</h2>
          <span className="text-xs text-slate-400">({steps.length} turns)</span>
        </div>

        <div className="flex items-center gap-2">
          {isStreaming && (
            <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-sky-500/10 border border-sky-500/30 text-[11px] font-semibold text-primary animate-pulse">
              <span className="w-1.5 h-1.5 rounded-full bg-primary" />
              Agent Active
            </div>
          )}
          <span
            className={`text-xs uppercase font-mono px-2.5 py-0.5 rounded border font-semibold ${
              status === 'completed' || run?.passed
                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                : status === 'cancelled'
                ? 'bg-amber-500/10 text-amber-400 border-amber-500/30'
                : status === 'error' || run?.passed === false
                ? 'bg-rose-500/10 text-rose-400 border-rose-500/30'
                : 'bg-slate-800 text-slate-300 border-slate-700'
            }`}
          >
            {status}
          </span>
        </div>
      </div>

      {/* Trajectory Timeline */}
      <div className="flex-1 overflow-y-auto p-5 space-y-4 font-sans">
        {steps.length === 0 && !isStreaming && (
          <div className="flex flex-col items-center justify-center h-64 text-center text-slate-500">
            <Terminal className="w-8 h-8 mb-2 opacity-50 text-slate-400" />
            <p className="text-sm">No active evaluation run.</p>
            <p className="text-xs text-slate-600 mt-1">Select a task & model, then click "Launch Evaluation Run".</p>
          </div>
        )}

        {steps.map((step) => {
          const isFinishTool = step.action.tool === 'finish';

          return (
            <div
              key={step.step_number}
              className={`p-4 rounded-xl border shadow-sm space-y-3 transition-all ${
                isFinishTool
                  ? 'bg-gradient-to-br from-purple-950/30 via-surface to-surface-elevated border-purple-500/40 ring-1 ring-purple-500/20'
                  : 'bg-surface border-border/90'
              }`}
            >
              {/* Step Header */}
              <div className="flex items-center justify-between text-xs pb-2 border-b border-border/40">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-bold text-primary px-1.5 py-0.5 rounded bg-primary/10 border border-primary/20">
                    Turn {step.step_number}
                  </span>
                  <span
                    className={`flex items-center gap-1 font-mono text-[11px] px-2 py-0.5 rounded border font-semibold ${getToolBadgeColor(
                      step.action.tool
                    )}`}
                  >
                    {getToolIcon(step.action.tool)}
                    {step.action.tool === 'finish' ? 'Resolution Submitted' : step.action.tool}
                  </span>
                </div>

                <div className="flex items-center gap-3 text-[11px] font-mono text-slate-400">
                  <span className="flex items-center gap-1">
                    <Clock className="w-3 h-3 text-slate-500" />
                    {step.latency_ms}ms
                  </span>
                  <span className="flex items-center gap-1">
                    <Sparkles className="w-3 h-3 text-accent" />
                    {step.tokens_used} toks
                  </span>
                </div>
              </div>

              {/* Thought */}
              <div className="space-y-1">
                <div className="text-[11px] uppercase font-bold tracking-wider text-slate-400 flex items-center gap-1">
                  <ChevronRight className="w-3 h-3 text-primary" />
                  Inner Reasoning / Thought
                </div>
                <p className="text-xs text-slate-200 leading-relaxed pl-4 border-l-2 border-primary/40 bg-surface-elevated/30 py-1.5 rounded-r">
                  {step.thought}
                </p>
              </div>

              {/* Action: Bash Command */}
              {step.action.command && (
                <div className="space-y-1">
                  <div className="text-[10px] uppercase font-mono tracking-wider text-sky-400 flex items-center gap-1">
                    <Terminal className="w-3 h-3" />
                    Executed Bash Command
                  </div>
                  <div className="text-xs font-mono bg-black/70 p-3 rounded-lg border border-sky-500/20 text-sky-300 overflow-x-auto">
                    <span className="text-slate-500 select-none">$ </span>
                    {step.action.command}
                  </div>
                </div>
              )}

              {/* Action: View File */}
              {step.action.tool === 'view_file' && step.action.path && (
                <div className="space-y-1">
                  <div className="text-[10px] uppercase font-mono tracking-wider text-amber-400 flex items-center gap-1">
                    <FileText className="w-3 h-3" />
                    Read File Content
                  </div>
                  <div className="text-xs font-mono bg-black/70 p-2.5 rounded-lg border border-amber-500/20 text-amber-300">
                    <span className="text-slate-500 select-none">Target: </span>
                    {step.action.path}
                  </div>
                </div>
              )}

              {/* Action: Write File */}
              {step.action.tool === 'write_file' && (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-[10px] uppercase font-mono tracking-wider text-emerald-400">
                    <span className="flex items-center gap-1">
                      <FileCode className="w-3 h-3" />
                      Write File: <span className="text-emerald-300 font-bold">{step.action.path}</span>
                    </span>
                    {step.action.content && (
                      <button
                        type="button"
                        onClick={() => toggleFileExpand(step.step_number)}
                        className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-white underline"
                      >
                        {expandedFiles[step.step_number] ? 'Hide Payload' : 'Show Payload'}
                      </button>
                    )}
                  </div>
                  {step.action.content && (
                    <div
                      className={`text-xs font-mono bg-black/70 p-3 rounded-lg border border-emerald-500/20 text-slate-300 overflow-x-auto transition-all ${
                        expandedFiles[step.step_number] ? 'max-h-96' : 'max-h-24'
                      }`}
                    >
                      <pre className="whitespace-pre-wrap">{step.action.content}</pre>
                    </div>
                  )}
                </div>
              )}

              {/* Action: Final Resolution / Summary */}
              {isFinishTool && (
                <div className="p-3.5 rounded-xl bg-purple-950/40 border border-purple-500/30 space-y-2">
                  <div className="flex items-center gap-1.5 text-xs font-bold text-purple-300 uppercase tracking-wider">
                    <CheckCircle2 className="w-4 h-4 text-purple-400" />
                    Agent Final Summary & Solution
                  </div>
                  <p className="text-xs text-slate-200 leading-relaxed font-sans whitespace-pre-wrap pl-2 border-l-2 border-purple-500/50">
                    {step.action.summary || step.thought || 'Task reported completed.'}
                  </p>
                </div>
              )}

              {/* Observation Terminal Block */}
              {step.observation && (
                <div className="space-y-1 pt-1">
                  <div className="text-[10px] uppercase font-mono tracking-wider text-slate-400 flex items-center gap-1">
                    <Terminal className="w-3 h-3" />
                    Container Observation (Stdout / Stderr)
                  </div>
                  <div className="bg-[#040711] border border-border/80 rounded-lg p-3 text-[11px] font-mono text-slate-300 max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed shadow-inner">
                    {step.observation}
                  </div>
                </div>
              )}
            </div>
          );
        })}

        {/* Live Running Indicator */}
        {isStreaming && (
          <div className="p-4 rounded-xl bg-surface/50 border border-dashed border-primary/40 flex items-center gap-3 text-xs text-primary animate-pulse">
            <Bot className="w-4 h-4 animate-bounce" />
            <span>Agent is reasoning and executing tools in Docker container sandbox...</span>
          </div>
        )}

        {/* Benchmark Finished Summary Card */}
        {isCompleted && (
          <div
            className={`p-5 rounded-2xl border shadow-xl space-y-4 ${
              run.passed
                ? 'bg-gradient-to-br from-emerald-950/40 via-surface to-surface-elevated border-emerald-500/40 ring-1 ring-emerald-500/20'
                : 'bg-gradient-to-br from-rose-950/40 via-surface to-surface-elevated border-rose-500/40 ring-1 ring-rose-500/20'
            }`}
          >
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div
                  className={`w-10 h-10 rounded-xl flex items-center justify-center ${
                    run.passed ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'
                  }`}
                >
                  {run.passed ? <CheckCircle2 className="w-6 h-6" /> : <XCircle className="w-6 h-6" />}
                </div>
                <div>
                  <h3 className="text-sm font-bold text-white tracking-tight">
                    {run.passed ? 'Benchmark Evaluation Passed! 🎉' : 'Benchmark Evaluation Failed ❌'}
                  </h3>
                  <p className="text-xs text-slate-300">
                    {run.passed
                      ? 'The agent met all verification test criteria in the held-out test suite.'
                      : run.failure_reason || 'Solution failed to satisfy the ground-truth verifier tests.'}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2 font-mono text-xs">
                <div className="px-3 py-1.5 rounded-lg bg-surface border border-border flex items-center gap-1.5 text-slate-300">
                  <Clock className="w-3.5 h-3.5 text-primary" />
                  <span>{run.total_duration_sec.toFixed(1)}s</span>
                </div>
                <div className="px-3 py-1.5 rounded-lg bg-surface border border-border flex items-center gap-1.5 text-slate-300">
                  <Sparkles className="w-3.5 h-3.5 text-accent" />
                  <span>{run.total_tokens.toLocaleString()} toks</span>
                </div>
                <div className="px-3 py-1.5 rounded-lg bg-surface border border-border flex items-center gap-1.5 text-slate-300">
                  <Coins className="w-3.5 h-3.5 text-emerald-400" />
                  <span>${run.estimated_cost_usd.toFixed(4)}</span>
                </div>
              </div>
            </div>

            {/* Toggle Verifier Pytest Output */}
            {run.failure_reason && (
              <div className="pt-2 border-t border-border/40 space-y-2">
                <button
                  type="button"
                  onClick={() => setShowVerifierOutput((prev) => !prev)}
                  className="flex items-center gap-1 text-xs font-mono text-slate-400 hover:text-white"
                >
                  {showVerifierOutput ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  <span>{showVerifierOutput ? 'Hide Verifier Log' : 'View Verifier Diagnostics'}</span>
                </button>

                {showVerifierOutput && (
                  <div className="p-3 rounded-lg bg-[#040711] border border-border/80 text-[11px] font-mono text-rose-300 whitespace-pre-wrap">
                    {run.failure_reason}
                  </div>
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
