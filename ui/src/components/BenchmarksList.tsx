import { Icon } from '../lib/icon';
import {
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  Code2,
  Copy,
  ExternalLink,
  FileCode,
  FileText,
  FolderTree,
  Layers,
  Play,
  Search,
  Server,
  Shield,
  X,
} from 'lucide-react';
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';

import { RunRecord, TaskDetail, TaskSummary } from '../types';

interface BenchmarkDescriptionProps {
  description?: string | null;
  className?: string;
}

export const BenchmarkDescription: React.FC<BenchmarkDescriptionProps> = ({
  description,
  className = 'mt-1.5',
}) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);
  const [isOverflowing, setIsOverflowing] = useState<boolean>(false);
  const textRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    setIsExpanded(false);
  }, [description]);

  useLayoutEffect(() => {
    const el = textRef.current;
    if (!el) return;

    const checkOverflow = () => {
      if (!isExpanded) {
        setIsOverflowing(el.scrollHeight > el.clientHeight + 1);
      }
    };

    checkOverflow();

    window.addEventListener('resize', checkOverflow);

    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => {
        checkOverflow();
      });
      observer.observe(el);
    }

    return () => {
      window.removeEventListener('resize', checkOverflow);
      observer?.disconnect();
    };
  }, [description, isExpanded]);

  const toggleExpand = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsExpanded((prev) => !prev);
  };

  return (
    <div className={className}>
      <p
        ref={textRef}
        className={`text-xs text-text-secondary leading-relaxed ${isExpanded ? '' : 'line-clamp-3'}`}
      >
        {description || 'No description available for this benchmark.'}
      </p>
      {isOverflowing && (
        <button
          type="button"
          onClick={toggleExpand}
          className="text-[11px] font-semibold text-brand-purple hover:text-purple-900 mt-1 flex items-center gap-0.5 cursor-pointer"
        >
          <span>{isExpanded ? 'Collapse' : 'Read more'}</span>
          <Icon icon={isExpanded ? ChevronUp : ChevronDown} className="text-[10px]" />
        </button>
      )}
    </div>
  );
};

interface BenchmarksListProps {
  tasks: TaskSummary[];
  runs: RunRecord[];
  onLaunchTask: (taskId: string) => void;
  onSelectRun?: (runId: string) => void;
  onNavigateToRuns?: () => void;
}

export const BenchmarksList: React.FC<BenchmarksListProps> = ({
  tasks,
  runs,
  onLaunchTask,
  onSelectRun,
  onNavigateToRuns,
}) => {
  const [searchText, setSearchText] = useState<string>('');
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  const [difficultyFilter, setDifficultyFilter] = useState<string>('all');

  // Benchmark detail modal state
  const [inspectTaskId, setInspectTaskId] = useState<string | null>(null);
  const [taskDetail, setTaskDetail] = useState<TaskDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState<boolean>(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'instructions' | 'toml' | 'dockerfile' | 'seeds' | 'verifier'>('instructions');
  const [selectedSeedPath, setSelectedSeedPath] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const handleCopyText = (key: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  useEffect(() => {
    if (!inspectTaskId) {
      setTaskDetail(null);
      setDetailError(null);
      return;
    }
    let cancelled = false;
    setLoadingDetail(true);
    setDetailError(null);
    setActiveTab('instructions');
    setSelectedSeedPath(null);

    fetch(`/api/tasks/${encodeURIComponent(inspectTaskId)}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to load task details`);
        return res.json();
      })
      .then((data: TaskDetail) => {
        if (!cancelled) {
          setTaskDetail(data);
          if (data.seed_files && data.seed_files.length > 0) {
            setSelectedSeedPath(data.seed_files[0].path);
          }
          setLoadingDetail(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setDetailError(err.message || 'Failed loading benchmark specification');
          setLoadingDetail(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [inspectTaskId]);

  // Categories normalization & deduplication
  const normalizeCatKey = (cat: string | null | undefined): string => {
    if (!cat) return 'other';
    return cat.toLowerCase().replace(/[-_]/g, ' ').trim();
  };

  const formatCatLabel = (catKey: string): string => {
    if (catKey === 'ai safety') return 'AI Safety';
    if (catKey === 'inspect evals') return 'Inspect Evals';
    if (catKey === 'rag incident') return 'RAG Incident';
    return catKey
      .split(' ')
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
  };

  const uniqueCategories = Array.from(
    new Set(tasks.map((t) => normalizeCatKey(t.category)).filter(Boolean))
  ) as string[];

  const filteredTasks = tasks.filter((task) => {
    if (searchText) {
      const q = searchText.toLowerCase();
      const matchId = task.task_id.toLowerCase().includes(q);
      const matchCat = task.category?.toLowerCase().includes(q);
      const matchInstr = task.instruction_preview?.toLowerCase().includes(q);
      const matchTags = task.tags?.some((tag) => tag.toLowerCase().includes(q));
      if (!matchId && !matchCat && !matchInstr && !matchTags) return false;
    }
    if (categoryFilter !== 'all' && normalizeCatKey(task.category) !== categoryFilter) return false;
    if (difficultyFilter !== 'all' && task.difficulty?.toLowerCase() !== difficultyFilter) return false;
    return true;
  });

  const selectedSeedFile = taskDetail?.seed_files.find((f) => f.path === selectedSeedPath);

  return (
    <div className="flex-1 min-h-0 flex flex-col h-full bg-[#fcfcfd] text-[#1e2029] font-sans p-6 space-y-5 overflow-y-auto">
      {/* 1. Card Block Header */}
      <div className="bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-base font-bold text-slate-900 tracking-tight flex items-center gap-2.5">
            <span>Evaluation Benchmarks</span>
            <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 font-mono text-xs font-semibold">
              {tasks.length} Available
            </span>
          </h1>
          <p className="text-xs text-text-secondary mt-0.5">
            Hermetic task contracts with formal prompts, Dockerfiles, and independent verifiers.
          </p>
        </div>

        {onNavigateToRuns && (
          <button
            type="button"
            onClick={onNavigateToRuns}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 text-xs font-medium text-slate-700 transition-all shadow-xs cursor-pointer"
          >
            <span>Historical Runs ({runs.length})</span>
          </button>
        )}
      </div>

      {/* 2. Filters & Search */}
      <div className="bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col md:flex-row items-stretch md:items-center justify-between gap-4">
        {/* Category Pills */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 bg-slate-100/90 p-1.5 rounded-xl text-xs">
            <button
              type="button"
              onClick={() => setCategoryFilter('all')}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition-all cursor-pointer ${
                categoryFilter === 'all'
                  ? 'bg-white text-slate-900 shadow-xs font-bold'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              All Categories ({tasks.length})
            </button>
            {uniqueCategories.map((catKey) => {
              const count = tasks.filter((t) => normalizeCatKey(t.category) === catKey).length;
              const isActive = categoryFilter === catKey;
              return (
                <button
                  key={catKey}
                  type="button"
                  onClick={() => setCategoryFilter(catKey)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition-all cursor-pointer ${
                    isActive
                      ? 'bg-white text-slate-900 shadow-xs font-bold'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  {formatCatLabel(catKey)} ({count})
                </button>
              );
            })}
          </div>
        </div>

        {/* Right Search & Difficulty Controls */}
        <div className="flex items-center gap-2.5 shrink-0">
          <select
            value={difficultyFilter}
            onChange={(e) => setDifficultyFilter(e.target.value)}
            className="text-xs rounded-xl border border-slate-200 bg-white px-3 py-2 text-slate-700 font-medium focus:ring-1 focus:ring-brand-purple cursor-pointer shadow-xs"
          >
            <option value="all">All Difficulties</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </select>

          <div className="relative w-full sm:w-64">
            <Icon icon={Search} className="absolute left-3 top-2.5 text-slate-400 text-xs" />
            <input
              type="text"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Search benchmarks..."
              className="w-full pl-8 pr-8 py-2 rounded-xl border border-slate-200 bg-white text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-brand-purple/20 focus:border-brand-purple shadow-xs transition-all"
            />
            {searchText && (
              <button
                type="button"
                onClick={() => setSearchText('')}
                className="absolute right-2.5 top-2 text-slate-400 hover:text-slate-600 text-xs cursor-pointer"
              >
                ✕
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 3. Benchmarks Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filteredTasks.map((task) => {
          const taskRuns = runs.filter((r) => r.task_id === task.task_id);
          const passedCount = taskRuns.filter((r) => r.passed === true).length;
          const passRate = taskRuns.length > 0 ? Math.round((passedCount / taskRuns.length) * 100) : null;
          const latestRun = taskRuns.length > 0 ? taskRuns[0] : null;

          return (
            <div
              key={task.task_id}
              className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col justify-between space-y-4 hover:border-brand-purple/40 hover:shadow-md transition-all group"
            >
              <div className="space-y-3">
                {/* Top Badge Row */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded-lg bg-surface-subtle border border-border-subtle text-[11px] font-bold text-brand-purple font-mono capitalize">
                      {task.category?.replace('-', ' ') || 'General'}
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded-lg text-[10px] uppercase font-mono font-bold ${
                        task.difficulty === 'hard'
                          ? 'bg-rose-50 text-rose-700 border border-rose-200'
                          : 'bg-amber-50 text-amber-800 border border-amber-200'
                      }`}
                    >
                      {task.difficulty || 'Medium'}
                    </span>
                  </div>

                  {passRate !== null && (
                    <span
                      className={`text-[11px] font-mono font-bold px-2 py-0.5 rounded-full ${
                        passRate >= 70
                          ? 'bg-emerald-50 text-emerald-700'
                          : passRate > 0
                          ? 'bg-amber-50 text-amber-700'
                          : 'bg-rose-50 text-rose-700'
                      }`}
                    >
                      {passRate}% Pass Rate
                    </span>
                  )}
                </div>

                {/* Title & Description with Full Expand/Collapse */}
                <div>
                  <h3
                    onClick={() => setInspectTaskId(task.task_id)}
                    className="text-sm font-bold text-text-primary font-mono group-hover:text-brand-purple transition-colors break-words [overflow-wrap:anywhere] cursor-pointer hover:underline flex items-center justify-between"
                    title="Click to view full instructions, Dockerfile, and seed files"
                  >
                    <span>{task.task_id}</span>
                    <Icon icon={ExternalLink} className="text-xs text-text-muted opacity-0 group-hover:opacity-100 transition-opacity ml-1.5" />
                  </h3>

                  <BenchmarkDescription description={task.instruction_preview} />
                </div>

                {/* Quotas & Tags */}
                <div className="space-y-2 pt-2 border-t border-border-subtle/60 text-[11px] font-mono text-text-secondary">
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1" title="Timeout limit">
                      <Icon icon={Clock} className="text-xs text-text-muted" />
                      Limit: <strong>{task.timeout_sec || 600}s</strong>
                    </span>
                    <span className="flex items-center gap-1" title="Max agent steps">
                      <Icon icon={Layers} className="text-xs text-text-muted" />
                      Turns: <strong>{task.max_steps || 25}</strong>
                    </span>
                    <span className="flex items-center gap-1" title="Memory limit">
                      <Icon icon={Server} className="text-xs text-text-muted" />
                      RAM: <strong>{task.memory_mb || 2048}MB</strong>
                    </span>
                  </div>

                  {task.tags && task.tags.length > 0 && (
                    <div className="flex items-center gap-1 flex-wrap pt-1">
                      {task.tags.map((tag) => (
                        <span key={tag} className="text-[10px] px-1.5 py-0.2 rounded bg-canvas text-text-muted border border-border-subtle">
                          #{tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Bottom Action Footer */}
              <div className="pt-3 border-t border-border-subtle flex items-center justify-between gap-2">
                <button
                  type="button"
                  onClick={() => setInspectTaskId(task.task_id)}
                  className="px-2.5 py-1.5 rounded-xl border border-slate-200 bg-slate-50 hover:bg-slate-100 text-slate-700 text-xs font-semibold flex items-center gap-1 transition-colors cursor-pointer"
                  title="Inspect instruction.md, Dockerfile, task.toml, and seeds"
                >
                  <Icon icon={FileCode} className="text-xs text-brand-purple" />
                  <span>Specs & Seeds</span>
                </button>

                <div className="flex items-center gap-2">
                  {latestRun && onSelectRun && (
                    <button
                      type="button"
                      onClick={() => onSelectRun(latestRun.run_id)}
                      className="px-2.5 py-1.5 rounded-lg bg-surface-subtle text-brand-purple text-xs font-medium hover:bg-purple-100 transition-colors cursor-pointer"
                    >
                      Latest Run
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={() => onLaunchTask(task.task_id)}
                    className="px-3.5 py-1.5 rounded-xl bg-dark-base text-white text-xs font-bold flex items-center gap-1.5 hover:bg-black transition-all shadow-sm active:scale-95 cursor-pointer"
                  >
                    <Icon icon={Play} className="text-[10px]" />
                    <span>Launch</span>
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* 4. Full Benchmark Specification & Files Modal */}
      {inspectTaskId && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 sm:p-6 animate-in fade-in duration-150">
          <div className="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-5xl h-[88vh] flex flex-col overflow-hidden">
            {/* Modal Top Bar */}
            <div className="p-4 sm:p-5 border-b border-border-subtle bg-slate-50/70 flex items-center justify-between shrink-0">
              <div className="flex items-center gap-3 min-w-0 flex-1">
                <div className="w-10 h-10 rounded-xl bg-purple-100 text-brand-purple flex items-center justify-center flex-shrink-0">
                  <Icon icon={FileCode} className="text-xl" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h2 className="text-sm sm:text-base font-bold text-slate-900 font-mono truncate">
                      {inspectTaskId}
                    </h2>
                    <button
                      type="button"
                      onClick={() => handleCopyText('task_id', inspectTaskId)}
                      className="text-slate-400 hover:text-slate-600 transition-colors cursor-pointer text-xs"
                      title="Copy Task ID"
                    >
                      <Icon icon={copiedKey === 'task_id' ? Check : Copy} className="text-xs text-brand-purple" />
                    </button>
                    {taskDetail?.category && (
                      <span className="px-2 py-0.5 rounded-lg bg-white border border-border-subtle text-[11px] font-bold text-brand-purple font-mono capitalize">
                        {taskDetail.category.replace('-', ' ')}
                      </span>
                    )}
                    {taskDetail?.difficulty && (
                      <span className="px-2 py-0.5 rounded-lg text-[10px] uppercase font-mono font-bold bg-amber-50 text-amber-800 border border-amber-200">
                        {taskDetail.difficulty}
                      </span>
                    )}
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded-md bg-slate-200 text-slate-700">
                      {taskDetail?.allow_internet ? '🌐 Network Allowed' : '🔒 Hermetic (No Net)'}
                    </span>
                  </div>
                  <p className="text-[11px] text-text-secondary mt-0.5 font-mono">
                    Limit: {taskDetail?.timeout_sec ?? 600}s · Turns: {taskDetail?.max_steps ?? 100} max · RAM: {taskDetail?.memory_mb ?? 2048}MB · CPUs: {taskDetail?.cpus ?? 2}
                  </p>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center gap-2.5 flex-shrink-0 ml-4">
                <button
                  type="button"
                  onClick={() => {
                    onLaunchTask(inspectTaskId);
                    setInspectTaskId(null);
                  }}
                  className="px-4 py-2 rounded-xl bg-brand-purple text-white text-xs font-bold flex items-center gap-1.5 hover:bg-purple-900 shadow-sm transition-all cursor-pointer"
                >
                  <Icon icon={Play} className="text-xs" />
                  <span>Launch in Studio</span>
                </button>
                <button
                  type="button"
                  onClick={() => setInspectTaskId(null)}
                  className="w-8 h-8 rounded-xl bg-white border border-slate-200 hover:bg-slate-100 text-slate-600 flex items-center justify-center transition-colors cursor-pointer"
                >
                  <Icon icon={X} className="text-base" />
                </button>
              </div>
            </div>

            {/* Modal Tab Bar */}
            <div className="px-5 border-b border-border-subtle bg-white flex items-center gap-1 overflow-x-auto shrink-0 select-none">
              <button
                type="button"
                onClick={() => setActiveTab('instructions')}
                className={`py-3 px-3.5 text-xs font-bold border-b-2 flex items-center gap-1.5 whitespace-nowrap transition-all cursor-pointer ${
                  activeTab === 'instructions'
                    ? 'border-brand-purple text-brand-purple'
                    : 'border-transparent text-slate-500 hover:text-slate-800'
                }`}
              >
                <Icon icon={FileText} className="text-sm" />
                <span>Instructions (instruction.md)</span>
              </button>

              <button
                type="button"
                onClick={() => setActiveTab('toml')}
                className={`py-3 px-3.5 text-xs font-bold border-b-2 flex items-center gap-1.5 whitespace-nowrap transition-all cursor-pointer ${
                  activeTab === 'toml'
                    ? 'border-brand-purple text-brand-purple'
                    : 'border-transparent text-slate-500 hover:text-slate-800'
                }`}
              >
                <Icon icon={Code2} className="text-sm" />
                <span>Config (task.toml)</span>
              </button>

              <button
                type="button"
                onClick={() => setActiveTab('dockerfile')}
                className={`py-3 px-3.5 text-xs font-bold border-b-2 flex items-center gap-1.5 whitespace-nowrap transition-all cursor-pointer ${
                  activeTab === 'dockerfile'
                    ? 'border-brand-purple text-brand-purple'
                    : 'border-transparent text-slate-500 hover:text-slate-800'
                }`}
              >
                <Icon icon={Server} className="text-sm" />
                <span>Docker Environment (Dockerfile)</span>
              </button>

              <button
                type="button"
                onClick={() => setActiveTab('seeds')}
                className={`py-3 px-3.5 text-xs font-bold border-b-2 flex items-center gap-1.5 whitespace-nowrap transition-all cursor-pointer ${
                  activeTab === 'seeds'
                    ? 'border-brand-purple text-brand-purple'
                    : 'border-transparent text-slate-500 hover:text-slate-800'
                }`}
              >
                <Icon icon={FolderTree} className="text-sm" />
                <span>Seed Files ({taskDetail?.seed_files?.length || 0})</span>
              </button>

              <button
                type="button"
                onClick={() => setActiveTab('verifier')}
                className={`py-3 px-3.5 text-xs font-bold border-b-2 flex items-center gap-1.5 whitespace-nowrap transition-all cursor-pointer ${
                  activeTab === 'verifier'
                    ? 'border-brand-purple text-brand-purple'
                    : 'border-transparent text-slate-500 hover:text-slate-800'
                }`}
              >
                <Icon icon={Shield} className="text-sm" />
                <span>Verifier Tests (test_outputs.py)</span>
              </button>
            </div>

            {/* Modal Body / Tab Content */}
            <div className="flex-1 min-h-0 bg-slate-900 text-slate-100 overflow-y-auto">
              {loadingDetail ? (
                <div className="h-full flex flex-col items-center justify-center p-12 text-slate-400 space-y-3 font-mono text-xs">
                  <div className="w-8 h-8 border-2 border-brand-purple border-t-transparent rounded-full animate-spin" />
                  <span>Loading benchmark specification & files...</span>
                </div>
              ) : detailError ? (
                <div className="h-full flex flex-col items-center justify-center p-12 text-rose-400 space-y-2 font-mono text-xs">
                  <Icon icon={X} className="text-2xl text-rose-500" />
                  <span>{detailError}</span>
                </div>
              ) : taskDetail ? (
                <div className="h-full flex flex-col">
                  {/* TAB 1: Instructions */}
                  {activeTab === 'instructions' && (
                    <div className="p-6 space-y-4 max-w-4xl mx-auto w-full">
                      <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                        <div className="flex items-center gap-2 text-xs text-slate-400 font-mono">
                          <Icon icon={FileText} className="text-brand-purple" />
                          <span>Exact prompt instruction rendered for agent:</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleCopyText('instr', taskDetail.instruction_text)}
                          className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono flex items-center gap-1.5 cursor-pointer transition-colors"
                        >
                          <Icon icon={copiedKey === 'instr' ? Check : Copy} className="text-xs text-brand-purple" />
                          <span>{copiedKey === 'instr' ? 'Copied Prompt' : 'Copy Prompt'}</span>
                        </button>
                      </div>

                      <div className="bg-slate-950 p-6 rounded-xl border border-slate-800 font-mono text-xs leading-relaxed text-slate-200 whitespace-pre-wrap selection:bg-purple-900">
                        {taskDetail.instruction_text}
                      </div>
                    </div>
                  )}

                  {/* TAB 2: Task TOML */}
                  {activeTab === 'toml' && (
                    <div className="p-6 space-y-4 max-w-4xl mx-auto w-full">
                      <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                        <span className="text-xs text-slate-400 font-mono">task.toml (benchmark contract):</span>
                        {taskDetail.task_toml && (
                          <button
                            type="button"
                            onClick={() => handleCopyText('toml', taskDetail.task_toml || '')}
                            className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono flex items-center gap-1.5 cursor-pointer transition-colors"
                          >
                            <Icon icon={copiedKey === 'toml' ? Check : Copy} className="text-xs text-brand-purple" />
                            <span>{copiedKey === 'toml' ? 'Copied' : 'Copy TOML'}</span>
                          </button>
                        )}
                      </div>

                      <pre className="bg-slate-950 p-6 rounded-xl border border-slate-800 font-mono text-xs leading-relaxed text-emerald-400 overflow-x-auto">
                        {taskDetail.task_toml || '# No task.toml configuration available.'}
                      </pre>
                    </div>
                  )}

                  {/* TAB 3: Dockerfile */}
                  {activeTab === 'dockerfile' && (
                    <div className="p-6 space-y-4 max-w-4xl mx-auto w-full">
                      <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                        <span className="text-xs text-slate-400 font-mono">environment/Dockerfile (sandbox execution environment):</span>
                        {taskDetail.dockerfile && (
                          <button
                            type="button"
                            onClick={() => handleCopyText('docker', taskDetail.dockerfile || '')}
                            className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono flex items-center gap-1.5 cursor-pointer transition-colors"
                          >
                            <Icon icon={copiedKey === 'docker' ? Check : Copy} className="text-xs text-brand-purple" />
                            <span>{copiedKey === 'docker' ? 'Copied' : 'Copy Dockerfile'}</span>
                          </button>
                        )}
                      </div>

                      <pre className="bg-slate-950 p-6 rounded-xl border border-slate-800 font-mono text-xs leading-relaxed text-sky-300 overflow-x-auto">
                        {taskDetail.dockerfile || '# No Dockerfile provided for this benchmark.'}
                      </pre>
                    </div>
                  )}

                  {/* TAB 4: Seed Files */}
                  {activeTab === 'seeds' && (
                    <div className="flex-1 min-h-0 flex flex-col md:flex-row h-full">
                      {/* Left: Seed Files List */}
                      <div className="w-full md:w-72 border-r border-slate-800 bg-slate-950 p-4 space-y-2 shrink-0 overflow-y-auto">
                        <div className="text-[11px] font-mono font-bold uppercase tracking-wider text-slate-400 pb-2 border-b border-slate-800 flex items-center gap-1.5">
                          <Icon icon={FolderTree} className="text-xs text-brand-purple" />
                          <span>Initial Workspace ({taskDetail.seed_files?.length || 0})</span>
                        </div>

                        {(!taskDetail.seed_files || taskDetail.seed_files.length === 0) ? (
                          <div className="p-4 text-center text-slate-500 font-mono text-xs">
                            No static seed files in environment/.
                          </div>
                        ) : (
                          taskDetail.seed_files.map((file) => {
                            const isSelected = selectedSeedPath === file.path;
                            return (
                              <button
                                key={file.path}
                                type="button"
                                onClick={() => setSelectedSeedPath(file.path)}
                                className={`w-full text-left px-3 py-2 rounded-lg font-mono text-xs flex items-center justify-between transition-colors cursor-pointer ${
                                  isSelected
                                    ? 'bg-purple-950/70 text-purple-300 border border-purple-800'
                                    : 'text-slate-300 hover:bg-slate-900'
                                }`}
                              >
                                <span className="truncate" title={file.path}>{file.path}</span>
                                <span className="text-[10px] text-slate-500 font-sans ml-2">
                                  {file.size_bytes}B
                                </span>
                              </button>
                            );
                          })
                        )}
                      </div>

                      {/* Right: Selected File Content */}
                      <div className="flex-1 min-h-0 p-5 overflow-y-auto bg-slate-900 flex flex-col space-y-3">
                        {selectedSeedFile ? (
                          <>
                            <div className="flex items-center justify-between pb-2 border-b border-slate-800">
                              <span className="font-mono text-xs text-slate-300 font-bold">
                                {selectedSeedFile.path}
                              </span>
                              <button
                                type="button"
                                onClick={() => handleCopyText('seed_content', selectedSeedFile.content)}
                                className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono flex items-center gap-1 cursor-pointer"
                              >
                                <Icon icon={copiedKey === 'seed_content' ? Check : Copy} className="text-xs text-brand-purple" />
                                <span>{copiedKey === 'seed_content' ? 'Copied' : 'Copy File'}</span>
                              </button>
                            </div>
                            <pre className="bg-slate-950 p-4 rounded-xl border border-slate-800 font-mono text-xs text-slate-200 leading-relaxed overflow-x-auto flex-1">
                              {selectedSeedFile.content}
                            </pre>
                          </>
                        ) : (
                          <div className="h-full flex items-center justify-center text-slate-500 font-mono text-xs">
                            Select a seed file from the left sidebar to inspect its content.
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* TAB 5: Verifier Tests */}
                  {activeTab === 'verifier' && (
                    <div className="p-6 space-y-4 max-w-4xl mx-auto w-full">
                      <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                        <div className="text-xs text-slate-400 font-mono flex items-center gap-1.5">
                          <Icon icon={Shield} className="text-emerald-400" />
                          <span>tests/test_outputs.py (held-out grading assertions):</span>
                        </div>
                        {taskDetail.verifier_code && (
                          <button
                            type="button"
                            onClick={() => handleCopyText('verifier', taskDetail.verifier_code || '')}
                            className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono flex items-center gap-1.5 cursor-pointer transition-colors"
                          >
                            <Icon icon={copiedKey === 'verifier' ? Check : Copy} className="text-xs text-brand-purple" />
                            <span>{copiedKey === 'verifier' ? 'Copied' : 'Copy Test Code'}</span>
                          </button>
                        )}
                      </div>

                      <pre className="bg-slate-950 p-6 rounded-xl border border-slate-800 font-mono text-xs leading-relaxed text-emerald-300 overflow-x-auto">
                        {taskDetail.verifier_code || '# No independent verifier test file found for this benchmark.'}
                      </pre>
                    </div>
                  )}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
