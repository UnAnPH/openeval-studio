import React, { useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  gitCompareOutline,
  gridOutline,
  shieldCheckmarkOutline,
  terminalOutline,
  folderOpenOutline,
  bookOutline,
  chevronBackOutline,
  chevronForwardOutline,
} from 'ionicons/icons';
import { MainNavTab } from '../types';

interface SidebarProps {
  activeTab: MainNavTab;
  onTabChange: (tab: MainNavTab) => void;
  serverConnected: boolean;
  activeModelName?: string;
  totalTasks: number;
  totalRuns: number;
  totalBlocked?: number;
  onExportSFT?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  onTabChange,
  serverConnected,
  totalTasks,
  totalRuns,
  totalBlocked = 0,
}) => {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem('openeval_sidebar_collapsed') === 'true';
    } catch {
      return false;
    }
  });

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('openeval_sidebar_collapsed', String(next));
      } catch {}
      return next;
    });
  };

  const isRunsActive = activeTab === 'runs' || activeTab === 'run_detail';
  const isDashboardActive = activeTab === 'dashboard' || activeTab === 'overview';
  const isBenchmarksActive = activeTab === 'benchmarks' || activeTab === 'test_cases';
  const isFirewallActive = activeTab === 'firewall' || activeTab === 'incident_detail';

  const navItems = [
    {
      id: 'overview' as MainNavTab,
      label: 'Dashboard',
      icon: gridOutline,
      isActive: isDashboardActive,
    },
    {
      id: 'firewall' as MainNavTab,
      label: 'Sessions',
      icon: shieldCheckmarkOutline,
      badge: totalBlocked ? `${totalBlocked}` : undefined,
      isActive: isFirewallActive,
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
  ];

  return (
    <aside
      className={`${
        collapsed ? 'w-18' : 'w-60'
      } flex-shrink-0 bg-[#14121F] border-r border-[#221F33] flex flex-col justify-between h-screen sticky top-0 select-none z-40 text-slate-100 font-sans transition-all duration-200 ease-in-out`}
    >
      {/* Top Section */}
      <div className={`p-3 space-y-5 ${collapsed ? 'px-2' : 'p-4'}`}>
        {/* Brand Header & Toggle */}
        <div className={`flex items-center ${collapsed ? 'flex-col gap-3 justify-center' : 'justify-between px-1'} pt-1`}>
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-[#6B46C1] to-[#9F7AEA] flex items-center justify-center text-white shadow-md flex-shrink-0">
              <span className="font-mono font-bold text-base leading-none">⑂</span>
            </div>
            {!collapsed && (
              <div>
                <div className="font-bold text-sm text-white tracking-tight leading-tight whitespace-nowrap">
                  OpenEval Studio
                </div>
                <div className="text-[10px] font-mono text-purple-300 font-medium whitespace-nowrap">
                  AI Safety Observability
                </div>
              </div>
            )}
          </div>

          {/* Collapse / Expand Button */}
          <button
            type="button"
            onClick={toggleCollapsed}
            className="w-7 h-7 rounded-lg bg-[#1C182E] hover:bg-[#282342] text-slate-400 hover:text-white flex items-center justify-center transition-colors cursor-pointer border border-[#2B2644]"
            title={collapsed ? 'Expand Sidebar' : 'Collapse Sidebar'}
          >
            <IonIcon icon={collapsed ? chevronForwardOutline : chevronBackOutline} className="text-xs" />
          </button>
        </div>

        {/* Navigation Items */}
        <nav className="space-y-1.5 pt-1">
          {navItems.map((item) => {
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onTabChange(item.id)}
                title={item.label}
                className={`w-full flex items-center ${
                  collapsed ? 'justify-center p-2.5' : 'justify-between px-3.5 py-2.5'
                } rounded-xl text-xs font-medium transition-all cursor-pointer relative group ${
                  item.isActive
                    ? 'bg-[#2E2682] text-white font-bold shadow-sm'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-[#1C182E]'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <IonIcon
                    icon={item.icon}
                    className={`text-base flex-shrink-0 ${item.isActive ? 'text-white' : 'text-slate-400 group-hover:text-white'}`}
                  />
                  {!collapsed && <span className="truncate">{item.label}</span>}
                </div>

                {/* Badge when expanded */}
                {!collapsed && item.badge && (
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

                {/* Mini Badge dot when collapsed */}
                {collapsed && item.badge && (
                  <span
                    className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-brand-purple border border-[#14121F]"
                    title={`${item.label}: ${item.badge}`}
                  />
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Bottom Status */}
      <div className={`p-3 border-t border-[#221F33] ${collapsed ? 'p-2 flex justify-center' : 'p-4'}`}>
        {!collapsed ? (
          <div className="flex items-center justify-between px-1 text-[11px] font-mono text-slate-400">
            <div className="flex items-center gap-2">
              <span
                className={`w-2 h-2 rounded-full ${
                  serverConnected ? 'bg-emerald-400 shadow-xs shadow-emerald-400/50' : 'bg-amber-400'
                }`}
              />
              <span>{serverConnected ? 'Connected' : 'Offline'}</span>
            </div>
            <span className="text-[10px] text-slate-500 font-bold">v2.0</span>
          </div>
        ) : (
          <div
            className="flex items-center justify-center p-1 cursor-help"
            title={serverConnected ? 'Backend Connected (v2.0)' : 'Backend Offline'}
          >
            <span
              className={`w-2.5 h-2.5 rounded-full ${
                serverConnected ? 'bg-emerald-400 shadow-xs shadow-emerald-400/50 animate-pulse' : 'bg-amber-400'
              }`}
            />
          </div>
        )}
      </div>
    </aside>
  );
};
