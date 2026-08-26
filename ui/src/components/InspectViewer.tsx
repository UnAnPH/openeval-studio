import React, { useEffect, useState } from 'react';
import { CheckCircle2, ExternalLink, Layers, ShieldCheck, Sparkles, Terminal } from 'lucide-react';

interface InspectViewerProps {
  inspectPort?: number;
}

export const InspectViewer: React.FC<InspectViewerProps> = ({ inspectPort = 7575 }) => {
  const [isServerLive, setIsServerLive] = useState<boolean>(false);
  const inspectUrl = `http://127.0.0.1:${inspectPort}`;

  useEffect(() => {
    const checkLiveness = async () => {
      try {
        await fetch(inspectUrl, { mode: 'no-cors' });
        setIsServerLive(true);
      } catch {
        setIsServerLive(false);
      }
    };
    checkLiveness();
    const interval = setInterval(checkLiveness, 3000);
    return () => clearInterval(interval);
  }, [inspectUrl]);

  return (
    <div className="flex flex-col h-full bg-surface-elevated/40 rounded-2xl border border-border overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 bg-surface border-b border-border">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400">
            <ShieldCheck className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-bold text-white tracking-tight">
                UK AI Safety Institute — Inspect AI Visualizer
              </h2>
              <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 font-bold">
                Official Studio
              </span>
            </div>
            <p className="text-xs text-slate-400">Frontier evaluation framework & trace visualizer</p>
          </div>
        </div>

        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface border border-border text-xs">
          <span className={`w-2 h-2 rounded-full ${isServerLive ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
          <span className="text-slate-300 font-mono text-[11px]">
            {isServerLive ? `Live at 127.0.0.1:${inspectPort}` : `Port ${inspectPort} Standby`}
          </span>
        </div>
      </div>

      {/* Main Content Explorer */}
      <div className="flex-1 p-6 space-y-6 overflow-y-auto font-sans">
        {/* Hero CTA Card */}
        <div className="p-6 rounded-2xl bg-gradient-to-br from-indigo-950/40 via-surface to-surface-elevated border border-indigo-500/30 shadow-xl space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-indigo-400 font-bold text-base">
                <Sparkles className="w-5 h-5" />
                Launch Full UK AISI Trace Explorer
              </div>
              <p className="text-xs text-slate-300 max-w-xl leading-relaxed">
                UK AISI's standalone visualizer is designed for full-screen analysis of agent trajectories, token waterfall graphs, and held-out scoring breakdowns.
              </p>
            </div>

            <a
              href={inspectUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="px-5 py-3 rounded-xl bg-gradient-to-r from-indigo-500 to-sky-500 hover:from-indigo-400 hover:to-sky-400 font-bold text-sm text-white flex items-center justify-center gap-2 shadow-lg shadow-indigo-500/25 transition-all active:scale-[0.98] flex-shrink-0"
            >
              <ExternalLink className="w-4 h-4" />
              Open Inspect View ({inspectUrl})
            </a>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2 border-t border-border/40 text-xs text-slate-400 font-mono">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span>Native @task Bridge</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span>Token Waterfall Charts</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span>Held-Out Scorer Metrics</span>
            </div>
          </div>
        </div>

        {/* 2-Column Details Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Left: Registered Tasks */}
          <div className="p-4 rounded-xl bg-surface border border-border space-y-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-300 uppercase tracking-wider">
              <Layers className="w-4 h-4 text-primary" />
              Registered Inspect AI Tasks (5)
            </div>
            <div className="space-y-1.5 font-mono text-xs text-slate-300">
              <div className="p-2 rounded bg-surface-elevated border border-border/60 flex items-center justify-between">
                <span>inspect_tasks.py@cancel_async_tasks</span>
                <span className="text-[10px] text-emerald-400 font-bold">READY</span>
              </div>
              <div className="p-2 rounded bg-surface-elevated border border-border/60 flex items-center justify-between">
                <span>inspect_tasks.py@regex_log</span>
                <span className="text-[10px] text-emerald-400 font-bold">READY</span>
              </div>
              <div className="p-2 rounded bg-surface-elevated border border-border/60 flex items-center justify-between">
                <span>inspect_tasks.py@openssl_selfsigned_cert</span>
                <span className="text-[10px] text-emerald-400 font-bold">READY</span>
              </div>
              <div className="p-2 rounded bg-surface-elevated border border-border/60 flex items-center justify-between">
                <span>inspect_tasks.py@feed_sync_platform</span>
                <span className="text-[10px] text-emerald-400 font-bold">READY</span>
              </div>
              <div className="p-2 rounded bg-surface-elevated border border-border/60 flex items-center justify-between">
                <span>inspect_tasks.py@build_pov_ray</span>
                <span className="text-[10px] text-emerald-400 font-bold">READY</span>
              </div>
            </div>
          </div>

          {/* Right: Quick CLI Commands */}
          <div className="p-4 rounded-xl bg-surface border border-border space-y-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-300 uppercase tracking-wider">
              <Terminal className="w-4 h-4 text-primary" />
              CLI Quick Commands
            </div>
            <div className="space-y-2 text-xs">
              <div className="p-2.5 rounded-lg bg-[#040711] border border-border/80 font-mono text-slate-300 space-y-1">
                <div className="text-[11px] text-slate-500"># Start Inspect View Visualizer:</div>
                <div className="text-sky-300">uv run inspect view --port 7575</div>
              </div>
              <div className="p-2.5 rounded-lg bg-[#040711] border border-border/80 font-mono text-slate-300 space-y-1">
                <div className="text-[11px] text-slate-500"># Run Evaluation with Inspect AI:</div>
                <div className="text-emerald-300">uv run inspect eval inspect_tasks.py@cancel_async_tasks --model google/gemini-2.0-flash</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
