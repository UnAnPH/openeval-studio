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
  Download,
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
  const [activeSiem, setActiveSiem] = useState<'splunk' | 'datadog' | 'syslog'>('splunk');
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedScript, setCopiedScript] = useState(false);
  const [copiedSiem, setCopiedSiem] = useState(false);
  const [isRotating, setIsRotating] = useState(false);
  const [showRotateConfirm, setShowRotateConfirm] = useState(false);
  const [localApiKey, setLocalApiKey] = useState<string | null>(apiKey || null);

  React.useEffect(() => {
    if (apiKey) {
      setLocalApiKey(apiKey);
    } else if (!localApiKey) {
      fetch('/api/auth/key')
        .then((res) => {
          if (res.ok) return res.json();
          return null;
        })
        .then((data) => {
          if (data && data.api_key) {
            setLocalApiKey(data.api_key);
          }
        })
        .catch(() => {});
    }
  }, [apiKey]);

  const displayKey =
    localApiKey || apiKey || (_isDemo ? 'oe_live_demo_readonly_token' : '<rotate to issue a one-time key>');
  const watcherUrl = typeof window !== 'undefined'
    ? `${window.location.origin}/api/v1/watcher/evaluate`
    : 'https://openeval.studio/api/v1/watcher/evaluate';

  const copyToClipboard = async (text: string, target: 'key' | 'script' | 'siem' = 'script') => {
    try {
      await navigator.clipboard.writeText(text);
      if (target === 'key') {
        setCopiedKey(true);
        setTimeout(() => setCopiedKey(false), 2000);
      } else if (target === 'siem') {
        setCopiedSiem(true);
        setTimeout(() => setCopiedSiem(false), 2000);
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

  const getScriptFilename = () => {
    if (activeAgent === 'cursor') return 'install_cursor_watcher_hook.sh';
    if (activeAgent === 'claude') return 'claude_code_watcher_hook.py';
    return 'install_antigravity_watcher_hook.sh';
  };

  const getCommands = () => {
    const envExports = `export OPENEVAL_WATCHER_URL="${watcherUrl}"\nexport OPENEVAL_API_KEY="${displayKey}"`;
    const serverOrigin = typeof window !== 'undefined' ? window.location.origin : 'https://openeval.studio';

    if (activeAgent === 'cursor') {
      return `${envExports}\n\n# Option 1: One-line install (downloads & configures .cursor/hooks.json)\ncurl -fsSL "${serverOrigin}/api/scripts/install_cursor_watcher_hook.sh" | bash\n\n# Option 2: From local cloned repo\n./scripts/install_cursor_watcher_hook.sh`;
    }
    if (activeAgent === 'claude') {
      return `${envExports}\n\n# Option 1: Download Python hook and point Claude Code to it\nmkdir -p ~/scripts && curl -fsSL "${serverOrigin}/api/scripts/claude_code_watcher_hook.py" -o ~/scripts/claude_code_watcher_hook.py\npython3 ~/scripts/claude_code_watcher_hook.py\n\n# Option 2: From local cloned repo\npython3 scripts/claude_code_watcher_hook.py`;
    }
    return `${envExports}\n\n# Option 1: One-line install (downloads & configures ~/.gemini/config/hooks.json)\ncurl -fsSL "${serverOrigin}/api/scripts/install_antigravity_watcher_hook.sh" | bash\n\n# Option 2: From local cloned repo\n./scripts/install_antigravity_watcher_hook.sh`;
  };

  const getSiemConfig = () => {
    if (activeSiem === 'splunk') {
      return `# Configure Splunk HTTP Event Collector (HEC)
export OPENEVAL_SIEM_TYPE="splunk"
export OPENEVAL_SIEM_ENDPOINT="https://splunk.internal.corp:8088/services/collector/event"
export OPENEVAL_SIEM_TOKEN="<splunk_hec_token>"

# Sample Splunk HEC Event Payload:
{
  "time": 1789230000,
  "source": "watcher:runtime_gate",
  "sourcetype": "openeval:security:review",
  "index": "security",
  "event": {
    "decision": "deny",
    "agent_id": "cursor",
    "risk_score": 0.95,
    "threat_category": "destructive_modification_of_shared_resources",
    "rule_violation_tag": "RULE_FORCE_PUSH_MAIN",
    "action_preview": "run_command: git push --force origin main",
    "explanation": "Blocked destructive force push to main branch"
  }
}`;
    }
    if (activeSiem === 'datadog') {
      return `# Configure Datadog Logs API
export OPENEVAL_SIEM_TYPE="datadog"
export OPENEVAL_SIEM_ENDPOINT="https://http-intake.logs.datadoghq.com/api/v2/logs"
export OPENEVAL_SIEM_TOKEN="<datadog_api_key>"

# Sample Datadog JSON Event Payload:
{
  "timestamp": "2026-10-29T21:00:00Z",
  "ddsource": "watcher",
  "service": "openeval-watcher",
  "status": "ERROR",
  "message": "[DENY] git push --force: Blocked destructive force push to main branch",
  "risk_score": 0.95,
  "threat_category": "destructive_modification_of_shared_resources",
  "agent_id": "cursor"
}`;
    }
    return `# Configure Syslog RFC 5424 (CEF / LEEF Compatible)
export OPENEVAL_SIEM_TYPE="syslog_rfc5424"
export OPENEVAL_SIEM_ENDPOINT="syslog://log-collector.internal.corp:514"

# Sample RFC 5424 Syslog Audit Message:
<131>1 2026-10-29T21:00:00.000000Z host openeval-watcher - RULE_FORCE_PUSH_MAIN [openeval@42424 session_id="session-123" agent_id="cursor" decision="deny" score="0.95"] run_command: git push --force origin main - Blocked destructive force push`;
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
              <code className="h-8 px-3 rounded-lg bg-white border border-purple-300 font-mono text-xs text-purple-950 font-bold select-all inline-flex items-center">
                {localApiKey}
              </code>
              <button
                type="button"
                onClick={() => copyToClipboard(localApiKey, 'key')}
                className="h-8 px-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-colors shadow-xs cursor-pointer"
              >
                {copiedKey ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                <span>{copiedKey ? 'Copied!' : 'Copy Key'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Header Info */}
      <div className="bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-base font-bold text-slate-900 tracking-tight flex items-center gap-2.5">
            Agent Hook Setup
          </h1>
        </div>

        {username && (
          <div className="flex items-center gap-2.5">
            <div className="h-9 px-3 rounded-xl bg-slate-100 border border-slate-200 text-xs font-mono text-slate-700 inline-flex items-center gap-1.5">
              User: <span className="font-semibold text-indigo-700">@{username}</span>
            </div>
            <button
              type="button"
              onClick={() => setShowRotateConfirm(true)}
              className="h-9 px-3.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 text-xs font-semibold text-slate-700 shadow-2xs inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer"
            >
              <RefreshCw className="w-3.5 h-3.5 text-slate-500" />
              <span>Rotate Key</span>
            </button>
          </div>
        )}
      </div>

      {/* IDE Switcher Tabs */}
      <div className="space-y-4">
        <div className="h-9 bg-slate-100 p-1 rounded-xl border border-slate-200/70 inline-flex items-center gap-1">
          <button
            type="button"
            onClick={() => setActiveAgent('cursor')}
            className={`h-7 px-3 rounded-lg text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer ${
              activeAgent === 'cursor'
                ? 'bg-white text-slate-900 shadow-xs font-bold'
                : 'text-slate-500 hover:text-slate-900'
            }`}
          >
            <Code2 className="w-3.5 h-3.5" />
            <span>Cursor</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveAgent('claude')}
            className={`h-7 px-3 rounded-lg text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer ${
              activeAgent === 'claude'
                ? 'bg-white text-slate-900 shadow-xs font-bold'
                : 'text-slate-500 hover:text-slate-900'
            }`}
          >
            <Terminal className="w-3.5 h-3.5" />
            <span>Claude Code</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveAgent('antigravity')}
            className={`h-7 px-3 rounded-lg text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer ${
              activeAgent === 'antigravity'
                ? 'bg-white text-slate-900 shadow-xs font-bold'
                : 'text-slate-500 hover:text-slate-900'
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5" />
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
            <div className="flex items-center gap-2">
              <a
                href={`/api/scripts/${getScriptFilename()}`}
                download={getScriptFilename()}
                className="h-8 px-3 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-colors shadow-xs"
              >
                <Download className="w-3.5 h-3.5" />
                <span>Download Script</span>
              </a>
              <button
                type="button"
                onClick={() => copyToClipboard(getCommands(), 'script')}
                className="h-8 px-2.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-colors cursor-pointer font-mono"
              >
                {copiedScript ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                <span>{copiedScript ? 'Copied' : 'Copy'}</span>
              </button>
            </div>
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

      {/* Enterprise SIEM & SecOps Streaming Card */}
      <div className="bg-white p-6 rounded-2xl border border-border-subtle shadow-sm space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-sm font-bold text-slate-900 tracking-tight flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-indigo-600" />
              <span>Enterprise SIEM & SecOps Streaming</span>
            </h2>
            <p className="text-xs text-slate-500">
              Stream real-time agent interception events into corporate SIEM pipelines (Splunk HEC, Datadog Logs API, Syslog RFC 5424).
            </p>
          </div>

          {/* SIEM Selector Tabs */}
          <div className="inline-flex rounded-xl bg-slate-100 p-1 border border-slate-200">
            <button
              type="button"
              onClick={() => setActiveSiem('splunk')}
              className={`h-8 px-3 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
                activeSiem === 'splunk'
                  ? 'bg-white text-indigo-700 shadow-2xs font-bold'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Splunk HEC
            </button>
            <button
              type="button"
              onClick={() => setActiveSiem('datadog')}
              className={`h-8 px-3 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
                activeSiem === 'datadog'
                  ? 'bg-white text-indigo-700 shadow-2xs font-bold'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Datadog
            </button>
            <button
              type="button"
              onClick={() => setActiveSiem('syslog')}
              className={`h-8 px-3 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
                activeSiem === 'syslog'
                  ? 'bg-white text-indigo-700 shadow-2xs font-bold'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Syslog RFC 5424
            </button>
          </div>
        </div>

        {/* SIEM Code Box */}
        <div className="rounded-xl bg-slate-900 border border-slate-800 overflow-hidden shadow-md">
          <div className="px-4 py-2 bg-slate-950/60 border-b border-slate-800 flex items-center justify-between">
            <span className="text-xs font-mono text-slate-400">
              {activeSiem === 'splunk' && 'Splunk HTTP Event Collector (HEC) Configuration'}
              {activeSiem === 'datadog' && 'Datadog Logs API Ingestion Configuration'}
              {activeSiem === 'syslog' && 'Syslog RFC 5424 Audit Stream Configuration'}
            </span>
            <button
              type="button"
              onClick={() => copyToClipboard(getSiemConfig(), 'siem')}
              className="h-7 px-2.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold inline-flex items-center justify-center gap-1.5 transition-colors cursor-pointer font-mono"
            >
              {copiedSiem ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
              <span>{copiedSiem ? 'Copied' : 'Copy'}</span>
            </button>
          </div>
          <pre className="p-4 text-xs font-mono text-emerald-400 overflow-x-auto leading-relaxed whitespace-pre-wrap">
            {getSiemConfig()}
          </pre>
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
            <div className="flex items-center justify-end gap-2.5 pt-2">
              <button
                type="button"
                onClick={() => setShowRotateConfirm(false)}
                disabled={isRotating}
                className="h-9 px-3.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-semibold shadow-2xs inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleRotate}
                disabled={isRotating}
                className="h-9 px-3.5 rounded-xl bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold shadow-xs inline-flex items-center justify-center gap-1.5 transition-all cursor-pointer disabled:opacity-50"
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
