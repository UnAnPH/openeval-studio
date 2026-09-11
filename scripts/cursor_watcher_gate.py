#!/usr/bin/env python3
"""Cursor beforeShellExecution / preToolUse gate → OpenEval Watcher.

Project hooks (.cursor/hooks.json):
{
  "version": 1,
  "hooks": {
    "beforeShellExecution": [
      {
        "command": "python3 /ABS/PATH/openeval-studio/scripts/cursor_watcher_gate.py",
        "timeout": 5
      }
    ],
    "preToolUse": [
      {
        "command": "python3 /ABS/PATH/openeval-studio/scripts/cursor_watcher_gate.py",
        "timeout": 5
      }
    ]
  }
}

Stdout: { "permission": "allow"|"deny"|"ask", "user_message": "...", "agent_message": "..." }
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
LOCKOUT_PERMISSION = os.environ.get("OPENEVAL_LOCKOUT_PERMISSION", "ask")
LOG_PATH = Path.home() / ".openeval" / "cursor-watcher-gate.log"

LOCAL_BLACKLIST = [
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\b", "Recursive force-delete"),
    (r"\bsudo\b", "sudo privilege escalation"),
    (r"git\s+push\s+.*--(force|force-with-lease)\b", "git force push"),
    (r"curl\s+.*\|\s*(ba)?sh", "curl | bash"),
    (r"169\.254\.169\.254", "cloud metadata exfil"),
]


def _log(event: str, **fields: object) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "event": event, **fields}) + "\n")
    except Exception:
        pass


def _emit(permission: str, message: str = "") -> None:
    out = {
        "permission": permission,
        "continue": permission == "allow",
        "user_message": message,
        "agent_message": message,
    }
    print(json.dumps(out, ensure_ascii=False))
    _log("emit", permission=permission, message=message[:240])


def _report_to_watcher(
    *,
    tool_name: str,
    args: dict,
    conversation_id: str,
    cmd: str,
) -> None:
    """Best-effort POST so Live Stream / Sessions see local-blacklist denies."""
    try:
        req = urllib.request.Request(
            WATCHER_URL,
            data=json.dumps(
                {
                    "agent_id": "cursor",
                    "tool_name": tool_name,
                    "arguments": args
                    if args
                    else ({"command": cmd, "CommandLine": cmd} if cmd else {}),
                    "session_id": f"cursor-{conversation_id[:8]}" if conversation_id else None,
                    "thought_context": conversation_id,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            verdict = json.loads(resp.read().decode("utf-8"))
            _log(
                "server_verdict",
                decision=str(verdict.get("decision", "")).lower(),
                risk_score=float(verdict.get("risk_score") or 0.0),
                shadow_decision=verdict.get("shadow_decision"),
                mode_applied=verdict.get("mode_applied"),
                source="local_blacklist_report",
            )
    except Exception as err:
        _log("server_unreachable", error=str(err), source="local_blacklist_report")


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

    hook_event = str(payload.get("hook_event_name") or payload.get("hookEventName") or "")
    cmd = str(payload.get("command") or "").strip()
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "")
    tool_input = payload.get("tool_input") or payload.get("arguments") or {}

    if not cmd and isinstance(tool_input, dict):
        cmd = str(tool_input.get("command") or tool_input.get("CommandLine") or "").strip()
    if not tool_name:
        tool_name = "Shell" if cmd else "unknown"

    conversation_id = str(payload.get("conversation_id") or payload.get("conversationId") or "")
    _log("pretool", hook_event=hook_event, tool_name=tool_name, cmd=cmd[:200])

    for pattern, reason in LOCAL_BLACKLIST:
        if cmd and re.search(pattern, cmd, re.IGNORECASE):
            args = tool_input if isinstance(tool_input, dict) else {}
            if cmd:
                args = {**args, "command": cmd, "CommandLine": cmd}
            _report_to_watcher(
                tool_name=tool_name,
                args=args,
                conversation_id=conversation_id,
                cmd=cmd,
            )
            _emit(
                LOCKOUT_PERMISSION,
                f"[WATCHER INTERACTIVE LOCKOUT]: {reason}. Execution frozen pending human operator approval.",
            )
            return

    args = tool_input if isinstance(tool_input, dict) else {}
    if cmd:
        args = {**args, "command": cmd, "CommandLine": cmd}

    try:
        req = urllib.request.Request(
            WATCHER_URL,
            data=json.dumps(
                {
                    "agent_id": "cursor",
                    "tool_name": tool_name,
                    "arguments": args,
                    "session_id": f"cursor-{conversation_id[:8]}" if conversation_id else None,
                    "thought_context": conversation_id,
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
        _emit(
            LOCKOUT_PERMISSION,
            f"[WATCHER INTERACTIVE LOCKOUT]: {reason} (Risk: {int(risk * 100)}%). Execution frozen pending human operator approval.",
        )
        return
    if decision in ("escalate", "ask", "warn"):
        _emit("ask", f"[WATCHER ESCALATION] {reason}")
        return
    _emit("allow")


if __name__ == "__main__":
    main()
