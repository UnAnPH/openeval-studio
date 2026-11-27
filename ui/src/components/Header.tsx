import React from 'react';
import { MainNavTab } from '../types';

interface HeaderProps {
  activeTab: MainNavTab;
  onTabChange?: (tab: MainNavTab) => void;
  selectedRunId?: string | null;
  onBackToRuns?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  selectedRunId,
  onBackToRuns,
}) => {
  const getBreadcrumb = () => {
    switch (activeTab) {
      case 'dashboard':
      case 'overview':
        return 'Overview';
      case 'control':
      case 'firewall':
        return 'Safety / Control';
      case 'policy':
      case 'watcher_live':
        return 'Safety / Policy';
      case 'sessions':
      case 'transcripts':
      case 'incident_detail':
        return 'Safety / Sessions';
      case 'graders':
        return 'Evaluate / Judges';
      case 'runs':
        return 'Evaluate / Runs';
      case 'run_detail':
        return `Runs / ${selectedRunId || 'Detail'}`;
      case 'benchmarks':
      case 'test_cases':
        return 'Evaluate / Catalog';
      case 'compare':
        return 'Evaluate / Compare';
      case 'studio':
        return 'Evaluate / Launch';
      case 'inspect':
        return 'Evaluate / Inspect';
      default:
        return 'OpenEval Studio';
    }
  };

  const productPrefix =
    activeTab === 'control' ||
    activeTab === 'firewall' ||
    activeTab === 'policy' ||
    activeTab === 'watcher_live' ||
    activeTab === 'sessions' ||
    activeTab === 'transcripts' ||
    activeTab === 'incident_detail'
      ? 'Safety'
      : 'Evaluate';

  return (
    <header className="h-16 border-b border-border-subtle bg-white px-7 flex items-center justify-between sticky top-0 z-30 select-none">
      {/* Left: Section Breadcrumb */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted">{productPrefix} /</span>
        {activeTab === 'run_detail' && onBackToRuns ? (
          <div className="flex items-center gap-1.5 text-xs font-bold">
            <button
              type="button"
              onClick={onBackToRuns}
              className="text-text-muted hover:text-brand-purple transition-colors cursor-pointer"
            >
              Runs
            </button>
            <span className="text-text-muted">/</span>
            <span className="text-text-primary font-mono">{selectedRunId}</span>
          </div>
        ) : (
          <h1 className="text-xs font-bold text-text-primary">{getBreadcrumb().replace(/^(Evaluate|Safety) \/ /, '')}</h1>
        )}
      </div>
    </header>
  );
};
