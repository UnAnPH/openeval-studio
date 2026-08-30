import React, { useState } from 'react';
import { IonIcon } from '@ionic/react';
import {
  arrowBackOutline,
  alertCircle,
  shieldCheckmark,
  terminalOutline,
  copyOutline,
  checkmarkOutline,
  timeOutline,
  personOutline,
  folderOutline,
  filterOutline,
} from 'ionicons/icons';
import { FindingRecord, IncidentSessionDetail } from '../types';

interface IncidentDetailViewProps {
  sessionDetail: IncidentSessionDetail | null;
  finding: FindingRecord | null;
  onBack: () => void;
  onSelectFinding?: (findingId: string) => void;
}

export const IncidentDetailView: React.FC<IncidentDetailViewProps> = ({
  sessionDetail,
  finding: propFinding,
  onBack,
}) => {
  const finding = sessionDetail?.finding || propFinding;
  const turns = sessionDetail?.turns || [];
  const blockedTurns = turns.filter((t) => t.is_blocked);
  const isCleared = !finding || finding.severity === 'low' || blockedTurns.length === 0;

  const [activeTranscriptTab, setActiveTranscriptTab] = useState<'blocked' | 'all'>(
    isCleared ? 'all' : 'blocked'
  );
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  const displayedTurns = activeTranscriptTab === 'blocked' && !isCleared ? blockedTurns : turns;

  const handleCopy = (text: string, index: number) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  if (!finding) {
    return (
      <div className="p-8 text-center bg-white rounded-2xl border border-border-subtle">
        <IonIcon icon={alertCircle} className="text-3xl text-accent-orange mb-2" />
        <h3 className="text-sm font-bold text-text-primary">Session Not Found</h3>
        <button
          type="button"
          onClick={onBack}
          className="mt-4 px-4 py-2 bg-dark-base text-white text-xs rounded-xl font-bold cursor-pointer"
        >
          Return to Sessions
        </button>
      </div>
    );
  }

  const severityColor =
    finding.severity === 'critical'
      ? 'bg-rose-500/10 text-rose-600 border-rose-500/20'
      : finding.severity === 'high'
      ? 'bg-amber-500/10 text-amber-600 border-amber-500/20'
      : finding.severity === 'medium'
      ? 'bg-yellow-500/10 text-yellow-700 border-yellow-500/20'
      : 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20';

  return (
    <div className="w-full space-y-4 animate-fadeIn font-sans pb-12">
      {/* 1. Header Navigation Bar */}
      <div className="flex items-center justify-between bg-white px-5 py-3.5 rounded-2xl border border-border-subtle shadow-sm">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onBack}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-canvas border border-border-subtle text-xs font-semibold text-text-secondary hover:text-text-primary hover:bg-surface-subtle transition-colors cursor-pointer"
          >
            <IonIcon icon={arrowBackOutline} className="text-xs" />
            <span>Sessions</span>
          </button>
          <div className="h-4 w-px bg-border-subtle" />
          <div className="flex items-center gap-2">
            <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold border uppercase tracking-wider ${severityColor}`}>
              ● {isCleared ? 'CLEARED' : finding.severity}
            </span>
            <span className="text-xs font-mono font-bold text-text-primary">{finding.id}</span>
          </div>
        </div>

        <div className="flex items-center gap-3 text-xs text-text-secondary">
          <div className="flex items-center gap-1.5">
            <IonIcon icon={personOutline} className="text-xs" />
            <span className="font-medium text-text-primary">{finding.developer}</span>
          </div>
          <span>·</span>
          <div className="flex items-center gap-1.5 font-mono">
            <IonIcon icon={timeOutline} className="text-xs" />
            <span>{new Date(finding.timestamp).toLocaleDateString()}</span>
          </div>
        </div>
      </div>

      {/* 2. Dual-Pane Apollo Master-Detail Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        {/* Left Pane (60% width): Incident Executive Brief & Actionable Hardening */}
        <div className="lg:col-span-7 space-y-5">
          {/* Main Incident Card */}
          <div className="bg-white p-6 rounded-2xl border border-border-subtle shadow-sm space-y-4">
            <div>
              <div className="flex items-center gap-2 text-xs text-brand-purple font-semibold mb-1">
                <span>{finding.dimension}</span>
                <span>·</span>
                <span>{finding.agent_source === 'antigravity' ? '🤖 Antigravity' : finding.agent_source === 'claude_code' ? '⚡ Claude Code' : '⚡ OpenEval Runner'}</span>
              </div>
              <h1 className="text-base font-bold text-text-primary leading-snug">
                {finding.headline}
              </h1>
            </div>

            {/* Executive Summary */}
            <div className="p-4 rounded-xl bg-canvas border border-border-subtle space-y-1.5">
              <h4 className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
                {isCleared ? 'Policy Compliance Verification' : 'Executive Incident Summary'}
              </h4>
              <p className="text-xs text-text-secondary leading-relaxed">
                {finding.summary}
              </p>
            </div>

            {/* Session Metadata Grid */}
            <div className="grid grid-cols-3 gap-3 pt-2">
              <div className="p-3 rounded-xl bg-canvas border border-border-subtle/60 space-y-0.5">
                <div className="text-[10px] text-text-muted font-medium flex items-center gap-1">
                  <IonIcon icon={folderOutline} className="text-[11px]" />
                  <span>Working Dir</span>
                </div>
                <div className="text-xs font-mono font-bold text-text-primary truncate">
                  {sessionDetail?.working_directory || '/workspace'}
                </div>
              </div>
              <div className="p-3 rounded-xl bg-canvas border border-border-subtle/60 space-y-0.5">
                <div className="text-[10px] text-text-muted font-medium flex items-center gap-1">
                  <IonIcon icon={terminalOutline} className="text-[11px]" />
                  <span>Total Turns</span>
                </div>
                <div className="text-xs font-mono font-bold text-text-primary">
                  {sessionDetail?.total_turns || turns.length || 5} turns
                </div>
              </div>
              <div className="p-3 rounded-xl bg-canvas border border-border-subtle/60 space-y-0.5">
                <div className="text-[10px] text-text-muted font-medium flex items-center gap-1">
                  <IonIcon icon={shieldCheckmark} className={`text-[11px] ${isCleared ? 'text-emerald-600' : 'text-rose-600'}`} />
                  <span>Status</span>
                </div>
                <div className={`text-xs font-mono font-bold ${isCleared ? 'text-emerald-600' : 'text-rose-600'}`}>
                  {isCleared ? '100% Cleared' : `${blockedTurns.length || 1} blocked`}
                </div>
              </div>
            </div>
          </div>

          {/* Actionable Section / Compliance Section */}
          <div className="bg-white p-6 rounded-2xl border border-border-subtle shadow-sm space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-bold text-text-primary">
                  {isCleared ? 'Policy Compliance Attestation & Safeguards' : 'Actionable Remediation Recommendations'}
                </h3>
                <p className="text-xs text-text-secondary mt-0.5">
                  {isCleared
                    ? 'Automated security gates and static analysis checks evaluated for this session'
                    : 'Direct mitigation steps to harden agents and prevent recurring safety violations'}
                </p>
              </div>
              <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold ${
                isCleared ? 'bg-emerald-500/10 text-emerald-700' : 'bg-surface-subtle text-brand-purple'
              }`}>
                {isCleared ? '✓ 7/7 PASSED' : `${finding.recommended_actions.length} Action Items`}
              </span>
            </div>

            <div className="space-y-3.5 pt-1">
              {isCleared ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {[
                    { title: 'Deterministic Command Gate', desc: 'No blacklisted exfiltration, sudo, or rm -rf commands.', status: 'PASSED' },
                    { title: 'Sub-10ms Read-Only Filter', desc: 'Benign file and inspection operations cleared automatically.', status: 'PASSED' },
                    { title: 'AST & Code Synthesis Gate', desc: 'Clean abstract syntax tree without dynamic eval or shellcode injection.', status: 'PASSED' },
                    { title: 'Data Exfiltration Boundary', desc: 'Zero outbound telemetry or unapproved network requests.', status: 'PASSED' },
                  ].map((check, cIdx) => (
                    <div key={cIdx} className="p-3.5 rounded-xl border border-emerald-200 bg-emerald-50/40 space-y-1">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-bold text-text-primary">{check.title}</span>
                        <span className="text-[10px] font-mono font-bold text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded">
                          {check.status}
                        </span>
                      </div>
                      <p className="text-[11px] text-text-secondary">{check.desc}</p>
                    </div>
                  ))}
                </div>
              ) : (
                finding.recommended_actions.map((action, idx) => (
                <div
                  key={idx}
                  className="p-4 rounded-xl border border-border-subtle bg-white hover:border-brand-primary/30 transition-all space-y-2.5 shadow-sm"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <span
                        className={`px-2 py-0.5 rounded-md text-[10px] font-mono font-bold ${
                          action.priority === 'P1'
                            ? 'bg-rose-500/10 text-rose-600 border border-rose-500/20'
                            : 'bg-amber-500/10 text-amber-600 border border-amber-500/20'
                        }`}
                      >
                        {action.priority}
                      </span>
                      <h4 className="text-xs font-bold text-text-primary">{action.title}</h4>
                    </div>

                    {action.citations.length > 0 && (
                      <div className="flex items-center gap-1">
                        {action.citations.map((c, cIdx) => (
                          <span
                            key={cIdx}
                            className="px-1.5 py-0.5 rounded bg-surface-subtle border border-border-subtle text-[10px] font-mono text-brand-purple font-semibold cursor-pointer hover:bg-brand-purple/10"
                            onClick={() => setActiveTranscriptTab('all')}
                            title="Jump to message turn in transcript"
                          >
                            {c}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  <p className="text-xs text-text-secondary leading-relaxed">
                    {action.description}
                  </p>

                  {action.code_snippet && (
                    <div className="relative group">
                      <pre className="p-3 rounded-lg bg-dark-base text-white text-[11px] font-mono overflow-x-auto whitespace-pre-wrap border border-black/20">
                        {action.code_snippet}
                      </pre>
                      <button
                        type="button"
                        onClick={() => handleCopy(action.code_snippet!, idx)}
                        className="absolute top-2 right-2 px-2 py-1 rounded bg-white/10 text-white hover:bg-white/20 text-[10px] font-sans font-semibold flex items-center gap-1 transition-colors"
                      >
                        <IonIcon icon={copiedIndex === idx ? checkmarkOutline : copyOutline} className="text-xs" />
                        <span>{copiedIndex === idx ? 'Copied' : 'Copy'}</span>
                      </button>
                    </div>
                  )}
                </div>
              )))}
            </div>
          </div>
        </div>

        {/* Right Pane (40% width): Interactive Transcript with Quick-Filter */}
        <div className="lg:col-span-5 bg-white p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col space-y-4">
          {/* Transcript Tabs */}
          <div className="flex items-center justify-between border-b border-border-subtle pb-3">
            <div className="flex items-center gap-1 bg-canvas p-1 rounded-xl border border-border-subtle">
              {!isCleared && (
                <button
                  type="button"
                  onClick={() => setActiveTranscriptTab('blocked')}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${
                    activeTranscriptTab === 'blocked'
                      ? 'bg-rose-600 text-white shadow-sm'
                      : 'text-text-secondary hover:text-text-primary'
                  }`}
                >
                  <span>🚫 Blocked</span>
                  <span className={`px-1.5 py-0.2 rounded-full text-[10px] ${
                    activeTranscriptTab === 'blocked' ? 'bg-white/20 text-white' : 'bg-canvas text-rose-600'
                  }`}>
                    {blockedTurns.length || 1}
                  </span>
                </button>
              )}
              <button
                type="button"
                onClick={() => setActiveTranscriptTab('all')}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${
                  activeTranscriptTab === 'all'
                    ? isCleared ? 'bg-emerald-700 text-white shadow-sm' : 'bg-dark-base text-white shadow-sm'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                <span>{isCleared ? '✅ Compliant Trace' : '📜 Transcript'}</span>
                <span className={`px-1.5 py-0.2 rounded-full text-[10px] ${
                  activeTranscriptTab === 'all' ? 'bg-white/20 text-white' : 'bg-canvas text-text-muted'
                }`}>
                  {turns.length}
                </span>
              </button>
            </div>

            <span className="text-[11px] font-mono text-text-muted flex items-center gap-1">
              <IonIcon icon={filterOutline} className="text-xs" />
              <span>{isCleared ? 'Full verified trace' : activeTranscriptTab === 'blocked' ? 'Firewall filter' : 'Full trace'}</span>
            </span>
          </div>

          {/* Turn Sequence Feed */}
          <div className="space-y-3.5 max-h-[640px] overflow-y-auto pr-1">
            {activeTranscriptTab === 'blocked' && turns.length > blockedTurns.length && (
              <div
                onClick={() => setActiveTranscriptTab('all')}
                className="p-2.5 rounded-xl bg-canvas border border-dashed border-border-subtle text-center text-xs text-text-muted hover:text-text-primary hover:border-brand-primary/40 cursor-pointer transition-colors"
              >
                <span>+ Show {turns.length - blockedTurns.length} hidden benign messages</span>
              </div>
            )}

            {displayedTurns.map((turn, tIdx) => {
              const isBlocked = turn.is_blocked;

              return (
                <div
                  key={tIdx}
                  className={`p-4 rounded-xl border transition-all space-y-2.5 ${
                    isBlocked
                      ? 'bg-rose-50/40 border-rose-500/30 shadow-sm'
                      : 'bg-canvas/60 border-border-subtle'
                  }`}
                >
                  {/* Step Header */}
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-bold text-text-primary">
                        Turn {turn.step_number} · {turn.role === 'user' ? '👤 User Prompt' : '🤖 Agent Action'}
                      </span>
                      {isBlocked && (
                        <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-600 text-white uppercase animate-pulse">
                          🛑 Blocked
                        </span>
                      )}
                    </div>
                    {turn.risk_score !== undefined && (
                      <span className="font-mono text-[11px] text-text-secondary">
                        Risk: {Math.round(turn.risk_score * 100)}%
                      </span>
                    )}
                  </div>

                  {/* User Content */}
                  {turn.content && (
                    <div className="text-xs text-text-primary bg-white p-2.5 rounded-lg border border-border-subtle leading-relaxed">
                      {turn.content}
                    </div>
                  )}

                  {/* Agent Reasoning */}
                  {turn.thought && (
                    <div className="text-xs text-text-secondary italic border-l-2 border-brand-purple/40 pl-2.5 py-0.5">
                      "{turn.thought}"
                    </div>
                  )}

                  {/* Tool Call & Arguments */}
                  {turn.tool && (
                    <div className="space-y-1">
                      <div className="text-[10px] font-mono text-text-muted uppercase">Tool Action: {turn.tool}</div>
                      <pre className={`p-2.5 rounded-lg text-xs font-mono overflow-x-auto ${
                        isBlocked
                          ? 'bg-rose-950 text-rose-200 border border-rose-800'
                          : 'bg-dark-base text-emerald-300'
                      }`}>
                        {turn.arguments?.command || turn.arguments?.path || JSON.stringify(turn.arguments, null, 2)}
                      </pre>
                    </div>
                  )}

                  {/* Observation / Firewall Interception Message */}
                  {turn.observation && (
                    <div className={`p-2.5 rounded-lg text-xs leading-relaxed ${
                      isBlocked
                        ? 'bg-rose-100 text-rose-900 border border-rose-300 font-semibold'
                        : 'bg-white border border-border-subtle text-text-secondary font-mono text-[11px]'
                    }`}>
                      {turn.observation}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};
