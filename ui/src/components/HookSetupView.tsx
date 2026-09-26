import React, { useState } from 'react';
import {
  Key,
  Copy,
  Check,
  RefreshCw,
  Terminal,
  ShieldCheck,
  AlertTriangle,
  Code2,
} from 'lucide-react';

interface HookSetupViewProps {
  apiKey?: string | null;
  username?: string | null;
  onRotateKey: () => Promise<string | null>;
  isDemo?: boolean;
}

export const HookSetupView: React.FC<HookSetupViewProps> = ({
  apiKey,
  username,
  onRotateKey,
  isDemo: _isDemo = false,
}) => {

  const [activeAgent, setActiveAgent] = useState<'cursor' | 'claude' | 'antigravity'>('cursor');
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedScript, setCopiedScript] = useState(false);
  const [isRotating, setIsRotating] = useState(false);
  const [showRotateConfirm, setShowRotateConfirm] = useState(false);
  const [localApiKey, setLocalApiKey] = useState<string | null>(apiKey || null);

  const displayKey = localApiKey || apiKey || '<YOUR_API_KEY>';
  const watcherUrl = typeof window !== 'undefined'
    ? `${window.location.origin}/api/watcher/evaluate`
    : 'https://openeval.studio/api/watcher/evaluate';

  const copyToClipboard = async (text: string, isKey = false) => {
    try {
      await navigator.clipboard.writeText(text);
      if (isKey) {
        setCopiedKey(true);
        setTimeout(() => setCopiedKey(false), 2000);
      } else {
        setCopiedScript(true);
        setTimeout(() => setCopiedScript(false), 2000);
      }
    } catch (err) {
      console.error('Failed to copy text: ', err);
    }
  };

  const handleRotate = async () => {
    setIsRotating(true);
    try {
      const newKey = await onRotateKey();
      if (newKey) {
        setLocalApiKey(newKey);
      }
    } finally {
      setIsRotating(false);
      setShowRotateConfirm(false);
    }
  };

  const getCommands = () => {
    const envExports = `export OPENEVAL_WATCHER_URL="${watcherUrl}"\nexport OPENEVAL_API_KEY="${displayKey}"`;

    if (activeAgent === 'cursor') {
      return `${envExports}\n\n# Run the installer to configure .cursor/hooks.json\n./scripts/install_cursor_watcher_hook.sh`;
    }
    if (activeAgent === 'claude') {
      return `${envExports}\n\n# Point Claude Code to the Python hook\npython3 scripts/claude_code_watcher_hook.py`;
    }
    return `${envExports}\n\n# Install the hook into ~/.gemini/config/hooks.json\n./scripts/install_antigravity_watcher_hook.sh`;
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 max-w-5xl mx-auto space-y-6 text-slate-800">
      {/* Top Banner if new API key was just issued */}
      {localApiKey && (
        <div className="p-4 rounded-xl bg-purple-50 border border-purple-200 shadow-sm flex items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-purple-900 font-semibold text-sm">
              <Key className="w-4 h-4 text-purple-600" />
              <span>Your New API Key (Shown Once)</span>
            </div>
            <p className="text-xs text-purple-700">
              Copy this key now. For your security, raw API keys are never stored on the server and cannot be retrieved again.
            </p>
            <div className="mt-2 flex items-center gap-2">
              <code className="px-3 py-1.5 rounded-lg bg-white border border-purple-300 font-mono text-xs text-purple-950 font-bold select-all">
                {localApiKey}
              </code>
              <button
                type="button"
                onClick={() => copyToClipboard(localApiKey, true)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-xs font-medium transition-colors shadow-xs"
              >
                {copiedKey ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                <span>{copiedKey ? 'Copied!' : 'Copy Key'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Header Info */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-slate-200">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 flex items-center gap-2">
            <Terminal className="w-6 h-6 text-indigo-600" />
            Agent Hook Setup
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Connect local IDE agents to OpenEval Studio to intercept destructive commands and track safety reviews.
          </p>
        </div>

        {username && (
          <div className="flex items-center gap-3">
            <div className="px-3 py-1.5 rounded-lg bg-slate-100 border border-slate-200 text-xs font-mono text-slate-700">
              User: <span className="font-semibold text-indigo-700">@{username}</span>
            </div>
            <button
              type="button"
              onClick={() => setShowRotateConfirm(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 text-xs font-medium transition-colors shadow-xs"
            >
              <RefreshCw className="w-3.5 h-3.5 text-slate-500" />
              <span>Rotate Key</span>
            </button>
          </div>
        )}
      </div>

      {/* IDE Switcher Tabs */}
      <div className="space-y-4">
        <div className="flex items-center gap-2 border-b border-slate-200">
          <button
            type="button"
            onClick={() => setActiveAgent('cursor')}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
              activeAgent === 'cursor'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-slate-800'
            }`}
          >
            <Code2 className="w-4 h-4" />
            <span>Cursor</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveAgent('claude')}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
              activeAgent === 'claude'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-slate-800'
            }`}
          >
            <Terminal className="w-4 h-4" />
            <span>Claude Code</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveAgent('antigravity')}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
              activeAgent === 'antigravity'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-slate-800'
            }`}
          >
            <ShieldCheck className="w-4 h-4" />
            <span>Google Antigravity</span>
          </button>
        </div>

        {/* Code Box */}
        <div className="rounded-xl bg-slate-900 border border-slate-800 overflow-hidden shadow-md">
          <div className="px-4 py-2.5 bg-slate-950/60 border-b border-slate-800 flex items-center justify-between">
            <span className="text-xs font-mono text-slate-400">
              {activeAgent === 'cursor' && 'Terminal Setup (Cursor Gate)'}
              {activeAgent === 'claude' && 'Terminal Setup (Claude Code Hook)'}
              {activeAgent === 'antigravity' && 'Terminal Setup (Antigravity Hook)'}
            </span>
            <button
              type="button"
              onClick={() => copyToClipboard(getCommands(), false)}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono transition-colors"
            >
              {copiedScript ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copiedScript ? 'Copied' : 'Copy'}</span>
            </button>
          </div>
          <pre className="p-4 text-xs font-mono text-emerald-400 overflow-x-auto leading-relaxed whitespace-pre-wrap">
            {getCommands()}
          </pre>
        </div>

        {/* Instructions Summary */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
          <div className="p-4 rounded-xl bg-white border border-slate-200 shadow-xs space-y-1.5">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600">1. Environment</h3>
            <p className="text-xs text-slate-500 leading-normal">
              Set <code className="text-indigo-600 font-mono">OPENEVAL_WATCHER_URL</code> and <code className="text-indigo-600 font-mono">OPENEVAL_API_KEY</code> in your shell configuration (<code className="text-slate-700 font-mono">~/.zshrc</code> or <code className="text-slate-700 font-mono">~/.bashrc</code>).
            </p>
          </div>

          <div className="p-4 rounded-xl bg-white border border-slate-200 shadow-xs space-y-1.5">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600">2. PreToolUse Gate</h3>
            <p className="text-xs text-slate-500 leading-normal">
              Every tool call or terminal execution is checked against deterministic safety rules and tiered policy gateways.
            </p>
          </div>

          <div className="p-4 rounded-xl bg-white border border-slate-200 shadow-xs space-y-1.5">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600">3. Live Auditing</h3>
            <p className="text-xs text-slate-500 leading-normal">
              Blocked actions, latency metrics, and execution history stream directly to the <strong>Sessions</strong> and <strong>Control</strong> tabs.
            </p>
          </div>
        </div>

        {/* Verification Command */}
        <div className="p-4 rounded-xl bg-slate-50 border border-slate-200 text-xs text-slate-600 space-y-2">
          <div className="font-semibold text-slate-800 flex items-center gap-1.5">
            <Terminal className="w-4 h-4 text-slate-500" />
            <span>Verify Connection (Smoke Test)</span>
          </div>
          <p>You can verify that your API key is active by running a quick smoke test from your terminal:</p>
          <div className="relative rounded-lg bg-slate-900 p-3 font-mono text-[11px] text-slate-200 overflow-x-auto">
            {`curl -s -X POST "${watcherUrl}" \\
  -H "Authorization: Bearer ${displayKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"tool_name": "bash", "tool_input": "echo hello"}'`}
          </div>
        </div>
      </div>

      {/* Rotate Confirmation Modal */}
      {showRotateConfirm && (
        <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-4 border border-slate-100">
            <div className="flex items-center gap-3 text-amber-600">
              <AlertTriangle className="w-6 h-6" />
              <h2 className="text-base font-bold text-slate-900">Rotate API Key?</h2>
            </div>
            <p className="text-xs text-slate-600 leading-relaxed">
              Rotating your API key will immediately invalidate your active key. Any running hooks using the old key will receive 401 Unauthorized until updated.
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowRotateConfirm(false)}
                disabled={isRotating}
                className="px-3 py-1.5 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 text-xs font-medium"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleRotate}
                disabled={isRotating}
                className="px-4 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-xs font-semibold shadow-xs"
              >
                {isRotating ? 'Rotating...' : 'Yes, Rotate Key'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
