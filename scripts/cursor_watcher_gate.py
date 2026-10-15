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

import contextlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

WATCHER_URL = os.environ.get(
    "OPENEVAL_WATCHER_URL", "http://127.0.0.1:8000/api/v1/watcher/evaluate"
)
OPENEVAL_API_KEY = os.environ.get("OPENEVAL_API_KEY", "").strip()
TIMEOUT_SEC = float(os.environ.get("OPENEVAL_WATCHER_TIMEOUT", "1.5"))
# For beforeShellExecution, default to "ask" so Cursor prompts the user
SHELL_LOCKOUT_PERMISSION = os.environ.get("OPENEVAL_LOCKOUT_PERMISSION", "ask")
LOG_PATH = Path.home() / ".openeval" / "cursor-watcher-gate.log"

# Stateful Circuit Breaker Configuration
CIRCUIT_STATE_FILE = Path(
    os.environ.get(
        "OPENEVAL_CIRCUIT_STATE_FILE",
        str(Path.home() / ".openeval" / "circuit_breaker.json"),
    )
)
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("OPENEVAL_MAX_GATEWAY_FAILURES", "3"))
FAILURE_WINDOW_SEC = float(os.environ.get("OPENEVAL_FAILURE_WINDOW_SEC", "60.0"))


def _record_gateway_failure() -> int:
    """Record a gateway failure timestamp and return active failures within the sliding window."""
    now = time.time()
    failures: list[float] = []
    try:
        if CIRCUIT_STATE_FILE.exists():
            data = json.loads(CIRCUIT_STATE_FILE.read_text(encoding="utf-8"))
            failures = [
                float(ts)
                for ts in data.get("failures", [])
                if (now - float(ts)) < FAILURE_WINDOW_SEC
            ]
    except Exception:
        failures = []

    failures.append(now)
    try:
        CIRCUIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CIRCUIT_STATE_FILE.with_name(f"{CIRCUIT_STATE_FILE.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"failures": failures, "updated_at": now}), encoding="utf-8")
        os.replace(tmp, CIRCUIT_STATE_FILE)
    except Exception:
        pass
    return len(failures)


def _reset_gateway_failures() -> None:
    """Reset circuit breaker failure records upon a successful server evaluation."""
    try:
        if CIRCUIT_STATE_FILE.exists():
            CIRCUIT_STATE_FILE.unlink()
    except Exception:
        pass


def _get_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if OPENEVAL_API_KEY:
        headers["X-OpenEval-Key"] = OPENEVAL_API_KEY
        headers["Authorization"] = f"Bearer {OPENEVAL_API_KEY}"
    return headers


LOCAL_BLACKLIST = [
    (
        r"\brm\s+(?:-[a-zA-Z0-9_-]+\s+)*(?:-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*|-[a-zA-Z]*r[a-zA-Z]*\s+-[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*\s+-[a-zA-Z]*r[a-zA-Z]*|--recursive\s+--force|--force\s+--recursive)\b",
        "Recursive force-delete (rm -rf / separated flags)",
    ),
    (r"\bsudo\b", "sudo privilege escalation"),
    (r"git\s+push\s+.*--(force|force-with-lease)\b", "git force push"),
    (
        r"(?:base64\s+(?:-d|--decode)|openssl\s+enc\s+-d|xxd\s+-r)\s*(?:[^\n|]*)\|\s*(?:ba|z)?sh\b",
        "Obfuscated encoded payload piped to shell interpreter",
    ),
    (r"curl\s+.*\|\s*(ba)?sh", "Remote script piped to shell (curl | bash)"),
    (
        r"\b(?:python[23]?|perl|ruby|node)\s+(?:-e|-c)\s+[\"'].*?(?:shutil\.rmtree|os\.system\s*\(|open\s*\([^)]*(?:\.env|id_rsa|credentials)|\.unlink|\.rmdir|\.rmdirSync|\.rmSync|fs\.).*?[\"']",
        "Inline script interpreter destructive wipe or secret read",
    ),
    (
        r"\b(?:curl|wget)\s+[^\n]*(?:-d\s+@|-F\s+[^\s=]*=@|--post-file=)[^\s\"']*(?:\.env|\.secrets|id_rsa|id_ed25519|\.pem|/\.aws/credentials|/etc/shadow)",
        "Credential file exfiltration attempted via network upload",
    ),
    (
        r"(?:cat|less|more|head|tail|bat|grep|awk|sed|strings|xxd|od)\s+[^\n]*(?:\.env\b|\.envrc\b|\.secrets\b|id_rsa|id_ed25519|\.pem\b|/etc/shadow|/\.aws/credentials)",
        "Reading secret/credential files via shell",
    ),
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


NEGATIVE_CONSTRAINT_PATTERNS = [
    re.compile(
        r"(?:do\s+not|don't|never|must\s+not|cannot|should\s+not)\s+((?:(?!\.\s|\.$)[a-zA-Z0-9_\-./*`:,;\s'\"]){2,80})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:under\s+no\s+circumstances(?:\s+should\s+you|\s+shall\s+you|\s+can\s+you|\s+do)?)\s+((?:(?!\.\s|\.$)[a-zA-Z0-9_\-./*`:,;\s'\"]){2,80})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:prohibited|forbidden):\s*((?:(?!\.\s|\.$)[^\n;])+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:strictly|explicitly)\s+(?:prohibited|forbidden)(?:\s+to)?\s+((?:(?!\.\s|\.$)[^\n;])+)",
        re.IGNORECASE,
    ),
]


def _extract_negative_constraints_offline(context: str) -> list[str]:
    constraints: list[str] = []
    seen: set[str] = set()
    if not context:
        return constraints
    for pat in NEGATIVE_CONSTRAINT_PATTERNS:
        for match in pat.finditer(context):
            raw = match.group(1).strip(" '\"`.,;:!?").lower()
            raw = re.sub(
                r"\b(under\s+any\s+circumstances|at\s+all|please|again|ever)\b",
                "",
                raw,
            ).strip(" '\"`.,;:!?")
            if raw and len(raw) >= 2 and raw not in seen:
                seen.add(raw)
                constraints.append(raw)
    return constraints


_GENERIC_TARGETS: set[str] = {
    "file",
    "files",
    "directory",
    "directories",
    "folder",
    "folders",
    "code",
    "data",
    "item",
    "items",
}
_DELETE_ACTIONS: set[str] = {
    "delete",
    "remove",
    "rm",
    "wipe",
    "unlink",
    "drop",
    "truncate",
    "destroy",
    "purge",
}
_MODIFY_ACTIONS: set[str] = {
    "modify",
    "edit",
    "change",
    "write",
    "update",
    "replace",
    "touch",
    "overwrite",
    "patch",
    "create",
}
_PUSH_ACTIONS: set[str] = {"push"}
_ACTION_WORDS: set[str] = (
    _DELETE_ACTIONS
    | _MODIFY_ACTIONS
    | _PUSH_ACTIONS
    | {"run", "execute", "use", "make", "do", "perform", "access"}
)


_TARGET_SYNONYMS: dict[str, list[str]] = {
    "database": ["db"],
    "databases": ["db"],
    "db": ["database", "databases"],
    "repository": ["repo"],
    "repositories": ["repo"],
    "repo": ["repository", "repositories"],
    "configuration": ["config"],
    "configurations": ["config"],
    "config": ["configuration", "configurations"],
    "environment": ["env"],
    "environments": ["env"],
    "env": ["environment", "environments"],
    "credentials": ["creds", "cred"],
    "creds": ["credentials", "credential"],
}


def _get_target_stems(target: str) -> list[str]:
    """Generate candidate singular/stem forms for a target word."""
    stems = [target]
    if target in _TARGET_SYNONYMS:
        stems.extend(_TARGET_SYNONYMS[target])
    if len(target) > 3:
        if target.endswith("ies") and len(target) > 4:
            stems.append(target[:-3] + "y")
            stems.append(target[:-3])
        elif target.endswith("es"):
            if any(target[:-2].endswith(end) for end in ("ch", "sh", "x", "ss", "z")):
                stems.append(target[:-2])
            else:
                stems.append(target[:-1])
        elif target.endswith("s") and not target.endswith("ss"):
            stems.append(target[:-1])
    return stems


def _check_negative_constraint_offline(
    constraints: list[str], tool_name: str, tool_input: str, diff: str = ""
) -> tuple[bool, str]:
    if not constraints or not tool_input:
        return False, ""
    input_lower = tool_input.lower()
    tool_lower = tool_name.lower()
    diff_lower = diff.lower()[:2000]
    combined_target = f"{input_lower} {diff_lower}".strip()
    full_combined = f"{tool_lower} {combined_target}".strip()

    is_delete_tool = any(k in tool_lower for k in ("delete", "remove", "unlink", "rm")) or any(
        re.search(rf"\b{cmd}\b", input_lower) for cmd in ("rm", "unlink", "drop", "truncate")
    )

    stop_words = {
        "the",
        "a",
        "an",
        "to",
        "any",
        "all",
        "our",
        "this",
        "that",
        "it",
        "of",
        "in",
        "for",
        "on",
        "with",
        "from",
        "and",
        "or",
        "nor",
        "either",
    }
    for constraint in constraints:
        exc_match = re.split(r"\b(?:except|unless)\b", constraint, maxsplit=1, flags=re.IGNORECASE)
        prohibition_part = exc_match[0].strip()
        exception_part = exc_match[1].strip() if len(exc_match) > 1 else ""
        if exception_part:
            exc_tokens = [w.strip("`'\"*") for w in re.split(r"[\s_,]+", exception_part) if w]
            exc_core = [w for w in exc_tokens if w and w not in stop_words]
            if exc_core and any(t in full_combined for t in exc_core):
                continue
        sub_prohibitions = re.split(r"\b(?:or|nor|and)\b|,", prohibition_part, flags=re.IGNORECASE)
        for sub in sub_prohibitions:
            sub = sub.strip()
            if not sub:
                continue
            raw_tokens = [w.strip("`'\"*") for w in re.split(r"[\s_]+", sub) if w]
            tokens = [w for w in raw_tokens if w and w not in stop_words]
            actions_in_sub = [w for w in tokens if w in _ACTION_WORDS]
            core_targets = [w for w in tokens if w not in _ACTION_WORDS]

            # Separate specific targets from generic targets (e.g. 'files', 'data')
            specific_targets = [ct for ct in core_targets if ct not in _GENERIC_TARGETS]

            matched = False
            if specific_targets:
                all_found = True
                for ct in specific_targets:
                    stems = _get_target_stems(ct)
                    if not any(stem in combined_target or stem in full_combined for stem in stems):
                        all_found = False
                        break
                matched = all_found
            elif core_targets:
                # Only generic targets exist (e.g. 'delete files')
                is_delete_prohibition = any(a in _DELETE_ACTIONS for a in actions_in_sub)
                if is_delete_prohibition:
                    matched = is_delete_tool
                else:
                    matched = any(
                        stem in combined_target
                        for ct in core_targets
                        for stem in _get_target_stems(ct)
                    )
            elif tokens:
                matched = any(t in full_combined for t in tokens)
            if matched:
                return True, constraint
    return False, ""


def _log(event: str, **fields: object) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "event": event, **fields}) + "\n")
    except Exception:
        pass


SUBAGENT_REGISTRY_PATH = Path(
    os.environ.get(
        "OPENEVAL_SUBAGENT_REGISTRY",
        str(Path.home() / ".openeval" / "subagent_registry.json"),
    )
)


def _detect_subagent_provenance(
    payload: dict,
    tool_name: str,
    tool_input: object,
    conversation_id: str,
) -> dict[str, Any]:
    """Multi-signal detection of delegated subagents for Cursor IDE (Apollo Codex safeguard)."""
    env_depth_raw = os.environ.get("CURSOR_DELEGATION_DEPTH") or os.environ.get("DELEGATION_DEPTH")
    env_depth = int(env_depth_raw) if env_depth_raw and env_depth_raw.isdigit() else 1

    # Signal 1: Environment variables
    env_subagent = os.environ.get("CURSOR_SUBAGENT_ID") or os.environ.get("ANTIGRAVITY_SUBAGENT_ID")
    if env_subagent:
        return {
            "call_origin": "subagent",
            "parent_session_id": env_subagent,
            "delegation_depth": env_depth,
            "delegated_by": "env:CURSOR_SUBAGENT_ID",
        }
    role = os.environ.get("AGENT_ROLE", "").lower()
    if any(k in role for k in ("subagent", "delegate", "worker")):
        return {
            "call_origin": "subagent",
            "parent_session_id": None,
            "delegation_depth": env_depth,
            "delegated_by": f"env:AGENT_ROLE={role}",
        }

    # Signal 2: Payload fields
    call_origin = str(payload.get("call_origin") or payload.get("callOrigin") or "").lower()
    if call_origin == "subagent":
        depth = int(payload.get("delegation_depth") or payload.get("delegationDepth") or env_depth)
        delegated_by = str(payload.get("delegated_by") or payload.get("delegatedBy") or "payload")
        parent_sid = payload.get("parent_session_id") or payload.get("parentSessionId")
        return {
            "call_origin": "subagent",
            "parent_session_id": str(parent_sid) if parent_sid else None,
            "delegation_depth": depth,
            "delegated_by": delegated_by,
        }

    if payload.get("is_subagent") or payload.get("isSubagent"):
        depth = int(payload.get("delegation_depth") or payload.get("delegationDepth") or env_depth)
        return {
            "call_origin": "subagent",
            "parent_session_id": None,
            "delegation_depth": depth,
            "delegated_by": "payload:is_subagent",
        }

    parent_sid = (
        payload.get("parent_session_id")
        or payload.get("parentSessionId")
        or payload.get("parent_conversation_id")
    )
    if parent_sid:
        return {
            "call_origin": "subagent",
            "parent_session_id": str(parent_sid),
            "delegation_depth": 1,
            "delegated_by": f"parent_session:{parent_sid}",
        }

    # Signal 3: Registry lookup
    if conversation_id and SUBAGENT_REGISTRY_PATH.exists():
        try:
            data = json.loads(SUBAGENT_REGISTRY_PATH.read_text(encoding="utf-8"))
            registered = data.get("subagents", {})
            if conversation_id in registered:
                info = registered[conversation_id]
                return {
                    "call_origin": "subagent",
                    "parent_session_id": info.get("parent"),
                    "delegation_depth": info.get("depth", 1),
                    "delegated_by": info.get("delegated_by", "registry"),
                }
        except Exception:
            pass

    return {
        "call_origin": "user_direct",
        "parent_session_id": None,
        "delegation_depth": 0,
        "delegated_by": None,
    }


def _track_delegation_tool(tool_name: str, tool_input: object, parent_conv_id: str) -> None:
    """Register child subagents when delegation tools are invoked in Cursor with concurrency lock."""
    delegation_tools = {
        "invoke_subagent",
        "define_subagent",
        "send_message",
        "Agent",
        "spawn_agent",
        "TaskCreate",
        "subagent",
    }
    if tool_name not in delegation_tools or not isinstance(tool_input, dict):
        return

    target_id = (
        tool_input.get("Recipient")
        or tool_input.get("agent_id")
        or tool_input.get("conversationId")
        or tool_input.get("subagent_id")
        or tool_input.get("TaskId")
        or tool_input.get("name")
    )
    if not target_id:
        return

    target_str = str(target_id).strip()
    try:
        SUBAGENT_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_file = SUBAGENT_REGISTRY_PATH.with_suffix(".lock")
        with open(lock_file, "w", encoding="utf-8") as lf:
            try:
                import fcntl

                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
            current_data = {}
            if SUBAGENT_REGISTRY_PATH.exists():
                with contextlib.suppress(Exception):
                    current_data = json.loads(SUBAGENT_REGISTRY_PATH.read_text(encoding="utf-8"))
            subagents = current_data.get("subagents", {})
            parent_info = subagents.get(parent_conv_id, {})
            parent_depth = int(parent_info.get("depth", 0)) if parent_info else 0
            subagents[target_str] = {
                "parent": parent_conv_id,
                "depth": parent_depth + 1,
                "delegated_by": tool_name,
                "registered_at": time.time(),
            }
            current_data["subagents"] = subagents
            tmp = SUBAGENT_REGISTRY_PATH.with_name(
                f"{SUBAGENT_REGISTRY_PATH.name}.{os.getpid()}.tmp"
            )
            tmp.write_text(json.dumps(current_data, indent=2), encoding="utf-8")
            os.replace(tmp, SUBAGENT_REGISTRY_PATH)
            try:
                import fcntl

                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
    except Exception:
        pass


def _format_consent_box(
    decision: str,
    reason: str,
    disclosure: dict | None = None,
    tool_name: str = "",
    cmd: str = "",
    path: str = "",
) -> str:
    """Format structured visual informed consent box matching Apollo specification."""
    if disclosure and isinstance(disclosure, dict):
        targets = disclosure.get("target_resources") or (
            [path or cmd or tool_name] if (path or cmd or tool_name) else []
        )
        destination = disclosure.get("destination")
        disclosed = disclosure.get("disclosed_data_types") or []
        blast = disclosure.get("blast_radius") or "local_reversible"
        reversibility = disclosure.get("reversibility_summary") or "Reversible: local change"
        disc_ctx = disclosure.get("discovered_context")
    else:
        targets = [p for p in [path, cmd[:60] if cmd else None, tool_name] if p]
        destination = None
        url_m = re.search(r"(https?://[^\s\"'>]+)", cmd)
        if url_m:
            destination = url_m.group(1)
        disclosed = []
        lower_all = (cmd + " " + path).lower()
        if any(k in lower_all for k in (".env", "secret", "credential")):
            disclosed.append("environment_variables")
        if any(k in lower_all for k in ("id_rsa", "id_ed25519", ".ssh")):
            disclosed.append("ssh_keys")
        blast = (
            "external_shared"
            if destination
            else ("local_destructive" if "rm " in cmd else "local_reversible")
        )
        reversibility = (
            "Non-reversible: data sent outside local host"
            if destination
            else (
                "Partially reversible: file deletion"
                if "rm " in cmd
                else "Reversible: local change"
            )
        )
        disc_ctx = None

    radius_desc = {
        "external_shared": "external_shared (Irreversible external disclosure)",
        "critical_unrecoverable": "critical_unrecoverable (Catastrophic unrecoverable damage)",
        "local_destructive": "local_destructive (Destructive local modification)",
        "local_reversible": "local_reversible (Reversible local modification)",
    }.get(blast, blast)

    lines = [
        "================ [OPENEVAL INFORMED CONSENT REQUIRED] ================",
        f"Decision: {decision.upper()} (Pending Operator Approval)",
        f"Reason: {reason}",
        f"🎯 Target Resources: {targets}",
    ]
    if destination:
        lines.append(f"🌐 Destination: {destination}")
    if disclosed:
        lines.append(f"⚠️ Disclosed Data: {disclosed}")
    lines.append(f"💥 Blast Radius: {radius_desc}")
    lines.append(f"🔒 Reversibility: {reversibility}")
    if disc_ctx:
        lines.append(f"🔍 Discovered Context: {disc_ctx}")
    lines.append("========================================================================")
    return "\n".join(lines)


def _emit(
    permission: str,
    message: str = "",
    consent_box: str = "",
    disclosure: dict | None = None,
) -> None:
    if consent_box:
        sys.stderr.write(f"\n{consent_box}\n\n")
        sys.stderr.flush()
        display_message = (
            f"{message}\n\n{consent_box}" if message and message not in consent_box else consent_box
        )
    else:
        display_message = message

    out = {
        "permission": permission,
        "continue": permission == "allow",
        "user_message": display_message,
        "agent_message": display_message,
    }
    if disclosure:
        out["consent_disclosure"] = disclosure

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
    provenance: dict | None = None,
    thought_context: str = "",
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
                    "thought_context": thought_context or conversation_id,
                    "provenance": provenance
                    or {"call_origin": "user_direct", "delegation_depth": 0},
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


def _handle_shell_execution(
    cmd: str,
    conversation_id: str,
    provenance: dict | None = None,
    thought_context: str = "",
) -> None:
    """Handle beforeShellExecution hook with local blacklist + server check."""
    if not cmd:
        _emit("allow")
        return

    _log("before_shell", cmd=cmd[:200], conversation_id=conversation_id[:64], provenance=provenance)

    # 1. Local offline blacklist check
    for pattern, reason in LOCAL_BLACKLIST:
        if re.search(pattern, cmd, re.IGNORECASE):
            _report_to_watcher(
                tool_name="Shell",
                args={"command": cmd, "CommandLine": cmd},
                conversation_id=conversation_id,
                cmd=cmd,
                provenance=provenance,
                thought_context=thought_context,
            )
            # In beforeShellExecution, "ask" prompts the operator in Cursor IDE
            prompt_msg = f"[OPENEVAL SAFETY GATE]: {reason} detected (`{cmd[:120]}`). Do you want to proceed with this command?"
            consent_box = _format_consent_box(
                decision=SHELL_LOCKOUT_PERMISSION,
                reason=reason,
                tool_name="Shell",
                cmd=cmd,
            )
            _emit(SHELL_LOCKOUT_PERMISSION, prompt_msg, consent_box=consent_box)
            return

    is_sub = bool(
        (provenance and provenance.get("call_origin") == "subagent")
        or (provenance and provenance.get("delegation_depth", 0) > 0)
    )
    if is_sub and any(
        re.search(p, cmd, re.IGNORECASE)
        for p in (r"\.env\b", r"\.secrets\b", r"id_rsa", r"id_ed25519", r"sudo\b", r"/etc/shadow")
    ):
        prompt_msg = f"[OPENEVAL SAFETY GATE]: [SUBAGENT DELEGATE RESTRICTION]: Subagents strictly prohibited from credential access or privilege escalation (`{cmd[:120]}`)."
        consent_box = _format_consent_box(
            decision=SHELL_LOCKOUT_PERMISSION,
            reason="[SUBAGENT DELEGATE RESTRICTION] Credential access or privilege escalation prohibited.",
            tool_name="Shell",
            cmd=cmd,
        )
        _emit(SHELL_LOCKOUT_PERMISSION, prompt_msg, consent_box=consent_box)
        return

    # 1b. Local offline negative constraint check (Reversibility ≠ Authorization)
    neg_constraints = _extract_negative_constraints_offline(thought_context)
    is_neg_viol, neg_desc = _check_negative_constraint_offline(neg_constraints, "Shell", cmd)
    if is_neg_viol:
        _report_to_watcher(
            tool_name="Shell",
            args={"command": cmd, "CommandLine": cmd},
            conversation_id=conversation_id,
            cmd=cmd,
            provenance=provenance,
            thought_context=thought_context,
        )
        prompt_msg = f"[OPENEVAL SAFETY GATE]: Reversibility ≠ Authorization: Explicit negative constraint '{neg_desc}' violated (`{cmd[:120]}`). Do you want to proceed with this command?"
        consent_box = _format_consent_box(
            decision=SHELL_LOCKOUT_PERMISSION,
            reason=f"[REVERSIBILITY_NOT_AUTH] Explicit negative constraint '{neg_desc}' violated.",
            tool_name="Shell",
            cmd=cmd,
        )
        _emit(SHELL_LOCKOUT_PERMISSION, prompt_msg, consent_box=consent_box)
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
                    "thought_context": thought_context or conversation_id,
                    "provenance": provenance
                    or {"call_origin": "user_direct", "delegation_depth": 0},
                }
            ).encode("utf-8"),
            headers=_get_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            verdict = json.loads(resp.read().decode("utf-8"))
        # Server query succeeded: reset circuit breaker failure records
        _reset_gateway_failures()
    except Exception as err:
        if isinstance(err, urllib.error.HTTPError) and err.code == 422:
            try:
                err_body = json.loads(err.read().decode("utf-8"))
                unknown_keys = [
                    str(d["loc"][-1])
                    for d in err_body.get("detail", [])
                    if isinstance(d, dict) and d.get("type") == "extra_forbidden" and "loc" in d
                ]
            except Exception:
                unknown_keys = []
            keys_str = ", ".join(unknown_keys) if unknown_keys else "unknown"
            drift_msg = f"payload schema drift: unexpected fields [{keys_str}] rejected by Watcher gate"
            sys.stderr.write(f"[OPENEVAL WARNING] {drift_msg}\n")
            _log("payload_schema_drift", unknown_keys=unknown_keys, error=str(err))
            prompt_msg = f"[OPENEVAL SCHEMA DRIFT]: {drift_msg}. Execution frozen pending operator review."
            consent_box = _format_consent_box(
                decision=SHELL_LOCKOUT_PERMISSION,
                reason="[SCHEMA_DRIFT] Watcher gate payload schema drift detected.",
                tool_name="Shell",
                cmd=cmd,
            )
            _emit(SHELL_LOCKOUT_PERMISSION, prompt_msg, consent_box=consent_box)
            return
        _log("server_unreachable", error=str(err), cmd=cmd[:120])
        recent_failures = _record_gateway_failure()
        if recent_failures >= MAX_CONSECUTIVE_FAILURES:
            prompt_msg = (
                f"[OPENEVAL CIRCUIT BREAKER TRIPPED]: Watcher Gateway unreachable "
                f"({recent_failures} consecutive failures in {int(FAILURE_WINDOW_SEC)}s). "
                f"Execution frozen pending operator confirmation for: `{cmd[:120]}`."
            )
            consent_box = _format_consent_box(
                decision=SHELL_LOCKOUT_PERMISSION,
                reason="[CIRCUIT_BREAKER_TRIPPED] Gateway unavailable; safety lockout engaged.",
                tool_name="Shell",
                cmd=cmd,
            )
            _emit(SHELL_LOCKOUT_PERMISSION, prompt_msg, consent_box=consent_box)
            return
        _emit("allow", "Watcher gate: gateway timeout/unreachable (transient fail-open)")
        return

    decision = str(verdict.get("decision", "allow")).lower()
    risk = float(verdict.get("risk_score") or 0.0)
    reason = str(verdict.get("reason") or "Safety policy check")
    disclosure = verdict.get("consent_disclosure")
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
        consent_box = _format_consent_box(
            decision=SHELL_LOCKOUT_PERMISSION,
            reason=reason,
            disclosure=disclosure,
            tool_name="Shell",
            cmd=cmd,
        )
        _emit(SHELL_LOCKOUT_PERMISSION, prompt_msg, consent_box=consent_box, disclosure=disclosure)
        return

    _emit("allow")


def _handle_pre_tool_use(
    tool_name: str,
    tool_input: object,
    conversation_id: str,
    provenance: dict | None = None,
    thought_context: str = "",
) -> None:
    """Handle preToolUse hook. Note: Cursor preToolUse ONLY supports 'allow' or 'deny'."""
    lower_tool = tool_name.lower()

    _log(
        "pre_tool_use",
        tool_name=tool_name,
        conversation_id=conversation_id[:64],
        provenance=provenance,
    )

    # 1. Shell commands via tool call: delegate to beforeShellExecution hook
    if lower_tool in ("shell", "terminal", "bash", "sh", "exec", "powershell"):
        _emit("allow")
        return

    # 1b. Offline negative constraint check (Reversibility ≠ Authorization)
    path = _extract_path(tool_input)
    diff = ""
    if isinstance(tool_input, dict):
        diff = str(
            tool_input.get("diff")
            or tool_input.get("contents")
            or tool_input.get("content")
            or tool_input.get("CodeContent")
            or tool_input.get("ReplacementContent")
            or ""
        )
    neg_constraints = _extract_negative_constraints_offline(thought_context)
    target_text = path or str(tool_input)
    is_neg_viol, neg_desc = _check_negative_constraint_offline(
        neg_constraints, tool_name, target_text, diff=diff
    )
    if is_neg_viol:
        _report_to_watcher(
            tool_name=tool_name,
            args={"path": path} if path else {},
            conversation_id=conversation_id,
            cmd="",
            provenance=provenance,
            thought_context=thought_context,
        )
        msg = f"[OPENEVAL SAFETY GATE]: Reversibility ≠ Authorization: Explicit negative constraint '{neg_desc}' violated ({tool_name} on {target_text[:80]})."
        consent_box = _format_consent_box(
            decision="deny",
            reason=f"[REVERSIBILITY_NOT_AUTH] Explicit negative constraint '{neg_desc}' violated.",
            tool_name=tool_name,
            path=path,
        )
        _emit("deny", msg, consent_box=consent_box)
        return

    # Subagent credential restriction (Apollo Codex safeguard)
    is_sub = bool(
        (provenance and provenance.get("call_origin") == "subagent")
        or (provenance and provenance.get("delegation_depth", 0) > 0)
    )
    if (
        is_sub
        and path
        and re.search(
            r"(^|/)(id_rsa|id_ed25519|id_ecdsa|/etc/shadow|\.aws/credentials|\.secrets|\.env)",
            path,
        )
    ):
        msg = f"[OPENEVAL SAFETY GATE]: [SUBAGENT DELEGATE RESTRICTION]: Subagents strictly prohibited from credential access ('{path}')."
        consent_box = _format_consent_box(
            decision="deny",
            reason=msg,
            tool_name=tool_name,
            path=path,
        )
        _emit("deny", msg, consent_box=consent_box)
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
        if path and re.search(r"(^|/)(id_rsa|id_ed25519|id_ecdsa|/etc/shadow)", path):
            msg = f"[OPENEVAL SAFETY GATE]: Reading protected private credential file ('{path}') is blocked."
            consent_box = _format_consent_box(
                decision="deny",
                reason=msg,
                tool_name=tool_name,
                path=path,
            )
            _emit("deny", msg, consent_box=consent_box)
            return
        _emit("allow")
        return

    # 3. File modification tools (Write, Edit, ApplyPatch, etc.)
    is_sensitive, sensitive_reason = _is_sensitive_path(path)
    if is_sensitive:
        _report_to_watcher(
            tool_name=tool_name,
            args={"path": path},
            conversation_id=conversation_id,
            cmd="",
            provenance=provenance,
            thought_context=thought_context,
        )
        # In preToolUse, Cursor only supports 'allow' or 'deny' (not 'ask')
        msg = f"[OPENEVAL SAFETY GATE]: Modifying protected path ('{path}') is blocked ({sensitive_reason})."
        consent_box = _format_consent_box(
            decision="deny",
            reason=msg,
            tool_name=tool_name,
            path=path,
        )
        _emit("deny", msg, consent_box=consent_box)
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
                        "thought_context": thought_context or conversation_id,
                        "provenance": provenance
                        or {"call_origin": "user_direct", "delegation_depth": 0},
                    }
                ).encode("utf-8"),
                headers=_get_headers(),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
                verdict = json.loads(resp.read().decode("utf-8"))
            _reset_gateway_failures()
            decision = str(verdict.get("decision", "allow")).lower()
            risk = float(verdict.get("risk_score") or 0.0)
            reason = str(verdict.get("reason") or "Safety policy violation")
            if decision in ("deny", "reject", "block"):
                disclosure = verdict.get("consent_disclosure")
                msg = f"[OPENEVAL SAFETY GATE]: {reason} (Risk: {int(risk * 100)}%)."
                consent_box = _format_consent_box(
                    decision="deny",
                    reason=reason,
                    disclosure=disclosure,
                    tool_name=tool_name,
                    path=path,
                )
                _emit("deny", msg, consent_box=consent_box, disclosure=disclosure)
                return
        except Exception as err:
            _log("server_unreachable", error=str(err), path=path)
            recent_failures = _record_gateway_failure()
            if recent_failures >= MAX_CONSECUTIVE_FAILURES:
                msg = (
                    f"[OPENEVAL CIRCUIT BREAKER TRIPPED]: Watcher Gateway unreachable "
                    f"({recent_failures} consecutive failures in {int(FAILURE_WINDOW_SEC)}s). "
                    f"Tool execution blocked pending operator approval for path: `{path}`."
                )
                consent_box = _format_consent_box(
                    decision="deny",
                    reason="[CIRCUIT_BREAKER_TRIPPED] Gateway unavailable",
                    tool_name=tool_name,
                    path=path,
                )
                _emit("deny", msg, consent_box=consent_box)
                return
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

    # Provenance detection & delegation tracking (Apollo Codex auto-review)
    provenance = _detect_subagent_provenance(payload, tool_name, tool_input, conversation_id)
    _track_delegation_tool(tool_name, tool_input, conversation_id)
    thought_context = str(
        payload.get("thought_context")
        or payload.get("thoughtContext")
        or payload.get("thought")
        or payload.get("thinking")
        or payload.get("userPrompt")
        or payload.get("user_prompt")
        or conversation_id
    )

    # Route based on hook event
    if hook_event == "beforeShellExecution" or (cmd and hook_event != "preToolUse"):
        _handle_shell_execution(cmd, conversation_id, provenance, thought_context)
    elif hook_event == "preToolUse":
        if not tool_name:
            tool_name = "Shell" if cmd else "unknown"
        _handle_pre_tool_use(tool_name, tool_input, conversation_id, provenance, thought_context)
    else:
        # Unknown hook event: fail-open
        _emit("allow")


if __name__ == "__main__":
    main()
