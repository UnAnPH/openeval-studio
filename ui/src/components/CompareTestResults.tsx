import React, { useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  checkmarkCircle,
  codeSlashOutline,
  documentTextOutline,
  terminalOutline,
  warningOutline,
} from 'ionicons/icons';
import { AgentStep, RunRecord } from '../types';

interface CompareTestResultsProps {
  runs: RunRecord[];
}

export const CompareTestResults: React.FC<CompareTestResultsProps> = ({ runs }) => {
  const [runAId, setRunAId] = useState<string>(runs[0]?.run_id || '');
  const [runBId, setRunBId] = useState<string>(runs[1]?.run_id || runs[0]?.run_id || '');
  const [activeTab, setActiveTab] = useState<'timeline' | 'diff' | 'metrics'>('timeline');

  const runA = runs.find((r) => r.run_id === runAId) || runs[0] || null;
  const runB = runs.find((r) => r.run_id === runBId) || runs[1] || runs[0] || null;

  if (!runA || !runB) {
    return (
      <div className="bg-white p-8 rounded-2xl border border-border-subtle text-center text-text-muted">
        Select at least two evaluation runs to enable comparative analysis.
      </div>
    );
  }

  // 1. Dynamic Metric Differentials
  const durationDelta = runA.total_duration_sec - runB.total_duration_sec;
  const tokenDelta = runA.total_tokens - runB.total_tokens;
  const costDelta = runA.estimated_cost_usd - runB.estimated_cost_usd;
  const rewardDelta = (runA.reward ?? 0) - (runB.reward ?? 0);

  const statusShiftText =
    runA.passed === runB.passed
      ? runA.passed
        ? 'Both Passed'
        : 'Both Failed'
      : runA.passed
      ? 'Fail ➔ Pass (Improvement)'
      : 'Pass ➔ Fail (Regression)';

  // 2. Synchronized Steps & Divergence Detection
  const maxSteps = Math.max(runA.steps?.length || 0, runB.steps?.length || 0);
  const stepPairs: { stepA?: AgentStep; stepB?: AgentStep; turn: number }[] = [];

  let divergenceTurn: number | null = null;

  for (let i = 0; i < maxSteps; i++) {
    const stepA = runA.steps?.[i];
    const stepB = runB.steps?.[i];
    const turn = i + 1;

    if (divergenceTurn === null) {
      if (!stepA || !stepB) {
        divergenceTurn = turn;
      } else if (
        stepA.action.tool !== stepB.action.tool ||
        stepA.action.command !== stepB.action.command ||
        stepA.action.path !== stepB.action.path
      ) {
        divergenceTurn = turn;
      }
    }

    stepPairs.push({ stepA, stepB, turn });
  }

  // 3. Find files written to compute code diffs
  const filesA = runA.steps?.filter((s) => s.action.tool === 'write_file' && s.action.content) || [];
  const filesB = runB.steps?.filter((s) => s.action.tool === 'write_file' && s.action.content) || [];

  const getToolIcon = (tool?: string) => {
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

  // Simple line diff generator
  const renderSimpleDiff = (textA: string, textB: string) => {
    const linesA = textA.split('\n');
    const linesB = textB.split('\n');
    const maxLines = Math.max(linesA.length, linesB.length);
    const diffRows = [];

    for (let i = 0; i < maxLines; i++) {
      const lineA = linesA[i];
      const lineB = linesB[i];
      const isDiff = lineA !== lineB;

      diffRows.push(
        <div key={i} className={`grid grid-cols-2 text-[11px] font-mono ${isDiff ? 'bg-amber-500/10' : ''}`}>
          <div className={`p-1 border-r border-border-subtle overflow-x-auto ${isDiff && lineA ? 'text-rose-700 bg-rose-50' : 'text-slate-700'}`}>
            <span className="text-slate-400 select-none mr-2">{lineA !== undefined ? i + 1 : ''}</span>
            {lineA !== undefined ? (isDiff ? `- ${lineA}` : `  ${lineA}`) : ''}
          </div>
          <div className={`p-1 overflow-x-auto ${isDiff && lineB ? 'text-emerald-700 bg-emerald-50' : 'text-slate-700'}`}>
            <span className="text-slate-400 select-none mr-2">{lineB !== undefined ? i + 1 : ''}</span>
            {lineB !== undefined ? (isDiff ? `+ ${lineB}` : `  ${lineB}`) : ''}
          </div>
        </div>
      );
    }

    return (
      <div className="bg-[#14121F] rounded-xl overflow-hidden border border-border-subtle max-h-80 overflow-y-auto">
        <div className="grid grid-cols-2 bg-[#1C182E] text-slate-300 text-[10px] font-mono uppercase font-bold p-2 border-b border-[#2A2445]">
          <div>Run A File Payload</div>
          <div>Run B File Payload</div>
        </div>
        <div className="bg-white">{diffRows}</div>
      </div>
    );
  };

  return (
    <div className="w-full space-y-5 animate-fadeIn font-sans">
      {/* Header */}
      <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-bold text-text-primary">
            Dynamic Side-by-Side Run Comparison
          </h2>
          <p className="text-xs text-text-secondary mt-0.5 font-mono">
            Synchronized trajectory alignment, divergence detection, and code diffing
          </p>
        </div>

        {/* View mode toggle */}
        <div className="flex items-center gap-1.5 bg-canvas p-1 rounded-xl border border-border-subtle text-xs">
          <button
            type="button"
            onClick={() => setActiveTab('timeline')}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
              activeTab === 'timeline'
                ? 'bg-white text-brand-primary shadow-sm'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            Trajectory Timeline
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('diff')}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
              activeTab === 'diff'
                ? 'bg-white text-brand-primary shadow-sm'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            Code Diffs ({filesA.length + filesB.length})
          </button>
        </div>
      </div>

      {/* Selectors */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Run A Selector */}
        <div className="bg-white p-4 rounded-2xl border border-border-subtle shadow-sm space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="font-bold text-text-primary flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-brand-purple" />
              Run A (Candidate)
            </span>
            <span
              className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded ${
                runA.passed ? 'text-emerald-700 bg-emerald-50' : 'text-rose-700 bg-rose-50'
              }`}
            >
              {runA.passed ? 'PASSED (1.0)' : 'FAILED (0.0)'}
            </span>
          </div>
          <select
            value={runAId}
            onChange={(e) => setRunAId(e.target.value)}
            className="w-full bg-canvas border border-border-subtle rounded-xl px-3 py-2 text-xs text-text-primary focus:outline-none focus:border-brand-primary cursor-pointer font-mono"
          >
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.task_id} • {r.model} • {r.passed ? 'Pass' : 'Fail'} ({r.total_duration_sec.toFixed(1)}s, ${r.estimated_cost_usd.toFixed(4)})
              </option>
            ))}
          </select>
        </div>

        {/* Run B Selector */}
        <div className="bg-white p-4 rounded-2xl border border-border-subtle shadow-sm space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="font-bold text-text-primary flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-accent-orange" />
              Run B (Baseline)
            </span>
            <span
              className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded ${
                runB.passed ? 'text-emerald-700 bg-emerald-50' : 'text-rose-700 bg-rose-50'
              }`}
            >
              {runB.passed ? 'PASSED (1.0)' : 'FAILED (0.0)'}
            </span>
          </div>
          <select
            value={runBId}
            onChange={(e) => setRunBId(e.target.value)}
            className="w-full bg-canvas border border-border-subtle rounded-xl px-3 py-2 text-xs text-text-primary focus:outline-none focus:border-accent-orange cursor-pointer font-mono"
          >
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.task_id} • {r.model} • {r.passed ? 'Pass' : 'Fail'} ({r.total_duration_sec.toFixed(1)}s, ${r.estimated_cost_usd.toFixed(4)})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Dynamic Live Metric Differentials Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {/* Pass/Fail Status Shift */}
        <div className="p-4 rounded-2xl bg-white border border-border-subtle shadow-sm space-y-1">
          <div className="text-[10px] uppercase font-mono font-bold text-text-muted">Outcome Shift</div>
          <div className="text-xs font-bold text-text-primary font-mono">{statusShiftText}</div>
          <div className="text-[10px] font-mono text-brand-purple">
            Δ Reward: {rewardDelta >= 0 ? `+${rewardDelta.toFixed(1)}` : rewardDelta.toFixed(1)}
          </div>
        </div>

        {/* Duration Delta */}
        <div className="p-4 rounded-2xl bg-white border border-border-subtle shadow-sm space-y-1">
          <div className="text-[10px] uppercase font-mono font-bold text-text-muted">Δ Duration</div>
          <div className={`text-base font-bold font-mono ${durationDelta <= 0 ? 'text-emerald-700' : 'text-rose-700'}`}>
            {durationDelta <= 0 ? `${durationDelta.toFixed(1)}s (Faster)` : `+${durationDelta.toFixed(1)}s (Slower)`}
          </div>
          <div className="text-[10px] font-mono text-text-secondary">
            {runA.total_duration_sec.toFixed(1)}s vs {runB.total_duration_sec.toFixed(1)}s
          </div>
        </div>

        {/* Tokens Delta */}
        <div className="p-4 rounded-2xl bg-white border border-border-subtle shadow-sm space-y-1">
          <div className="text-[10px] uppercase font-mono font-bold text-text-muted">Δ Tokens</div>
          <div className={`text-base font-bold font-mono ${tokenDelta <= 0 ? 'text-emerald-700' : 'text-rose-700'}`}>
            {tokenDelta <= 0 ? `${tokenDelta.toLocaleString()} tok` : `+${tokenDelta.toLocaleString()} tok`}
          </div>
          <div className="text-[10px] font-mono text-text-secondary">
            {runA.total_tokens.toLocaleString()} vs {runB.total_tokens.toLocaleString()}
          </div>
        </div>

        {/* Cost Delta */}
        <div className="p-4 rounded-2xl bg-white border border-border-subtle shadow-sm space-y-1">
          <div className="text-[10px] uppercase font-mono font-bold text-text-muted">Δ Estimated Cost</div>
          <div className={`text-base font-bold font-mono ${costDelta <= 0 ? 'text-emerald-700' : 'text-rose-700'}`}>
            {costDelta <= 0 ? `-$${Math.abs(costDelta).toFixed(4)}` : `+$${costDelta.toFixed(4)}`}
          </div>
          <div className="text-[10px] font-mono text-text-secondary">
            ${runA.estimated_cost_usd.toFixed(4)} vs ${runB.estimated_cost_usd.toFixed(4)}
          </div>
        </div>
      </div>

      {/* Divergence Notification Banner */}
      {divergenceTurn !== null && (
        <div className="p-4 rounded-2xl bg-amber-500/10 border border-amber-500/30 text-amber-900 flex items-center justify-between text-xs shadow-sm">
          <div className="flex items-center gap-2.5">
            <IonIcon icon={warningOutline} className="text-accent-orange text-lg flex-shrink-0" />
            <span>
              <strong>Execution Divergence Detected at Turn #{divergenceTurn}:</strong> Reasoning, tool selection, or parameters diverged between Model A (<code>{runA.model}</code>) and Model B (<code>{runB.model}</code>).
            </span>
          </div>
        </div>
      )}

      {/* TAB 1: SYNCHRONIZED TIMELINE */}
      {activeTab === 'timeline' && (
        <div className="space-y-4">
          {stepPairs.map(({ stepA, stepB, turn }) => {
            const isDiverged = divergenceTurn !== null && turn >= divergenceTurn;

            return (
              <div
                key={turn}
                className={`bg-white rounded-2xl border shadow-sm overflow-hidden transition-all ${
                  turn === divergenceTurn ? 'border-amber-400 ring-2 ring-amber-400/20' : 'border-border-subtle'
                }`}
              >
                {/* Turn Header */}
                <div className="p-3 bg-canvas border-b border-border-subtle flex items-center justify-between text-xs font-mono">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-brand-purple bg-surface-subtle px-2 py-0.5 rounded">
                      Turn #{turn}
                    </span>
                    {turn === divergenceTurn && (
                      <span className="px-2 py-0.5 rounded bg-amber-100 text-amber-900 font-bold text-[10px] uppercase">
                        ⚡ Point of Divergence
                      </span>
                    )}
                  </div>
                  <span className="text-[11px] text-text-muted">
                    {isDiverged ? 'Divergent Trajectory' : 'Synchronous Action'}
                  </span>
                </div>

                {/* Side-by-Side Turn Payload */}
                <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-border-subtle">
                  {/* Step A */}
                  <div className="p-4 space-y-2.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-mono font-bold text-brand-purple flex items-center gap-1.5">
                        <IonIcon icon={getToolIcon(stepA?.action.tool)} />
                        {stepA ? stepA.action.tool : '(Turn not executed)'}
                      </span>
                      {stepA && (
                        <span className="text-[10px] font-mono text-text-muted">
                          {stepA.latency_ms}ms • {stepA.tokens_used} tok
                        </span>
                      )}
                    </div>

                    {stepA?.thought && (
                      <p className="text-xs text-text-secondary bg-canvas p-2.5 rounded-xl border border-border-subtle leading-relaxed">
                        {stepA.thought}
                      </p>
                    )}

                    {stepA?.action.command && (
                      <pre className="p-2.5 rounded-xl bg-[#14121F] text-emerald-400 font-mono text-xs overflow-x-auto">
                        $ {stepA.action.command}
                      </pre>
                    )}

                    {stepA?.observation && (
                      <pre className="p-2.5 rounded-xl bg-[#14121F] text-slate-300 font-mono text-[11px] overflow-x-auto max-h-32 whitespace-pre-wrap">
                        {stepA.observation}
                      </pre>
                    )}
                  </div>

                  {/* Step B */}
                  <div className="p-4 space-y-2.5 bg-canvas/30">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-mono font-bold text-accent-orange flex items-center gap-1.5">
                        <IonIcon icon={getToolIcon(stepB?.action.tool)} />
                        {stepB ? stepB.action.tool : '(Turn not executed)'}
                      </span>
                      {stepB && (
                        <span className="text-[10px] font-mono text-text-muted">
                          {stepB.latency_ms}ms • {stepB.tokens_used} tok
                        </span>
                      )}
                    </div>

                    {stepB?.thought && (
                      <p className="text-xs text-text-secondary bg-canvas p-2.5 rounded-xl border border-border-subtle leading-relaxed">
                        {stepB.thought}
                      </p>
                    )}

                    {stepB?.action.command && (
                      <pre className="p-2.5 rounded-xl bg-[#14121F] text-amber-300 font-mono text-xs overflow-x-auto">
                        $ {stepB.action.command}
                      </pre>
                    )}

                    {stepB?.observation && (
                      <pre className="p-2.5 rounded-xl bg-[#14121F] text-slate-300 font-mono text-[11px] overflow-x-auto max-h-32 whitespace-pre-wrap">
                        {stepB.observation}
                      </pre>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* TAB 2: CODE DIFFS */}
      {activeTab === 'diff' && (
        <div className="space-y-4">
          {filesA.length === 0 && filesB.length === 0 ? (
            <div className="p-8 text-center bg-white rounded-2xl border border-border-subtle text-text-muted text-xs">
              No written files recorded for comparison.
            </div>
          ) : (
            filesA.map((fileA, idx) => {
              const fileB = filesB[idx] || filesB.find((f) => f.action.path === fileA.action.path);
              return (
                <div key={idx} className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-3">
                  <div className="flex items-center justify-between text-xs font-mono">
                    <span className="font-bold text-text-primary">
                      File: {fileA.action.path || fileB?.action.path || `mutation_${idx + 1}.py`}
                    </span>
                    <span className="text-text-muted text-[11px]">
                      Turn #{fileA.step_number} (A) vs Turn #{fileB?.step_number || '?'} (B)
                    </span>
                  </div>
                  {renderSimpleDiff(fileA.action.content || '', fileB?.action.content || '')}
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
};




