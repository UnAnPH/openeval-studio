import { Icon } from '../lib/icon';
import {
  BarChart2,
  Box,
  ExternalLink,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import React from 'react';

interface InspectViewerProps {
  inspectPort?: number;
}

export const InspectViewer: React.FC<InspectViewerProps> = ({ inspectPort = 7575 }) => {
  const inspectUrl = `http://127.0.0.1:${inspectPort}/`;

  const handleOpenInspectWindow = () => {
    window.open(inspectUrl, '_blank', 'noopener,noreferrer');
  };

  return (
    <div className="m-0 p-0 flex flex-col h-full bg-surface-elevated/40 rounded-2xl border border-border overflow-hidden">
      {/* Header */}
      <div className="flex flex-row items-center justify-between p-4 sm:p-5 bg-surface border-b border-border shrink-0">
        <div className="flex items-center gap-2.5">
          <Icon icon={ShieldCheck} className="text-indigo-400 text-lg" />
          <div>
            <div className="text-base font-bold text-white tracking-tight flex items-center gap-2.5">
              UK AISI Inspect AI Visualizer
              <span className="inline-flex items-center px-2 py-0.5 rounded-lg text-[10px] uppercase font-bold tracking-wider bg-indigo-500/10 border border-indigo-500/30 text-indigo-300">
                Official Log Viewer
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2.5">
          <div className="h-9 inline-flex items-center px-3 rounded-xl text-xs bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 font-mono">
            <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 mr-1.5 animate-pulse" />
            Port {inspectPort}
          </div>
          <button
            type="button"
            onClick={handleOpenInspectWindow}
            className="h-9 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-xs inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer"
          >
            <Icon icon={ExternalLink} className="text-xs" />
            <span>Open Visualizer</span>
          </button>
        </div>
      </div>

      {/* Main Hub Content */}
      <div className="flex-1 overflow-y-auto p-6 flex flex-col justify-center items-center text-center space-y-6">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-indigo-600 to-sky-500 flex items-center justify-center shadow-xl shadow-indigo-500/20">
          <Icon icon={ShieldCheck} className="text-3xl text-white" />
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
              <Icon icon={Box} className="text-sm" />
              <span>Full Trajectory Replay</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Step through prompt turns, Docker tool calls, and observations with complete token metadata.
            </p>
          </div>

          <div className="p-3.5 rounded-xl bg-surface border border-border space-y-1">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-sky-300">
              <Icon icon={BarChart2} className="text-sm" />
              <span>Score & Metric Cards</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Inspect held-out pytest verifier outcomes, accuracy distributions, and cost accounting.
            </p>
          </div>

          <div className="p-3.5 rounded-xl bg-surface border border-border space-y-1">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-emerald-300">
              <Icon icon={Sparkles} className="text-sm" />
              <span>Direct Log Links</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Open and share deep-linked evaluation reports directly at <span className="font-mono text-emerald-400">/tasks/</span>.
            </p>
          </div>
        </div>

        {/* Primary Action Button */}
        <div className="pt-2 flex flex-col items-center gap-2">
          <button
            type="button"
            onClick={handleOpenInspectWindow}
            className="h-9 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-xs inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer"
          >
            <Icon icon={ExternalLink} className="text-xs" />
            <span>Launch Inspect Visualizer ({inspectUrl})</span>
          </button>
          <span className="text-[11px] text-slate-500 font-mono">
            CLI Command: uv run inspect view --port {inspectPort}
          </span>
        </div>
      </div>
    </div>
  );
};
