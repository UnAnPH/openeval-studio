import React from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  downloadOutline,
  gitCompareOutline,
  gridOutline,
  layersOutline,
  shieldCheckmarkOutline,
  terminalOutline,
  folderOpenOutline,
  bookOutline,
} from 'ionicons/icons';
import { MainNavTab } from '../types';

interface SidebarProps {
  activeTab: MainNavTab;
  onTabChange: (tab: MainNavTab) => void;
  serverConnected: boolean;
  activeModelName?: string;
  totalTasks: number;
  totalRuns: number;
  onExportSFT?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  onTabChange,
  serverConnected,
  activeModelName,
  totalTasks,
  totalRuns,
  onExportSFT,
}) => {
  const isRunsActive = activeTab === 'runs' || activeTab === 'run_detail';
  const isDashboardActive = activeTab === 'dashboard' || activeTab === 'overview';
  const isBenchmarksActive = activeTab === 'benchmarks' || activeTab === 'test_cases';

  const navItems = [
    {
      id: 'overview' as MainNavTab,
      label: 'Dashboard',
      icon: gridOutline,
      isActive: isDashboardActive,
    },
    {
      id: 'runs' as MainNavTab,
      label: 'Evaluation Runs',
      icon: folderOpenOutline,
      badge: totalRuns ? `${totalRuns}` : undefined,
      isActive: isRunsActive,
    },
    {
      id: 'benchmarks' as MainNavTab,
      label: 'Benchmarks',
      icon: bookOutline,
      badge: totalTasks ? `${totalTasks}` : undefined,
      isActive: isBenchmarksActive,
    },
    {
      id: 'studio' as MainNavTab,
      label: 'Live Studio',
      icon: terminalOutline,
      isActive: activeTab === 'studio',
    },
    {
      id: 'compare' as MainNavTab,
      label: 'Compare Runs',
      icon: gitCompareOutline,
      isActive: activeTab === 'compare',
    },
    {
      id: 'graph' as MainNavTab,
      label: 'Execution Graph',
      icon: layersOutline,
      isActive: activeTab === 'graph',
    },
    {
      id: 'inspect' as MainNavTab,
      label: 'Inspect AI Portal',
      icon: shieldCheckmarkOutline,
      isActive: activeTab === 'inspect',
    },
  ];

  return (
    <aside className="w-60 flex-shrink-0 bg-[#14121F] border-r border-[#221F33] flex flex-col justify-between h-screen sticky top-0 select-none z-40 text-slate-100 font-sans">
      {/* Top Section */}
      <div className="p-4 space-y-6">
        {/* Brand Header */}
        <div className="flex items-center justify-between px-1 pt-1">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-[#6B46C1] to-[#9F7AEA] flex items-center justify-center text-white shadow-md">
              <span className="font-mono font-bold text-base leading-none">⑂</span>
            </div>
            <div>
              <div className="font-bold text-sm text-white tracking-tight leading-tight">
                OpenEval Studio
              </div>
              <div className="text-[10px] font-mono text-purple-300 font-medium">
                AI Safety Observability
              </div>
            </div>
          </div>
        </div>

        {/* Navigation Items */}
        <nav className="space-y-1">
          {navItems.map((item) => {
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onTabChange(item.id)}
                className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all cursor-pointer ${
                  item.isActive
                    ? 'bg-[#2E2682] text-white font-bold shadow-sm'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-[#1C182E]'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <IonIcon
                    icon={item.icon}
                    className={`text-sm ${item.isActive ? 'text-white' : 'text-slate-400'}`}
                  />
                  <span>{item.label}</span>
                </div>

                {item.badge && (
                  <span
                    className={`text-[10px] font-mono px-2 py-0.5 rounded-full ${
                      item.isActive
                        ? 'bg-white/20 text-white font-bold'
                        : 'bg-[#221F33] text-slate-400'
                    }`}
                  >
                    {item.badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Bottom Actions & Status */}
      <div className="p-4 border-t border-[#221F33] space-y-3">
        {onExportSFT && (
          <button
            type="button"
            onClick={onExportSFT}
            className="w-full py-2 px-3 rounded-xl bg-[#1C182E] hover:bg-[#25203D] text-slate-300 hover:text-white text-[11px] font-medium flex items-center justify-center gap-1.5 transition-all border border-[#2A2445] cursor-pointer"
          >
            <IonIcon icon={downloadOutline} className="text-xs text-brand-purple" />
            <span>Export SFT Dataset</span>
          </button>
        )}

        {activeModelName && (
          <div className="p-2 rounded-lg bg-[#1C182E]/60 border border-[#2A2445] text-[10px] font-mono text-slate-300 flex items-center justify-between">
            <span className="text-slate-400">Target:</span>
            <span className="text-purple-300 font-bold truncate max-w-[120px]">{activeModelName}</span>
          </div>
        )}

        <div className="flex items-center justify-between px-1 text-[11px] font-mono text-slate-400">
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                serverConnected ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'
              }`}
            />
            <span>{serverConnected ? 'Inspect Native' : 'Offline Mode'}</span>
          </div>
          <span className="text-[10px] text-slate-500 font-bold">v2.0</span>
        </div>
      </div>
    </aside>
  );
};
