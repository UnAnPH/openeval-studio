#!/usr/bin/env bash
# Install / refresh OpenEval Watcher hooks for Cursor (project + user-level).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GATE="$ROOT/scripts/cursor_watcher_gate.py"
PROJECT_HOOKS="$ROOT/.cursor/hooks.json"
USER_HOOKS="${HOME}/.cursor/hooks.json"

mkdir -p "$ROOT/.cursor" "${HOME}/.cursor" "${HOME}/.openeval"
chmod +x "$GATE"

python3 - <<PY
import json
from pathlib import Path

gate = ${GATE@Q}
def build_payload(cmd: str) -> dict:
    return {
        "version": 1,
        "hooks": {
            "beforeShellExecution": [
                {"command": cmd, "timeout": 5}
            ],
            "preToolUse": [
                {"command": cmd, "timeout": 5}
            ],
        },
    }

def merge_hooks(path: Path, cmd: str) -> None:
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}
    payload = build_payload(cmd)
    existing["version"] = payload["version"]
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    hooks["beforeShellExecution"] = payload["hooks"]["beforeShellExecution"]
    hooks["preToolUse"] = payload["hooks"]["preToolUse"]
    existing["hooks"] = hooks
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"Updated {path} -> {cmd}")

# Project hooks use portable relative invocation; user-level hooks use absolute path
merge_hooks(Path(${PROJECT_HOOKS@Q}), "python3 scripts/cursor_watcher_gate.py")
merge_hooks(Path(${USER_HOOKS@Q}), f'python3 "{gate}"')
print(f"Gate: {gate}")
PY

echo "Smoke-testing local deny..."
printf '%s' '{"hook_event_name":"beforeShellExecution","command":"git push --force origin main","conversation_id":"install-smoke"}' \
  | python3 "$GATE"
echo
echo "Done. Reload Cursor window / start a new Agent chat so hooks take effect."
echo "Gate log: ~/.openeval/cursor-watcher-gate.log"
echo "Watcher must be running: http://127.0.0.1:8000 (Control mode Enforce)."
