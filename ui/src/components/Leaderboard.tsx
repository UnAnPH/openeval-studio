import React from 'react';
import {
  IonBadge,
  IonButton,
  IonCard,
  IonCardContent,
  IonCardHeader,
  IonCardTitle,
  IonIcon,
} from '@ionic/react';
import {
  checkmarkCircle,
  closeCircle,
  hourglassOutline,
  trophyOutline,
  eyeOutline,
} from 'ionicons/icons';
import { RunRecord } from '../types';

interface LeaderboardProps {
  runs: RunRecord[];
  activeRunId?: string | null;
  onSelectRun: (runId: string) => void;
}

export const Leaderboard: React.FC<LeaderboardProps> = ({
  runs,
  activeRunId,
  onSelectRun,
}) => {
  return (
    <IonCard className="m-0 p-0 rounded-2xl bg-surface border border-border shadow-sm">
      <IonCardHeader className="p-4 pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <IonIcon icon={trophyOutline} className="text-amber-400 text-lg" />
            <IonCardTitle className="text-sm font-bold text-white tracking-tight">
              Evaluation History & Leaderboard
            </IonCardTitle>
          </div>
          <IonBadge color="medium" className="text-[11px] font-mono">
            {runs.length} Runs Logged
          </IonBadge>
        </div>
      </IonCardHeader>

      <IonCardContent className="p-4 pt-0">
        {runs.length === 0 ? (
          <div className="p-6 text-center text-slate-500 text-xs">
            No historical runs recorded yet. Complete an evaluation to populate the leaderboard.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-border/80 text-[11px] uppercase font-bold tracking-wider text-slate-400">
                  <th className="py-2.5 px-3">Status</th>
                  <th className="py-2.5 px-3">Task ID</th>
                  <th className="py-2.5 px-3">Model</th>
                  <th className="py-2.5 px-3">Score</th>
                  <th className="py-2.5 px-3">Duration</th>
                  <th className="py-2.5 px-3">Tokens</th>
                  <th className="py-2.5 px-3">Cost ($)</th>
                  <th className="py-2.5 px-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40 font-mono">
                {runs.map((run) => {
                  const isSelected = run.run_id === activeRunId;
                  const isPassed = run.passed === true;
                  const isFailed = run.passed === false;
                  const isRunning = run.status === 'running' || run.status === 'pending';

                  return (
                    <tr
                      key={run.run_id}
                      onClick={() => onSelectRun(run.run_id)}
                      className={`hover:bg-surface-elevated/80 transition-colors cursor-pointer ${
                        isSelected ? 'bg-sky-500/10' : ''
                      }`}
                    >
                      {/* Status */}
                      <td className="py-2.5 px-3 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          <IonIcon
                            icon={isPassed ? checkmarkCircle : isFailed ? closeCircle : hourglassOutline}
                            className={`text-sm ${
                              isPassed ? 'text-emerald-400' : isFailed ? 'text-rose-400' : 'text-sky-400 animate-spin'
                            }`}
                          />
                          <IonBadge
                            color={isPassed ? 'success' : isFailed ? 'danger' : isRunning ? 'primary' : 'medium'}
                            className="text-[10px] uppercase px-1.5 py-0.5"
                          >
                            {isPassed ? 'Passed' : isFailed ? 'Failed' : run.status}
                          </IonBadge>
                        </div>
                      </td>

                      {/* Task ID */}
                      <td className="py-2.5 px-3 text-slate-200 font-sans font-semibold">
                        {run.task_id}
                      </td>

                      {/* Model */}
                      <td className="py-2.5 px-3 text-slate-300 font-sans">
                        <span className="truncate max-w-[140px] block">{run.model}</span>
                      </td>

                      {/* Score / Reward */}
                      <td className="py-2.5 px-3">
                        <span className={`font-bold ${isPassed ? 'text-emerald-400' : isFailed ? 'text-rose-400' : 'text-slate-300'}`}>
                          {run.reward !== null ? `${run.reward.toFixed(1)}/1.0` : '—'}
                        </span>
                      </td>

                      {/* Duration */}
                      <td className="py-2.5 px-3 text-slate-400">
                        {run.total_duration_sec.toFixed(1)}s
                      </td>

                      {/* Tokens */}
                      <td className="py-2.5 px-3 text-slate-400">
                        {run.total_tokens.toLocaleString()}
                      </td>

                      {/* Cost */}
                      <td className="py-2.5 px-3 text-emerald-400 font-semibold">
                        ${run.estimated_cost_usd.toFixed(4)}
                      </td>

                      {/* Action */}
                      <td className="py-2.5 px-3 text-right">
                        <IonButton
                          fill={isSelected ? 'solid' : 'outline'}
                          color={isSelected ? 'primary' : 'medium'}
                          size="small"
                          onClick={(e) => {
                            e.stopPropagation();
                            onSelectRun(run.run_id);
                          }}
                          className="text-[10px] h-6 min-h-0"
                        >
                          <IonIcon icon={eyeOutline} slot="start" className="text-xs" />
                          {isSelected ? 'Viewing' : 'View'}
                        </IonButton>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </IonCardContent>
    </IonCard>
  );
};
