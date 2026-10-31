#!/usr/bin/env bash
# Install / refresh OpenEval Watcher hooks for Cursor (project + user-level).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
ROOT=""
if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/../.git" ]; then
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

GATE="${ROOT:+$ROOT/scripts/cursor_watcher_gate.py}"
PROJECT_HOOKS="${ROOT:+$ROOT/.cursor/hooks.json}"
USER_HOOKS="${HOME}/.cursor/hooks.json"

INSTALL_DIR="${HOME}/scripts"
INSTALL_GATE="${INSTALL_DIR}/cursor_watcher_gate.py"

mkdir -p "${HOME}/.cursor" "${HOME}/.openeval" "$INSTALL_DIR"
if [ -n "$ROOT" ]; then
  mkdir -p "$ROOT/.cursor"
fi

if [ -n "$GATE" ] && [ -f "$GATE" ]; then
  chmod +x "$GATE"
  cp "$GATE" "$INSTALL_GATE"
else
  SERVER_URL="${OPENEVAL_WATCHER_URL:-https://openeval.studio}"
  SERVER_URL="${SERVER_URL%/api/v1/watcher/evaluate}"
  SERVER_URL="${SERVER_URL%/api/watcher/evaluate}"
  echo "Downloading cursor_watcher_gate.py from ${SERVER_URL}..."
  curl -fsSL "${SERVER_URL}/api/scripts/cursor_watcher_gate.py" -o "$INSTALL_GATE"
fi
chmod +x "$INSTALL_GATE"
# Persist configuration to ~/.openeval/config.json so GUI apps (Cursor IDE) load it reliably
python3 - <<CFG
import json, os
cfg_path = os.path.expanduser("~/.openeval/config.json")
data = {}
if os.path.exists(cfg_path):
    try:
        data = json.load(open(cfg_path))
    except Exception:
        pass
key = os.environ.get("OPENEVAL_API_KEY", "").strip()
url = os.environ.get("OPENEVAL_WATCHER_URL", "https://openeval.studio/api/v1/watcher/evaluate").strip()
if key:
    data["api_key"] = key
if url:
    data["watcher_url"] = url
with open(cfg_path, "w") as f:
    json.dump(data, f, indent=2)
os.chmod(cfg_path, 0o600)
CFG
if ! grep -q "OPENEVAL_API_KEY" "$INSTALL_GATE"; then
  echo "Installed gate is missing OPENEVAL_API_KEY auth headers: $INSTALL_GATE" >&2
  exit 1
fi

python3 - <<PY
import json
from pathlib import Path

gate = ${INSTALL_GATE@Q}
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

project_hooks_str = ${PROJECT_HOOKS@Q}
if project_hooks_str:
    merge_hooks(Path(project_hooks_str), "python3 scripts/cursor_watcher_gate.py")
merge_hooks(Path(${USER_HOOKS@Q}), f'python3 "{gate}"')
print(f"Gate: {gate}")
PY

echo "Smoke-testing local deny..."
printf '%s' '{"hook_event_name":"beforeShellExecution","command":"git push --force origin main","conversation_id":"install-smoke"}' \
  | python3 "$INSTALL_GATE"
echo
echo "Done. Reload Cursor window / start a new Agent chat so hooks take effect."
echo "Gate log: ~/.openeval/cursor-watcher-gate.log"
echo "Watcher target: ${SERVER_URL:-https://openeval.studio}"
