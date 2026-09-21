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

Cursor Protocol Rules:
1. beforeShellExecution supports "permission": "allow" | "deny" | "ask".
   When "ask" is returned, Cursor renders an interactive confirmation prompt
   asking the operator to proceed or reject the command.
2. preToolUse ONLY supports "permission": "allow" | "deny".
   Cursor crashes if "ask" is returned on preToolUse ("The 'ask' permission for
   preToolUse hooks is not yet implemented").
3. When Cursor runs a shell command, it fires BOTH preToolUse (tool_name="Shell")
   and beforeShellExecution. preToolUse must allow "Shell" so that beforeShellExecution
   can perform command inspection and render the interactive confirmation prompt.
4. File write tools (Write, Edit, ApplyPatch) must NOT have their file contents
   evaluated against shell regexes. Only sensitive path mutations (.env, /etc/*, id_rsa)
   are blocked. Normal project file edits are allowed.
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
OPENEVAL_API_KEY = os.environ.get("OPENEVAL_API_KEY", "").strip()
TIMEOUT_SEC = float(os.environ.get("OPENEVAL_WATCHER_TIMEOUT", "1.5"))
# For beforeShellExecution, default to "ask" so Cursor prompts the user
SHELL_LOCKOUT_PERMISSION = os.environ.get("OPENEVAL_LOCKOUT_PERMISSION", "ask")
LOG_PATH = Path.home() / ".openeval" / "cursor-watcher-gate.log"


def _get_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if OPENEVAL_API_KEY:
        headers["X-OpenEval-Key"] = OPENEVAL_API_KEY
        headers["Authorization"] = f"Bearer {OPENEVAL_API_KEY}"
    return headers


LOCAL_BLACKLIST = [
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\b", "Recursive force-delete (rm -rf)"),
    (r"\bsudo\b", "sudo privilege escalation"),
    (r"git\s+push\s+.*--(force|force-with-lease)\b", "git force push"),
    (r"curl\s+.*\|\s*(ba)?sh", "Remote script piped to shell (curl | bash)"),
    (r"169\.254\.169\.254", "Cloud IMDS metadata credential exfiltration"),
    (r"\bchmod\s+(-R\s+)?(777|a\+rwx)\b", "Security permissions weakening (chmod 777)"),
]

PROTECTED_PATH_PATTERNS = [
    (
        r"(^|/)(\.env|\.envrc|\.secrets|secret[s]?\.json)(\.|$)",
        "Environment / secret credentials (.env)",
    ),
    (r"(^|/)(id_rsa|id_ed25519|id_ecdsa|\.pem|\.key)$", "Private encryption key / credential file"),
    (r"^/etc/(shadow|passwd|sudoers)", "System authentication and security file"),
    (r"(^|/)\.git/hooks/", "Git hook script modification"),
    (r"(^|/)\.watcherignore$", "Watcher monitoring bypass (.watcherignore)"),
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


def _extract_path(tool_input: object) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in (
        "path",
        "file_path",
        "filePath",
        "target_file",
        "TargetFile",
        "targetFile",
        "file",
        "AbsolutePath",
        "uri",
        "filename",
        "name",
    ):
        val = tool_input.get(key)
        if val and isinstance(val, str):
            return val.strip()
    for nested_key in ("input", "parameters", "args", "arguments"):
        nested = tool_input.get(nested_key)
        if isinstance(nested, dict):
            found = _extract_path(nested)
            if found:
                return found
    return ""


def _is_sensitive_path(path: str) -> tuple[bool, str]:
    if not path:
        return False, ""
    normalized = path.replace("\\", "/")
    for pattern, desc in PROTECTED_PATH_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return True, desc
    return False, ""


def _session_id(conversation_id: str) -> str | None:
    if not conversation_id:
        return None
    clean = re.sub(r"[^a-zA-Z0-9_-]", "", conversation_id)[:32]
    return f"cursor-{clean}" if not clean.startswith("cursor-") else clean


def _report_to_watcher(
    *,
    tool_name: str,
    args: dict,
    conversation_id: str,
    cmd: str,
) -> None:
    """Best-effort POST so Live Stream / Sessions see blocked actions."""
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
                    "session_id": _session_id(conversation_id),
                    "thought_context": conversation_id,
                }
            ).encode("utf-8"),
            headers=_get_headers(),
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
                source="report_to_watcher",
            )
    except Exception as err:
        _log("server_unreachable", error=str(err), source="report_to_watcher")


def _handle_shell_execution(cmd: str, conversation_id: str) -> None:
    """Handle beforeShellExecution hook with local blacklist + server check."""
    if not cmd:
        _emit("allow")
        return

    _log("before_shell", cmd=cmd[:200], conversation_id=conversation_id[:64])

    # 1. Local offline blacklist check
    for pattern, reason in LOCAL_BLACKLIST:
        if re.search(pattern, cmd, re.IGNORECASE):
            _report_to_watcher(
                tool_name="Shell",
                args={"command": cmd, "CommandLine": cmd},
                conversation_id=conversation_id,
                cmd=cmd,
            )
            # In beforeShellExecution, "ask" prompts the operator in Cursor IDE
            prompt_msg = f"[OPENEVAL SAFETY GATE]: {reason} detected (`{cmd[:120]}`). Do you want to proceed with this command?"
            _emit(SHELL_LOCKOUT_PERMISSION, prompt_msg)
            return

    # 2. Query Watcher server
    try:
        req = urllib.request.Request(
            WATCHER_URL,
            data=json.dumps(
                {
                    "agent_id": "cursor",
                    "tool_name": "Shell",
                    "arguments": {"command": cmd, "CommandLine": cmd},
                    "session_id": _session_id(conversation_id),
                    "thought_context": conversation_id,
                }
            ).encode("utf-8"),
            headers=_get_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            verdict = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        _log("server_unreachable", error=str(err), cmd=cmd[:120])
        # Fail-open only after local checks passed
        _emit("allow")
        return

    decision = str(verdict.get("decision", "allow")).lower()
    risk = float(verdict.get("risk_score") or 0.0)
    reason = str(verdict.get("reason") or "Safety policy check")
    _log(
        "server_verdict",
        decision=decision,
        risk_score=risk,
        reason=reason[:200],
        shadow_decision=verdict.get("shadow_decision"),
        mode_applied=verdict.get("mode_applied"),
    )

    if decision in ("deny", "reject", "block", "escalate", "ask", "warn"):
        prompt_msg = f"[OPENEVAL SAFETY GATE]: {reason} (Risk: {int(risk * 100)}%): `{cmd[:120]}`. Do you want to proceed?"
        _emit(SHELL_LOCKOUT_PERMISSION, prompt_msg)
        return

    _emit("allow")


def _handle_pre_tool_use(
    tool_name: str,
    tool_input: object,
    conversation_id: str,
) -> None:
    """Handle preToolUse hook. Note: Cursor preToolUse ONLY supports 'allow' or 'deny'."""
    lower_tool = tool_name.lower()

    _log("pre_tool_use", tool_name=tool_name, conversation_id=conversation_id[:64])

    # 1. Shell commands via tool call: delegate to beforeShellExecution hook
    if lower_tool in ("shell", "terminal", "bash", "sh", "exec", "powershell"):
        _emit("allow")
        return

    # 2. Read-only tools
    if lower_tool in (
        "read",
        "readfile",
        "view_file",
        "grep",
        "glob",
        "listdir",
        "find_by_name",
        "grep_search",
    ):
        path = _extract_path(tool_input)
        if path and re.search(r"(^|/)(id_rsa|id_ed25519|id_ecdsa|/etc/shadow)", path):
            _emit(
                "deny",
                f"[OPENEVAL SAFETY GATE]: Reading protected private credential file ('{path}') is blocked.",
            )
            return
        _emit("allow")
        return

    # 3. File modification tools (Write, Edit, ApplyPatch, etc.)
    path = _extract_path(tool_input)
    is_sensitive, sensitive_reason = _is_sensitive_path(path)
    if is_sensitive:
        _report_to_watcher(
            tool_name=tool_name,
            args={"path": path},
            conversation_id=conversation_id,
            cmd="",
        )
        # In preToolUse, Cursor only supports 'allow' or 'deny' (not 'ask')
        _emit(
            "deny",
            f"[OPENEVAL SAFETY GATE]: Modifying protected path ('{path}') is blocked ({sensitive_reason}).",
        )
        return

    # 4. Normal workspace file modification: query Watcher with path-only payload
    if path:
        try:
            req = urllib.request.Request(
                WATCHER_URL,
                data=json.dumps(
                    {
                        "agent_id": "cursor",
                        "tool_name": tool_name,
                        "arguments": {"path": path},
                        "session_id": _session_id(conversation_id),
                        "thought_context": conversation_id,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
                verdict = json.loads(resp.read().decode("utf-8"))
            decision = str(verdict.get("decision", "allow")).lower()
            risk = float(verdict.get("risk_score") or 0.0)
            reason = str(verdict.get("reason") or "Safety policy violation")
            if decision in ("deny", "reject", "block"):
                _emit(
                    "deny",
                    f"[OPENEVAL SAFETY GATE]: {reason} (Risk: {int(risk * 100)}%).",
                )
                return
        except Exception as err:
            _log("server_unreachable", error=str(err), path=path)
            # Fail-open for normal project files
            _emit("allow")
            return

    # All standard project file edits and benign tools are allowed
    _emit("allow")


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
    conversation_id = str(payload.get("conversation_id") or payload.get("conversationId") or "")
    cmd = str(payload.get("command") or "").strip()
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "")
    tool_input = payload.get("tool_input") or payload.get("arguments") or {}

    if not cmd and isinstance(tool_input, dict):
        cmd = str(tool_input.get("command") or tool_input.get("CommandLine") or "").strip()

    # Route based on hook event
    if hook_event == "beforeShellExecution" or (cmd and hook_event != "preToolUse"):
        _handle_shell_execution(cmd, conversation_id)
    elif hook_event == "preToolUse":
        if not tool_name:
            tool_name = "Shell" if cmd else "unknown"
        _handle_pre_tool_use(tool_name, tool_input, conversation_id)
    else:
        # Unknown hook event: fail-open
        _emit("allow")


if __name__ == "__main__":
    main()
