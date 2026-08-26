import React from 'react';
import { Activity, Cpu, ShieldCheck, Sparkles } from 'lucide-react';

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
    <header className="border-b border-border bg-surface/80 backdrop-blur sticky top-0 z-50 px-6 py-3.5">
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        {/* Brand */}
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-sky-500 to-indigo-500 flex items-center justify-center shadow-lg shadow-sky-500/20">
            <Cpu className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-bold text-lg text-white tracking-tight">OpenEval Studio</h1>
              <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-sky-500/10 text-primary border border-sky-500/20">
                Frontier IDE
              </span>
            </div>
            <p className="text-xs text-slate-400">Zero-Trust Agentic Evaluation & Trajectory Harness</p>
          </div>
        </div>

        {/* Global Status Badges */}
        <div className="flex items-center gap-4">
          <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-elevated border border-border text-xs text-slate-300">
            <Sparkles className="w-3.5 h-3.5 text-accent" />
            <span className="text-slate-400">Default Model:</span>
            <span className="font-semibold text-slate-200">{activeModelName}</span>
          </div>

          <div className="hidden md:flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-elevated border border-border text-xs text-slate-300">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-slate-400">Tasks Loaded:</span>
            <span className="font-semibold text-slate-200">{totalTasks} Benchmarks</span>
          </div>

          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-elevated border border-border text-xs">
            <span
              className={`w-2 h-2 rounded-full ${
                serverConnected ? 'bg-emerald-400 animate-pulse' : 'bg-rose-500'
              }`}
            />
            <Activity className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-slate-300 font-medium">
              {serverConnected ? 'API Connected' : 'Server Offline'}
            </span>
          </div>
        </div>
      </div>
    </header>
  );
};
