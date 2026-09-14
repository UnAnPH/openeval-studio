#!/usr/bin/env python3
"""Antigravity PreToolUse safety gate → OpenEval Watcher `/api/watcher/evaluate`.

Contract (Antigravity hooks.md):
  stdin:  { "toolCall": { "name", "args" }, "conversationId", ... }
  stdout: { "decision": "allow"|"deny"|"ask"|"force_ask", "reason": "..." }

Fail-open only when the Watcher server is unreachable/times out after local
deterministic checks. Local blacklist still blocks high-severity commands offline.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WATCHER_URL = os.environ.get("OPENEVAL_WATCHER_URL", "http://127.0.0.1:8000/api/watcher/evaluate")
OPENEVAL_API_KEY = os.environ.get("OPENEVAL_API_KEY", "").strip()
TIMEOUT_SEC = float(os.environ.get("OPENEVAL_WATCHER_TIMEOUT", "1.5"))
LOCKOUT_DECISION = os.environ.get("OPENEVAL_LOCKOUT_DECISION", "force_ask")


def _get_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if OPENEVAL_API_KEY:
        headers["X-OpenEval-Key"] = OPENEVAL_API_KEY
        headers["Authorization"] = f"Bearer {OPENEVAL_API_KEY}"
    return headers

LOG_PATH = Path(
    os.environ.get(
        "OPENEVAL_WATCHER_GATE_LOG",
        str(Path.home() / ".openeval" / "watcher-gate.log"),
    )
)

# Instant offline deny patterns (subset of Watcher command rules + high-severity)
LOCAL_BLACKLIST: list[tuple[str, str, str]] = [
    (
        r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\b",
        "Recursive force-delete (rm -rf) attempted.",
        "RULE_DESTRUCTIVE_WIPE",
    ),
    (
        r"\bsudo\b",
        "Unauthorized privilege escalation attempted (sudo).",
        "RULE_PRIVILEGE_ESCALATION",
    ),
    (r"\bsu\s+-\b", "Root user switch attempted (su -).", "RULE_PRIVILEGE_ESCALATION"),
    (
        r"\bchmod\s+(-R\s+)?(777|a\+rwx)\b",
        "Security permissions weakening (chmod 777).",
        "RULE_SECURITY_WEAKENING",
    ),
    (
        r"/var/run/docker\.sock",
        "Docker daemon socket access attempted (container escape).",
        "RULE_CONTAINER_ESCAPE",
    ),
    (
        r"169\.254\.169\.254",
        "Cloud IMDS metadata credential exfiltration attempted.",
        "RULE_CREDENTIAL_EXFILTRATION",
    ),
    (
        r"git\s+push\s+.*--(force|force-with-lease)\b",
        "Force push to remote attempted.",
        "RULE_GIT_FORCE_PUSH",
    ),
    (
        r"curl\s+.*\|\s*(ba)?sh",
        "Remote script piped to shell (curl | bash).",
        "RULE_CURL_BASH",
    ),
    (
        r"wget\s+.*\|\s*(ba)?sh",
        "Remote script piped to shell (wget | bash).",
        "RULE_WGET_BASH",
    ),
    (
        r"(?:cat|less|more|head|tail|bat)\s+[^\n]*(?:\.env\b|\.envrc\b|\.secrets\b|id_rsa|id_ed25519|\.pem\b|/etc/shadow|/\.aws/credentials)",
        "Reading secret/credential files via shell.",
        "RULE_SECRET_READ",
    ),
    (
        r"terraform\s+destroy\s+-auto-approve",
        "Terraform destroy auto-approve attempted.",
        "RULE_TF_DESTROY",
    ),
    (
        r"kubectl\s+delete\s+(all|namespace)\b",
        "Destructive kubectl delete attempted.",
        "RULE_K8S_DELETE",
    ),
    (
        r"\bkillall\s+-9\s+watchdog\b",
        "Attempted sabotage of safety monitoring process.",
        "RULE_AUDIT_SABOTAGE",
    ),
    (
        r'(api[_-]?key|secret|token|password)=["\']?[a-zA-Z0-9_\-]{16,}',
        "Inline credentials in command line.",
        "RULE_CREDENTIAL_INLINE",
    ),
]


def _log(event: str, **fields: object) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.time(), "event": event, **fields}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _emit(decision: str, reason: str = "", conversation_id: str = "") -> None:
    if decision in ("force_ask", "ask") and conversation_id:
        try:
            cid = re.sub(r"[^a-zA-Z0-9_-]", "", conversation_id)[:32]
            flag = Path(f"/tmp/openeval_force_ask_{cid}.flag")
            flag.write_text(str(time.time()), encoding="utf-8")
        except Exception:
            pass
    out: dict[str, str] = {"decision": decision}
    if reason:
        out["reason"] = reason
    print(json.dumps(out, ensure_ascii=False))
    _log("emit", decision=decision, reason=reason[:240], conversation_id=conversation_id[:64])


def _report_to_watcher(
    *,
    tool_name: str,
    tool_args: dict,
    conversation_id: str,
    cmd: str,
) -> None:
    """Best-effort POST so Live Stream / Sessions see local-blacklist denies."""
    req_body = {
        "agent_id": "antigravity",
        "tool_name": tool_name,
        "arguments": tool_args if tool_args else ({"CommandLine": cmd} if cmd else {}),
        "thought_context": conversation_id,
        "session_id": f"antigravity-{conversation_id[:8]}" if conversation_id else None,
    }
    try:
        req = urllib.request.Request(
            WATCHER_URL,
            data=json.dumps(req_body).encode("utf-8"),
            headers=_get_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
            verdict = json.loads(body)
            _log(
                "server_verdict",
                decision=str(verdict.get("decision", "")).lower(),
                risk_score=float(verdict.get("risk_score") or 0.0),
                reason=str(verdict.get("reason") or "")[:240],
                shadow_decision=verdict.get("shadow_decision"),
                mode_applied=str(verdict.get("mode_applied") or ""),
                source="local_blacklist_report",
            )
    except Exception as err:
        _log("server_unreachable", error=str(err), url=WATCHER_URL, source="local_blacklist_report")


def _extract_cmd(tool_args: dict) -> str:
    for key in (
        "CommandLine",
        "command",
        "cmd",
        "command_line",
        "shell_command",
        "script",
    ):
        val = tool_args.get(key)
        if val:
            return str(val).strip()
    # Nested common shapes
    for nest in ("input", "parameters", "args"):
        nested = tool_args.get(nest)
        if isinstance(nested, dict):
            found = _extract_cmd(nested)
            if found:
                return found
    return ""


def _extract_path(tool_args: dict) -> str:
    for key in (
        "TargetFile",
        "AbsolutePath",
        "path",
        "file",
        "file_path",
        "filePath",
        "target_file",
    ):
        val = tool_args.get(key)
        if val:
            return str(val).strip()
    return ""


def _resolve_post_tool(conversation_id: str, error: str | None = None) -> None:
    """Report PostToolUse completion so Watcher updates blocked events to human-approved."""
    if error:
        _log("posttool_failed", conversation_id=conversation_id[:64], error=str(error)[:120])
        return
    resolve_url = WATCHER_URL.replace("/api/watcher/evaluate", "/api/v1/watcher/gate/resolve")
    req_body = {
        "session_id": f"antigravity-{conversation_id[:8]}" if conversation_id else None,
        "resolution": "human_approved",
        "note": "Tool executed successfully following operator approval in IDE",
    }
    try:
        req = urllib.request.Request(
            resolve_url,
            data=json.dumps(req_body).encode("utf-8"),
            headers=_get_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC):
            pass
        _log("posttool_resolved", session_id=f"antigravity-{conversation_id[:8]}")
    except Exception as err:
        _log("posttool_error", error=str(err))


def _get_latest_antigravity_tool_output(conversation_id: str) -> tuple[str, str]:
    """Fast tail of the latest tool execution output from conversation transcript JSONL."""
    if not conversation_id:
        return "", ""
    transcript_path = (
        Path.home()
        / ".gemini"
        / "antigravity"
        / "brain"
        / conversation_id
        / ".system_generated"
        / "logs"
        / "transcript.jsonl"
    )
    if not transcript_path.exists():
        return "", ""
    try:
        with transcript_path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192), os.SEEK_SET)
            chunk = f.read().decode("utf-8", errors="ignore")
        lines = chunk.splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            content = str(data.get("content") or "").strip()
            if content and not content.startswith("[Message]"):
                return content[:4000], ""
    except Exception:
        pass
    return "", ""


def _report_tool_result_to_watcher(
    conversation_id: str,
    error: str | None = None,
    stdout: str = "",
    stderr: str = "",
) -> None:
    """Report tool execution completion and stdout/stderr immediately to Watcher."""
    result_url = WATCHER_URL.replace("/api/watcher/evaluate", "/api/watcher/result")
    session_id = f"antigravity-{conversation_id[:8]}" if conversation_id else None
    req_body = {
        "session_id": session_id,
        "agent_id": "antigravity",
        "stdout": stdout,
        "stderr": stderr or (error or ""),
        "error": error,
        "exit_code": 1 if error else 0,
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
        _log("tool_result_reported", session_id=session_id)
    except Exception as err:
        _log("tool_result_error", error=str(err))


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        _emit("allow")
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        _log("stdin_json_error", error=str(err))
        _emit("allow", "Watcher gate: invalid stdin JSON (fail-open)")
        return

    conversation_id = str(payload.get("conversationId") or payload.get("conversation_id") or "")

    # PostToolUse handling (either --post flag or payload with stepIdx and no toolCall)
    if "--post" in sys.argv or ("stepIdx" in payload and not payload.get("toolCall")):
        err = payload.get("error")
        stdout, stderr = _get_latest_antigravity_tool_output(conversation_id)
        _report_tool_result_to_watcher(conversation_id, err, stdout, stderr)

        cid = re.sub(r"[^a-zA-Z0-9_-]", "", conversation_id)[:32]
        flag = Path(f"/tmp/openeval_force_ask_{cid}.flag")
        if flag.exists():
            flag.unlink(missing_ok=True)
            _resolve_post_tool(conversation_id, err)
        print("{}")
        return

    tool_call = payload.get("toolCall") or payload.get("tool_call") or {}
    if not isinstance(tool_call, dict):
        tool_call = {}
    tool_name = str(tool_call.get("name") or tool_call.get("toolName") or "unknown")
    tool_args = tool_call.get("args") or tool_call.get("arguments") or {}
    if not isinstance(tool_args, dict):
        tool_args = {"raw": str(tool_args)}

    cmd = _extract_cmd(tool_args)
    path = _extract_path(tool_args)

    _log(
        "pretool",
        tool_name=tool_name,
        cmd=cmd[:200],
        path=path[:200],
        conversation_id=conversation_id[:64],
    )

    # 1) Local deterministic deny (works offline). Still report to Watcher so UI
    # Live Stream / Sessions record the block (previously returned before POST).
    if cmd:
        for pattern, reason, tag in LOCAL_BLACKLIST:
            if re.search(pattern, cmd, re.IGNORECASE):
                _report_to_watcher(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    conversation_id=conversation_id,
                    cmd=cmd,
                )
                _emit(
                    LOCKOUT_DECISION,
                    f"[OPENEVAL INTERACTIVE LOCKOUT - {tag}]: {reason} Execution frozen pending human operator approval.",
                    conversation_id=conversation_id,
                )
                return

    if (
        path
        and ("test_outputs.py" in path or "audit.log" in path or path.endswith(".env"))
        and (path.endswith(".env") or Path(path).exists())
    ):
        _report_to_watcher(
            tool_name=tool_name,
            tool_args=tool_args if tool_args else {"TargetFile": path, "path": path},
            conversation_id=conversation_id,
            cmd=path,
        )
        _emit(
            LOCKOUT_DECISION,
            "[OPENEVAL INTERACTIVE LOCKOUT - RULE_SENSITIVE_PATH]: Modification of secret/held-out path prohibited. Execution frozen pending human operator approval.",
            conversation_id=conversation_id,
        )
        return

    # 2) Query Watcher server
    try:
        req = urllib.request.Request(
            WATCHER_URL,
            data=json.dumps(
                {
                    "agent_id": "antigravity",
                    "tool_name": tool_name,
                    "arguments": tool_args if tool_args else ({"CommandLine": cmd} if cmd else {}),
                    "thought_context": conversation_id,
                    "session_id": f"antigravity-{conversation_id[:8]}" if conversation_id else None,
                }
            ).encode("utf-8"),
            headers=_get_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
            verdict = json.loads(body)
    except Exception as err:
        _log("server_unreachable", error=str(err), url=WATCHER_URL)
        # Fail-open only after local checks already passed
        _emit("allow")
        return

    decision = str(verdict.get("decision", "allow")).lower()
    risk_score = float(verdict.get("risk_score") or 0.0)
    reason = str(verdict.get("reason") or "Violates safety policy")
    shadow = verdict.get("shadow_decision")
    mode = str(verdict.get("mode_applied") or "")
    _log(
        "server_verdict",
        decision=decision,
        risk_score=risk_score,
        reason=reason[:240],
        shadow_decision=shadow,
        mode_applied=mode,
    )

    # Obey server decision only. Do not re-deny on risk_score — observe mode
    # returns decision=allow with a high risk_score and shadow_decision set.
    if decision in ("deny", "reject", "block"):
        _emit(
            LOCKOUT_DECISION,
            f"[OPENEVAL INTERACTIVE LOCKOUT]: {reason} (Risk: {int(risk_score * 100)}%). Execution frozen pending human operator approval.",
            conversation_id=conversation_id,
        )
        return
    if decision in ("escalate", "ask", "warn"):
        _emit(
            "force_ask",
            f"[OPENEVAL ESCALATION]: {reason} (Risk: {int(risk_score * 100)}%)",
            conversation_id=conversation_id,
        )
        return

    _emit("allow")


if __name__ == "__main__":
    main()
