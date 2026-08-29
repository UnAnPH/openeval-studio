import React, { useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  checkmarkCircle,
  closeCircle,
  downloadOutline,
  hourglassOutline,
  searchOutline,
  shieldOutline,
  playSharp,
} from 'ionicons/icons';
import { RunRecord, TaskSummary } from '../types';

interface TestCasesTableProps {
  tasks: TaskSummary[];
  runs: RunRecord[];
  onSelectRun: (runId: string) => void;
  onLaunchTask: (taskId: string) => void;
  onExportSFT?: () => void;
}

export const TestCasesTable: React.FC<TestCasesTableProps> = ({
  tasks,
  runs,
  onSelectRun,
  onLaunchTask,
  onExportSFT,
}) => {
  const [searchText, setSearchText] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'needs_judgment' | 'cleared' | 'failed' | 'safety_flagged'>('all');

  const items = tasks.map((task) => {
    const matchedRuns = runs.filter((r) => r.task_id === task.task_id);
    const latestRun = matchedRuns.length > 0 ? matchedRuns[0] : null;
    return { task, latestRun, runCount: matchedRuns.length };
  });

  const totalCases = items.length;
  const clearedCases = items.filter((i) => i.latestRun?.passed === true).length;
  const failedCases = items.filter((i) => i.latestRun?.passed === false).length;
  const needsJudgmentCases = items.filter((i) => !i.latestRun || i.latestRun.status === 'pending').length;
  const safetyFlaggedCases = items.filter(
    (i) => i.latestRun?.audit_verdicts?.some((v) => !v.passed) || i.latestRun?.failure_reason?.includes('Safety')
  ).length;

  const filteredItems = items.filter(({ task, latestRun }) => {
    if (searchText) {
      const q = searchText.toLowerCase();
      const matchTask = task.task_id.toLowerCase().includes(q);
      const matchCategory = task.category?.toLowerCase().includes(q);
      const matchInstr = task.instruction_preview?.toLowerCase().includes(q);
      if (!matchTask && !matchCategory && !matchInstr) return false;
    }
    if (statusFilter === 'cleared' && latestRun?.passed !== true) return false;
    if (statusFilter === 'failed' && latestRun?.passed !== false) return false;
    if (statusFilter === 'needs_judgment' && latestRun?.passed !== null && latestRun) return false;
    if (
      statusFilter === 'safety_flagged' &&
      !latestRun?.audit_verdicts?.some((v) => !v.passed) &&
      !latestRun?.failure_reason?.includes('Safety')
    ) {
      return false;
    }
    return true;
  });

  // Client-side fallback SFT export if onExportSFT is not provided
  const handleExport = () => {
    if (onExportSFT) {
      onExportSFT();
      return;
    }
    const passingRuns = runs.filter((r) => r.passed === true || r.reward === 1.0);
    if (passingRuns.length === 0) {
      alert('No passed evaluation runs found to export.');
      return;
    }
    const sftData = passingRuns.map((r) => ({
      run_id: r.run_id,
      task_id: r.task_id,
      model: r.model,
      reward: r.reward,
      messages: [
        { role: 'system', content: 'You are an autonomous AI engineering agent executing bash and code tools inside a Docker sandbox.' },
        { role: 'user', content: `Solve task ${r.task_id}: ${r.steps[0]?.thought || 'Fix the repository defect.'}` },
        ...r.steps.flatMap((s) => [
          { role: 'assistant', content: `Thought: ${s.thought}\nAction: ${s.action.tool} ${s.action.command || s.action.path || ''}\n${s.action.content || ''}` },
          { role: 'user', content: `Observation:\n${s.observation || '(empty)'}` },
        ]),
      ],
    }));
    const jsonl = sftData.map((d) => JSON.stringify(d)).join('\n');
    const blob = new Blob([jsonl], { type: 'application/x-jsonlines' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `openeval_sft_passed_dataset_${new Date().toISOString().slice(0, 10)}.jsonl`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="w-full space-y-4 animate-fadeIn font-sans">
      {/* Header & Filter Controls */}
      <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-bold text-text-primary">
            Benchmark Test Cases
          </h2>
          <p className="text-xs text-text-secondary mt-0.5 font-mono">
            {totalCases} test harness scenarios in SWE-Bench suite
          </p>
        </div>

        {/* Filter Pills & Export CTA */}
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <button
            type="button"
            onClick={() => setStatusFilter('all')}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
              statusFilter === 'all'
                ? 'bg-dark-base text-white font-bold'
                : 'bg-canvas text-text-secondary hover:text-text-primary'
            }`}
          >
            All ({totalCases})
          </button>

          <button
            type="button"
            onClick={() => setStatusFilter('cleared')}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all flex items-center gap-1.5 ${
              statusFilter === 'cleared'
                ? 'bg-emerald-600 text-white font-bold'
                : 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
            }`}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
            <span>Passed ({clearedCases})</span>
          </button>

          <button
            type="button"
            onClick={() => setStatusFilter('failed')}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all flex items-center gap-1.5 ${
              statusFilter === 'failed'
                ? 'bg-rose-600 text-white font-bold'
                : 'bg-rose-50 text-rose-700 hover:bg-rose-100'
            }`}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />
            <span>Failed ({failedCases})</span>
          </button>

          <button
            type="button"
            onClick={() => setStatusFilter('safety_flagged')}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all flex items-center gap-1.5 ${
              statusFilter === 'safety_flagged'
                ? 'bg-purple-600 text-white font-bold'
                : 'bg-purple-50 text-brand-purple hover:bg-purple-100'
            }`}
          >
            <IonIcon icon={shieldOutline} className="text-xs" />
            <span>Safety Flagged ({safetyFlaggedCases})</span>
          </button>

          <button
            type="button"
            onClick={() => setStatusFilter('needs_judgment')}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all flex items-center gap-1.5 ${
              statusFilter === 'needs_judgment'
                ? 'bg-amber-600 text-white font-bold'
                : 'bg-amber-50 text-amber-700 hover:bg-amber-100'
            }`}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
            <span>Pending ({needsJudgmentCases})</span>
          </button>

          <div className="h-4 w-px bg-border-subtle mx-1 hidden sm:block" />

          {/* SFT Dataset Export Button */}
          <button
            type="button"
            onClick={handleExport}
            className="px-3.5 py-1.5 rounded-xl bg-dark-base text-white text-xs font-bold hover:bg-black transition-all flex items-center gap-1.5 shadow-sm active:scale-98"
          >
            <IonIcon icon={downloadOutline} className="text-xs" />
            <span>Export Passed SFT Data</span>
          </button>
        </div>
      </div>

      {/* Search Bar */}
      <div className="relative w-full max-w-sm">
        <input
          type="text"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          placeholder="Filter test cases by ID, category, or prompt..."
          className="w-full bg-white border border-border-subtle rounded-xl pl-8 pr-3 py-2 text-xs text-text-primary placeholder-text-muted focus:outline-none focus:border-brand-primary transition-all shadow-sm"
        />
        <IonIcon
          icon={searchOutline}
          className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted text-xs pointer-events-none"
        />
      </div>

      {/* Clean Modern Data Table */}
      <div className="bg-white rounded-2xl border border-border-subtle shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="border-b border-border-subtle text-[10px] uppercase font-bold tracking-wider text-text-muted bg-canvas/60">
                <th className="py-3 px-4 w-32">Status</th>
                <th className="py-3 px-4">Task ID</th>
                <th className="py-3 px-4">Category</th>
                <th className="py-3 px-4">Difficulty</th>
                <th className="py-3 px-4">Last Duration</th>
                <th className="py-3 px-4">Score</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {filteredItems.map(({ task, latestRun }) => {
                const isPassed = latestRun?.passed === true;
                const isFailed = latestRun?.passed === false;

                return (
                  <tr
                    key={task.task_id}
                    onClick={() => latestRun ? onSelectRun(latestRun.run_id) : onLaunchTask(task.task_id)}
                    className="hover:bg-canvas transition-colors cursor-pointer"
                  >
                    {/* Status */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      <div className="flex items-center gap-1.5">
                        <IonIcon
                          icon={isPassed ? checkmarkCircle : isFailed ? closeCircle : hourglassOutline}
                          className={`text-sm ${
                            isPassed ? 'text-status-cleared' : isFailed ? 'text-risk-high' : 'text-text-muted'
                          }`}
                        />
                        <span
                          className={`text-[10px] uppercase font-mono px-2 py-0.5 rounded font-bold ${
                            isPassed
                              ? 'bg-emerald-50 text-emerald-700'
                              : isFailed
                              ? 'bg-rose-50 text-rose-700'
                              : 'bg-canvas text-text-muted border border-border-subtle'
                          }`}
                        >
                          {isPassed ? 'Passed' : isFailed ? 'Failed' : 'Pending'}
                        </span>
                      </div>
                    </td>

                    {/* Task ID */}
                    <td className="py-3 px-4 font-mono font-medium text-text-primary">
                      {task.task_id}
                    </td>

                    {/* Category */}
                    <td className="py-3 px-4 text-text-secondary capitalize">
                      {task.category?.replace('_', ' ') || 'General'}
                    </td>

                    {/* Difficulty */}
                    <td className="py-3 px-4">
                      <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-canvas border border-border-subtle text-text-secondary">
                        {task.difficulty || 'Medium'}
                      </span>
                    </td>

                    {/* Duration */}
                    <td className="py-3 px-4 font-mono text-text-secondary">
                      {latestRun ? `${latestRun.total_duration_sec.toFixed(1)}s` : '—'}
                    </td>

                    {/* Score */}
                    <td className="py-3 px-4 font-mono font-bold">
                      <span className={isPassed ? 'text-emerald-700' : isFailed ? 'text-rose-700' : 'text-text-muted'}>
                        {latestRun && latestRun.reward !== null ? `${latestRun.reward.toFixed(1)}/1.0` : '—'}
                      </span>
                    </td>

                    {/* Action */}
                    <td className="py-3 px-4 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                      {latestRun ? (
                        <button
                          type="button"
                          onClick={() => onSelectRun(latestRun.run_id)}
                          className="text-[11px] font-medium text-brand-purple hover:underline"
                        >
                          Inspect Trace
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => onLaunchTask(task.task_id)}
                          className="px-2.5 py-1 rounded bg-dark-base text-white text-[10px] font-bold flex items-center gap-1 ml-auto hover:bg-black transition-colors"
                        >
                          <IonIcon icon={playSharp} className="text-[9px]" />
                          <span>Run</span>
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

