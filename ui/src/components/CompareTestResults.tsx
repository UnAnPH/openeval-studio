import { Icon } from '../lib/icon';
import {
  AlertTriangle,
  ArrowLeftRight,
  Check,
  CheckCircle,
  Code,
  FileText,
  GitFork,
  Info,
  Search,
  Sparkles,
  Terminal,
  X,
  Zap,
} from 'lucide-react';
import React, { useMemo, useState } from 'react';
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

function formatRunIdTail(id: string, maxLen: number = 34): string {
  if (!id) return '';
  if (id.length <= maxLen) return id;
  const tailLen = 18; // Preserves e.g. "-192514-1ti8q0" or "-20261016-192514"
  const headLen = Math.max(8, maxLen - tailLen - 3);
  return `${id.slice(0, headLen)}...${id.slice(-tailLen)}`;
}

// ---------------------------------------------------------------------------
// Scalable Run Picker Modal (Searchable across 10,000+ runs with faceted filters)
// ---------------------------------------------------------------------------
interface RunPickerModalProps {
  isOpen: boolean;
  title: string;
  accentColor: 'purple' | 'orange';
  selectedRunId: string;
  preferredTaskId?: string;
  runs: RunRecord[];
  onSelect: (runId: string) => void;
  onClose: () => void;
}

const RunPickerModal: React.FC<RunPickerModalProps> = ({
  isOpen,
  title,
  accentColor,
  selectedRunId,
  preferredTaskId,
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
            className="h-9 w-9 rounded-xl bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 inline-flex items-center justify-center transition-colors cursor-pointer shadow-2xs"
          >
            <Icon icon={X} className="text-base" />
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
              className="h-9 w-full bg-white border border-slate-200 rounded-xl pl-9 pr-8 text-xs text-slate-800 focus:outline-none focus:border-brand-primary placeholder:text-slate-400 font-mono shadow-2xs"
            />
            <Icon
              icon={Search}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm pointer-events-none"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-700 text-xs cursor-pointer"
              >
                <Icon icon={X} />
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
                className="h-9 bg-white border border-slate-200 rounded-xl pl-3 pr-8 text-xs text-slate-700 font-semibold focus:outline-none focus:border-brand-primary shadow-2xs cursor-pointer"
              >
                <option value="all">All Tasks ({uniqueTasks.length})</option>
                {uniqueTasks.map((taskId) => (
                  <option key={taskId} value={taskId}>
                    Task: {taskId}
                  </option>
                ))}
              </select>

              {preferredTaskId && uniqueTasks.includes(preferredTaskId) && (
                <button
                  type="button"
                  onClick={() => setTaskFilter(taskFilter === preferredTaskId ? 'all' : preferredTaskId)}
                  className={`h-9 px-3 rounded-xl border text-xs font-semibold inline-flex items-center gap-1.5 transition-all cursor-pointer shadow-2xs ${
                    taskFilter === preferredTaskId
                      ? 'bg-indigo-600 text-white border-indigo-600 font-bold'
                      : 'bg-white hover:bg-slate-50 text-slate-700 border-slate-200'
                  }`}
                  title={`Filter to runs for task "${preferredTaskId}"`}
                >
                  <Icon icon={Sparkles} className="text-xs" />
                  <span>Only {preferredTaskId}</span>
                </button>
              )}

              {/* Model Filter */}
              <select
                value={modelFilter}
                onChange={(e) => setModelFilter(e.target.value)}
                className="h-9 bg-white border border-slate-200 rounded-xl pl-3 pr-8 text-xs text-slate-700 font-semibold focus:outline-none focus:border-brand-primary shadow-2xs cursor-pointer"
              >
                <option value="all">All Models ({uniqueModels.length})</option>
                {uniqueModels.map((m) => (
                  <option key={m} value={m}>
                    Model: {m}
                  </option>
                ))}
              </select>

              {/* Status Segmented Buttons */}
              <div className="h-9 bg-slate-100 p-1 rounded-xl border border-slate-200/70 inline-flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setStatusFilter('all')}
                  className={`h-7 px-3 rounded-lg text-xs font-semibold inline-flex items-center justify-center transition-all cursor-pointer ${
                    statusFilter === 'all' ? 'bg-white text-slate-900 shadow-xs font-bold' : 'text-slate-500 hover:text-slate-900'
                  }`}
                >
                  All
                </button>
                <button
                  type="button"
                  onClick={() => setStatusFilter('passed')}
                  className={`h-7 px-3 rounded-lg text-xs font-semibold inline-flex items-center justify-center transition-all cursor-pointer ${
                    statusFilter === 'passed' ? 'bg-white text-slate-900 shadow-xs font-bold' : 'text-slate-500 hover:text-slate-900'
                  }`}
                >
                  Passed
                </button>
                <button
                  type="button"
                  onClick={() => setStatusFilter('failed')}
                  className={`h-7 px-3 rounded-lg text-xs font-semibold inline-flex items-center justify-center transition-all cursor-pointer ${
                    statusFilter === 'failed' ? 'bg-white text-slate-900 shadow-xs font-bold' : 'text-slate-500 hover:text-slate-900'
                  }`}
                >
                  Failed
                </button>
              </div>
            </div>

            {/* Sort Dropdown */}
            <div className="flex items-center gap-1.5 text-xs text-text-muted font-mono">
              <span>Sort:</span>
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as any)}
                className="h-8 bg-canvas border border-border-subtle rounded-lg pl-2.5 pr-7 text-xs text-text-primary font-semibold focus:outline-none cursor-pointer"
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
              <Icon icon={AlertTriangle} className="text-3xl text-accent-orange mb-1" />
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
                      <span className="px-2 py-0.5 rounded-md bg-slate-100 border border-slate-200 text-slate-700 text-[11px] font-mono" title={r.run_id}>
                        id: <strong className="text-slate-900">{formatRunIdTail(r.run_id, 38)}</strong>
                      </span>
                    </div>

                    {/* Meta details */}
                    <div className="flex items-center gap-3 text-[11px] text-text-muted font-mono">
                      <span>🕒 {dateStr}</span>
                      <span>·</span>
                      <span>⏱ {(r.total_duration_sec != null ? r.total_duration_sec : 0).toFixed(1)}s</span>
                      <span>·</span>
                      <span>⚡ {r.total_tokens.toLocaleString()} tok</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {isSelected ? (
                      <span className="h-8 px-3 rounded-lg text-xs font-semibold font-mono inline-flex items-center justify-center bg-slate-900 text-white shadow-xs">
                        Selected
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="h-8 px-3 rounded-lg text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 shadow-2xs"
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
            className="h-9 px-4 rounded-xl text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer bg-slate-900 hover:bg-black text-white shadow-xs"
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
          <Icon icon={AlertTriangle} className="text-2xl text-accent-orange" />
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
              className="h-9 px-4 rounded-xl text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer bg-slate-900 hover:bg-black text-white shadow-xs"
            >
              Launch Evaluation
            </button>
          )}
          {onNavigateToRuns && (
            <button
              type="button"
              onClick={onNavigateToRuns}
              className="h-9 px-4 rounded-xl text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 shadow-2xs"
            >
              Go to Runs
            </button>
          )}
        </div>      </div>
    );
  }

  // 1. Dynamic Metric Differentials
  const durationDelta = (runA.total_duration_sec || 0) - (runB.total_duration_sec || 0);
  const tokenDelta = runA.total_tokens - runB.total_tokens;
  const costDelta = runA.estimated_cost_usd - runB.estimated_cost_usd;

  const statusShiftText =
    runA.passed === runB.passed
      ? runA.passed
        ? 'Both Passed'
        : 'Both Failed'
      : runA.passed
      ? 'Fail ➔ Pass'
      : 'Pass ➔ Fail';

  // 2. Synchronized Steps & Strategic Divergence Tracking
  const maxSteps = Math.max(runA.steps?.length || 0, runB.steps?.length || 0);
  const stepPairs: { stepA?: AgentStep; stepB?: AgentStep; turn: number }[] = [];

  // Strategic divergence turn is determined by the AI Semantic Trajectory Judge rather than naive syntax diffs
  const strategicForkTurn = semanticVerdict?.true_divergence_turn ?? null;

  for (let i = 0; i < maxSteps; i++) {
    const stepA = runA.steps?.[i];
    const stepB = runB.steps?.[i];
    const turn = i + 1;
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
        return Terminal;
      case 'view_file':
      case 'read_file':
        return FileText;
      case 'write_file':
      case 'replace_file_content':
      case 'write_to_file':
        return Code;
      case 'finish':
        return CheckCircle;
      default:
        return Terminal;
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
            className="h-9 px-3.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 text-xs font-semibold text-slate-700 shadow-2xs inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer"
            title="Swap Run A (Candidate) and Run B (Baseline)"
          >
            <Icon icon={ArrowLeftRight} className="text-sm text-indigo-600" />
            <span>Swap A ⇄ B</span>
          </button>

          {/* Same Task Matcher Button */}
          {hasMatchingTaskForRunB && runA.task_id !== runB.task_id && (
            <button
              type="button"
              onClick={handleMatchSameTask}
              className="h-9 px-3.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 text-xs font-semibold text-slate-700 shadow-2xs inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer"
            >
              <Icon icon={Sparkles} className="text-sm text-indigo-600" />
              <span>Match Task</span>
            </button>
          )}

          {/* View mode toggle */}
          <div className="h-9 bg-slate-100 p-1 rounded-xl border border-slate-200/70 inline-flex items-center gap-1">
            <button
              type="button"
              onClick={() => setActiveTab('timeline')}
              className={`h-7 px-3 rounded-lg text-xs font-semibold inline-flex items-center justify-center transition-all cursor-pointer ${
                activeTab === 'timeline'
                  ? 'bg-white text-slate-900 shadow-xs font-bold'
                  : 'text-slate-500 hover:text-slate-900'
              }`}
            >
              Trajectory
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('diff')}
              className={`h-7 px-3 rounded-lg text-xs font-semibold inline-flex items-center justify-center transition-all cursor-pointer ${
                activeTab === 'diff'
                  ? 'bg-white text-slate-900 shadow-xs font-bold'
                  : 'text-slate-500 hover:text-slate-900'
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
                <strong className="text-text-primary">{(runA.total_duration_sec != null ? runA.total_duration_sec : 0).toFixed(1)}s</strong>
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
            <div className="h-9 flex-1 flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-xl px-3 text-xs font-mono text-slate-700 min-w-0 overflow-hidden shadow-2xs">
              <span className="text-slate-400 select-none text-[11px] shrink-0">Selected:</span>
              <span className="font-bold text-slate-900" title={runA.run_id}>{formatRunIdTail(runA.run_id, 48)}</span>
              {/* <span className="text-slate-500 truncate text-[11px] min-w-0">({runA.model})</span> */}
            </div>
            <button
              type="button"
              onClick={() => setPickerTarget('candidate')}
              className="h-9 px-4 rounded-xl text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer bg-slate-900 hover:bg-black text-white shadow-xs shrink-0"
            >
              <Icon icon={Search} className="text-sm" />
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
                <strong className="text-text-primary">{(runB.total_duration_sec != null ? runB.total_duration_sec : 0).toFixed(1)}s</strong>
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
            <div className="h-9 flex-1 flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-xl px-3 text-xs font-mono text-slate-700 min-w-0 overflow-hidden shadow-2xs">
              <span className="text-slate-400 select-none text-[11px] shrink-0">Selected:</span>
              <span className="font-bold text-slate-900" title={runB.run_id}>{formatRunIdTail(runB.run_id, 48)}</span>
              {/* <span className="text-slate-500 truncate text-[11px] min-w-0">({runB.model})</span> */}
            </div>
            <button
              type="button"
              onClick={() => setPickerTarget('baseline')}
              className="h-9 px-4 rounded-xl text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer bg-slate-900 hover:bg-black text-white shadow-xs shrink-0"
            >
              <Icon icon={Search} className="text-sm" />
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
        </div>

        {/* Duration Delta */}
        <div className="p-4 rounded-2xl bg-white border border-border-subtle shadow-sm space-y-1">
          <div className="text-[10px] uppercase font-mono font-bold text-text-muted">Duration</div>
          <div className={`text-base font-bold font-mono ${durationDelta <= 0 ? 'text-emerald-700' : 'text-rose-700'}`}>
            {durationDelta <= 0 ? `${durationDelta.toFixed(1)}s (Faster)` : `+${durationDelta.toFixed(1)}s (Slower)`}
          </div>
          <div className="text-[10px] font-mono text-text-secondary">
            {(runA.total_duration_sec != null ? runA.total_duration_sec : 0).toFixed(1)}s vs {(runB.total_duration_sec != null ? runB.total_duration_sec : 0).toFixed(1)}s
          </div>
        </div>

        {/* Tokens Delta */}
        <div className="p-4 rounded-2xl bg-white border border-border-subtle shadow-sm space-y-1">
          <div className="text-[10px] uppercase font-mono font-bold text-text-muted">Tokens</div>
          <div className={`text-base font-bold font-mono ${tokenDelta <= 0 ? 'text-emerald-700' : 'text-rose-700'}`}>
            {tokenDelta <= 0 ? `${tokenDelta.toLocaleString()} tok` : `+${tokenDelta.toLocaleString()} tok`}
          </div>
          <div className="text-[10px] font-mono text-text-secondary">
            {runA.total_tokens.toLocaleString()} vs {runB.total_tokens.toLocaleString()}
          </div>
        </div>

        {/* Cost Delta */}
        <div className="p-4 rounded-2xl bg-white border border-border-subtle shadow-sm space-y-1">
          <div className="text-[10px] uppercase font-mono font-bold text-text-muted">Estimated Cost</div>
          <div className={`text-base font-bold font-mono ${costDelta <= 0 ? 'text-emerald-700' : 'text-rose-700'}`}>
            {costDelta <= 0 ? `-$${Math.abs(costDelta).toFixed(4)}` : `+$${costDelta.toFixed(4)}`}
          </div>
          <div className="text-[10px] font-mono text-text-secondary">
            ${runA.estimated_cost_usd.toFixed(4)} vs ${runB.estimated_cost_usd.toFixed(4)}
          </div>
        </div>
      </div>

      {/* 4. Unified AI Semantic Comparison Report */}
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden transition-all">
        {/* Header Bar */}
        <div className="px-5 py-4 border-b border-slate-200/80 bg-slate-50/60 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl bg-purple-100 text-brand-purple flex items-center justify-center shrink-0 shadow-2xs">
              <Icon icon={Sparkles} className="text-base text-indigo-600" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-bold text-slate-900">
                  AI Semantic Comparison Report
                </h3>
                {semanticVerdict && (
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-slate-200/70 text-slate-600 font-semibold">
                    Synthesized
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-500">
                Functional equivalence synthesis, workflow shifts, and root cause divergence analysis
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={handleRunSemanticCompare}
              disabled={isLoadingSemantic}
              className="h-9 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-all shadow-xs cursor-pointer disabled:opacity-50 shrink-0"
            >
              <Icon icon={Sparkles} className="text-xs" />
              <span>{isLoadingSemantic ? 'Evaluating Trajectory Delta...' : semanticVerdict ? 'Re-run Comparison' : 'Run AI Semantic Comparison'}</span>
            </button>
          </div>
        </div>

        {semanticError && (
          <div className="m-4 p-3.5 bg-rose-50 border border-rose-200 text-rose-800 text-xs rounded-xl flex items-center gap-2.5">
            <Icon icon={AlertTriangle} className="text-base text-rose-600 shrink-0" />
            <span>{semanticError}</span>
          </div>
        )}

        {semanticVerdict ? (
          <div className="divide-y divide-slate-100 animate-fadeIn">
            {/* 1. Unified Verdict & Outcome Status Bar */}
            <div className="p-4 sm:px-5 bg-gradient-to-r from-slate-50 via-white to-slate-50/50 flex flex-col lg:flex-row lg:items-center justify-between gap-3.5 border-b border-slate-200/70">
              {/* Divergence Verdict & Score */}
              <div className="flex items-center gap-3 flex-wrap">
                <span
                  className={`text-xs font-mono font-bold uppercase px-3 py-1 rounded-full shadow-2xs flex items-center gap-1.5 ${
                    semanticVerdict.task_id?.includes(' vs ')
                      ? 'bg-rose-100 text-rose-900 border border-rose-300'
                      : semanticVerdict.nature_of_divergence === 'identical_strategy' ||
                        semanticVerdict.nature_of_divergence === 'cosmetic_variation'
                      ? 'bg-emerald-100 text-emerald-800 border border-emerald-300'
                      : semanticVerdict.nature_of_divergence === 'opposite_strategies'
                      ? 'bg-rose-100 text-rose-900 border border-rose-300'
                      : 'bg-amber-100 text-amber-900 border border-amber-300'
                  }`}
                >
                  <span>
                    {semanticVerdict.task_id?.includes(' vs ')
                      ? '⚠️ Cross-Task Mismatch'
                      : semanticVerdict.nature_of_divergence === 'identical_strategy'
                      ? '🟢 Identical Strategy'
                      : semanticVerdict.nature_of_divergence === 'cosmetic_variation'
                      ? '🟢 Cosmetic Variation'
                      : semanticVerdict.nature_of_divergence === 'opposite_strategies'
                      ? '🔴 Opposite Strategies'
                      : '🟠 Substantive Strategic Divergence'}
                  </span>
                </span>

                {/* Equivalence Progress Pill */}
                <div className="flex items-center gap-2 px-3 py-1 bg-white border border-slate-200 rounded-full shadow-2xs text-xs font-mono text-slate-600">
                  <span>Equivalence Score:</span>
                  <strong className="text-slate-900 font-bold">
                    {Math.round(semanticVerdict.semantic_similarity_score * 100)}%
                  </strong>
                  <div className="w-12 h-1.5 bg-slate-100 rounded-full overflow-hidden shrink-0">
                    <div
                      className={`h-full rounded-full ${
                        semanticVerdict.semantic_similarity_score >= 0.8
                          ? 'bg-emerald-500'
                          : semanticVerdict.semantic_similarity_score >= 0.4
                          ? 'bg-amber-500'
                          : 'bg-rose-500'
                      }`}
                      style={{ width: `${Math.round(semanticVerdict.semantic_similarity_score * 100)}%` }}
                    />
                  </div>
                </div>

                {/* Strategic Fork */}
                <div className="text-xs font-mono">
                  {semanticVerdict.true_divergence_turn !== null ? (
                    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-amber-50 text-amber-900 border border-amber-200 font-bold">
                      <Icon icon={Zap} className="text-xs text-amber-600" />
                      Strategic Fork at Turn #{semanticVerdict.true_divergence_turn}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-800 border border-emerald-200 font-bold">
                      <Icon icon={Check} className="text-xs text-emerald-600" />
                      No Strategic Fork (Cosmetic Only)
                    </span>
                  )}
                </div>
              </div>

              {/* Outcome Context Summary (Result of Candidate vs Baseline) */}
              <div className="flex items-center gap-2 flex-wrap text-xs font-mono">
                <span className="text-[11px] uppercase tracking-wider text-slate-400 font-bold">Result Context:</span>
                <div className="flex items-center gap-1.5 px-2.5 py-1 bg-white border border-slate-200 rounded-lg shadow-2xs">
                  <span className="w-2 h-2 rounded-full bg-brand-purple shrink-0" />
                  <span className="text-slate-600 font-medium truncate max-w-[120px]" title={runA.model}>
                    {runA.model}
                  </span>
                  <span className={`text-[10px] font-bold px-1.5 py-0.2 rounded uppercase ${
                    runA.passed ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'
                  }`}>
                    {runA.passed ? 'Pass' : 'Fail'}
                  </span>
                </div>

                <span className="text-slate-300 font-bold">vs</span>

                <div className="flex items-center gap-1.5 px-2.5 py-1 bg-white border border-slate-200 rounded-lg shadow-2xs">
                  <span className="w-2 h-2 rounded-full bg-accent-orange shrink-0" />
                  <span className="text-slate-600 font-medium truncate max-w-[120px]" title={runB.model}>
                    {runB.model}
                  </span>
                  <span className={`text-[10px] font-bold px-1.5 py-0.2 rounded uppercase ${
                    runB.passed ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'
                  }`}>
                    {runB.passed ? 'Pass' : 'Fail'}
                  </span>
                </div>
              </div>
            </div>

            {/* 2. Executive Synthesis */}
            <div className="p-5 bg-white space-y-2">
              <div className="flex items-center gap-2 text-[11px] font-mono font-bold uppercase tracking-wider text-slate-500">
                <Icon icon={FileText} className="text-xs text-indigo-500" />
                <span>Executive Synthesis</span>
              </div>
              <p className="text-sm text-slate-800 leading-relaxed font-normal bg-slate-50/70 p-3.5 rounded-xl border border-slate-200/60">
                {semanticVerdict.verdict_summary}
              </p>
            </div>

            {/* 3. Key Strategic Differentials & Workflow Shifts */}
            {semanticVerdict.key_strategic_differences?.length > 0 && (
              <div className="p-5 bg-slate-50/30 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-[11px] font-mono font-bold uppercase tracking-wider text-slate-500">
                    <Icon icon={GitFork} className="text-xs text-amber-500" />
                    <span>Key Strategic Differentials & Workflow Shifts</span>
                    <span className="px-1.5 py-0.2 rounded-full bg-slate-200 text-slate-700 text-[10px]">
                      {semanticVerdict.key_strategic_differences.length}
                    </span>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                  {semanticVerdict.key_strategic_differences.map((diff, idx) => (
                    <div
                      key={idx}
                      className="p-3 rounded-xl bg-white text-slate-700 border border-slate-200/80 shadow-2xs flex items-start gap-2.5 hover:border-slate-300 transition-colors"
                    >
                      <span className="px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-100 font-bold font-mono text-[11px] shrink-0">
                        #{idx + 1}
                      </span>
                      <span className="text-xs leading-relaxed text-slate-700 flex-1">{diff}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 4. Attribution & Root Cause */}
            <div className="p-4 px-5 bg-purple-50/50 border-t border-purple-100 flex items-start gap-3 text-xs text-purple-950">
              <div className="w-6 h-6 rounded-lg bg-purple-100 text-indigo-700 flex items-center justify-center shrink-0 mt-0.5 shadow-2xs">
                <Icon icon={Info} className="text-xs" />
              </div>
              <div className="space-y-0.5 flex-1">
                <span className="font-mono font-bold text-[11px] uppercase tracking-wider text-brand-purple block">
                  Attribution & Root Cause
                </span>
                <p className="text-xs text-slate-700 leading-relaxed">
                  {semanticVerdict.attribution_reasoning}
                </p>
              </div>
            </div>
          </div>
        ) : isLoadingSemantic ? (
          <div className="p-8 text-center space-y-3 animate-pulse">
            <div className="w-10 h-10 rounded-2xl bg-indigo-50 text-indigo-600 flex items-center justify-center mx-auto">
              <Icon icon={Sparkles} className="text-xl animate-spin" />
            </div>
            <div className="space-y-1">
              <div className="text-sm font-bold text-slate-800">Synthesizing Trajectories with AI Judge...</div>
              <div className="text-xs text-slate-500 max-w-md mx-auto">
                Comparing step-by-step tool choices, filtering cosmetic syntax noise, and identifying true strategic divergence.
              </div>
            </div>
          </div>
        ) : (
          <div className="p-6 text-center space-y-2 bg-slate-50/40">
            <div className="text-xs text-slate-500 max-w-lg mx-auto leading-relaxed">
              Click <strong className="text-slate-800 font-semibold">Run AI Semantic Comparison</strong> above to evaluate functional equivalence, identify the strategic fork turn, and generate an executive divergence synthesis between <span className="font-mono font-semibold text-brand-purple">{runA.model}</span> and <span className="font-mono font-semibold text-accent-orange">{runB.model}</span>.
            </div>
          </div>
        )}
      </div>

      {/* 5. TAB 1: SYNCHRONIZED TIMELINE */}
      {activeTab === 'timeline' && (
        <div className="space-y-4">
          {stepPairs.map(({ stepA, stepB, turn }) => {
            const isStrategicFork = strategicForkTurn === turn;
            const isPostFork = strategicForkTurn !== null && turn >= strategicForkTurn;

            // Contextual turn alignment indicator
            let alignmentLabel = '';
            if (!stepA || !stepB) {
              alignmentLabel = !stepA
                ? `${runB.model} active · ${runA.model} finished`
                : `${runA.model} active · ${runB.model} finished`;
            } else if (stepA.action?.tool === stepB.action?.tool) {
              alignmentLabel = `Both invoked ${stepA.action?.tool || 'tool'}`;
            } else {
              alignmentLabel = `Tool shift: ${stepA.action?.tool || 'none'} vs ${stepB.action?.tool || 'none'}`;
            }

            return (
              <div
                key={turn}
                className={`bg-white rounded-2xl border shadow-sm overflow-hidden transition-all ${
                  isStrategicFork
                    ? 'border-indigo-400 ring-2 ring-indigo-400/20'
                    : isPostFork
                    ? 'border-slate-300'
                    : 'border-border-subtle'
                }`}
              >
                {/* Turn Header */}
                <div className="p-3 bg-canvas border-b border-border-subtle flex items-center justify-between text-xs font-mono">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-brand-purple bg-surface-subtle px-2 py-0.5 rounded">
                      Turn #{turn}
                    </span>
                    {isStrategicFork && (
                      <span className="px-2 py-0.5 rounded bg-indigo-100 text-indigo-900 border border-indigo-200 font-bold text-[10px] uppercase flex items-center gap-1">
                        <Icon icon={Sparkles} className="text-xs text-indigo-600" />
                        Strategic Fork (AI Judge)
                      </span>
                    )}
                  </div>
                  <span className="text-[11px] text-text-muted">
                    {alignmentLabel}
                  </span>
                </div>

                {/* Side-by-Side Turn Payload */}
                <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-border-subtle">
                  {/* Step A */}
                  <div className="p-4 space-y-2.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-mono font-bold text-brand-purple flex items-center gap-1.5">
                        <Icon icon={getToolIcon(stepA?.action?.tool)} />
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
                        <Icon icon={getToolIcon(stepB?.action?.tool)} />
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
              <Icon icon={Code} className="text-3xl text-brand-purple mx-auto" />
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
        preferredTaskId={pickerTarget === 'baseline' ? runA?.task_id : undefined}
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




