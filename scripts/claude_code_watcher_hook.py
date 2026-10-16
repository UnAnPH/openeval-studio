#!/usr/bin/env python3
"""Claude Code PreToolUse hook → OpenEval Watcher `/api/v1/watcher/evaluate`.

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

import contextlib
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from engine.redaction import redact_payload, redact_text
except ImportError:
    def redact_text(text: str) -> str:
        if not text:
            return text
        t = re.sub(r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b", "[REDACTED:ANTHROPIC_API_KEY]", text)
        t = re.sub(r"\b(?:sk-proj-[a-zA-Z0-9_-]{20,}|sk-[a-zA-Z0-9]{20,})\b", "[REDACTED:OPENAI_API_KEY]", t)
        t = re.sub(r"\b(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}\b", "[REDACTED:GITHUB_TOKEN]", t)
        t = re.sub(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "[REDACTED:AWS_ACCESS_KEY]", t)
        return t

    def redact_payload(obj: object) -> object:
        if isinstance(obj, str):
            return redact_text(obj)
        if isinstance(obj, dict):
            return {k: redact_payload(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [redact_payload(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(redact_payload(v) for v in obj)
        return obj


def _safe_int(val: object, default: int = 0) -> int:
    try:
        if val is None:
            return default
        return int(val)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def _safe_float(val: object, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


MAX_STDIN_BYTES = 2 * 1024 * 1024  # 2 MiB cap
MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MiB cap
REQUEST_ID = uuid.uuid4().hex[:16]

WATCHER_URL = os.environ.get(
    "OPENEVAL_WATCHER_URL", "http://127.0.0.1:8000/api/v1/watcher/evaluate"
)
OPENEVAL_API_KEY = os.environ.get("OPENEVAL_API_KEY", "").strip()
TIMEOUT_SEC = _safe_float(os.environ.get("OPENEVAL_WATCHER_TIMEOUT"), 1.5)
LOCKOUT_PERMISSION = os.environ.get("OPENEVAL_LOCKOUT_PERMISSION", "ask")
FAIL_MODE = os.environ.get(
    "OPENEVAL_GATE_FAIL_MODE",
    os.environ.get("OPENEVAL_FAIL_MODE", os.environ.get("OPENEVAL_ON_FAILURE", "open")),
).strip().lower()
LOG_PATH = Path.home() / ".openeval" / "claude-watcher-gate.log"


def _rotate_log_file_if_needed(log_path: Path) -> None:
    try:
        if log_path.exists() and log_path.stat().st_size >= MAX_LOG_BYTES:
            rotated = log_path.with_name(f"{log_path.name}.1")
            if rotated.exists():
                with contextlib.suppress(OSError):
                    rotated.unlink()
            os.replace(log_path, rotated)
    except Exception:  # noqa: BLE001  # Boundary: log rotation failure must not crash hook
        pass


def _get_headers(req_id: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-ID": req_id or REQUEST_ID,
    }
    if OPENEVAL_API_KEY:
        headers["X-OpenEval-Key"] = OPENEVAL_API_KEY
        headers["Authorization"] = f"Bearer {OPENEVAL_API_KEY}"
    return headers


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
        _rotate_log_file_if_needed(LOG_PATH)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        redacted = {k: redact_payload(v) for k, v in fields.items()}
        row = {"ts": time.time(), "request_id": REQUEST_ID, "event": event, **redacted}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001  # Boundary: local logging write failure must never crash hook
        pass


def _emit(permission: str, reason: str = "") -> None:
    # Modern Claude Code PreToolUse schema
    redacted_reason = redact_text(reason)
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission,
            "permissionDecisionReason": redacted_reason,
        }
    }
    try:
        print(json.dumps(out, ensure_ascii=False))
        sys.stdout.flush()
    except BrokenPipeError:
        pass
    _log("emit", permission=permission, reason=redacted_reason[:240])


def _claude_session_id(raw: str) -> str | None:
    """Same session id for PreToolUse and PostToolUse so results attach in the UI."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", (raw or "").strip())
    if not cleaned:
        return None
    if cleaned.startswith("claude-"):
        return cleaned
    return f"claude-{cleaned[:12]}"


def _report_to_watcher(
    *,
    tool_name: str,
    args: dict,
    session_id: str | None,
    cmd: str,
) -> None:
    """Best-effort POST so local blacklist hits show up on the live dashboard."""
    try:
        req = urllib.request.Request(
            WATCHER_URL,
            data=json.dumps(
                {
                    "agent_id": "claude_code",
                    "tool_name": tool_name,
                    "arguments": args if args else ({"command": cmd} if cmd else {}),
                    "session_id": session_id,
                    "thought_context": session_id,
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
                source="local_blacklist_report",
                session_id=session_id,
            )
    except Exception as err:  # noqa: BLE001  # Boundary: best-effort report failure must not block execution
        _log("server_unreachable", error=str(err), source="local_blacklist_report")


def _cmd_from_input(tool_name: str, tool_input: dict) -> str:
    if tool_name.lower() in ("bash", "shell", "powershell"):
        return str(tool_input.get("command") or tool_input.get("cmd") or "").strip()
    return str(
        tool_input.get("command") or tool_input.get("file_path") or tool_input.get("path") or ""
    ).strip()


def _report_tool_result(payload: dict) -> None:
    result_url = os.environ.get(
        "OPENEVAL_WATCHER_RESULT_URL",
        WATCHER_URL.replace("/api/watcher/evaluate", "/api/v1/watcher/result").replace(
            "/api/v1/watcher/evaluate", "/api/v1/watcher/result"
        ),
    )
    hook_input = payload.get("hookSpecificInput") or payload
    tool_name = str(payload.get("tool_name") or hook_input.get("toolName") or "unknown")
    raw_session = str(payload.get("session_id") or hook_input.get("sessionId") or "")
    session_id = _claude_session_id(raw_session) or ""

    tool_response = (
        hook_input.get("toolResponse")
        or hook_input.get("tool_result")
        or hook_input.get("toolResult")
        or {}
    )
    if isinstance(tool_response, str):
        stdout = tool_response
        stderr = ""
        exit_code = 0
    elif isinstance(tool_response, dict):
        stdout = str(
            tool_response.get("stdout")
            or tool_response.get("output")
            or tool_response.get("content")
            or ""
        )
        stderr = str(tool_response.get("stderr") or "")
        exit_code = int(tool_response.get("exitCode") or tool_response.get("exit_code") or 0)
    else:
        stdout = str(tool_response or "")
        stderr = ""
        exit_code = 0

    err = str(payload.get("error") or hook_input.get("error") or "")
    if err and not stderr:
        stderr = err

    req_body = {
        "session_id": session_id or None,
        "agent_id": "claude_code",
        "tool_name": tool_name,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "error": err or None,
    }
    try:
        req = urllib.request.Request(
            result_url,
            data=json.dumps(req_body).encode("utf-8"),
            headers=_get_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC):
            pass
        _log("posttool_result_reported", session_id=session_id)
    except Exception as err:  # noqa: BLE001  # Boundary: best-effort posttool result logging
        _log("posttool_result_error", error=str(err))


def main() -> None:
    try:
        raw = sys.stdin.read(MAX_STDIN_BYTES + 1)
    except Exception as err:  # noqa: BLE001  # Boundary: stdin read error handling
        _log("stdin_read_error", error=str(err))
        raw = ""

    if not raw.strip():
        if FAIL_MODE in ("closed", "fail-closed", "fail_closed", "deny"):
            _emit("deny", "Empty stdin payload in fail-closed mode")
        else:
            _emit("allow")
        return

    if len(raw) > MAX_STDIN_BYTES:
        _log("stdin_payload_too_large", size=len(raw), max_bytes=MAX_STDIN_BYTES)
        _emit(LOCKOUT_PERMISSION, "Payload exceeds maximum allowed size (2 MiB)")
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        _log("stdin_json_error", error=str(err))
        _emit(LOCKOUT_PERMISSION, "Invalid stdin JSON")
        return

    if not isinstance(payload, dict):
        _emit(LOCKOUT_PERMISSION, "Invalid stdin payload: expected JSON object")
        return

    hook_name = str(
        payload.get("hookSpecificInput", {}).get("hookEventName")
        or payload.get("hookEventName")
        or payload.get("hook_name")
        or ""
    )
    if "--post" in sys.argv or hook_name == "PostToolUse":
        _report_tool_result(payload)
        print("{}")
        return

    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "unknown")
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        tool_input = {"raw": str(tool_input)}

    cmd = _cmd_from_input(tool_name, tool_input)
    session_id = _claude_session_id(
        str(payload.get("session_id") or payload.get("sessionId") or "")
    )
    _log("pretool", tool_name=tool_name, cmd=cmd[:200], session_id=(session_id or "")[:64])

    args = dict(tool_input)
    if cmd and "command" not in args:
        args["command"] = cmd

    for pattern, reason in LOCAL_BLACKLIST:
        if cmd and re.search(pattern, cmd, re.IGNORECASE):
            _report_to_watcher(
                tool_name=tool_name,
                args=args,
                session_id=session_id,
                cmd=cmd,
            )
            _emit(
                LOCKOUT_PERMISSION,
                f"[OPENEVAL INTERACTIVE LOCKOUT]: {reason}. Execution frozen pending human operator approval.",
            )
            return

    # Sensitive path writes
    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if path.endswith(".env") or "id_rsa" in path or "/.aws/credentials" in path:
        _report_to_watcher(
            tool_name=tool_name,
            args=args,
            session_id=session_id,
            cmd=path,
        )
        _emit(
            LOCKOUT_PERMISSION,
            "[OPENEVAL INTERACTIVE LOCKOUT]: Sensitive file path. Execution frozen pending human operator approval.",
        )
        return

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
            headers=_get_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            verdict = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code == 422:
            try:
                err_body = json.loads(err.read().decode("utf-8"))
                unknown_keys = [
                    str(d["loc"][-1])
                    for d in err_body.get("detail", [])
                    if isinstance(d, dict) and d.get("type") == "extra_forbidden" and "loc" in d
                ]
            except Exception:  # noqa: BLE001  # Boundary: JSON decode failure on 422 error body
                unknown_keys = []
            keys_str = ", ".join(unknown_keys) if unknown_keys else "unknown"
            drift_msg = (
                f"payload schema drift: unexpected fields [{keys_str}] rejected by Watcher gate"
            )
            sys.stderr.write(f"[OPENEVAL WARNING] {drift_msg}\n")
            _log("payload_schema_drift", unknown_keys=unknown_keys, error=str(err))
            _emit(
                LOCKOUT_PERMISSION,
                f"[OPENEVAL SCHEMA DRIFT]: {drift_msg}. Execution frozen pending operator review.",
            )
            return
        _log("server_error", error=str(err))
        _emit("allow")
        return
    except Exception as err:  # noqa: BLE001  # Boundary: network transport failure during evaluate call
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
            f"[OPENEVAL INTERACTIVE LOCKOUT]: {reason} (Risk: {int(risk * 100)}%). Execution frozen pending human operator approval.",
        )
        return
    if decision in ("escalate", "ask", "warn"):
        _emit("ask", f"[OPENEVAL ESCALATION] {reason}")
        return
    _emit("allow")


def _setup_signal_handlers() -> None:
    def _sig_handler(signum: int, frame: object) -> None:
        sys.exit(128 + signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(ValueError, AttributeError):
            signal.signal(sig, _sig_handler)


if __name__ == "__main__":
    _setup_signal_handlers()
    try:
        main()
    except BrokenPipeError:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            os.dup2(devnull, sys.stderr.fileno())
        except Exception:  # noqa: BLE001  # Boundary: devnull redirection on broken pipe
            pass
        sys.exit(0)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001  # Boundary: top-level safety gate error handler
        _log("unhandled_exception", error=str(exc))
        if FAIL_MODE in ("closed", "fail-closed", "fail_closed", "deny"):
            _emit("deny", f"Unhandled error in gate: {exc}")
        else:
            _emit("allow", f"Unhandled error in gate (fail-open): {exc}")
