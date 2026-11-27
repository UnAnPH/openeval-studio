#!/usr/bin/env bash
# Install / refresh the OpenEval Watcher gate for OpenCode.
#
# Installs the Python gate to ~/.openeval/hooks/opencode_watcher_hook.py and
# writes the OpenCode plugin (V2 `Plugin.define` hooks + V1 `server()` hook) to
# ~/.config/opencode/plugins/openeval-watcher/index.ts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
ROOT=""
if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/../.git" ]; then
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
HOOK_SRC="${ROOT:+$ROOT/scripts/opencode_watcher_hook.py}"
INSTALL_DIR="${HOME}/.openeval/hooks"
INSTALL_HOOK="${INSTALL_DIR}/opencode_watcher_hook.py"
PLUGIN_DIR="${HOME}/.config/opencode/plugins/openeval-watcher"
PLUGIN_FILE="${PLUGIN_DIR}/index.ts"

mkdir -p "$INSTALL_DIR" "$PLUGIN_DIR" "${HOME}/.openeval"

if [ -n "$HOOK_SRC" ] && [ -f "$HOOK_SRC" ]; then
  chmod +x "$HOOK_SRC"
  cp "$HOOK_SRC" "$INSTALL_HOOK"
else
  SERVER_URL="${OPENEVAL_WATCHER_URL:-https://openeval.studio}"
  SERVER_URL="${SERVER_URL%/api/v1/watcher/evaluate}"
  SERVER_URL="${SERVER_URL%/api/v1/watcher/gate}"
  SERVER_URL="${SERVER_URL%/api/watcher/evaluate}"
  echo "Downloading opencode_watcher_hook.py from ${SERVER_URL}..."
  curl -fsSL "${SERVER_URL}/api/scripts/opencode_watcher_hook.py" -o "$INSTALL_HOOK"
fi
chmod +x "$INSTALL_HOOK"

# Persist configuration to ~/.openeval/config.json so the hook works when
# OpenCode spawns it without the user's shell environment.
python3 - <<CFG
import json
import os

cfg_path = os.path.expanduser("~/.openeval/config.json")
data = {}
if os.path.exists(cfg_path):
    try:
        with open(cfg_path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
if not isinstance(data, dict):
    data = {}
key = os.environ.get("OPENEVAL_API_KEY", "").strip()
url = os.environ.get("OPENEVAL_WATCHER_URL", "https://openeval.studio/api/v1/watcher/gate").strip()
if key:
    data["api_key"] = key
if url:
    data["watcher_url"] = url
with open(cfg_path, "w") as fh:
    json.dump(data, fh, indent=2)
os.chmod(cfg_path, 0o600)
CFG

if ! grep -q "OPENEVAL_API_KEY" "$INSTALL_HOOK"; then
  echo "Installed hook is missing OPENEVAL_API_KEY auth headers: $INSTALL_HOOK" >&2
  exit 1
fi

# Single default export: V2 plugin hooks (permission.evaluate + tool.execute.before)
# spread alongside the V1 server() hook map, so either OpenCode runtime works.
cat > "$PLUGIN_FILE" <<'PLUGIN_TS'
/**
 * OpenEval Watcher plugin for OpenCode.
 *
 * Supports OpenCode V2 (`Plugin.define` + `ctx.permission` / `ctx.tool` hooks)
 * and OpenCode V1 (`server()` returning a legacy `tool.execute.before` handler)
 * from one default export. Every check shells out to the local Python gate at
 * ~/.openeval/hooks/opencode_watcher_hook.py, which runs a sub-millisecond local
 * blacklist and then asks the OpenEval Watcher gateway.
 */
import { spawnSync } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";

import { Plugin } from "@opencode/plugin";

const HOOK_PATH = join(homedir(), ".openeval", "hooks", "opencode_watcher_hook.py");
const HOOK_TIMEOUT_MS = 10_000;

type Decision = "allow" | "deny" | "ask" | "force_ask";
type Effect = "allow" | "deny" | "ask";

interface Verdict {
  decision: Decision;
  permission: Effect;
  effect: Effect;
  reason: string;
  ruleId?: string | null;
  terminalReason?: string;
}

interface GateEvent {
  effect?: string;
  message?: string;
}

type Payload = Record<string, unknown>;

function isFailClosed(): boolean {
  const mode = (process.env.OPENEVAL_GATE_FAIL_MODE ?? "open").trim().toLowerCase();
  return ["closed", "fail-closed", "fail_closed", "deny"].includes(mode);
}

function fallbackVerdict(): Verdict {
  if (isFailClosed()) {
    return {
      decision: "deny",
      permission: "deny",
      effect: "deny",
      reason: "OpenEval Watcher gate unavailable (OPENEVAL_GATE_FAIL_MODE=closed)",
    };
  }
  return {
    decision: "allow",
    permission: "allow",
    effect: "allow",
    reason: "OpenEval Watcher gate unavailable (fail-open)",
  };
}

function asRecord(value: unknown): Payload {
  return value !== null && typeof value === "object" ? (value as Payload) : {};
}

/** Build the stdin payload understood by the Python gate hook. */
function buildPayload(input: unknown, output?: unknown): Payload {
  const source = asRecord(input);
  const extra = asRecord(output);
  const payload: Payload = {};

  if (typeof source.action === "string") payload.action = source.action;
  if (Array.isArray(source.resources)) payload.resources = source.resources;

  const tool = source.tool ?? source.tool_name ?? source.name ?? extra.tool ?? source.action;
  if (typeof tool === "string") payload.tool = tool;

  // V2 carries args on the input record; the V1 `tool.execute.before` hook
  // hands them over on the output record.
  const args =
    source.input ??
    source.args ??
    source.arguments ??
    extra.input ??
    extra.args ??
    extra.arguments;
  if (args !== undefined) {
    payload.input = asRecord(args);
  } else if (typeof tool === "string") {
    payload.input = source;
  }

  const session = source.sessionID ?? source.sessionId ?? source.session_id;
  if (typeof session === "string") payload.sessionID = session;
  if (typeof source.model === "string") payload.model = source.model;

  return payload;
}

function evaluate(input: unknown, output?: unknown): Verdict {
  const payload = buildPayload(input, output);
  const stdout = (() => {
    try {
      const proc = spawnSync("python3", [HOOK_PATH], {
        input: JSON.stringify(payload),
        encoding: "utf8",
        timeout: HOOK_TIMEOUT_MS,
        env: { ...process.env },
      });
      if (proc.error || typeof proc.stdout !== "string") return "";
      return proc.stdout.trim();
    } catch {
      return "";
    }
  })();

  if (!stdout) return fallbackVerdict();

  try {
    const parsed = asRecord(JSON.parse(stdout) as unknown);
    const decision = String(parsed.decision ?? "allow").toLowerCase();
    if (!["allow", "deny", "ask", "force_ask"].includes(decision)) return fallbackVerdict();
    const effect = String(parsed.effect ?? parsed.permission ?? "allow").toLowerCase();
    const permission = String(parsed.permission ?? effect).toLowerCase();
    return {
      decision: decision as Decision,
      permission: (["allow", "deny", "ask"].includes(permission) ? permission : "ask") as Effect,
      effect: (["allow", "deny", "ask"].includes(effect) ? effect : "ask") as Effect,
      reason: typeof parsed.reason === "string" ? parsed.reason : "",
      ruleId: typeof parsed.rule_id === "string" ? parsed.rule_id : null,
      terminalReason:
        typeof parsed.terminal_reason === "string" ? parsed.terminal_reason : undefined,
    };
  } catch {
    return fallbackVerdict();
  }
}

function lockoutMessage(verdict: Verdict): string {
  const base = verdict.reason || "Blocked by OpenEval Watcher";
  return verdict.terminalReason ? `${base} (terminal_reason=${verdict.terminalReason})` : base;
}

/** deny -> effect="deny"; ask/force_ask -> effect="ask"; allow -> untouched. */
function applyPermissionVerdict(event: GateEvent, verdict: Verdict): void {
  if (verdict.decision === "deny") {
    event.effect = "deny";
    event.message = verdict.reason;
    return;
  }
  if (verdict.decision === "ask" || verdict.decision === "force_ask") {
    event.effect = "ask";
    event.message = verdict.reason;
  }
}

async function permissionEvaluate(input: unknown, event?: GateEvent): Promise<void> {
  const verdict = evaluate(input);
  if (verdict.decision === "allow") return;

  if (!event || typeof event !== "object") {
    // V2 always passes an event; if a future runtime does not, halt instead of
    // silently allowing a denied action.
    throw new Error(lockoutMessage(verdict));
  }
  applyPermissionVerdict(event, verdict);
}

async function toolExecuteBefore(input: unknown, output?: unknown): Promise<void> {
  const verdict = evaluate(input, output);
  if (verdict.decision === "allow") return;
  // Tool hooks cannot render an interactive prompt: throwing halts execution and
  // lets OpenCode surface the denial / operator approval request.
  throw new Error(lockoutMessage(verdict));
}

export default {
  ...Plugin.define({
    id: "openeval.watcher",
    async setup(ctx) {
      await ctx.permission.hook("evaluate", permissionEvaluate);
      await ctx.tool.hook("execute.before", toolExecuteBefore);
    },
  }),
  async server() {
    return {
      "tool.execute.before": async (input: unknown, output: unknown) => {
        await toolExecuteBefore(input, output);
      },
    };
  },
};
PLUGIN_TS
chmod 644 "$PLUGIN_FILE"

echo "Smoke-testing local deny..."
printf '%s' '{"tool":"bash","input":{"command":"sudo rm -rf /"}}' | python3 "$INSTALL_HOOK"
echo
echo "Done. Restart OpenCode / start a new session so the plugin reloads."
echo "Hook:   ${INSTALL_HOOK}"
echo "Plugin: ${PLUGIN_FILE}"
echo "Gate log: ~/.openeval/opencode-watcher-gate.log"
