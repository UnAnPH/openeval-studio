import React from 'react';
import { MainNavTab } from '../types';

interface HeaderProps {
  activeTab: MainNavTab;
  onTabChange?: (tab: MainNavTab) => void;
  onQuickRun?: () => void;
  isStreaming?: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
}) => {
  const getTabTitle = () => {
    switch (activeTab) {
      case 'overview':
        return 'Overview';
      case 'graph':
        return 'Execution Graph';
      case 'test_cases':
        return 'Test Cases';
      case 'compare':
        return 'Compare Runs';
      case 'studio':
        return 'Live Studio';
      case 'inspect':
        return 'Inspect AI';
      default:
        return 'Evaluations';
    }
  };

  return (
    <header className="h-16 border-b border-border-subtle bg-white px-7 flex items-center justify-between sticky top-0 z-30 select-none">
      {/* Left: Section Title */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted">Evaluations /</span>
        <h1 className="text-xs font-bold text-text-primary">{getTabTitle()}</h1>
      </div>
    </header>
  );
};
