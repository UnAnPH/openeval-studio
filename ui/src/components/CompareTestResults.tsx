import React, { useMemo, useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  checkmarkCircle,
  codeSlashOutline,
  documentTextOutline,
  terminalOutline,
  warningOutline,
  searchOutline,
  swapHorizontalOutline,
  closeOutline,
  sparklesOutline,
} from 'ionicons/icons';
import { AgentStep, RunRecord } from '../types';

interface SemanticVerdict {
  run_a_id: string;
  run_b_id: string;
  task_id: string;
  nature_of_divergence: 'identical_strategy' | 'cosmetic_variation' | 'substantive_divergence' | 'opposite_strategies';
  semantic_similarity_score: number;
  verdict_summary: string;
  key_strategic_differences: string[];
  true_divergence_turn: number | null;
  attribution_reasoning: string;
}

interface CompareTestResultsProps {
  runs: RunRecord[];
  initialRunAId?: string;
  initialRunBId?: string;
  onNavigateToRuns?: () => void;
  onNavigateToStudio?: () => void;
}

// ---------------------------------------------------------------------------
// Scalable Run Picker Modal (Searchable across 10,000+ runs with faceted filters)
// ---------------------------------------------------------------------------
interface RunPickerModalProps {
  isOpen: boolean;
  title: string;
  accentColor: 'purple' | 'orange';
  selectedRunId: string;
  runs: RunRecord[];
  onSelect: (runId: string) => void;
  onClose: () => void;
}

const RunPickerModal: React.FC<RunPickerModalProps> = ({
  isOpen,
  title,
  accentColor,
  selectedRunId,
  runs,
  onSelect,
  onClose,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [taskFilter, setTaskFilter] = useState('all');
  const [modelFilter, setModelFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState<'all' | 'passed' | 'failed'>('all');
  const [sortBy, setSortBy] = useState<'newest' | 'oldest' | 'duration' | 'cost' | 'tokens'>('newest');

  // Distinct tasks & models
  const uniqueTasks = useMemo(() => {
    const tasks = Array.from(new Set(runs.map((r) => r.task_id).filter(Boolean)));
    return tasks.sort();
  }, [runs]);

  const uniqueModels = useMemo(() => {
    const models = Array.from(new Set(runs.map((r) => r.model).filter(Boolean)));
    return models.sort();
  }, [runs]);

  // Filtered & Sorted Runs
  const filteredRuns = useMemo(() => {
    return runs
      .filter((r) => {
        // Status filter
        if (statusFilter === 'passed' && !r.passed) return false;
        if (statusFilter === 'failed' && r.passed) return false;

        // Task filter
        if (taskFilter !== 'all' && r.task_id !== taskFilter) return false;

        // Model filter
        if (modelFilter !== 'all' && r.model !== modelFilter) return false;

        // Search query
        if (searchQuery.trim()) {
          const q = searchQuery.toLowerCase();
          const matchTask = r.task_id?.toLowerCase().includes(q);
          const matchModel = r.model?.toLowerCase().includes(q);
          const matchId = r.run_id?.toLowerCase().includes(q);
          const matchSummary = r.final_summary?.toLowerCase().includes(q);
          const matchReason = r.failure_reason?.toLowerCase().includes(q);
          const matchDev = r.human_reviewer?.toLowerCase().includes(q);
          if (!matchTask && !matchModel && !matchId && !matchSummary && !matchReason && !matchDev) {
            return false;
          }
        }
        return true;
      })
      .sort((a, b) => {
        if (sortBy === 'newest') {
          return new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime();
        }
        if (sortBy === 'oldest') {
          return new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime();
        }
        if (sortBy === 'duration') {
          return (a.total_duration_sec || 0) - (b.total_duration_sec || 0);
        }
        if (sortBy === 'cost') {
          return (a.estimated_cost_usd || 0) - (b.estimated_cost_usd || 0);
        }
        if (sortBy === 'tokens') {
          return (a.total_tokens || 0) - (b.total_tokens || 0);
        }
        return 0;
      });
  }, [runs, searchQuery, taskFilter, modelFilter, statusFilter, sortBy]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-dark-base/60 backdrop-blur-xs animate-fadeIn font-sans">
      <div className="bg-white w-full max-w-4xl rounded-2xl shadow-2xl border border-border-subtle flex flex-col max-h-[85vh] overflow-hidden">
        {/* Modal Header */}
        <div className="p-5 border-b border-border-subtle flex items-center justify-between bg-canvas/40">
          <div className="flex items-center gap-3">
            <div className={`w-3.5 h-3.5 rounded-full ${accentColor === 'purple' ? 'bg-brand-purple' : 'bg-accent-orange'}`} />
            <div>
              <h3 className="text-base font-bold text-text-primary">{title}</h3>
              <p className="text-xs text-text-secondary">
                Filter and select from {runs.length.toLocaleString()} evaluation runs
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-xl bg-white border border-border-subtle hover:bg-canvas text-text-secondary flex items-center justify-center transition-colors cursor-pointer"
          >
            <IonIcon icon={closeOutline} className="text-base" />
          </button>
        </div>

        {/* Search & Filter Controls Bar */}
        <div className="p-4 border-b border-border-subtle bg-white space-y-3">
          {/* Main Search Input */}
          <div className="relative">
            <input
              type="text"
              autoFocus
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by Task ID, Model, Run ID prefix, or failure reason..."
              className="w-full bg-canvas border border-border-subtle rounded-xl pl-9 pr-8 py-2 text-xs text-text-primary focus:outline-none focus:border-brand-primary placeholder:text-text-muted"
            />
            <IonIcon
              icon={searchOutline}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted text-sm pointer-events-none"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary text-xs cursor-pointer"
              >
                <IonIcon icon={closeOutline} />
              </button>
            )}
          </div>

          {/* Faceted Filter Dropdowns & Pills */}
          <div className="flex items-center justify-between gap-3 flex-wrap text-xs">
            <div className="flex items-center gap-2 flex-wrap">
              {/* Task Filter */}
              <select
                value={taskFilter}
                onChange={(e) => setTaskFilter(e.target.value)}
                className="bg-canvas border border-border-subtle rounded-lg px-2.5 py-1 text-xs text-text-primary font-medium focus:outline-none focus:border-brand-primary"
              >
                <option value="all">All Tasks ({uniqueTasks.length})</option>
                {uniqueTasks.map((taskId) => (
                  <option key={taskId} value={taskId}>
                    Task: {taskId}
                  </option>
                ))}
              </select>

              {/* Model Filter */}
              <select
                value={modelFilter}
                onChange={(e) => setModelFilter(e.target.value)}
                className="bg-canvas border border-border-subtle rounded-lg px-2.5 py-1 text-xs text-text-primary font-medium focus:outline-none focus:border-brand-primary"
              >
                <option value="all">All Models ({uniqueModels.length})</option>
                {uniqueModels.map((m) => (
                  <option key={m} value={m}>
                    Model: {m}
                  </option>
                ))}
              </select>

              {/* Status Segmented Buttons */}
              <div className="flex items-center bg-canvas p-0.5 rounded-lg border border-border-subtle text-[11px] font-bold">
                <button
                  type="button"
                  onClick={() => setStatusFilter('all')}
                  className={`px-2.5 py-0.5 rounded-md transition-colors ${
                    statusFilter === 'all' ? 'bg-white text-text-primary shadow-2xs' : 'text-text-muted hover:text-text-primary'
                  }`}
                >
                  All
                </button>
                <button
                  type="button"
                  onClick={() => setStatusFilter('passed')}
                  className={`px-2.5 py-0.5 rounded-md transition-colors ${
                    statusFilter === 'passed' ? 'bg-emerald-600 text-white shadow-2xs' : 'text-text-muted hover:text-text-primary'
                  }`}
                >
                  Passed
                </button>
                <button
                  type="button"
                  onClick={() => setStatusFilter('failed')}
                  className={`px-2.5 py-0.5 rounded-md transition-colors ${
                    statusFilter === 'failed' ? 'bg-rose-600 text-white shadow-2xs' : 'text-text-muted hover:text-text-primary'
                  }`}
                >
                  Failed
                </button>
              </div>
            </div>

            {/* Sort Dropdown */}
            <div className="flex items-center gap-1.5 text-[11px] text-text-muted font-mono">
              <span>Sort:</span>
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as any)}
                className="bg-canvas border border-border-subtle rounded-lg px-2 py-1 text-[11px] text-text-primary font-medium focus:outline-none"
              >
                <option value="newest">Newest First</option>
                <option value="oldest">Oldest First</option>
                <option value="duration">Fastest Duration</option>
                <option value="cost">Lowest Cost</option>
                <option value="tokens">Least Tokens</option>
              </select>
            </div>
          </div>
        </div>

        {/* Runs List (Scrollable) */}
        <div className="flex-1 overflow-y-auto divide-y divide-border-subtle p-2">
          {filteredRuns.length === 0 ? (
            <div className="p-12 text-center text-xs text-text-muted space-y-2">
              <IonIcon icon={warningOutline} className="text-3xl text-accent-orange mb-1" />
              <div className="font-bold text-text-primary">No Runs Match Your Filter</div>
              <p>Try clearing your search query or adjusting your filters.</p>
            </div>
          ) : (
            filteredRuns.map((r) => {
              const isSelected = r.run_id === selectedRunId;
              const dateStr = r.created_at ? new Date(r.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'Recent';
              return (
                <div
                  key={r.run_id}
                  onClick={() => {
                    onSelect(r.run_id);
                    onClose();
                  }}
                  className={`p-3.5 rounded-xl transition-all cursor-pointer flex flex-col sm:flex-row sm:items-center justify-between gap-3 ${
                    isSelected
                      ? accentColor === 'purple'
                        ? 'bg-brand-purple/10 border-2 border-brand-purple'
                        : 'bg-accent-orange/10 border-2 border-accent-orange'
                      : 'hover:bg-canvas/60 border border-transparent'
                  }`}
                >
                  <div className="space-y-1.5 flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      {/* Pass / Fail Status Badge */}
                      <span
                        className={`px-2 py-0.5 rounded-full text-[10px] font-bold font-mono uppercase tracking-wider ${
                          r.passed
                            ? 'bg-emerald-500/10 text-emerald-700 border border-emerald-500/20'
                            : 'bg-rose-500/10 text-rose-700 border border-rose-500/20'
                        }`}
                      >
                        {r.passed ? '✓ PASSED (1.0)' : '🛑 FAILED (0.0)'}
                      </span>

                      {/* Task ID Badge */}
                      <span className="px-2 py-0.5 rounded-md bg-dark-base text-white text-[11px] font-mono font-semibold">
                        {r.task_id}
                      </span>

                      {/* Model Name */}
                      <span className="px-2 py-0.5 rounded-md bg-surface-subtle text-text-primary text-[11px] font-mono">
                        {r.model}
                      </span>

                      {/* Run ID */}
                      <span className="text-[10px] font-mono text-text-muted">
                        id: <strong>{r.run_id.slice(0, 8)}</strong>
                      </span>
                    </div>

                    {/* Summary / Failure reason */}
                    <p className="text-xs text-text-secondary line-clamp-1">
                      {r.final_summary || r.failure_reason || (r.passed ? 'All verifier assertions passed.' : 'Task verification failure recorded.')}
                    </p>

                    {/* Meta details */}
                    <div className="flex items-center gap-3 text-[11px] text-text-muted font-mono">
                      <span>🕒 {dateStr}</span>
                      <span>·</span>
                      <span>⏱ {r.total_duration_sec.toFixed(1)}s</span>
                      <span>·</span>
                      <span>⚡ {r.total_tokens.toLocaleString()} tok</span>
                      <span>·</span>
                      <span>💰 ${r.estimated_cost_usd.toFixed(4)}</span>
                      {r.human_reviewer && (
                        <>
                          <span>·</span>
                          <span className="text-brand-purple font-semibold">Reviewer: {r.human_reviewer}</span>
                        </>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 flex-shrink-0">
                    {isSelected ? (
                      <span className="px-3 py-1 rounded-lg bg-dark-base text-white text-xs font-bold font-mono">
                        Selected
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="px-3 py-1.5 rounded-lg border border-border-subtle bg-white hover:bg-canvas text-xs font-bold text-text-primary shadow-2xs"
                      >
                        Select
                      </button>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Modal Footer */}
        <div className="p-3 px-5 border-t border-border-subtle bg-canvas/40 flex items-center justify-between text-xs text-text-muted font-mono">
          <span>
            Showing <strong className="text-text-primary">{filteredRuns.length}</strong> of <strong className="text-text-primary">{runs.length}</strong> runs
          </span>
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded-xl bg-white border border-border-subtle text-text-primary font-bold hover:bg-canvas cursor-pointer shadow-2xs"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main Compare Test Results View
// ---------------------------------------------------------------------------
export const CompareTestResults: React.FC<CompareTestResultsProps> = ({
  runs,
  initialRunAId,
  initialRunBId,
  onNavigateToRuns,
  onNavigateToStudio,
}) => {
  const [runAId, setRunAId] = useState<string>(initialRunAId || runs[0]?.run_id || '');
  const [runBId, setRunBId] = useState<string>(initialRunBId || runs[1]?.run_id || runs[0]?.run_id || '');
  const [activeTab, setActiveTab] = useState<'timeline' | 'diff' | 'metrics'>('timeline');
  const [pickerTarget, setPickerTarget] = useState<'candidate' | 'baseline' | null>(null);

  // Semantic Comparison State
  const [semanticVerdict, setSemanticVerdict] = useState<SemanticVerdict | null>(null);
  const [isLoadingSemantic, setIsLoadingSemantic] = useState<boolean>(false);
  const [semanticError, setSemanticError] = useState<string | null>(null);

  // React to prop changes if passed from outer view
  React.useEffect(() => {
    if (initialRunAId) setRunAId(initialRunAId);
    if (initialRunBId) setRunBId(initialRunBId);
  }, [initialRunAId, initialRunBId]);

  // Reset semantic verdict when run selection changes
  React.useEffect(() => {
    setSemanticVerdict(null);
    setSemanticError(null);
  }, [runAId, runBId]);

  const runA = runs.find((r) => r.run_id === runAId) || runs[0] || null;
  const runB = runs.find((r) => r.run_id === runBId) || runs[1] || runs[0] || null;

  // Handler to swap runs
  const handleSwapRuns = () => {
    const temp = runAId;
    setRunAId(runBId);
    setRunBId(temp);
  };

  // Handler to auto-match the same task
  const handleMatchSameTask = () => {
    if (!runA) return;
    const sameTaskRun = runs.find((r) => r.task_id === runA.task_id && r.run_id !== runA.run_id);
    if (sameTaskRun) {
      setRunBId(sameTaskRun.run_id);
    }
  };

  // Handler to trigger on-demand LLM semantic comparison
  const handleRunSemanticCompare = async () => {
    if (!runA || !runB) return;
    setIsLoadingSemantic(true);
    setSemanticError(null);
    try {
      const res = await fetch(`/api/eval/compare/semantic?run_a=${encodeURIComponent(runA.run_id)}&run_b=${encodeURIComponent(runB.run_id)}`);
      if (!res.ok) {
        throw new Error(`Server returned status ${res.status}`);
      }
      const data = await res.json();
      setSemanticVerdict(data);
    } catch (err: any) {
      setSemanticError(err?.message || 'Failed to generate AI semantic comparison');
    } finally {
      setIsLoadingSemantic(false);
    }
  };

  if (!runA || !runB || runs.length < 2) {
    return (
      <div className="bg-white p-12 rounded-2xl border border-border-subtle shadow-sm text-center space-y-4 font-sans">
        <div className="w-12 h-12 rounded-2xl bg-canvas border border-border-subtle mx-auto flex items-center justify-center text-text-muted">
          <IonIcon icon={warningOutline} className="text-2xl text-accent-orange" />
        </div>
        <h3 className="text-base font-bold text-text-primary">Need at Least 2 Runs to Compare</h3>
        <p className="text-xs text-text-secondary max-w-sm mx-auto">
          Launch two evaluations (same or different models/tasks), then return here for trajectory
          divergence and metric diffs. Offline fake-pass runs are disabled — real Launch or hermetic
          verifier only.
        </p>
        <div className="flex items-center justify-center gap-2 flex-wrap">
          {onNavigateToStudio && (
            <button
              type="button"
              onClick={onNavigateToStudio}
              className="px-4 py-2 rounded-xl bg-brand-purple text-white text-xs font-bold hover:opacity-90 cursor-pointer"
            >
              Launch Evaluation
            </button>
          )}
          {onNavigateToRuns && (
            <button
              type="button"
              onClick={onNavigateToRuns}
              className="px-4 py-2 rounded-xl bg-dark-base text-white text-xs font-bold hover:bg-black cursor-pointer"
            >
              Go to Runs
            </button>
          )}
        </div>      </div>
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

  // 3. Multi-Tool Code File & Command Extractor
  interface ExtractedCodeItem {
    step_number: number;
    tool: string;
    path: string;
    content: string;
  }

  const extractCodeFromRun = (r: RunRecord): ExtractedCodeItem[] => {
    const items: ExtractedCodeItem[] = [];
    if (!r.steps) return items;

    r.steps.forEach((s) => {
      const tool = s.action?.tool || '';
      const path = s.action?.path || '';
      const content = s.action?.content || '';
      const cmd = s.action?.command || '';

      // Direct file mutation tools
      if (['write_file', 'write_to_file', 'replace_file_content', 'edit_file'].includes(tool)) {
        items.push({
          step_number: s.step_number,
          tool,
          path: path || `mutation_step_${s.step_number}.txt`,
          content: content || cmd || '(empty file)',
        });
      } else if (['execute_bash', 'run_command', 'bash'].includes(tool) && cmd) {
        // Check for shell scripts with heredocs or inline file writes
        if (cmd.includes('cat <<') || cmd.includes('cat >') || cmd.includes('echo "') || cmd.includes('printf "') || cmd.includes('\n')) {
          const redirectMatch = cmd.match(/>>\s*([^\s;&|]+)|>\s*([^\s;&|]+)/);
          const targetPath = redirectMatch ? (redirectMatch[1] || redirectMatch[2]) : `script_step_${s.step_number}.sh`;
          items.push({
            step_number: s.step_number,
            tool: 'execute_bash (file write)',
            path: targetPath,
            content: cmd,
          });
        }
      }
    });

    return items;
  };

  const filesA = extractCodeFromRun(runA);
  const filesB = extractCodeFromRun(runB);

  // Group all unique file paths across both runs
  const allFilePaths = Array.from(
    new Set([...filesA.map((f) => f.path), ...filesB.map((f) => f.path)])
  );

  const getToolIcon = (tool?: string) => {
    switch (tool) {
      case 'execute_bash':
      case 'run_command':
        return terminalOutline;
      case 'view_file':
      case 'read_file':
        return documentTextOutline;
      case 'write_file':
      case 'replace_file_content':
      case 'write_to_file':
        return codeSlashOutline;
      case 'finish':
        return checkmarkCircle;
      default:
        return terminalOutline;
    }
  };

  // High-contrast line diff generator
  const renderSimpleDiff = (textA: string, textB: string) => {
    const linesA = textA ? textA.split('\n') : [];
    const linesB = textB ? textB.split('\n') : [];
    const maxLines = Math.max(linesA.length, linesB.length);
    const diffRows = [];

    for (let i = 0; i < maxLines; i++) {
      const lineA = linesA[i];
      const lineB = linesB[i];
      const isDiff = lineA !== lineB;

      diffRows.push(
        <div key={i} className={`grid grid-cols-2 text-[11px] font-mono border-b border-border-subtle/40 ${isDiff ? 'bg-amber-500/5' : ''}`}>
          {/* Candidate Line */}
          <div className={`p-1.5 px-3 border-r border-border-subtle overflow-x-auto flex items-start gap-2 ${
            isDiff && lineA !== undefined ? 'text-rose-700 bg-rose-500/10' : 'text-slate-700'
          }`}>
            <span className="text-slate-400 select-none w-6 text-right flex-shrink-0 text-[10px]">{lineA !== undefined ? i + 1 : ''}</span>
            <span className="font-mono whitespace-pre flex-1">
              {lineA !== undefined ? (isDiff ? `- ${lineA}` : `  ${lineA}`) : ''}
            </span>
          </div>

          {/* Baseline Line */}
          <div className={`p-1.5 px-3 overflow-x-auto flex items-start gap-2 ${
            isDiff && lineB !== undefined ? 'text-emerald-700 bg-emerald-500/10' : 'text-slate-700'
          }`}>
            <span className="text-slate-400 select-none w-6 text-right flex-shrink-0 text-[10px]">{lineB !== undefined ? i + 1 : ''}</span>
            <span className="font-mono whitespace-pre flex-1">
              {lineB !== undefined ? (isDiff ? `+ ${lineB}` : `  ${lineB}`) : ''}
            </span>
          </div>
        </div>
      );
    }

    return (
      <div className="bg-[#14121F] rounded-xl overflow-hidden border border-border-subtle shadow-inner">
        <div className="grid grid-cols-2 bg-[#1C182E] text-slate-300 text-[10px] font-mono uppercase font-bold p-2.5 px-3 border-b border-[#2A2445]">
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-brand-purple" />
            <span>Candidate (Run A: {runA.model})</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-accent-orange" />
            <span>Baseline (Run B: {runB.model})</span>
          </div>
        </div>
        <div className="bg-white max-h-96 overflow-y-auto divide-y divide-border-subtle/30">{diffRows}</div>
      </div>
    );
  };

  const hasMatchingTaskForRunB = runs.some((r) => r.task_id === runA.task_id && r.run_id !== runA.run_id);

  return (
    <div className="flex-1 min-h-0 flex flex-col h-full bg-[#fcfcfd] text-[#1e2029] font-sans p-6 space-y-5 overflow-y-auto">
      {/* 1. Card Block Header */}
      <div className="bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm flex items-center justify-between shrink-0">
        <h1 className="text-base font-bold text-slate-900 tracking-tight flex items-center gap-2.5">Compare Runs</h1>

        {/* View Mode & Quick Action Controls */}
        <div className="flex items-center gap-2.5">
          {/* Swap Runs Button */}
          <button
            type="button"
            onClick={handleSwapRuns}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 text-xs font-medium text-slate-700 transition-all shadow-xs cursor-pointer"
            title="Swap Run A (Candidate) and Run B (Baseline)"
          >
            <IonIcon icon={swapHorizontalOutline} className="text-sm text-indigo-600" />
            <span>Swap A ⇄ B</span>
          </button>

          {/* Same Task Matcher Button */}
          {hasMatchingTaskForRunB && runA.task_id !== runB.task_id && (
            <button
              type="button"
              onClick={handleMatchSameTask}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 text-xs font-semibold transition-all shadow-xs cursor-pointer"
            >
              <IonIcon icon={sparklesOutline} className="text-sm" />
              <span>Match Task</span>
            </button>
          )}

          {/* View mode toggle */}
          <div className="flex items-center bg-slate-100 p-1 rounded-xl border border-slate-200/60 text-xs font-medium">
            <button
              type="button"
              onClick={() => setActiveTab('timeline')}
              className={`px-3 py-1.5 rounded-lg text-xs transition-all cursor-pointer ${
                activeTab === 'timeline'
                  ? 'bg-white text-slate-900 shadow-xs font-bold'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Trajectory
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('diff')}
              className={`px-3 py-1.5 rounded-lg text-xs transition-all cursor-pointer ${
                activeTab === 'diff'
                  ? 'bg-white text-slate-900 shadow-xs font-bold'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Code Diffs ({filesA.length + filesB.length})
            </button>
          </div>
        </div>
      </div>

      {/* 2. Upgraded Searchable Run Selector Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Run A Selector Card (Candidate) */}
        <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-3.5 relative">
          <div className="flex items-center justify-between text-xs">
            <span className="font-bold text-text-primary flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-brand-purple" />
              Run A (Candidate)
            </span>
            <span
              className={`text-[10px] font-mono font-bold px-2.5 py-0.5 rounded-full uppercase tracking-wider ${
                runA.passed ? 'text-emerald-700 bg-emerald-50 border border-emerald-200' : 'text-rose-700 bg-rose-50 border border-rose-200'
              }`}
            >
              {runA.passed ? '✓ PASSED (1.0)' : '🛑 FAILED (0.0)'}
            </span>
          </div>

          {/* Selected Run Details Header */}
          <div className="p-3.5 bg-canvas/60 rounded-xl border border-border-subtle space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="font-bold text-sm text-text-primary font-mono truncate">
                {runA.task_id}
              </div>
              <span className="text-[11px] font-mono text-brand-purple bg-brand-purple/10 px-2 py-0.5 rounded">
                {runA.model}
              </span>
            </div>

            <div className="grid grid-cols-3 gap-2 text-[11px] font-mono text-text-muted pt-1">
              <div>
                <span className="block text-[9px] uppercase text-text-muted">Duration</span>
                <strong className="text-text-primary">{runA.total_duration_sec.toFixed(1)}s</strong>
              </div>
              <div>
                <span className="block text-[9px] uppercase text-text-muted">Tokens</span>
                <strong className="text-text-primary">{runA.total_tokens.toLocaleString()}</strong>
              </div>
              <div>
                <span className="block text-[9px] uppercase text-text-muted">Cost</span>
                <strong className="text-text-primary">${runA.estimated_cost_usd.toFixed(4)}</strong>
              </div>
            </div>
          </div>

          {/* Action Row: Non-editable selector on the left, Choose button on the right */}
          <div className="flex items-center gap-2">
            <div className="flex-1 flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-xs font-mono text-slate-700 min-w-0">
              <span className="text-slate-400 select-none text-[11px]">Selected:</span>
              <span className="font-bold text-slate-900 truncate">{runA.run_id.slice(0, 10)}...</span>
              <span className="text-slate-500 truncate text-[11px]">({runA.model})</span>
            </div>
            <button
              type="button"
              onClick={() => setPickerTarget('candidate')}
              className="px-4 py-2 rounded-xl bg-slate-900 hover:bg-black text-white text-xs font-bold flex items-center gap-1.5 transition-all shadow-xs cursor-pointer shrink-0"
            >
              <IonIcon icon={searchOutline} className="text-sm" />
              <span>Choose Run</span>
            </button>
          </div>
        </div>

        {/* Run B Selector Card (Baseline) */}
        <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-3.5 relative">
          <div className="flex items-center justify-between text-xs">
            <span className="font-bold text-text-primary flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-accent-orange" />
              Run B (Baseline)
            </span>
            <span
              className={`text-[10px] font-mono font-bold px-2.5 py-0.5 rounded-full uppercase tracking-wider ${
                runB.passed ? 'text-emerald-700 bg-emerald-50 border border-emerald-200' : 'text-rose-700 bg-rose-50 border border-rose-200'
              }`}
            >
              {runB.passed ? '✓ PASSED (1.0)' : '🛑 FAILED (0.0)'}
            </span>
          </div>

          {/* Selected Run Details Header */}
          <div className="p-3.5 bg-canvas/60 rounded-xl border border-border-subtle space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="font-bold text-sm text-text-primary font-mono truncate">
                {runB.task_id}
              </div>
              <span className="text-[11px] font-mono text-accent-orange bg-accent-orange/10 px-2 py-0.5 rounded">
                {runB.model}
              </span>
            </div>

            <div className="grid grid-cols-3 gap-2 text-[11px] font-mono text-text-muted pt-1">
              <div>
                <span className="block text-[9px] uppercase text-text-muted">Duration</span>
                <strong className="text-text-primary">{runB.total_duration_sec.toFixed(1)}s</strong>
              </div>
              <div>
                <span className="block text-[9px] uppercase text-text-muted">Tokens</span>
                <strong className="text-text-primary">{runB.total_tokens.toLocaleString()}</strong>
              </div>
              <div>
                <span className="block text-[9px] uppercase text-text-muted">Cost</span>
                <strong className="text-text-primary">${runB.estimated_cost_usd.toFixed(4)}</strong>
              </div>
            </div>
          </div>

          {/* Action Row: Non-editable selector on the left, Choose button on the right */}
          <div className="flex items-center gap-2">
            <div className="flex-1 flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-xs font-mono text-slate-700 min-w-0">
              <span className="text-slate-400 select-none text-[11px]">Selected:</span>
              <span className="font-bold text-slate-900 truncate">{runB.run_id.slice(0, 10)}...</span>
              <span className="text-slate-500 truncate text-[11px]">({runB.model})</span>
            </div>
            <button
              type="button"
              onClick={() => setPickerTarget('baseline')}
              className="px-4 py-2 rounded-xl bg-accent-orange hover:bg-orange-600 text-white text-xs font-bold flex items-center gap-1.5 transition-all shadow-xs cursor-pointer shrink-0"
            >
              <IonIcon icon={searchOutline} className="text-sm" />
              <span>Choose Run</span>
            </button>
          </div>
        </div>
      </div>

      {/* 3. Dynamic Live Metric Differentials Bar */}
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

      {/* 4. AI Semantic Trajectory Analysis (LLM Judge) */}
      <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-3.5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border-subtle pb-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-purple-50 text-brand-purple flex items-center justify-center flex-shrink-0">
              <IonIcon icon={sparklesOutline} className="text-lg" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-bold text-text-primary">
                  AI Semantic Trajectory Judge
                </h3>
                <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full bg-surface-subtle text-brand-purple">
                  LLM Semantic Evaluator
                </span>
              </div>
              <p className="text-xs text-text-secondary">
                Evaluates functional equivalence, filters cosmetic syntax noise, and identifies true strategic divergence
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={handleRunSemanticCompare}
            disabled={isLoadingSemantic}
            className="px-4 py-2 rounded-xl bg-brand-purple hover:bg-purple-700 text-white text-xs font-bold transition-all flex items-center justify-center gap-1.5 shadow-xs cursor-pointer disabled:opacity-50 flex-shrink-0"
          >
            <IonIcon icon={sparklesOutline} className={`text-sm ${isLoadingSemantic ? 'animate-spin' : ''}`} />
            <span>{isLoadingSemantic ? 'Evaluating Trajectory Delta...' : '⚡ Run AI Semantic Comparison'}</span>
          </button>
        </div>

        {semanticError && (
          <div className="p-3 bg-rose-50 border border-rose-200 text-rose-800 text-xs rounded-xl flex items-center gap-2">
            <IonIcon icon={warningOutline} className="text-base flex-shrink-0" />
            <span>{semanticError}</span>
          </div>
        )}

        {semanticVerdict ? (
          <div className="space-y-3 animate-fadeIn">
            {/* High level verdict badge + similarity bar */}
            <div className="p-3.5 bg-canvas/60 rounded-xl border border-border-subtle flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className={`text-[11px] font-mono font-bold uppercase px-3 py-1 rounded-full ${
                    semanticVerdict.nature_of_divergence === 'identical_strategy' ||
                    semanticVerdict.nature_of_divergence === 'cosmetic_variation'
                      ? 'bg-emerald-100 text-emerald-800 border border-emerald-300'
                      : 'bg-amber-100 text-amber-900 border border-amber-300'
                  }`}
                >
                  {semanticVerdict.nature_of_divergence === 'identical_strategy'
                    ? '🟢 Identical Strategy'
                    : semanticVerdict.nature_of_divergence === 'cosmetic_variation'
                    ? '🟢 Cosmetic Variation (Functionally Equivalent)'
                    : '🟠 Substantive Strategic Divergence'}
                </span>

                <span className="text-xs font-mono text-text-secondary">
                  Equivalence Score: <strong className="text-text-primary">{Math.round(semanticVerdict.semantic_similarity_score * 100)}%</strong>
                </span>
              </div>

              <div className="text-xs font-mono text-text-muted">
                {semanticVerdict.true_divergence_turn !== null ? (
                  <span className="text-amber-800 font-bold">
                    ⚡ Strategic Fork at Turn #{semanticVerdict.true_divergence_turn}
                  </span>
                ) : (
                  <span className="text-emerald-700 font-bold">
                    ✓ No Strategic Fork (Cosmetic Only)
                  </span>
                )}
              </div>
            </div>

            {/* Verdict Summary */}
            <div className="p-3.5 bg-white rounded-xl border border-border-subtle text-xs text-text-primary leading-relaxed space-y-1.5 shadow-2xs">
              <div className="font-bold text-text-secondary font-mono text-[11px] uppercase">
                Executive Synthesis
              </div>
              <p>{semanticVerdict.verdict_summary}</p>
            </div>

            {/* Key Differences List */}
            {semanticVerdict.key_strategic_differences?.length > 0 && (
              <div className="p-3.5 bg-white rounded-xl border border-border-subtle text-xs space-y-2 shadow-2xs">
                <div className="font-bold text-text-secondary font-mono text-[11px] uppercase">
                  Key Strategic Differentials & Workflow Shifts
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {semanticVerdict.key_strategic_differences.map((diff, idx) => (
                    <div
                      key={idx}
                      className="p-2.5 rounded-lg bg-canvas text-text-secondary border border-border-subtle/80 flex items-start gap-2"
                    >
                      <span className="text-brand-purple font-bold font-mono text-[11px] flex-shrink-0">
                        #{idx + 1}
                      </span>
                      <span className="leading-tight">{diff}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Attribution Reasoning */}
            <div className="p-3 rounded-xl bg-purple-50/60 border border-brand-purple/20 text-xs text-purple-950 font-sans flex items-start gap-2">
              <span className="font-bold font-mono text-[11px] uppercase text-brand-purple flex-shrink-0">
                Attribution:
              </span>
              <span>{semanticVerdict.attribution_reasoning}</span>
            </div>
          </div>
        ) : (
          !isLoadingSemantic && (
            <div className="p-3 bg-canvas rounded-xl text-center text-xs text-text-muted font-sans">
              Click <strong>Run AI Semantic Comparison</strong> above to have an LLM synthesize the exact behavioral delta between <code>{runA.model}</code> and <code>{runB.model}</code>, filtering out whitespace and cosmetic syntax differences.
            </div>
          )
        )}
      </div>

      {/* 5. Divergence Notification Banner */}
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

      {/* 5. TAB 1: SYNCHRONIZED TIMELINE */}
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
                        <IonIcon icon={getToolIcon(stepA?.action?.tool)} />
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

                    {stepA?.action?.command && (
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
                        <IonIcon icon={getToolIcon(stepB?.action?.tool)} />
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

                    {stepB?.action?.command && (
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

      {/* 6. TAB 2: CODE DIFFS */}
      {activeTab === 'diff' && (
        <div className="space-y-4">
          {allFilePaths.length === 0 ? (
            <div className="p-8 text-center bg-white rounded-2xl border border-border-subtle text-text-muted text-xs space-y-3">
              <IonIcon icon={codeSlashOutline} className="text-3xl text-brand-purple mx-auto" />
              <div className="font-bold text-text-primary">No Direct File Write Mutations Recorded</div>
              <p className="text-text-secondary max-w-md mx-auto">
                Neither run executed direct file-writing tools (e.g. <code>write_file</code>, <code>replace_file_content</code>). You can inspect the step-by-step bash commands and reasoning divergence in the <strong>Trajectory Timeline</strong> tab.
              </p>
            </div>
          ) : (
            allFilePaths.map((filePath, idx) => {
              const fileA = filesA.find((f) => f.path === filePath);
              const fileB = filesB.find((f) => f.path === filePath);
              return (
                <div key={idx} className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-3">
                  <div className="flex items-center justify-between text-xs font-mono">
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-text-primary bg-surface-subtle px-2.5 py-1 rounded-lg">
                        📄 {filePath}
                      </span>
                      <span className="text-text-muted text-[11px]">
                        ({fileA ? `Candidate Turn #${fileA.step_number}` : 'Not written in Candidate'} vs {fileB ? `Baseline Turn #${fileB.step_number}` : 'Not written in Baseline'})
                      </span>
                    </div>
                  </div>
                  {renderSimpleDiff(fileA?.content || '', fileB?.content || '')}
                </div>
              );
            })
          )}
        </div>
      )}

      {/* 7. Scalable Run Picker Modal */}
      <RunPickerModal
        isOpen={pickerTarget !== null}
        title={pickerTarget === 'candidate' ? 'Select Candidate Run (Run A)' : 'Select Baseline Run (Run B)'}
        accentColor={pickerTarget === 'candidate' ? 'purple' : 'orange'}
        selectedRunId={pickerTarget === 'candidate' ? runAId : runBId}
        runs={runs}
        onSelect={(id) => {
          if (pickerTarget === 'candidate') setRunAId(id);
          else if (pickerTarget === 'baseline') setRunBId(id);
        }}
        onClose={() => setPickerTarget(null)}
      />
    </div>
  );
};




