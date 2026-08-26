import React from 'react';
import { Clock, HardDrive, Layers, Terminal } from 'lucide-react';
import { TaskSummary } from '../types';

interface TaskSelectorProps {
  tasks: TaskSummary[];
  selectedTaskId: string;
  onSelectTask: (taskId: string) => void;
  disabled?: boolean;
}

export const TaskSelector: React.FC<TaskSelectorProps> = ({
  tasks,
  selectedTaskId,
  onSelectTask,
  disabled = false,
}) => {
  const getDifficultyColor = (diff: string | null) => {
    switch (diff?.toLowerCase()) {
      case 'easy':
        return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
      case 'medium':
        return 'text-amber-400 bg-amber-500/10 border-amber-500/30';
      case 'hard':
        return 'text-rose-400 bg-rose-500/10 border-rose-500/30';
      default:
        return 'text-slate-400 bg-slate-500/10 border-slate-500/30';
    }
  };

  return (
    <div className="space-y-3">
      <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
        <Layers className="w-3.5 h-3.5 text-primary" />
        Benchmark Task ({tasks.length})
      </label>

      <div className="space-y-2 max-h-[380px] overflow-y-auto pr-1">
        {tasks.map((task) => {
          const isSelected = task.task_id === selectedTaskId;
          return (
            <div
              key={task.task_id}
              onClick={() => !disabled && onSelectTask(task.task_id)}
              className={`p-3.5 rounded-xl border transition-all cursor-pointer text-left ${
                isSelected
                  ? 'bg-sky-500/10 border-primary shadow-lg shadow-sky-500/10'
                  : 'bg-surface border-border/80 hover:border-slate-600 hover:bg-surface-elevated'
              } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2">
                  <Terminal className={`w-4 h-4 ${isSelected ? 'text-primary' : 'text-slate-400'}`} />
                  <span className="font-semibold text-sm text-slate-100">{task.task_id}</span>
                </div>
                {task.difficulty && (
                  <span
                    className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border ${getDifficultyColor(
                      task.difficulty
                    )}`}
                  >
                    {task.difficulty}
                  </span>
                )}
              </div>

              <p className="text-xs text-slate-400 line-clamp-2 mb-2 leading-relaxed">
                {task.instruction_preview || 'No instructions provided.'}
              </p>

              <div className="flex items-center gap-3 text-[11px] text-slate-500">
                <span className="flex items-center gap-1">
                  <Clock className="w-3 h-3 text-slate-400" />
                  {task.timeout_sec}s limit
                </span>
                <span className="flex items-center gap-1">
                  <HardDrive className="w-3 h-3 text-slate-400" />
                  {task.memory_mb} MB RAM
                </span>
                {task.category && (
                  <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700/50">
                    {task.category}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
