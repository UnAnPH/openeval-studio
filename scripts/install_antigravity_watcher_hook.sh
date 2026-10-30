#!/usr/bin/env bash
# Install / refresh OpenEval Watcher PreToolUse gate for Google Antigravity.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
ROOT=""
if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/../.git" ]; then
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
GATE="${ROOT:+$ROOT/scripts/antigravity_watcher_gate.py}"
INSTALL_DIR="${HOME}/scripts"
INSTALL_GATE="${INSTALL_DIR}/antigravity_watcher_gate.py"
HOOKS_JSON="${HOME}/.gemini/config/hooks.json"

mkdir -p "$INSTALL_DIR" "${HOME}/.gemini/config" "${HOME}/.openeval"

if [ -n "$GATE" ] && [ -f "$GATE" ]; then
  chmod +x "$GATE"
  cp "$GATE" "$INSTALL_GATE"
else
  SERVER_URL="${OPENEVAL_WATCHER_URL:-https://openeval.studio}"
  SERVER_URL="${SERVER_URL%/api/v1/watcher/evaluate}"
  SERVER_URL="${SERVER_URL%/api/watcher/evaluate}"
  echo "Downloading antigravity_watcher_gate.py from ${SERVER_URL}..."
  curl -fsSL "${SERVER_URL}/api/scripts/antigravity_watcher_gate.py" -o "$INSTALL_GATE"
fi
chmod +x "$INSTALL_GATE"

# Persist configuration to ~/.openeval/config.json so GUI apps (Antigravity IDE) load it reliably
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

hooks_path = Path(${HOOKS_JSON@Q})
gate = ${INSTALL_GATE@Q}
payload = {
    "openeval-watcher-gate": {
        "enabled": True,
        "PreToolUse": [
            {
                # Use .* (regex) — bare "*" is invalid RE and may never match.
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python3 {gate}",
                        "timeout": 5,
                    }
                ],
            }
        ],
        "PostToolUse": [
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python3 {gate} --post",
                        "timeout": 5,
                    }
                ],
            }
        ],
    }
}
if hooks_path.exists():
    try:
        existing = json.loads(hooks_path.read_text())
        if not isinstance(existing, dict):
            existing = {}
    except Exception:
        existing = {}
else:
    existing = {}
existing["openeval-watcher-gate"] = payload["openeval-watcher-gate"]
hooks_path.write_text(json.dumps(existing, indent=2) + "\n")
print(f"Updated {hooks_path}")
print(f"Gate: {gate}")
PY

echo "Smoke-testing local deny..."
printf '%s' '{"toolCall":{"name":"run_command","args":{"CommandLine":"sudo rm -rf /"}}}' \
  | python3 "$INSTALL_GATE"
echo
echo "Done. Restart Antigravity / open a new agent session so hooks reload."
echo "Gate log: ~/.openeval/watcher-gate.log"
