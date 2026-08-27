import React from 'react';
import {
  IonBadge,
  IonCard,
  IonCardContent,
  IonCardHeader,
  IonCardTitle,
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
  const getDifficultyColor = (diff: string | null) => {
    switch (diff?.toLowerCase()) {
      case 'easy':
        return 'success';
      case 'medium':
        return 'warning';
      case 'hard':
        return 'danger';
      default:
        return 'medium';
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
          <IonIcon icon={layersOutline} className="text-sky-400 text-sm" />
          Benchmark Tasks ({tasks.length})
        </label>
      </div>

      <div className="space-y-2 max-h-[380px] overflow-y-auto pr-1">
        {tasks.map((task) => {
          const isSelected = task.task_id === selectedTaskId;
          return (
            <IonCard
              key={task.task_id}
              onClick={() => !disabled && onSelectTask(task.task_id)}
              className={`m-0 p-0 rounded-xl border transition-all cursor-pointer text-left ${
                isSelected
                  ? 'bg-sky-500/10 border-primary ring-1 ring-sky-500/30 shadow-lg shadow-sky-500/10'
                  : 'bg-surface border-border/80 hover:border-slate-600 hover:bg-surface-elevated'
              } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <IonCardHeader className="p-3.5 pb-1.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <IonIcon
                      icon={terminalOutline}
                      className={`text-base ${isSelected ? 'text-primary' : 'text-slate-400'}`}
                    />
                    <IonCardTitle className="font-semibold text-sm text-slate-100">
                      {task.task_id}
                    </IonCardTitle>
                  </div>
                  {task.difficulty && (
                    <IonBadge
                      color={getDifficultyColor(task.difficulty)}
                      className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5"
                    >
                      {task.difficulty}
                    </IonBadge>
                  )}
                </div>
              </IonCardHeader>

              <IonCardContent className="p-3.5 pt-0 space-y-2">
                <p className="text-xs text-slate-400 line-clamp-2 leading-relaxed">
                  {task.instruction_preview || 'No instructions provided.'}
                </p>

                <div className="flex items-center gap-3 text-[11px] text-slate-500 pt-1 border-t border-border/40">
                  <span className="flex items-center gap-1">
                    <IonIcon icon={timeOutline} className="text-slate-400 text-xs" />
                    {task.timeout_sec}s limit
                  </span>
                  <span className="flex items-center gap-1">
                    <IonIcon icon={serverOutline} className="text-slate-400 text-xs" />
                    {task.memory_mb} MB RAM
                  </span>
                  {task.category && (
                    <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700/50">
                      {task.category}
                    </span>
                  )}
                </div>
              </IonCardContent>
            </IonCard>
          );
        })}
      </div>
    </div>
  );
};
