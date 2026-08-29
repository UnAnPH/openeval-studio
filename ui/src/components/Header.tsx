import React from 'react';
import {
  IonIcon,
  IonSpinner,
} from '@ionic/react';
import {
  playSharp,
} from 'ionicons/icons';
import { MainNavTab } from '../types';

interface HeaderProps {
  activeTab: MainNavTab;
  onTabChange?: (tab: MainNavTab) => void;
  onQuickRun?: () => void;
  isStreaming?: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  onQuickRun,
  isStreaming = false,
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

      {/* Right Actions: Spacious Run Evaluation CTA */}
      <div className="flex items-center gap-4">
        {onQuickRun && (
          <button
            type="button"
            onClick={onQuickRun}
            disabled={isStreaming}
            className="px-5 py-2 rounded-xl bg-dark-base text-white text-xs font-bold flex items-center gap-2 hover:bg-black transition-all shadow-sm active:scale-98 disabled:opacity-75 cursor-pointer"
          >
            {isStreaming ? (
              <>
                <IonSpinner name="dots" className="w-3 h-3 text-white" />
                <span>Evaluating...</span>
              </>
            ) : (
              <>
                <IonIcon icon={playSharp} className="text-xs text-white" />
                <span>Run Evaluation</span>
              </>
            )}
          </button>
        )}
      </div>
    </header>
  );
};


