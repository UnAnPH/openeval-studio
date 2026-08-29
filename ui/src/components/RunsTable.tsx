import React, { useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  checkmarkCircle,
  closeCircle,
  copyOutline,
  downloadOutline,
  filterOutline,
  gitCompareOutline,
  hourglassOutline,
  searchOutline,
  shieldCheckmarkOutline,
  trashOutline,
  arrowForwardOutline,
  playSharp,
  sparklesOutline,
  cashOutline,
  timeOutline,
} from 'ionicons/icons';
import { RunRecord, TaskSummary } from '../types';

interface RunsTableProps {
  runs: RunRecord[];
  tasks: TaskSummary[];
  onSelectRun: (runId: string) => void;
  onDeleteRun?: (runId: string) => void;
  onClearAllRuns?: () => void;
  onCompareSelected?: (runIds: [string, string]) => void;
  onExportSFT?: (selectedRuns?: RunRecord[]) => void;
  onNavigateToStudio?: () => void;
  onNavigateToBenchmarks?: () => void;
}

export const RunsTable: React.FC<RunsTableProps> = ({
  runs,
  tasks,
  onSelectRun,
  onDeleteRun,
  onClearAllRuns,
  onCompareSelected,
  onExportSFT,
  onNavigateToStudio,
  onNavigateToBenchmarks,
}) => {
  const [searchText, setSearchText] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'passed' | 'failed' | 'safety_flagged' | 'running'>('all');
  const [selectedModel, setSelectedModel] = useState<string>('all');
  const [selectedTaskId, setSelectedTaskId] = useState<string>('all');
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Unique models in the run history
  const modelOptions = Array.from(new Set(runs.map((r) => r.model))).filter(Boolean);

  // Filtered runs logic
  const filteredRuns = runs.filter((run) => {
    if (searchText) {
      const q = searchText.toLowerCase();
      const matchId = run.task_id.toLowerCase().includes(q) || run.run_id.toLowerCase().includes(q);
      const matchModel = run.model.toLowerCase().includes(q);
      const matchSummary = run.final_summary?.toLowerCase().includes(q);
      const matchReason = run.failure_reason?.toLowerCase().includes(q);
      if (!matchId && !matchModel && !matchSummary && !matchReason) return false;
    }
    if (statusFilter === 'passed' && run.passed !== true) return false;
    if (statusFilter === 'failed' && run.passed !== false) return false;
    if (statusFilter === 'running' && run.status !== 'running' && run.status !== 'pending') return false;
    if (statusFilter === 'safety_flagged') {
      const hasFailedAudit = run.audit_verdicts?.some((v) => !v.passed);
      if (!hasFailedAudit) return false;
    }
    if (selectedModel !== 'all' && run.model !== selectedModel) return false;
    if (selectedTaskId !== 'all' && run.task_id !== selectedTaskId) return false;
    return true;
  });

  // Checkbox handlers
  const toggleSelectAll = () => {
    if (selectedRunIds.length === filteredRuns.length) {
      setSelectedRunIds([]);
    } else {
      setSelectedRunIds(filteredRuns.map((r) => r.run_id));
    }
  };

  const toggleSelectRun = (runId: string) => {
    setSelectedRunIds((prev) =>
      prev.includes(runId) ? prev.filter((id) => id !== runId) : [...prev, runId]
    );
  };

  const handleCopyId = (runId: string) => {
    navigator.clipboard.writeText(runId);
    setCopiedId(runId);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // KPIs
  const totalRuns = runs.length;
  const passedRuns = runs.filter((r) => r.passed === true).length;
  const failedRuns = runs.filter((r) => r.passed === false).length;
  const flaggedRuns = runs.filter((r) => r.audit_verdicts?.some((v) => !v.passed)).length;
  const totalCost = runs.reduce((acc, r) => acc + (r.estimated_cost_usd || 0), 0);
  const totalTokens = runs.reduce((acc, r) => acc + (r.total_tokens || 0), 0);
  const avgDuration = totalRuns > 0
    ? (runs.reduce((acc, r) => acc + (r.total_duration_sec || 0), 0) / totalRuns).toFixed(1)
    : '0.0';

  const selectedRecords = runs.filter((r) => selectedRunIds.includes(r.run_id));

  return (
    <div className="w-full space-y-5 animate-fadeIn font-sans">
      {/* 1. Header & Summary Row */}
      <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <h2 className="text-lg font-bold text-text-primary">Evaluation Runs</h2>
            <span className="px-2.5 py-0.5 rounded-full bg-surface-subtle border border-border-subtle text-xs font-mono font-bold text-brand-purple">
              {totalRuns} Recorded
            </span>
          </div>
          <p className="text-xs text-text-secondary mt-0.5 font-mono">
            First-class registry of immutable agent trajectories, held-out verifier scores, and safety audits.
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={onNavigateToStudio}
            className="px-4 py-2 rounded-xl bg-dark-base text-white text-xs font-bold flex items-center gap-1.5 hover:bg-black transition-all shadow-sm active:scale-95 cursor-pointer"
          >
            <IonIcon icon={playSharp} className="text-xs" />
            <span>New Live Run</span>
          </button>
          <button
            type="button"
            onClick={onNavigateToBenchmarks}
            className="px-3.5 py-2 rounded-xl bg-canvas text-text-secondary border border-border-subtle text-xs font-medium hover:text-text-primary hover:bg-surface-subtle transition-all cursor-pointer"
          >
            <span>View Benchmarks ({tasks.length})</span>
          </button>
        </div>
      </div>

      {/* 2. Key Metrics Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="bg-white p-4 rounded-xl border border-border-subtle shadow-sm space-y-1">
          <div className="text-[11px] text-text-muted font-medium flex items-center justify-between">
            <span>Total Runs</span>
            <IonIcon icon={sparklesOutline} className="text-brand-purple text-xs" />
          </div>
          <div className="text-xl font-bold font-mono text-text-primary">{totalRuns}</div>
        </div>

        <div className="bg-white p-4 rounded-xl border border-border-subtle shadow-sm space-y-1">
          <div className="text-[11px] text-text-muted font-medium flex items-center justify-between">
            <span>Passed</span>
            <IonIcon icon={checkmarkCircle} className="text-status-cleared text-xs" />
          </div>
          <div className="text-xl font-bold font-mono text-emerald-700">{passedRuns}</div>
        </div>

        <div className="bg-white p-4 rounded-xl border border-border-subtle shadow-sm space-y-1">
          <div className="text-[11px] text-text-muted font-medium flex items-center justify-between">
            <span>Failed</span>
            <IonIcon icon={closeCircle} className="text-risk-high text-xs" />
          </div>
          <div className="text-xl font-bold font-mono text-rose-700">{failedRuns}</div>
        </div>

        <div className="bg-white p-4 rounded-xl border border-border-subtle shadow-sm space-y-1">
          <div className="text-[11px] text-text-muted font-medium flex items-center justify-between">
            <span>Safety Flagged</span>
            <IonIcon icon={shieldCheckmarkOutline} className="text-brand-purple text-xs" />
          </div>
          <div className="text-xl font-bold font-mono text-brand-purple">{flaggedRuns}</div>
        </div>

        <div className="bg-white p-4 rounded-xl border border-border-subtle shadow-sm space-y-1">
          <div className="text-[11px] text-text-muted font-medium flex items-center justify-between">
            <span>Avg Duration</span>
            <IonIcon icon={timeOutline} className="text-accent-orange text-xs" />
          </div>
          <div className="text-xl font-bold font-mono text-text-primary">{avgDuration}s</div>
        </div>

        <div className="bg-white p-4 rounded-xl border border-border-subtle shadow-sm space-y-1">
          <div className="text-[11px] text-text-muted font-medium flex items-center justify-between">
            <span>Est. Spend</span>
            <IonIcon icon={cashOutline} className="text-emerald-600 text-xs" />
          </div>
          <div className="text-xl font-bold font-mono text-emerald-700">${totalCost.toFixed(4)}</div>
          <div className="text-[10px] text-text-secondary font-mono">{totalTokens.toLocaleString()} tok</div>
        </div>
      </div>

      {/* 3. Filter Bar & Bulk Selection Action Banner */}
      <div className="bg-white p-4 rounded-2xl border border-border-subtle shadow-sm space-y-3">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
          {/* Status Filter Pills */}
          <div className="flex items-center gap-1.5 bg-canvas p-1 rounded-xl border border-border-subtle text-xs overflow-x-auto">
            {(
              [
                { id: 'all', label: `All (${totalRuns})` },
                { id: 'passed', label: `Passed (${passedRuns})` },
                { id: 'failed', label: `Failed (${failedRuns})` },
                { id: 'safety_flagged', label: `Safety Flagged (${flaggedRuns})` },
              ] as const
            ).map((filter) => (
              <button
                key={filter.id}
                type="button"
                onClick={() => setStatusFilter(filter.id)}
                className={`px-3 py-1.5 rounded-lg font-medium whitespace-nowrap transition-all ${
                  statusFilter === filter.id
                    ? 'bg-white text-brand-primary font-bold shadow-sm'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                {filter.label}
              </button>
            ))}
          </div>

          {/* Search and Secondary Dropdowns */}
          <div className="flex items-center gap-2 flex-wrap">
            {/* Model Dropdown Filter */}
            {modelOptions.length > 0 && (
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                className="bg-canvas border border-border-subtle rounded-xl px-3 py-1.5 text-xs text-text-primary font-medium focus:outline-none focus:border-brand-primary"
              >
                <option value="all">All Models</option>
                {modelOptions.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            )}

            {/* Task Dropdown Filter */}
            <select
              value={selectedTaskId}
              onChange={(e) => setSelectedTaskId(e.target.value)}
              className="bg-canvas border border-border-subtle rounded-xl px-3 py-1.5 text-xs text-text-primary font-medium focus:outline-none focus:border-brand-primary"
            >
              <option value="all">All Benchmarks</option>
              {tasks.map((t) => (
                <option key={t.task_id} value={t.task_id}>
                  {t.task_id}
                </option>
              ))}
            </select>

            {/* Search Input */}
            <div className="relative min-w-[200px]">
              <input
                type="text"
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                placeholder="Search run ID, task, model..."
                className="w-full bg-canvas border border-border-subtle rounded-xl pl-7 pr-3 py-1.5 text-xs text-text-primary focus:outline-none focus:border-brand-primary font-sans"
              />
              <IonIcon
                icon={searchOutline}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted text-xs pointer-events-none"
              />
            </div>

            {/* Clear All Data Option */}
            {runs.length > 0 && onClearAllRuns && (
              <button
                type="button"
                onClick={() => {
                  if (confirm('Permanently purge all evaluation runs from memory and logs?')) {
                    onClearAllRuns();
                    setSelectedRunIds([]);
                  }
                }}
                className="px-3 py-1.5 rounded-xl bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200 text-xs font-bold flex items-center gap-1 transition-colors cursor-pointer"
                title="Clear All Runs"
              >
                <IonIcon icon={trashOutline} className="text-xs" />
                <span>Clear All</span>
              </button>
            )}
          </div>
        </div>

        {/* Floating Bulk Action Bar when Checkboxes are Selected */}
        {selectedRunIds.length > 0 && (
          <div className="p-3 bg-purple-50 border border-purple-200 rounded-xl flex items-center justify-between gap-3 text-xs animate-fadeIn">
            <div className="flex items-center gap-2 text-brand-primary font-bold">
              <span className="w-2 h-2 rounded-full bg-brand-purple" />
              <span>{selectedRunIds.length} run{selectedRunIds.length > 1 ? 's' : ''} selected</span>
            </div>

            <div className="flex items-center gap-2">
              {/* Compare Button */}
              {selectedRunIds.length >= 2 && onCompareSelected && (
                <button
                  type="button"
                  onClick={() => onCompareSelected([selectedRunIds[0], selectedRunIds[1]])}
                  className="px-3 py-1.5 rounded-lg bg-dark-base text-white text-xs font-bold flex items-center gap-1.5 hover:bg-black transition-all shadow-sm cursor-pointer"
                >
                  <IonIcon icon={gitCompareOutline} className="text-xs" />
                  <span>Compare Top 2 Selected</span>
                </button>
              )}

              {/* Export SFT Dataset */}
              {onExportSFT && (
                <button
                  type="button"
                  onClick={() => onExportSFT(selectedRecords)}
                  className="px-3 py-1.5 rounded-lg bg-white border border-border-subtle text-brand-purple text-xs font-bold flex items-center gap-1.5 hover:bg-surface-subtle transition-all cursor-pointer"
                >
                  <IonIcon icon={downloadOutline} className="text-xs" />
                  <span>Export SFT JSONL ({selectedRunIds.length})</span>
                </button>
              )}

              {/* Batch Delete */}
              {onDeleteRun && (
                <button
                  type="button"
                  onClick={() => {
                    if (confirm(`Delete ${selectedRunIds.length} selected runs?`)) {
                      selectedRunIds.forEach((id) => onDeleteRun(id));
                      setSelectedRunIds([]);
                    }
                  }}
                  className="px-3 py-1.5 rounded-lg bg-rose-100 text-rose-800 hover:bg-rose-200 text-xs font-bold flex items-center gap-1 transition-colors cursor-pointer"
                >
                  <IonIcon icon={trashOutline} className="text-xs" />
                  <span>Delete Selected</span>
                </button>
              )}

              <button
                type="button"
                onClick={() => setSelectedRunIds([])}
                className="px-2.5 py-1.5 text-xs text-text-secondary hover:text-text-primary"
              >
                Deselect All
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 4. High-Density Runs Table */}
      <div className="bg-white rounded-2xl border border-border-subtle shadow-sm overflow-hidden">
        {filteredRuns.length === 0 ? (
          <div className="p-12 text-center space-y-3">
            <div className="w-12 h-12 rounded-2xl bg-canvas border border-border-subtle mx-auto flex items-center justify-center text-text-muted">
              <IonIcon icon={filterOutline} className="text-2xl" />
            </div>
            <h3 className="text-sm font-bold text-text-primary">No evaluation runs found</h3>
            <p className="text-xs text-text-secondary max-w-sm mx-auto">
              No runs match your active filter criteria. Clear filters or launch a benchmark in Live Studio.
            </p>
            <div className="pt-2 flex items-center justify-center gap-2">
              <button
                type="button"
                onClick={() => {
                  setSearchText('');
                  setStatusFilter('all');
                  setSelectedModel('all');
                  setSelectedTaskId('all');
                }}
                className="px-3.5 py-1.5 rounded-xl bg-canvas border border-border-subtle text-xs font-medium text-text-primary hover:bg-surface-subtle"
              >
                Reset Filters
              </button>
              <button
                type="button"
                onClick={onNavigateToStudio}
                className="px-3.5 py-1.5 rounded-xl bg-dark-base text-white text-xs font-bold hover:bg-black"
              >
                Launch Evaluation
              </button>
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-border-subtle text-[10px] uppercase font-bold tracking-wider text-text-muted bg-canvas/60 select-none">
                  <th className="py-3 px-4 w-10 text-center">
                    <input
                      type="checkbox"
                      checked={selectedRunIds.length === filteredRuns.length && filteredRuns.length > 0}
                      onChange={toggleSelectAll}
                      className="rounded border-border-subtle text-brand-purple focus:ring-brand-purple cursor-pointer"
                    />
                  </th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Run ID</th>
                  <th className="py-3 px-4">Benchmark Task</th>
                  <th className="py-3 px-4">Model</th>
                  <th className="py-3 px-4">Duration & Turns</th>
                  <th className="py-3 px-4">Tokens & Cost</th>
                  <th className="py-3 px-4">Score</th>
                  <th className="py-3 px-4">Safety Audits</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle font-mono text-xs">
                {filteredRuns.map((run) => {
                  const isPassed = run.passed === true;
                  const isFailed = run.passed === false;
                  const isRunning = run.status === 'running' || run.status === 'pending';
                  const isSelected = selectedRunIds.includes(run.run_id);
                  const matchedTask = tasks.find((t) => t.task_id === run.task_id);

                  // Safety audit summary
                  const planAudit = run.audit_verdicts?.find((v) => v.metric_name === 'plan_adherence');
                  const halluAudit = run.audit_verdicts?.find((v) => v.metric_name === 'hallucination_detection');
                  const tamperAudit = run.audit_verdicts?.find((v) => v.metric_name === 'reward_tampering');

                  return (
                    <tr
                      key={run.run_id}
                      onClick={() => onSelectRun(run.run_id)}
                      className={`hover:bg-canvas transition-colors cursor-pointer ${
                        isSelected ? 'bg-purple-50/50' : ''
                      }`}
                    >
                      {/* Checkbox */}
                      <td className="py-3.5 px-4 text-center" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelectRun(run.run_id)}
                          className="rounded border-border-subtle text-brand-purple focus:ring-brand-purple cursor-pointer"
                        />
                      </td>

                      {/* Status */}
                      <td className="py-3.5 px-4 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          <IonIcon
                            icon={
                              isPassed
                                ? checkmarkCircle
                                : isFailed
                                ? closeCircle
                                : isRunning
                                ? hourglassOutline
                                : closeCircle
                            }
                            className={`text-sm ${
                              isPassed
                                ? 'text-status-cleared'
                                : isFailed
                                ? 'text-risk-high'
                                : isRunning
                                ? 'text-brand-purple animate-spin'
                                : 'text-amber-600'
                            }`}
                          />
                          <span
                            className={`text-[10px] uppercase px-2 py-0.5 rounded font-bold ${
                              isPassed
                                ? 'bg-emerald-50 text-emerald-700'
                                : isFailed
                                ? 'bg-rose-50 text-rose-700'
                                : isRunning
                                ? 'bg-purple-50 text-brand-purple'
                                : 'bg-amber-50 text-amber-800'
                            }`}
                          >
                            {isPassed ? 'Passed' : isFailed ? 'Failed' : run.status}
                          </span>
                        </div>
                      </td>

                      {/* Run ID */}
                      <td className="py-3.5 px-4 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center gap-1 text-text-primary">
                          <span className="font-mono text-xs">{run.run_id}</span>
                          <button
                            type="button"
                            onClick={() => handleCopyId(run.run_id)}
                            className="p-1 text-text-muted hover:text-text-primary rounded transition-colors"
                            title="Copy Run ID"
                          >
                            <IonIcon icon={copyOutline} className="text-xs" />
                          </button>
                          {copiedId === run.run_id && (
                            <span className="text-[9px] text-emerald-600 font-bold">Copied!</span>
                          )}
                        </div>
                      </td>

                      {/* Task ID */}
                      <td className="py-3.5 px-4 font-sans">
                        <div className="space-y-0.5">
                          <div className="font-bold text-text-primary text-xs font-mono">{run.task_id}</div>
                          <div className="text-[10px] text-text-secondary flex items-center gap-1.5">
                            <span className="capitalize">{matchedTask?.category || 'General'}</span>
                            <span>•</span>
                            <span className="uppercase text-[9px] px-1.5 py-0.2 rounded bg-canvas border border-border-subtle">
                              {matchedTask?.difficulty || 'Medium'}
                            </span>
                          </div>
                        </div>
                      </td>

                      {/* Model */}
                      <td className="py-3.5 px-4 font-sans text-xs">
                        <div className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg bg-surface-subtle text-text-primary font-mono text-[11px] border border-border-subtle">
                          {run.model}
                        </div>
                      </td>

                      {/* Duration & Turns */}
                      <td className="py-3.5 px-4 whitespace-nowrap text-text-secondary text-xs font-mono">
                        <div>{run.total_duration_sec.toFixed(1)}s</div>
                        <div className="text-[10px] text-text-muted">{run.total_steps || run.steps?.length || 0} turns</div>
                      </td>

                      {/* Tokens & Cost */}
                      <td className="py-3.5 px-4 whitespace-nowrap text-xs font-mono">
                        <div className="text-emerald-700 font-bold">${run.estimated_cost_usd?.toFixed(4) || '0.0000'}</div>
                        <div className="text-[10px] text-text-muted">{run.total_tokens?.toLocaleString() || 0} tok</div>
                      </td>

                      {/* Score */}
                      <td className="py-3.5 px-4 whitespace-nowrap font-bold text-xs">
                        <span className={isPassed ? 'text-emerald-700' : isFailed ? 'text-rose-700' : 'text-text-primary'}>
                          {run.reward !== null ? `${run.reward.toFixed(1)}/1.0` : '—'}
                        </span>
                      </td>

                      {/* Safety Audits */}
                      <td className="py-3.5 px-4 whitespace-nowrap">
                        <div className="flex items-center gap-1">
                          <span
                            title={`Plan Adherence: ${planAudit ? Math.round(planAudit.score * 100) + '%' : 'N/A'}`}
                            className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                              planAudit?.passed
                                ? 'bg-emerald-50 text-emerald-700'
                                : planAudit
                                ? 'bg-rose-50 text-rose-700'
                                : 'bg-canvas text-text-muted'
                            }`}
                          >
                            Plan
                          </span>
                          <span
                            title={`Hallucination Filter: ${halluAudit ? Math.round(halluAudit.score * 100) + '%' : 'N/A'}`}
                            className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                              halluAudit?.passed
                                ? 'bg-emerald-50 text-emerald-700'
                                : halluAudit
                                ? 'bg-rose-50 text-rose-700'
                                : 'bg-canvas text-text-muted'
                            }`}
                          >
                            Ground
                          </span>
                          <span
                            title={`Reward Tampering: ${tamperAudit ? (tamperAudit.passed ? 'Clean' : 'Tampered') : 'N/A'}`}
                            className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                              tamperAudit?.passed
                                ? 'bg-emerald-50 text-emerald-700'
                                : tamperAudit
                                ? 'bg-rose-50 text-rose-700'
                                : 'bg-canvas text-text-muted'
                            }`}
                          >
                            Safety
                          </span>
                        </div>
                      </td>

                      {/* Actions */}
                      <td className="py-3.5 px-4 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => onSelectRun(run.run_id)}
                            className="px-3 py-1 rounded-lg bg-surface-subtle text-brand-purple text-xs font-bold flex items-center gap-1 hover:bg-purple-100 transition-colors cursor-pointer"
                          >
                            <span>Inspect</span>
                            <IonIcon icon={arrowForwardOutline} className="text-[11px]" />
                          </button>

                          {onDeleteRun && (
                            <button
                              type="button"
                              onClick={() => {
                                if (confirm(`Delete run ${run.run_id}?`)) {
                                  onDeleteRun(run.run_id);
                                }
                              }}
                              className="p-1 rounded text-text-muted hover:text-rose-600 hover:bg-rose-50 transition-colors"
                              title="Delete run"
                            >
                              <IonIcon icon={trashOutline} className="text-xs" />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
