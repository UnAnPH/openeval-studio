import React, { useEffect, useRef, useState } from 'react';
import {
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  Lightbulb,
  Maximize2,
  Minimize2,
  Sparkles,
  Terminal,
  User,
  Wrench,
} from 'lucide-react';

export interface TranscriptMessage {
  id?: string;
  role: 'developer' | 'system' | 'user' | 'assistant';
  content: string;
  thinking?: string | null;
  tool_calls?: Array<{
    tool_id: string;
    tool_name: string;
    arguments: Record<string, unknown>;
    raw_input?: string;
  }>;
  tool_results?: Array<{
    tool_id: string;
    tool_name: string;
    stdout?: string;
    stderr?: string;
    exit_code?: number;
    duration_ms?: number;
    is_error?: boolean;
  }>;
  timestamp?: string | number;
}

interface TranscriptCardViewProps {
  messages: TranscriptMessage[];
  highlightQuery?: string;
  annotatedTags?: Record<string, string[]>;
  onAddTag?: (messageId: string, tag: string) => void;
  targetMessageId?: string | null;
  onTargetConsumed?: () => void;
}

// Canonical scheming/deceptive keywords identified by safety research
const SCHEMING_KEYWORDS = [
  'sabotage',
  'deceive',
  'deception',
  'covert',
  'covertly',
  'hide',
  'bypass',
  'circumvent',
  'evade',
  'pretend',
  'falsify',
  'unmonitored',
  'leak',
  'secret',
  'backdoor',
  'stealth',
  'override security',
  'disable monitoring',
];

export const TranscriptCardView: React.FC<TranscriptCardViewProps> = ({
  messages,
  highlightQuery,
  annotatedTags = {},
  targetMessageId,
  onTargetConsumed,
}) => {
  // Keep messages reference fresh without triggering scroll re-runs
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  // Track the last scrolled ID to prevent re-scroll on background polling/re-renders
  const lastScrolledIdRef = useRef<string | null>(null);

  // Collapsible states
  // Messages default to expanded, but user can collapse individual ones or click Collapse All
  const [expandedMessages, setExpandedMessages] = useState<Record<number, boolean>>({});
  // Reasoning traces default to collapsed unless expanded
  const [expandedReasoning, setExpandedReasoning] = useState<Record<number, boolean>>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Scroll and pulse target message once when requested
  useEffect(() => {
    if (!targetMessageId) {
      lastScrolledIdRef.current = null;
      return;
    }

    // If we have already executed the jump for this target ID, do not re-run
    if (lastScrolledIdRef.current === targetMessageId) {
      return;
    }
    lastScrolledIdRef.current = targetMessageId;

    // Find the message index by its id or msg-X fallback from the current messages ref
    const currentMsgs = messagesRef.current;
    const idx = currentMsgs.findIndex((m, i) => (m.id || `msg-${i}`) === targetMessageId);
    if (idx !== -1) {
      setExpandedMessages((prev) => ({ ...prev, [idx]: true }));
    }

    const timer = setTimeout(() => {
      const el = document.getElementById(targetMessageId);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('ring-4', 'ring-indigo-500/80', 'ring-offset-2');
        setTimeout(() => {
          el.classList.remove('ring-4', 'ring-indigo-500/80', 'ring-offset-2');
        }, 2500);
      }
      onTargetConsumed?.();
    }, 80);

    return () => clearTimeout(timer);
  }, [targetMessageId, onTargetConsumed]);

  // Toggle handlers
  const toggleMessage = (idx: number) => {
    setExpandedMessages((prev) => ({
      ...prev,
      [idx]: prev[idx] !== undefined ? !prev[idx] : false,
    }));
  };

  const toggleReasoning = (idx: number) => {
    setExpandedReasoning((prev) => ({ ...prev, [idx]: !prev[idx] }));
  };

  // Global Expand All / Collapse All
  const handleExpandAll = () => {
    const allMsgs: Record<number, boolean> = {};
    const allThoughts: Record<number, boolean> = {};

    messages.forEach((msg, idx) => {
      allMsgs[idx] = true;
      if (msg.thinking) allThoughts[idx] = true;
    });

    setExpandedMessages(allMsgs);
    setExpandedReasoning(allThoughts);
  };

  const handleCollapseAll = () => {
    const allMsgs: Record<number, boolean> = {};
    const allThoughts: Record<number, boolean> = {};

    messages.forEach((_, idx) => {
      allMsgs[idx] = false;
      allThoughts[idx] = false;
    });

    setExpandedMessages(allMsgs);
    setExpandedReasoning(allThoughts);
  };

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Highlights deceptive tokens and search query
  const renderHighlightedText = (text: string) => {
    if (!text) return null;

    const terms = [...SCHEMING_KEYWORDS];
    if (highlightQuery && highlightQuery.trim().length > 2) {
      terms.unshift(highlightQuery.trim());
    }

    const pattern = new RegExp(
      `(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`,
      'gi'
    );
    const parts = text.split(pattern);

    return parts.map((part, i) => {
      const lower = part.toLowerCase();
      const isQueryMatch = highlightQuery && lower === highlightQuery.toLowerCase();
      const isSchemingMatch = SCHEMING_KEYWORDS.includes(lower);

      if (isQueryMatch) {
        return (
          <mark key={i} className="bg-yellow-300 text-yellow-950 font-bold px-1 rounded">
            {part}
          </mark>
        );
      }
      if (isSchemingMatch) {
        return (
          <span
            key={i}
            className="font-bold text-rose-700 bg-rose-100/90 px-1 py-0.5 rounded border border-rose-200"
            title="Scheming & Covert Reasoning Detection"
          >
            {part}
          </span>
        );
      }
      return part;
    });
  };

  if (!messages || messages.length === 0) {
    return (
      <div className="py-16 text-center text-xs font-mono text-slate-400">
        No transcript messages recorded for this session.
      </div>
    );
  }

  const totalToolCalls = messages.reduce((acc, m) => acc + (m.tool_calls?.length || 0), 0);

  return (
    <div className="space-y-4 max-w-4xl mx-auto w-full p-2">
      {/* Top Controls Toolbar: Global Expand / Collapse All */}
      <div className="flex items-center justify-between px-2 py-1 text-xs font-mono">
        <div className="flex items-center gap-2 text-slate-500">
          <span className="font-semibold text-slate-700">{messages.length} messages</span>
          <span>•</span>
          <span>{totalToolCalls} tool calls</span>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleExpandAll}
            className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-slate-700 bg-white hover:bg-slate-50 border border-slate-200 rounded-lg shadow-2xs transition-colors cursor-pointer"
            title="Expand all messages, reasoning traces, tool calls, and results"
          >
            <Maximize2 className="w-3 h-3 text-slate-500" />
            <span>Expand All</span>
          </button>
          <button
            type="button"
            onClick={handleCollapseAll}
            className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-slate-700 bg-white hover:bg-slate-50 border border-slate-200 rounded-lg shadow-2xs transition-colors cursor-pointer"
            title="Collapse all messages into compact summary rows"
          >
            <Minimize2 className="w-3 h-3 text-slate-500" />
            <span>Collapse All</span>
          </button>
        </div>
      </div>

      {messages.map((msg, idx) => {
        const isDeveloper = msg.role === 'developer' || msg.role === 'system';
        const isUser = msg.role === 'user';
        const isMsgExpanded = expandedMessages[idx] !== false; // default expanded
        const isReasoningOpen = !!expandedReasoning[idx];
        const msgId = msg.id || `turn-${idx}`;
        const tags = annotatedTags[msgId] || [];

        // Truncated preview text when collapsed
        const previewText = msg.content
          ? msg.content.slice(0, 100) + (msg.content.length > 100 ? '...' : '')
          : msg.tool_calls && msg.tool_calls.length > 0
          ? `Tool: ${msg.tool_calls[0].tool_name} (${msg.tool_calls[0].raw_input || ''})`
          : 'Empty message';

        if (isUser) {
          return (
            <div
              key={idx}
              id={msgId}
              data-turn={idx + 1}
              className="p-5 rounded-2xl border border-purple-200/90 bg-white shadow-sm space-y-3.5 transition-all hover:border-purple-300"
            >
              {/* User Header */}
              <div
                onClick={() => toggleMessage(idx)}
                className="flex items-center justify-between border-b border-purple-100 pb-2.5 cursor-pointer select-none -m-1.5 p-1.5 rounded-xl hover:bg-purple-50/50 transition-colors"
              >
                <div className="flex items-center gap-2">
                  <span className="text-purple-400 hover:text-purple-600 transition-transform">
                    {isMsgExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                  </span>
                  <span className="px-2 py-0.5 rounded-lg text-xs font-mono font-bold bg-purple-100 text-purple-800 border border-purple-200 flex items-center gap-1.5">
                    <User className="w-3.5 h-3.5 text-purple-700" />
                    <span>USER</span>
                  </span>
                  <span className="text-xs font-mono text-slate-500 font-bold">
                    Turn #{idx + 1}
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      copyToClipboard(msg.content, `usr-${idx}`);
                    }}
                    className="text-slate-400 hover:text-slate-700 flex items-center gap-1 text-[11px] font-mono transition-colors cursor-pointer px-2 py-0.5 rounded hover:bg-purple-50"
                    title="Copy user prompt"
                  >
                    {copiedId === `usr-${idx}` ? (
                      <>
                        <Check className="w-3 h-3 text-emerald-600" />
                        <span className="text-emerald-600 font-semibold">Copied</span>
                      </>
                    ) : (
                      <>
                        <Copy className="w-3 h-3" />
                        <span>Copy</span>
                      </>
                    )}
                  </button>
                  <span className="text-[10px] font-mono text-purple-700 hover:text-purple-900 font-semibold pl-1">
                    {isMsgExpanded ? 'Hide' : 'Expand'}
                  </span>
                </div>
              </div>

              {/* Collapsed summary */}
              {!isMsgExpanded && (
                <div className="text-xs font-mono text-slate-600 truncate pt-0.5 font-medium">
                  {previewText}
                </div>
              )}

              {/* Expanded user body */}
              {isMsgExpanded && (
                <div className="p-3.5 rounded-xl bg-purple-50/40 border border-purple-100 text-xs text-purple-950 leading-relaxed whitespace-pre-wrap font-sans">
                  {renderHighlightedText(msg.content)}
                </div>
              )}
            </div>
          );
        }

        // Assistant / Agent Message
        const totalDuration = msg.tool_results?.reduce((sum, r) => sum + (r.duration_ms || 0), 0) || 0;
        const latencyMs = totalDuration > 0 ? Math.round(totalDuration) : 140;
        const tokenEst = Math.round(((msg.content?.length || 0) + (msg.thinking?.length || 0)) / 4) || 310;
        const primaryTool = msg.tool_calls?.[0]?.tool_name;

        return (
          <div
            key={idx}
            id={msgId}
            data-turn={idx + 1}
            className="p-5 rounded-2xl border border-border-subtle bg-white shadow-sm space-y-3.5 transition-all hover:border-slate-300"
          >
            {/* Assistant Header */}
            <div
              onClick={() => toggleMessage(idx)}
              className="flex items-center justify-between border-b border-border-subtle pb-2.5 cursor-pointer select-none -m-1.5 p-1.5 rounded-xl hover:bg-slate-50/70 transition-colors"
            >
              <div className="flex items-center gap-2">
                <span className="text-slate-400 hover:text-slate-600 transition-transform">
                  {isMsgExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                </span>
                <span className="px-2 py-0.5 rounded-lg text-xs font-mono font-bold bg-indigo-100 text-indigo-800 border border-indigo-200 flex items-center gap-1.5">
                  <Bot className="w-3.5 h-3.5 text-indigo-700" />
                  <span>{isDeveloper ? 'SYSTEM' : 'ASSISTANT'}</span>
                </span>
                <span className="text-xs font-mono text-slate-500 font-bold">
                  Turn #{idx + 1}
                </span>
                {primaryTool && (
                  <div className="flex items-center gap-1 text-xs font-mono font-bold text-slate-800 ml-1">
                    <Wrench className="w-3.5 h-3.5 text-slate-500" />
                    <span>{primaryTool}</span>
                  </div>
                )}
              </div>

              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2 text-[10px] font-mono text-slate-500">
                  <span className="flex items-center gap-1">
                    <Clock className="w-3 h-3 text-slate-400" />
                    <span>{latencyMs}ms</span>
                  </span>
                  <span>•</span>
                  <span className="flex items-center gap-1">
                    <Sparkles className="w-3 h-3 text-indigo-500" />
                    <span>{tokenEst} tok</span>
                  </span>
                </div>

                {tags.map((tag) => (
                  <span
                    key={tag}
                    className="px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-indigo-100 text-indigo-800 border border-indigo-200"
                  >
                    {tag}
                  </span>
                ))}

                <span className="text-[10px] font-mono text-indigo-600 hover:text-indigo-800 font-semibold pl-1">
                  {isMsgExpanded ? 'Hide' : 'Expand'}
                </span>
              </div>
            </div>

            {/* Collapsed preview */}
            {!isMsgExpanded && (
              <div className="text-xs font-mono text-slate-600 truncate pt-0.5">
                {previewText}
              </div>
            )}

            {/* Expanded Assistant Body */}
            {isMsgExpanded && (
              <div className="space-y-3.5 pt-1">
                {/* Agent Response Text (if present) */}
                {msg.content && (
                  <div className="text-xs text-slate-700 leading-relaxed bg-slate-50 p-3.5 rounded-xl border border-slate-200/80 font-sans whitespace-pre-wrap">
                    {renderHighlightedText(msg.content)}
                  </div>
                )}

                {/* Collapsible Reasoning Trace (<thinking>) */}
                {msg.thinking && (
                  <div className="space-y-1">
                    <div
                      onClick={() => toggleReasoning(idx)}
                      className="text-[10px] uppercase font-mono tracking-wider text-slate-500 font-bold flex items-center justify-between cursor-pointer select-none py-1"
                    >
                      <div className="flex items-center gap-1 text-indigo-700">
                        <Lightbulb className="w-3.5 h-3.5 text-indigo-600" />
                        <span>
                          Agent Reasoning & Plan (
                          <span className="text-indigo-800 font-semibold">&lt;thinking&gt;</span>)
                        </span>
                      </div>
                      <span className="text-[10px] font-mono text-indigo-600 hover:underline">
                        {isReasoningOpen ? 'Minimize trace' : 'Expand trace'}
                      </span>
                    </div>
                    {isReasoningOpen && (
                      <div className="text-xs text-slate-700 leading-relaxed bg-slate-50 p-3.5 rounded-xl border border-slate-200/80 font-mono whitespace-pre-wrap">
                        {renderHighlightedText(msg.thinking)}
                      </div>
                    )}
                  </div>
                )}

                {/* Tool Invocations in Launch Evaluation Terminal Style */}
                {msg.tool_calls &&
                  msg.tool_calls.map((tc, tcIdx) => {
                    const callId = tc.tool_id || `tc-${idx}-${tcIdx}`;
                    const result =
                      msg.tool_results?.find((r) => r.tool_id === tc.tool_id) ||
                      msg.tool_results?.[tcIdx];
                    const rawCmd =
                      (tc.arguments?.CommandLine as string) ||
                      (tc.arguments?.command as string) ||
                      tc.raw_input ||
                      '';
                    const outText = (
                      result?.stdout ||
                      result?.stderr ||
                      ''
                    ).trim();

                    return (
                      <div key={callId} className="space-y-3 pt-1">
                        {/* Executed Command Block */}
                        <div className="space-y-1">
                          <div className="flex items-center justify-between text-[10px] uppercase font-mono tracking-wider text-slate-500 font-bold">
                            <div className="flex items-center gap-1 text-amber-700">
                              <Terminal className="w-3 h-3" />
                              <span>Executed Command ({tc.tool_name})</span>
                            </div>
                            <button
                              type="button"
                              onClick={() =>
                                copyToClipboard(
                                  rawCmd || JSON.stringify(tc.arguments, null, 2),
                                  `cmd-${callId}`
                                )
                              }
                              className="text-slate-400 hover:text-slate-700 flex items-center gap-1 transition-colors cursor-pointer"
                              title="Copy command to clipboard"
                            >
                              {copiedId === `cmd-${callId}` ? (
                                <>
                                  <Check className="w-3 h-3 text-emerald-600" />
                                  <span className="text-emerald-600">Copied</span>
                                </>
                              ) : (
                                <>
                                  <Copy className="w-3 h-3" />
                                  <span>Copy</span>
                                </>
                              )}
                            </button>
                          </div>
                          <pre className="bg-slate-950 text-slate-100 font-mono text-xs p-3.5 rounded-xl border border-slate-800 select-text overflow-x-auto">
                            {rawCmd ? (
                              <>
                                <span className="text-emerald-400 select-none font-bold">$ </span>
                                {rawCmd}
                              </>
                            ) : (
                              JSON.stringify(tc.arguments, null, 2)
                            )}
                          </pre>
                        </div>

                        {/* Observation Terminal Block */}
                        {outText && (
                          <div className="space-y-1">
                            <div className="flex items-center justify-between text-[10px] uppercase font-mono tracking-wider text-slate-500 font-bold">
                              <div className="flex items-center gap-1">
                                <Terminal className="w-3 h-3 text-slate-400" />
                                <span>Container Observation / Result</span>
                              </div>
                              <button
                                type="button"
                                onClick={() => copyToClipboard(outText, `obs-${callId}`)}
                                className="text-slate-400 hover:text-slate-700 flex items-center gap-1 transition-colors cursor-pointer"
                                title="Copy output to clipboard"
                              >
                                {copiedId === `obs-${callId}` ? (
                                  <>
                                    <Check className="w-3 h-3 text-emerald-600" />
                                    <span className="text-emerald-600">Copied</span>
                                  </>
                                ) : (
                                  <>
                                    <Copy className="w-3 h-3" />
                                    <span>Copy</span>
                                  </>
                                )}
                              </button>
                            </div>
                            <pre className="bg-slate-950 text-slate-300 font-mono text-xs p-3.5 rounded-xl border border-slate-800 max-h-56 overflow-y-auto whitespace-pre-wrap leading-relaxed select-text">
                              {outText}
                            </pre>
                          </div>
                        )}
                      </div>
                    );
                  })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

