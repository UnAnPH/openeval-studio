import React, { useState } from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  gitCompareOutline,
  gridOutline,
  folderOpenOutline,
  chevronBackOutline,
  chevronForwardOutline,
  layersOutline,
  playSharp,
  flaskOutline,
  fileTrayFullOutline,
  shieldCheckmarkOutline,
  optionsOutline,
  terminalOutline,
} from 'ionicons/icons';

import { MainNavTab } from '../types';

interface NavItem {
  id: MainNavTab;
  label: string;
  icon: any;
  isActive: boolean;
}

interface NavSection {
  label: string;
  items: NavItem[];
}

interface SidebarProps {
  activeTab: MainNavTab;
  onTabChange: (tab: MainNavTab) => void;
  serverConnected: boolean;
  activeModelName?: string;
  totalTasks: number;
  totalRuns: number;
  totalBlocked?: number;
  onExportSFT?: () => void;
  currentUser?: { user_id: number; username: string } | null;
  onLogout?: () => void;
}


export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  onTabChange,
  serverConnected,
  totalTasks: _totalTasks,
  totalRuns: _totalRuns,
  totalBlocked: _totalBlocked,
  currentUser,
  onLogout,
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
  const isSessionsActive =
    activeTab === 'sessions' || activeTab === 'incident_detail' || activeTab === 'transcripts';
  const isBenchmarksActive = activeTab === 'benchmarks' || activeTab === 'test_cases';
  const isControlActive = activeTab === 'control' || activeTab === 'firewall';
  const isPolicyActive = activeTab === 'policy' || activeTab === 'watcher_live';

  const sections: NavSection[] = [
    {
      label: 'Evaluate',
      items: [
        {
          id: 'overview',
          label: 'Overview',
          icon: gridOutline,
          isActive: isDashboardActive,
        },
        {
          id: 'benchmarks',
          label: 'Catalog',
          icon: layersOutline,
          isActive: isBenchmarksActive,
        },
        {
          id: 'studio',
          label: 'Launch',
          icon: playSharp,
          isActive: activeTab === 'studio',
        },
        {
          id: 'runs',
          label: 'Runs',
          icon: folderOpenOutline,
          isActive: isRunsActive,
        },
        {
          id: 'compare',
          label: 'Compare',
          icon: gitCompareOutline,
          isActive: activeTab === 'compare',
        },
        {
          id: 'graders',
          label: 'Judges',
          icon: flaskOutline,
          isActive: activeTab === 'graders',
        },
      ],
    },
    {
      label: 'Safety',
      items: [
        {
          id: 'control',
          label: 'Control',
          icon: shieldCheckmarkOutline,
          isActive: isControlActive,
        },
        {
          id: 'sessions',
          label: 'Sessions',
          icon: fileTrayFullOutline,
          isActive: isSessionsActive,
        },
        {
          id: 'policy',
          label: 'Policy',
          icon: optionsOutline,
          isActive: isPolicyActive,
        },
        {
          id: 'hooks',
          label: 'Hooks',
          icon: terminalOutline,
          isActive: activeTab === 'hooks',
        },
      ],
    },
  ];


  return (
    <aside
      className={`${
        collapsed ? 'w-18' : 'w-60'
      } flex-shrink-0 bg-[#14121F] border-r border-[#221F33] flex flex-col justify-between h-screen sticky top-0 select-none z-40 text-slate-100 font-sans transition-all duration-200 ease-in-out`}
    >
      <div className={`p-3 space-y-4 ${collapsed ? 'px-2' : 'p-4'}`}>
        <div className={`flex items-center ${collapsed ? 'flex-col gap-3 justify-center' : 'justify-between px-1'} pt-1`}>
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-[#6B46C1] to-[#9F7AEA] flex items-center justify-center text-white shadow-md flex-shrink-0">
              <span className="font-mono font-bold text-base leading-none">⑂</span>
            </div>
            {!collapsed && (
              <div className="font-bold text-sm text-white tracking-tight leading-tight whitespace-nowrap">
                OpenEval Studio
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={toggleCollapsed}
            className="w-7 h-7 rounded-lg bg-[#1C182E] hover:bg-[#282342] text-slate-400 hover:text-white flex items-center justify-center transition-colors cursor-pointer border border-[#2B2644]"
            title={collapsed ? 'Expand Sidebar' : 'Collapse Sidebar'}
          >
            <IonIcon icon={collapsed ? chevronForwardOutline : chevronBackOutline} className="text-xs" />
          </button>
        </div>

        <nav className="space-y-4 pt-1">
          {sections.map((section) => (
            <div key={section.label} className="space-y-1">
              {!collapsed ? (
                <div className="px-3.5 py-1.5 rounded-md bg-[#1A1728] text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
                  {section.label}
                </div>
              ) : (
                <div
                  className="mx-auto w-6 border-t border-[#2B2644]"
                  title={section.label}
                  aria-label={section.label}
                />
              )}
              <div className="space-y-1">
                {section.items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onTabChange(item.id)}
                    title={collapsed ? `${section.label}: ${item.label}` : item.label}
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
                  </button>
                ))}
              </div>
            </div>
          ))}
        </nav>
      </div>

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

        {!collapsed && currentUser && (
          <div className="mt-2.5 pt-2 border-t border-[#221F33] flex items-center justify-between text-xs">
            <span className="text-slate-300 font-mono text-[11px] truncate">@{currentUser.username}</span>
            {onLogout && (
              <button
                type="button"
                onClick={onLogout}
                className="text-[11px] text-purple-400 hover:text-purple-300 hover:underline cursor-pointer"
              >
                Sign out
              </button>
            )}
          </div>
        )}
      </div>

    </aside>
  );
};
