import React, { useState } from 'react';
import { IonIcon } from '@ionic/react';
import {
  arrowBackOutline,
  alertCircle,
  documentTextOutline,
  copyOutline,
  checkmarkOutline,
  timeOutline,
  personOutline,
  codeSlashOutline,
  chevronDownOutline,
  chevronUpOutline,
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
  const turns = sessionDetail?.turns && sessionDetail.turns.length > 0 ? sessionDetail.turns : [];

  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const [expandedReasoning, setExpandedReasoning] = useState<Record<number, boolean>>({});

  const handleCopy = (text: string, index: number) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  const toggleReasoning = (stepNum: number) => {
    setExpandedReasoning((prev) => ({ ...prev, [stepNum]: !prev[stepNum] }));
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
          Return to Dashboard
        </button>
      </div>
    );
  }

  const headline = finding.headline?.trim() || 'No headline';
  const summaryText = finding.summary?.trim() || 'No summary available';
  const agentLabel =
    finding.agent_source === 'antigravity'
      ? 'Antigravity'
      : finding.agent_source === 'claude_code'
      ? 'Claude Code'
      : finding.agent_source === 'openeval_runner'
      ? 'OpenEval Runner'
      : finding.agent_source || 'agent';
  const reviewedAt = finding.timestamp?.trim() || 'Unknown time';
  const primaryAction = finding.recommended_actions?.[0];
  const trajectoryLabel = finding.session_id || finding.id || 'Trajectory';

  return (
    <div className="flex flex-col h-full bg-[#fcfcfd] text-[#1e2029] font-sans p-6 space-y-5 overflow-y-auto">
      {/* 1. Top Card Header & Breadcrumbs */}
      <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-3 shrink-0">
        <div className="flex items-center justify-between text-xs text-gray-400 font-mono">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onBack}
              className="flex items-center gap-1 text-gray-600 hover:text-gray-900 font-medium cursor-pointer"
            >
              <IonIcon icon={arrowBackOutline} className="text-xs" />
              <span>Dashboard</span>
            </button>
            <span>&gt;</span>
            <span className="text-gray-600">Sessions</span>
            <span>&gt;</span>
            <span className="text-gray-600">{finding.session_id || finding.id}</span>
            <span>&gt;</span>
            <span className="text-indigo-600 font-bold">Trajectory</span>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1">
              <IonIcon icon={personOutline} className="text-xs" />
              <span>{finding.developer || 'Operator'}</span>
            </div>
            <span>·</span>
            <div className="flex items-center gap-1">
              <IonIcon icon={timeOutline} className="text-xs" />
              <span>{reviewedAt}</span>
            </div>
            <span>·</span>
            <span>{turns.length} messages</span>
          </div>
        </div>

        <div className="flex items-start justify-between gap-4 pt-1">
          <div>
            <div className="flex items-center gap-2 mb-1.5">
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-50 text-rose-600 border border-rose-200">
                <span className="w-1.5 h-1.5 rounded-full bg-rose-600" />
                <span>{finding.severity || 'Critical'}</span>
              </span>
              <span className="text-xs font-mono text-gray-500">{agentLabel}</span>
            </div>
            <h1 className="text-lg font-bold text-gray-900 leading-snug">
              {headline}
            </h1>
          </div>
        </div>
      </div>

      {/* 2. Main Two-Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* Left Column (5 Cols): Just Automated Analysis and Recommended Action */}
        <div className="lg:col-span-5 space-y-4">
          {/* Card 1: Automated Analysis */}
          <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-3">
            <div className="flex items-center gap-2 text-xs font-bold text-gray-900">
              <IonIcon icon={documentTextOutline} className="text-indigo-600 text-sm" />
              <h3>Automated analysis</h3>
            </div>
            <p className="text-xs text-gray-600 leading-relaxed">
              {summaryText}
            </p>
            <div className="flex items-center gap-2 pt-2 border-t border-gray-100 text-[11px] font-mono text-gray-500">
              <span>Working Dir: <strong className="text-gray-700">{sessionDetail?.working_directory || '/workspace'}</strong></span>
              <span>·</span>
              <span>Turns: <strong className="text-gray-700">{turns.length}</strong></span>
            </div>
          </div>

          {/* Card 2: Recommended Action — only when finding provides one */}
          {primaryAction ? (
            <div className="bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-50 text-rose-600 border border-rose-200">
                    {primaryAction.priority} {primaryAction.category}
                  </span>
                  <h3 className="text-xs font-bold text-gray-900">Recommended Action</h3>
                </div>
                {primaryAction.citations?.length > 0 && (
                  <span className="text-[10px] font-mono text-gray-400">
                    cites {primaryAction.citations.length} message{primaryAction.citations.length === 1 ? '' : 's'}
                  </span>
                )}
              </div>

              <div className="text-xs font-bold text-gray-900">
                {primaryAction.title}
              </div>

              <div className="text-xs text-gray-600 leading-relaxed bg-gray-50 p-3 rounded-xl border border-gray-100 font-sans">
                {primaryAction.description}
              </div>
            </div>
          ) : null}
        </div>

        {/* Right Column (7 Cols): Full Transcript */}
        <div className="lg:col-span-7 bg-white p-5 rounded-2xl border border-border-subtle shadow-sm space-y-4">
          <div className="flex items-center justify-between border-b border-gray-100 pb-3">
            <div className="flex items-center gap-2">
              <IonIcon icon={codeSlashOutline} className="text-indigo-600 text-sm" />
              <h3 className="text-xs font-bold text-gray-900">Full Transcript</h3>
            </div>
            <span className="text-[11px] font-mono text-gray-400">
              {turns.length} total turns · {trajectoryLabel}
            </span>
          </div>

          {/* Transcript Message Stream */}
          <div className="space-y-4">
            {turns.length === 0 ? (
              <div className="p-8 text-center text-xs text-slate-500 font-mono border border-dashed border-slate-200 rounded-xl">
                No trajectory turns recorded for this session.
                <div className="mt-1 text-[11px] text-slate-400">
                  Live blocks appear here when OpenEval gates a tool call.
                </div>
              </div>
            ) : (
            turns.map((turn, tIdx) => {
              const isBlocked = turn.is_blocked;
              const hasReasoning = Boolean(turn.thought);
              const isReasoningOpen = expandedReasoning[turn.step_number] ?? true;

              return (
                <div
                  key={tIdx}
                  className={`p-4 rounded-xl border transition-all space-y-3 ${
                    isBlocked
                      ? 'bg-rose-50/30 border-rose-200 shadow-xs'
                      : 'bg-white border-gray-100 shadow-xs'
                  }`}
                >
                  {/* Turn Header */}
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-bold text-gray-900">
                        &gt; {turn.step_number} {turn.role === 'user' ? 'User' : `Assistant ${agentLabel}`}
                      </span>
                    </div>
                  </div>

                  {/* Internal Reasoning (Purple Box) */}
                  {hasReasoning && (
                    <div className="p-3 rounded-xl bg-[#faf5ff] border border-purple-100 space-y-1.5">
                      <button
                        type="button"
                        onClick={() => toggleReasoning(turn.step_number)}
                        className="flex items-center gap-1 text-[11px] font-bold text-purple-900 hover:text-purple-700 cursor-pointer"
                      >
                        <IonIcon icon={isReasoningOpen ? chevronUpOutline : chevronDownOutline} className="text-xs" />
                        <span>Internal reasoning</span>
                      </button>
                      {isReasoningOpen && (
                        <p className="text-xs text-purple-900/90 italic leading-relaxed font-sans pl-2 border-l-2 border-purple-200">
                          {turn.thought}
                        </p>
                      )}
                    </div>
                  )}

                  {/* Assistant Message Text */}
                  {turn.content && (
                    <div className="text-xs text-gray-800 leading-relaxed font-sans">
                      {turn.content}
                    </div>
                  )}

                  {/* Tool Call Box */}
                  {turn.tool && (
                    <div className="space-y-1">
                      <div className="text-[10px] font-mono text-gray-500 uppercase">
                        &gt;_ {turn.tool}
                      </div>
                      <div className="relative group">
                        <pre className="p-3 rounded-xl bg-[#0f172a] text-slate-200 text-xs font-mono overflow-x-auto">
                          {String(turn.arguments?.command || JSON.stringify(turn.arguments, null, 2))}
                        </pre>
                        <button
                          type="button"
                          onClick={() => handleCopy(String(turn.arguments?.command || JSON.stringify(turn.arguments)), tIdx)}
                          className="absolute top-2 right-2 px-2 py-1 rounded bg-white/10 text-white hover:bg-white/20 text-[10px] font-sans flex items-center gap-1 cursor-pointer"
                        >
                          <IonIcon icon={copiedIndex === tIdx ? checkmarkOutline : copyOutline} className="text-xs" />
                          <span>{copiedIndex === tIdx ? 'Copied' : 'Copy'}</span>
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Policy Gate Intercept Card */}
                  {isBlocked ? (
                    <div className="p-3.5 rounded-xl border border-rose-200 bg-rose-50/60 space-y-1.5">
                      <div className="flex items-center gap-1.5 text-xs font-bold text-rose-700">
                        <IonIcon icon={alertCircle} className="text-sm text-rose-600" />
                        <span>Blocked / escalated</span>
                      </div>
                      {turn.rule_violation_tag && (
                        <div className="text-xs font-bold text-rose-900">
                          {turn.rule_violation_tag}
                        </div>
                      )}
                      {turn.reason && (
                        <p className="text-[11px] text-rose-700 leading-relaxed">
                          {turn.reason}
                        </p>
                      )}
                    </div>
                  ) : (
                    turn.observation && (
                      <div className="p-2.5 rounded-xl bg-gray-50 border border-gray-100 text-xs text-gray-700 font-mono">
                        <span className="font-bold text-gray-900">Result: </span>
                        {turn.observation}
                      </div>
                    )
                  )}
                </div>
              );
            })
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
