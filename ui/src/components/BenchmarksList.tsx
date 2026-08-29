import React, { useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  playSharp,
  searchOutline,
  serverOutline,
  layersOutline,
  timeOutline,
} from 'ionicons/icons';
import { RunRecord, TaskSummary } from '../types';

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

  // Categories list
  const categories = Array.from(new Set(tasks.map((t) => t.category).filter(Boolean))) as string[];

  const filteredTasks = tasks.filter((task) => {
    if (searchText) {
      const q = searchText.toLowerCase();
      const matchId = task.task_id.toLowerCase().includes(q);
      const matchCat = task.category?.toLowerCase().includes(q);
      const matchInstr = task.instruction_preview?.toLowerCase().includes(q);
      const matchTags = task.tags?.some((tag) => tag.toLowerCase().includes(q));
      if (!matchId && !matchCat && !matchInstr && !matchTags) return false;
    }
    if (categoryFilter !== 'all' && task.category !== categoryFilter) return false;
    if (difficultyFilter !== 'all' && task.difficulty?.toLowerCase() !== difficultyFilter) return false;
    return true;
  });

  return (
    <div className="w-full space-y-5 animate-fadeIn font-sans">
      {/* 1. Header Row */}
      <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <h2 className="text-lg font-bold text-text-primary">Benchmarks & Test Suites</h2>
            <span className="px-2.5 py-0.5 rounded-full bg-surface-subtle border border-border-subtle text-xs font-mono font-bold text-brand-purple">
              {tasks.length} Benchmark Scenarios
            </span>
          </div>
          <p className="text-xs text-text-secondary mt-0.5 font-mono">
            SWE-Bench, cybersecurity reverse engineering, AI alignment probes, and held-out verifier tasks.
          </p>
        </div>

        {onNavigateToRuns && (
          <button
            type="button"
            onClick={onNavigateToRuns}
            className="px-4 py-2 rounded-xl bg-canvas border border-border-subtle text-xs font-medium text-text-secondary hover:text-text-primary hover:bg-surface-subtle transition-all cursor-pointer"
          >
            <span>View Historical Runs ({runs.length})</span>
          </button>
        )}
      </div>

      {/* 2. Filters & Search */}
      <div className="bg-white p-4 rounded-2xl border border-border-subtle shadow-sm flex flex-col lg:flex-row lg:items-center justify-between gap-3">
        {/* Category Pills */}
        <div className="flex items-center gap-1.5 bg-canvas p-1 rounded-xl border border-border-subtle text-xs overflow-x-auto">
          <button
            type="button"
            onClick={() => setCategoryFilter('all')}
            className={`px-3 py-1.5 rounded-lg font-medium whitespace-nowrap transition-all ${
              categoryFilter === 'all'
                ? 'bg-white text-brand-primary font-bold shadow-sm'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            All Categories ({tasks.length})
          </button>
          {categories.map((cat) => {
            const count = tasks.filter((t) => t.category === cat).length;
            return (
              <button
                key={cat}
                type="button"
                onClick={() => setCategoryFilter(cat)}
                className={`px-3 py-1.5 rounded-lg font-medium whitespace-nowrap capitalize transition-all ${
                  categoryFilter === cat
                    ? 'bg-white text-brand-primary font-bold shadow-sm'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                {cat.replace('-', ' ')} ({count})
              </button>
            );
          })}
        </div>

        {/* Difficulty & Search */}
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={difficultyFilter}
            onChange={(e) => setDifficultyFilter(e.target.value)}
            className="bg-canvas border border-border-subtle rounded-xl px-3 py-1.5 text-xs text-text-primary font-medium focus:outline-none focus:border-brand-primary"
          >
            <option value="all">All Difficulties</option>
            <option value="hard">Hard</option>
            <option value="medium">Medium</option>
            <option value="easy">Easy</option>
          </select>

          <div className="relative min-w-[220px]">
            <input
              type="text"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Search benchmark tasks..."
              className="w-full bg-canvas border border-border-subtle rounded-xl pl-7 pr-3 py-1.5 text-xs text-text-primary focus:outline-none focus:border-brand-primary"
            />
            <IonIcon
              icon={searchOutline}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted text-xs pointer-events-none"
            />
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
              className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col justify-between space-y-4 hover:border-brand-purple/40 transition-all group"
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

                {/* Title */}
                <div>
                  <h3 className="text-sm font-bold text-text-primary font-mono group-hover:text-brand-purple transition-colors">
                    {task.task_id}
                  </h3>
                  <p className="text-xs text-text-secondary mt-1.5 leading-relaxed line-clamp-3">
                    {task.instruction_preview || 'No description available for this benchmark.'}
                  </p>
                </div>

                {/* Quotas & Tags */}
                <div className="space-y-2 pt-2 border-t border-border-subtle/60 text-[11px] font-mono text-text-secondary">
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1">
                      <IonIcon icon={timeOutline} className="text-xs text-text-muted" />
                      Limit: <strong>{task.timeout_sec || 600}s</strong>
                    </span>
                    <span className="flex items-center gap-1">
                      <IonIcon icon={layersOutline} className="text-xs text-text-muted" />
                      Turns: <strong>{task.max_steps || 25} max</strong>
                    </span>
                    <span className="flex items-center gap-1">
                      <IonIcon icon={serverOutline} className="text-xs text-text-muted" />
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
                <div className="text-[11px] text-text-muted font-mono">
                  {taskRuns.length} run{taskRuns.length === 1 ? '' : 's'} executed
                </div>

                <div className="flex items-center gap-2">
                  {latestRun && onSelectRun && (
                    <button
                      type="button"
                      onClick={() => onSelectRun(latestRun.run_id)}
                      className="px-2.5 py-1.5 rounded-lg bg-surface-subtle text-brand-purple text-xs font-medium hover:bg-purple-100 transition-colors cursor-pointer"
                    >
                      Inspect Latest
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={() => onLaunchTask(task.task_id)}
                    className="px-3.5 py-1.5 rounded-xl bg-dark-base text-white text-xs font-bold flex items-center gap-1.5 hover:bg-black transition-all shadow-sm active:scale-95 cursor-pointer"
                  >
                    <IonIcon icon={playSharp} className="text-[10px]" />
                    <span>Launch</span>
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
