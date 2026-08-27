import React from 'react';
import {
  IonBadge,
  IonButton,
  IonCard,
  IonCardContent,
  IonCardHeader,
  IonCardTitle,
  IonChip,
  IonIcon,
} from '@ionic/react';
import {
  openOutline,
  shieldCheckmarkOutline,
  cubeOutline,
  statsChartOutline,
  sparklesOutline,
} from 'ionicons/icons';

interface InspectViewerProps {
  inspectPort?: number;
}

export const InspectViewer: React.FC<InspectViewerProps> = ({ inspectPort = 7575 }) => {
  const inspectUrl = `http://127.0.0.1:${inspectPort}/`;

  const handleOpenInspectWindow = () => {
    window.open(inspectUrl, '_blank', 'noopener,noreferrer');
  };

  return (
    <IonCard className="m-0 p-0 flex flex-col h-full bg-surface-elevated/40 rounded-2xl border border-border overflow-hidden">
      {/* Header */}
      <IonCardHeader className="flex flex-row items-center justify-between px-5 py-3 bg-surface border-b border-border">
        <div className="flex items-center gap-2.5">
          <IonIcon icon={shieldCheckmarkOutline} className="text-indigo-400 text-lg" />
          <div>
            <IonCardTitle className="text-sm font-bold text-white tracking-tight flex items-center gap-2">
              UK AISI Inspect AI Visualizer
              <IonBadge color="tertiary" className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5">
                Official Log Viewer
              </IonBadge>
            </IonCardTitle>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <IonChip className="bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 text-xs">
            <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 mr-1 animate-pulse" />
            Port {inspectPort}
          </IonChip>
          <IonButton
            size="small"
            color="primary"
            onClick={handleOpenInspectWindow}
            className="text-xs font-semibold"
          >
            <IonIcon icon={openOutline} slot="end" />
            Open Visualizer
          </IonButton>
        </div>
      </IonCardHeader>

      {/* Main Hub Content */}
      <IonCardContent className="flex-1 overflow-y-auto p-6 flex flex-col justify-center items-center text-center space-y-6">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-indigo-600 to-sky-500 flex items-center justify-center shadow-xl shadow-indigo-500/20">
          <IonIcon icon={shieldCheckmarkOutline} className="text-3xl text-white" />
        </div>

        <div className="max-w-md space-y-2">
          <h3 className="text-lg font-bold text-white tracking-tight">
            Official UK AI Safety Institute Visualizer
          </h3>
          <p className="text-xs text-slate-300 leading-relaxed">
            Inspect View runs as a dedicated server displaying sample message trees, solver actions,
            model thoughts, token costs, and verifier scorecards from your <code className="text-indigo-300">logs/*.eval</code> archives.
          </p>
        </div>

        {/* Feature Highlights Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 w-full max-w-xl text-left">
          <div className="p-3.5 rounded-xl bg-surface border border-border space-y-1">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-indigo-300">
              <IonIcon icon={cubeOutline} className="text-sm" />
              <span>Full Trajectory Replay</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Step through prompt turns, Docker tool calls, and observations with complete token metadata.
            </p>
          </div>

          <div className="p-3.5 rounded-xl bg-surface border border-border space-y-1">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-sky-300">
              <IonIcon icon={statsChartOutline} className="text-sm" />
              <span>Score & Metric Cards</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Inspect held-out pytest verifier outcomes, accuracy distributions, and cost accounting.
            </p>
          </div>

          <div className="p-3.5 rounded-xl bg-surface border border-border space-y-1">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-emerald-300">
              <IonIcon icon={sparklesOutline} className="text-sm" />
              <span>Direct Log Links</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Open and share deep-linked evaluation reports directly at <span className="font-mono text-emerald-400">/tasks/</span>.
            </p>
          </div>
        </div>

        {/* Primary Action Button */}
        <div className="pt-2 flex flex-col items-center gap-2">
          <IonButton
            size="default"
            color="tertiary"
            onClick={handleOpenInspectWindow}
            className="font-bold text-sm shadow-lg shadow-indigo-500/20"
          >
            <IonIcon icon={openOutline} slot="start" />
            Launch Inspect Visualizer ({inspectUrl})
          </IonButton>
          <span className="text-[11px] text-slate-500 font-mono">
            CLI Command: uv run inspect view --port {inspectPort}
          </span>
        </div>
      </IonCardContent>
    </IonCard>
  );
};
