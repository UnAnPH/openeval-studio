#!/usr/bin/env python3
"""Claude Code PreToolUse hook → OpenEval Watcher `/api/watcher/evaluate`.

Claude Code settings.json example:
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Edit|Write|MultiEdit|Read",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /ABS/PATH/openeval-studio/scripts/claude_code_watcher_hook.py"
          }
        ]
      }
    ]
  }
}

Stdout uses modern hookSpecificOutput.permissionDecision (allow|deny|ask).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

WATCHER_URL = os.environ.get("OPENEVAL_WATCHER_URL", "http://127.0.0.1:8000/api/watcher/evaluate")
TIMEOUT_SEC = float(os.environ.get("OPENEVAL_WATCHER_TIMEOUT", "1.5"))
LOG_PATH = Path.home() / ".openeval" / "claude-watcher-gate.log"

LOCAL_BLACKLIST = [
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\b", "Recursive force-delete"),
    (r"\bsudo\b", "sudo privilege escalation"),
    (r"git\s+push\s+.*--(force|force-with-lease)\b", "git force push"),
    (r"curl\s+.*\|\s*(ba)?sh", "curl | bash"),
    (r"169\.254\.169\.254", "cloud metadata exfil"),
    (r"\bchmod\s+(-R\s+)?(777|a\+rwx)\b", "chmod 777"),
]


def _log(event: str, **fields: object) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "event": event, **fields}) + "\n")
    except Exception:
        pass


def _emit(permission: str, reason: str = "") -> None:
    # Modern Claude Code PreToolUse schema
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission,
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(out, ensure_ascii=False))
    _log("emit", permission=permission, reason=reason[:240])


def _cmd_from_input(tool_name: str, tool_input: dict) -> str:
    if tool_name.lower() in ("bash", "shell", "powershell"):
        return str(tool_input.get("command") or tool_input.get("cmd") or "").strip()
    return str(
        tool_input.get("command") or tool_input.get("file_path") or tool_input.get("path") or ""
    ).strip()


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        _emit("allow")
        return
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _emit("allow", "invalid stdin JSON")
        return

    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "unknown")
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        tool_input = {"raw": str(tool_input)}

    cmd = _cmd_from_input(tool_name, tool_input)
    session_id = str(payload.get("session_id") or payload.get("sessionId") or "")
    _log("pretool", tool_name=tool_name, cmd=cmd[:200], session_id=session_id[:64])

    for pattern, reason in LOCAL_BLACKLIST:
        if cmd and re.search(pattern, cmd, re.IGNORECASE):
            _emit("deny", f"[WATCHER] {reason}")
            return

    # Sensitive path writes
    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if path.endswith(".env") or "id_rsa" in path or "/.aws/credentials" in path:
        _emit("deny", "[WATCHER] Sensitive file path")
        return

    args = dict(tool_input)
    if cmd and "command" not in args:
        args["command"] = cmd

    try:
        req = urllib.request.Request(
            WATCHER_URL,
            data=json.dumps(
                {
                    "agent_id": "claude_code",
                    "tool_name": tool_name,
                    "arguments": args,
                    "session_id": session_id or None,
                    "thought_context": session_id,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            verdict = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        _log("server_unreachable", error=str(err))
        _emit("allow")
        return

    decision = str(verdict.get("decision", "allow")).lower()
    risk = float(verdict.get("risk_score") or 0.0)
    reason = str(verdict.get("reason") or "")
    _log(
        "server_verdict",
        decision=decision,
        risk_score=risk,
        shadow_decision=verdict.get("shadow_decision"),
        mode_applied=verdict.get("mode_applied"),
    )

    # Obey server decision only (observe mode: allow + high risk + shadow_decision).
    if decision in ("deny", "reject", "block"):
        _emit("deny", f"[WATCHER BLOCKED] {reason}")
        return
    if decision in ("escalate", "ask", "warn"):
        _emit("ask", f"[WATCHER ESCALATION] {reason}")
        return
    _emit("allow")


if __name__ == "__main__":
    main()
