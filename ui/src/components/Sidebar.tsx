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
  flaskOutline,
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
  totalTasks,
  totalRuns,
  onExportSFT,
}) => {
  const navItems = [
    {
      id: 'overview' as MainNavTab,
      label: 'Overview',
      icon: gridOutline,
    },
    {
      id: 'graph' as MainNavTab,
      label: 'Execution Graph',
      icon: layersOutline,
    },
    {
      id: 'test_cases' as MainNavTab,
      label: 'Test Cases',
      icon: flaskOutline,
      badge: totalTasks ? `${totalTasks}` : undefined,
    },
    {
      id: 'studio' as MainNavTab,
      label: 'Live Studio',
      icon: terminalOutline,
    },
    {
      id: 'compare' as MainNavTab,
      label: 'Compare Runs',
      icon: gitCompareOutline,
      badge: totalRuns ? `${totalRuns}` : undefined,
    },
    {
      id: 'inspect' as MainNavTab,
      label: 'Inspect AI',
      icon: shieldCheckmarkOutline,
    },
  ];

  return (
    <aside className="w-56 flex-shrink-0 bg-[#14121F] border-r border-[#221F33] flex flex-col justify-between h-screen sticky top-0 select-none z-40 text-slate-100 font-sans">
      {/* Top Section */}
      <div className="p-4 space-y-6">
        {/* Brand Header */}
        <div className="flex items-center gap-2.5 px-1 pt-1">
          <div className="w-7 h-7 rounded-lg bg-[#6B46C1] flex items-center justify-center text-white shadow-sm">
            <span className="font-mono font-bold text-sm leading-none">⑂</span>
          </div>
          <div className="font-bold text-sm text-white tracking-tight">
            OpenEval
          </div>
        </div>

        {/* Navigation Items */}
        <nav className="space-y-1">
          {navItems.map((item) => {
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onTabChange(item.id)}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs font-medium transition-all ${
                  isActive
                    ? 'bg-[#2E2682] text-white font-semibold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-[#1C182E]'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <IonIcon
                    icon={item.icon}
                    className={`text-sm ${isActive ? 'text-white' : 'text-slate-400'}`}
                  />
                  <span>{item.label}</span>
                </div>

                {item.badge && (
                  <span
                    className={`text-[10px] font-mono px-1.5 py-0.2 rounded ${
                      isActive
                        ? 'bg-white/20 text-white'
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
            className="w-full py-2 px-3 rounded-lg bg-[#1C182E] hover:bg-[#25203D] text-slate-300 hover:text-white text-[11px] font-medium flex items-center justify-center gap-1.5 transition-all border border-[#2A2445]"
          >
            <IonIcon icon={downloadOutline} className="text-xs text-brand-purple" />
            <span>Export SFT Data</span>
          </button>
        )}

        <div className="flex items-center justify-between px-1 text-[11px] font-mono text-slate-400">
          <div className="flex items-center gap-2">
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                serverConnected ? 'bg-emerald-400' : 'bg-amber-400'
              }`}
            />
            <span>{serverConnected ? 'Connected' : 'Offline'}</span>
          </div>
          <span className="text-[10px] text-slate-500">v1.2</span>
        </div>
      </div>
    </aside>
  );
};

