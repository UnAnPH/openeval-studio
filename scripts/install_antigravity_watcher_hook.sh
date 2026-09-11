#!/usr/bin/env bash
# Install / refresh OpenEval Watcher PreToolUse gate for Google Antigravity.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GATE="$ROOT/scripts/antigravity_watcher_gate.py"
INSTALL_DIR="${HOME}/scripts"
INSTALL_GATE="${INSTALL_DIR}/antigravity_watcher_gate.py"
HOOKS_JSON="${HOME}/.gemini/config/hooks.json"

mkdir -p "$INSTALL_DIR" "${HOME}/.gemini/config" "${HOME}/.openeval"
chmod +x "$GATE"
cp "$GATE" "$INSTALL_GATE"
chmod +x "$INSTALL_GATE"

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
                        "timeout": 3,
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
                        "timeout": 3,
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
