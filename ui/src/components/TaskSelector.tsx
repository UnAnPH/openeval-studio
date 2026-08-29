import React from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  layersOutline,
  terminalOutline,
  timeOutline,
  serverOutline,
} from 'ionicons/icons';
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
  const selectedTask = tasks.find((t) => t.task_id === selectedTaskId);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-mono font-bold uppercase tracking-wider text-text-secondary flex items-center gap-1.5">
          <IonIcon icon={layersOutline} className="text-brand-purple text-sm" />
          Benchmark Task Target
        </label>
        <span className="text-[10px] font-mono font-bold text-brand-purple bg-surface-subtle px-2 py-0.5 rounded-full border border-border-subtle">
          {tasks.length} tasks
        </span>
      </div>

      <div className="relative">
        <select
          value={selectedTaskId}
          onChange={(e) => onSelectTask(e.target.value)}
          disabled={disabled}
          className="w-full bg-canvas border border-border-subtle rounded-xl px-3.5 py-2.5 text-xs text-text-primary font-medium focus:outline-none focus:border-brand-primary transition-all disabled:opacity-50 appearance-none cursor-pointer"
        >
          {tasks.map((t) => (
            <option key={t.task_id} value={t.task_id} className="bg-white text-text-primary">
              {t.task_id} [{t.category?.toUpperCase() || 'GENERAL'}] — {t.difficulty?.toUpperCase() || 'MEDIUM'}
            </option>
          ))}
        </select>
        <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-3 text-text-muted">
          <IonIcon icon={layersOutline} className="text-brand-purple text-sm" />
        </div>
      </div>

      {selectedTask && (
        <div className="p-3.5 rounded-xl bg-canvas border border-border-subtle space-y-2 text-xs">
          <div className="flex items-center justify-between">
            <span className="font-bold text-text-primary font-mono">{selectedTask.task_id}</span>
            <span className="text-[10px] font-mono font-bold uppercase px-2 py-0.5 rounded-full bg-surface-subtle text-brand-purple">
              {selectedTask.difficulty || 'Medium'}
            </span>
          </div>

          <p className="text-text-secondary leading-relaxed line-clamp-3 text-xs">
            {selectedTask.instruction_preview}
          </p>

          <div className="flex flex-wrap items-center gap-2 text-text-muted pt-2 border-t border-border-subtle text-[11px] font-mono">
            <span className="px-2 py-0.5 rounded bg-white text-text-primary border border-border-subtle flex items-center gap-1">
              <IonIcon icon={timeOutline} className="text-accent-orange" />
              <span>{selectedTask.timeout_sec}s limit</span>
            </span>
            <span className="px-2 py-0.5 rounded bg-white text-text-primary border border-border-subtle flex items-center gap-1">
              <IonIcon icon={terminalOutline} className="text-brand-purple" />
              <span>{selectedTask.max_steps} max turns</span>
            </span>
            <span className="px-2 py-0.5 rounded bg-white text-text-primary border border-border-subtle flex items-center gap-1">
              <IonIcon icon={serverOutline} className="text-emerald-600" />
              <span>{selectedTask.memory_mb}MB RAM</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

