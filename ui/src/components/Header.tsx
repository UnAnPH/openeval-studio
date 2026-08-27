import React from 'react';
import {
  IonBadge,
  IonChip,
  IonHeader,
  IonIcon,
  IonTitle,
  IonToolbar,
} from '@ionic/react';
import {
  checkmarkCircleOutline,
  hardwareChipOutline,
  pulseOutline,
  shieldCheckmarkOutline,
  sparklesOutline,
} from 'ionicons/icons';

interface HeaderProps {
  serverConnected: boolean;
  activeModelName?: string;
  totalTasks: number;
}

export const Header: React.FC<HeaderProps> = ({
  serverConnected,
  activeModelName = 'Gemini 3.1 Flash-Lite',
  totalTasks,
}) => {
  return (
    <IonHeader className="border-b border-border/80 bg-surface/90 backdrop-blur sticky top-0 z-50">
      <IonToolbar className="bg-transparent px-4 py-1">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          {/* Brand */}
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-sky-500 to-indigo-500 flex items-center justify-center shadow-lg shadow-sky-500/20">
              <IonIcon icon={hardwareChipOutline} className="text-xl text-white" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <IonTitle className="p-0 font-bold text-lg text-white tracking-tight">
                  OpenEval Studio
                </IonTitle>
                <IonBadge color="primary" className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded">
                  Ionic UI
                </IonBadge>
              </div>
              <p className="text-xs text-slate-400">Zero-Trust Agentic Evaluation & Trajectory Harness</p>
            </div>
          </div>

          {/* Global Status Badges & Chips */}
          <div className="flex items-center gap-2 sm:gap-3">
            <IonChip className="hidden sm:flex bg-surface-elevated border border-border text-xs text-slate-300">
              <IonIcon icon={sparklesOutline} color="warning" />
              <span className="text-slate-400 mr-1">Model:</span>
              <strong className="text-slate-200">{activeModelName}</strong>
            </IonChip>

            <IonChip className="hidden md:flex bg-surface-elevated border border-border text-xs text-slate-300">
              <IonIcon icon={shieldCheckmarkOutline} color="success" />
              <span className="text-slate-400 mr-1">Tasks:</span>
              <strong className="text-slate-200">{totalTasks} Loaded</strong>
            </IonChip>

            <IonChip
              className={`border text-xs transition-all ${
                serverConnected
                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                  : 'bg-rose-500/10 border-rose-500/30 text-rose-400'
              }`}
            >
              <IonIcon
                icon={serverConnected ? checkmarkCircleOutline : pulseOutline}
                className={serverConnected ? 'text-emerald-400 animate-pulse' : 'text-rose-400'}
              />
              <span className="font-medium">
                {serverConnected ? 'API Live' : 'Offline'}
              </span>
            </IonChip>
          </div>
        </div>
      </IonToolbar>
    </IonHeader>
  );
};
