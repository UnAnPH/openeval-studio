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
      case 'firewall':
        return 'Blocked Sessions & Firewall Gate';
      case 'incident_detail':
        return 'Incident & Session Trace Explorer';
      case 'runs':
        return 'Evaluation Runs';
      case 'run_detail':
        return `Runs / ${selectedRunId || 'Detail'}`;
      case 'benchmarks':
      case 'test_cases':
        return 'Benchmarks & Test Suites';
      case 'compare':
        return 'Compare Runs';
      case 'studio':
        return 'Live Studio';
      case 'inspect':
        return 'UK AISI Inspect Portal';
      default:
        return 'Evaluations';
    }
  };

  return (
    <header className="h-16 border-b border-border-subtle bg-white px-7 flex items-center justify-between sticky top-0 z-30 select-none">
      {/* Left: Section Breadcrumb */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted">Evaluations /</span>
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
          <h1 className="text-xs font-bold text-text-primary">{getBreadcrumb()}</h1>
        )}
      </div>
    </header>
  );
};
