#!/usr/bin/env python3
"""Antigravity PreToolUse safety gate → OpenEval Watcher `/api/v1/watcher/evaluate`.

Contract (Antigravity hooks.md):
  stdin:  { "toolCall": { "name", "args" }, "conversationId", ... }
  stdout: { "decision": "allow"|"deny"|"ask"|"force_ask", "reason": "..." }

Fail-open only when the Watcher server is unreachable/times out after local
deterministic checks. Local blacklist still blocks high-severity commands offline.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

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
        t = re.sub(
            r"\b(?:sk-proj-[a-zA-Z0-9_-]{20,}|sk-[a-zA-Z0-9]{20,})\b",
            "[REDACTED:OPENAI_API_KEY]",
            t,
        )
        t = re.sub(r"\b(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}\b", "[REDACTED:GITHUB_TOKEN]", t)
        t = re.sub(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "[REDACTED:AWS_ACCESS_KEY]", t)
        return t

    def redact_payload(obj: Any) -> Any:
        if isinstance(obj, str):
            return redact_text(obj)
        if isinstance(obj, dict):
            return {k: redact_payload(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [redact_payload(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(redact_payload(v) for v in obj)
        return obj


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        if val is None:
            return default
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
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
LOCKOUT_DECISION = os.environ.get("OPENEVAL_LOCKOUT_DECISION", "force_ask")
FAIL_MODE = (
    os.environ.get(
        "OPENEVAL_GATE_FAIL_MODE",
        os.environ.get("OPENEVAL_FAIL_MODE", os.environ.get("OPENEVAL_ON_FAILURE", "open")),
    )
    .strip()
    .lower()
)

# Stateful Circuit Breaker Configuration
CIRCUIT_STATE_FILE = Path(
    os.environ.get(
        "OPENEVAL_CIRCUIT_STATE_FILE",
        str(Path.home() / ".openeval" / "circuit_breaker.json"),
    )
)
MAX_CONSECUTIVE_FAILURES = _safe_int(os.environ.get("OPENEVAL_MAX_GATEWAY_FAILURES"), 3)
FAILURE_WINDOW_SEC = _safe_float(os.environ.get("OPENEVAL_FAILURE_WINDOW_SEC"), 60.0)


def _record_gateway_failure() -> int:
    """Record a gateway failure timestamp and return active failures within the sliding window."""
    now = time.time()
    failures: list[float] = []
    lock_file = CIRCUIT_STATE_FILE.with_suffix(".lock")
    try:
        CIRCUIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "w", encoding="utf-8") as lf:
            try:
                import fcntl

                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
            try:
                if CIRCUIT_STATE_FILE.exists():
                    data = json.loads(CIRCUIT_STATE_FILE.read_text(encoding="utf-8"))
                    failures = [
                        _safe_float(ts)
                        for ts in data.get("failures", [])
                        if (now - _safe_float(ts)) < FAILURE_WINDOW_SEC
                    ]
            except Exception:
                failures = []

            failures.append(now)
            try:
                tmp = CIRCUIT_STATE_FILE.with_name(f"{CIRCUIT_STATE_FILE.name}.{os.getpid()}.tmp")
                tmp.write_text(
                    json.dumps({"failures": failures, "updated_at": now}), encoding="utf-8"
                )
                os.replace(tmp, CIRCUIT_STATE_FILE)
            except Exception:
                pass
            try:
                import fcntl

                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
    except Exception:
        failures.append(now)
    return len(failures)


def _reset_gateway_failures() -> None:
    """Reset circuit breaker failure records upon a successful server evaluation."""
    lock_file = CIRCUIT_STATE_FILE.with_suffix(".lock")
    try:
        if CIRCUIT_STATE_FILE.exists() or lock_file.exists():
            CIRCUIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_file, "w", encoding="utf-8") as lf:
                try:
                    import fcntl

                    fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                except Exception:
                    pass
                if CIRCUIT_STATE_FILE.exists():
                    with contextlib.suppress(OSError):
                        CIRCUIT_STATE_FILE.unlink()
                try:
                    import fcntl

                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
    except Exception:
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


LOG_PATH = Path(
    os.environ.get(
        "OPENEVAL_WATCHER_GATE_LOG",
        str(Path.home() / ".openeval" / "watcher-gate.log"),
    )
)


def _rotate_log_file_if_needed(log_path: Path) -> None:
    try:
        if log_path.exists() and log_path.stat().st_size >= MAX_LOG_BYTES:
            rotated = log_path.with_name(f"{log_path.name}.1")
            if rotated.exists():
                with contextlib.suppress(OSError):
                    rotated.unlink()
            os.replace(log_path, rotated)
    except Exception:
        pass


# Instant offline deny patterns (subset of Watcher command rules + high-severity)
LOCAL_BLACKLIST: list[tuple[str, str, str]] = [
    # 1. Interpreter Subprocess Reflection (Python/Ruby/Perl/Node inline script executing destructive wipe or secret read)
    (
        r"\b(?:python[23]?|perl|ruby|node)\s+(?:-e|-c)\s+[\"'].*?(?:shutil\.rmtree|os\.system\s*\(|open\s*\([^)]*(?:\.env|id_rsa|credentials)|\.unlink|\.rmdir|\.rmdirSync|\.rmSync|fs\.).*?[\"']",
        "Destructive filesystem or secret exfiltration via inline script interpreter.",
        "RULE_SUBPROCESS_REFLECTION",
    ),
    # 2. Destructive Filesystem Wipe (handles combined flags -rf and separated flags -r -f / --recursive --force)
    (
        r"\brm\s+(?:-[a-zA-Z0-9_-]+\s+)*(?:-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*|-[a-zA-Z]*r[a-zA-Z]*\s+-[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*\s+-[a-zA-Z]*r[a-zA-Z]*|--recursive\s+--force|--force\s+--recursive)\b",
        "Recursive force-delete (rm -rf / separated flags) attempted.",
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
    # 2. Obfuscated / Encoded Shell Execution (base64/openssl/xxd pipe into shell)
    (
        r"(?:base64\s+(?:-d|--decode)|openssl\s+enc\s+-d|xxd\s+-r)\s*(?:[^\n|]*)\|\s*(?:ba|z)?sh\b",
        "Obfuscated encoded payload piped to shell interpreter.",
        "RULE_OBFUSCATED_EXECUTION",
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
    # 4. Credential Exfiltration via Network Tools
    (
        r"\b(?:curl|wget)\s+[^\n]*(?:-d\s+@|-F\s+[^\s=]*=@|--post-file=)[^\s\"']*(?:\.env|\.secrets|id_rsa|id_ed25519|\.pem|/\.aws/credentials|/etc/shadow)",
        "Credential file exfiltration attempted via network upload.",
        "RULE_CREDENTIAL_EXFILTRATION",
    ),
    # 5. Expanded Secret Reading Tools (cat, head, tail, grep, awk, sed, strings, xxd)
    (
        r"(?:cat|less|more|head|tail|bat|grep|awk|sed|strings|xxd|od)\s+[^\n]*(?:\.env\b|\.envrc\b|\.secrets\b|id_rsa|id_ed25519|\.pem\b|/etc/shadow|/\.aws/credentials)",
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
        _rotate_log_file_if_needed(LOG_PATH)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        redacted = {k: redact_payload(v) for k, v in fields.items()}
        row = {"ts": time.time(), "request_id": REQUEST_ID, "event": event, **redacted}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _force_ask_flag(conversation_id: str) -> Path | None:
    cid = re.sub(r"[^a-zA-Z0-9_-]", "", conversation_id)[:32]
    if not cid:
        return None
    return Path(f"/tmp/openeval_force_ask_{cid}.flag")


def _mark_force_ask(conversation_id: str) -> None:
    """Atomically publish the lockout flag (replace, not a check-then-write race)."""
    flag = _force_ask_flag(conversation_id)
    if flag is None:
        return
    tmp = flag.with_name(f"{flag.name}.{os.getpid()}.tmp")
    tmp.write_text(str(time.time()), encoding="utf-8")
    os.replace(tmp, flag)


def _consume_force_ask(conversation_id: str) -> bool:
    """Unlink the lockout flag. unlink is atomic; missing file means nothing to resolve."""
    flag = _force_ask_flag(conversation_id)
    if flag is None:
        return False
    try:
        flag.unlink()
    except FileNotFoundError:
        return False
    return True


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


SUBAGENT_REGISTRY_PATH = Path(
    os.environ.get(
        "OPENEVAL_SUBAGENT_REGISTRY",
        str(Path.home() / ".openeval" / "subagent_registry.json"),
    )
)


def _detect_subagent_provenance(
    payload: dict,
    tool_name: str,
    tool_args: dict,
    conversation_id: str,
) -> dict[str, Any]:
    """Multi-signal detection of delegated subagents (Apollo Codex auto-review).

    Signals:
    1. Environment variables (ANTIGRAVITY_SUBAGENT_ID, AGENT_ROLE, DELEGATION_DEPTH)
    2. Hook JSON payload fields (call_origin, is_subagent, parent_session_id, delegation_depth)
    3. Session registry lookup (subagent child conversation IDs)
    """
    env_depth_raw = os.environ.get("ANTIGRAVITY_DELEGATION_DEPTH") or os.environ.get(
        "DELEGATION_DEPTH"
    )
    env_depth = int(env_depth_raw) if env_depth_raw and env_depth_raw.isdigit() else 1

    # Signal 1: Environment variables
    env_subagent_id = os.environ.get("ANTIGRAVITY_SUBAGENT_ID")
    if env_subagent_id:
        return {
            "call_origin": "subagent",
            "parent_session_id": env_subagent_id,
            "delegation_depth": env_depth,
            "delegated_by": "env:ANTIGRAVITY_SUBAGENT_ID",
        }
    agent_role = os.environ.get("AGENT_ROLE", "").lower()
    if any(k in agent_role for k in ("subagent", "delegate", "worker", "sub_agent")):
        return {
            "call_origin": "subagent",
            "parent_session_id": None,
            "delegation_depth": env_depth,
            "delegated_by": f"env:AGENT_ROLE={agent_role}",
        }

    # Signal 2: Hook JSON payload fields
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

    # Signal 3: Registry lookup by conversation_id
    if conversation_id and SUBAGENT_REGISTRY_PATH.exists():
        try:
            lock_file = SUBAGENT_REGISTRY_PATH.with_suffix(".lock")
            data = {}
            if lock_file.exists() or SUBAGENT_REGISTRY_PATH.exists():
                try:
                    with open(lock_file, "a+", encoding="utf-8") as lf:
                        try:
                            import fcntl

                            fcntl.flock(lf.fileno(), fcntl.LOCK_SH)
                        except Exception:
                            pass
                        if SUBAGENT_REGISTRY_PATH.exists():
                            data = json.loads(SUBAGENT_REGISTRY_PATH.read_text(encoding="utf-8"))
                        try:
                            import fcntl

                            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                        except Exception:
                            pass
                except Exception:
                    if SUBAGENT_REGISTRY_PATH.exists():
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


def _track_delegation_tool(tool_name: str, tool_args: dict, parent_conv_id: str) -> None:
    """Register child subagents when delegation tools are invoked with concurrency lock & depth tracking."""
    delegation_tools = {
        "invoke_subagent",
        "define_subagent",
        "send_message",
        "Agent",
        "spawn_agent",
        "TaskCreate",
        "subagent",
    }
    if tool_name not in delegation_tools:
        return

    target_id = (
        tool_args.get("Recipient")
        or tool_args.get("agent_id")
        or tool_args.get("conversationId")
        or tool_args.get("subagent_id")
        or tool_args.get("TaskId")
        or tool_args.get("name")
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
            now = time.time()
            subagents = current_data.get("subagents", {})
            # Prune stale entries (> 24h) in subagent_registry.json
            subagents = {
                sid: sdata
                for sid, sdata in subagents.items()
                if isinstance(sdata, dict)
                and (now - _safe_float(sdata.get("registered_at"), 0.0)) < 86400.0
            }
            parent_info = subagents.get(parent_conv_id, {})
            parent_depth = _safe_int(parent_info.get("depth", 0)) if parent_info else 0
            subagents[target_str] = {
                "parent": parent_conv_id,
                "depth": parent_depth + 1,
                "delegated_by": tool_name,
                "registered_at": now,
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
    cmd = redact_text(cmd)
    path = redact_text(path)
    reason = redact_text(reason)
    if disclosure and isinstance(disclosure, dict):
        disclosure = redact_payload(disclosure)
        targets = disclosure.get("target_resources") or (
            [path or cmd or tool_name] if (path or cmd or tool_name) else []
        )
        destination = disclosure.get("destination")
        disclosed = disclosure.get("disclosed_data_types") or []
        blast = disclosure.get("blast_radius") or "local_reversible"
        reversibility = disclosure.get("reversibility_summary") or "Reversible: local change"
        disc_ctx = disclosure.get("discovered_context")
    else:
        # Generate local fallback disclosure
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
    decision: str,
    reason: str = "",
    conversation_id: str = "",
    terminal_reason: str = "",
    consent_box: str = "",
    disclosure: dict | None = None,
) -> None:
    if decision in ("force_ask", "ask") and conversation_id:
        with contextlib.suppress(Exception):
            _mark_force_ask(conversation_id)
    out: dict[str, Any] = {"decision": decision}
    if terminal_reason:
        out["terminal_reason"] = terminal_reason
    elif decision == "force_ask":
        out["terminal_reason"] = "lockout"

    try:
        if consent_box:
            # Redact secrets before writing to stderr
            redacted_box = redact_text(consent_box)
            sys.stderr.write(f"\n{redacted_box}\n\n")
            sys.stderr.flush()
            out["reason"] = (
                f"{reason}\n\n{redacted_box}"
                if reason and reason not in redacted_box
                else redacted_box
            )
        elif reason:
            out["reason"] = redact_text(reason)

        if disclosure:
            out["consent_disclosure"] = redact_payload(disclosure)

        print(json.dumps(out, ensure_ascii=False))
        sys.stdout.flush()
    except BrokenPipeError:
        pass
    _log("emit", decision=decision, reason=reason[:240], conversation_id=conversation_id[:64])


def _report_to_watcher(
    *,
    tool_name: str,
    tool_args: dict,
    conversation_id: str,
    cmd: str,
    provenance: dict | None = None,
    thought_context: str = "",
) -> None:
    """Best-effort POST so Live Stream / Sessions see local-blacklist denies."""
    req_body = {
        "agent_id": "antigravity",
        "tool_name": tool_name,
        "arguments": tool_args if tool_args else ({"CommandLine": cmd} if cmd else {}),
        "thought_context": thought_context or conversation_id,
        "session_id": f"antigravity-{conversation_id[:8]}" if conversation_id else None,
        "provenance": provenance or {"call_origin": "user_direct", "delegation_depth": 0},
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
        _log(
            "server_unreachable",
            error=str(err),
            url=WATCHER_URL,
            source="local_blacklist_report",
        )


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
    resolve_url = WATCHER_URL.replace(
        "/api/watcher/evaluate", "/api/v1/watcher/gate/resolve"
    ).replace("/api/v1/watcher/evaluate", "/api/v1/watcher/gate/resolve")
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
    result_url = WATCHER_URL.replace("/api/watcher/evaluate", "/api/v1/watcher/result").replace(
        "/api/v1/watcher/evaluate", "/api/v1/watcher/result"
    )
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
    try:
        raw = sys.stdin.read(MAX_STDIN_BYTES + 1)
    except Exception as err:
        _log("stdin_read_error", error=str(err))
        raw = ""

    if not raw.strip():
        if FAIL_MODE in ("closed", "fail-closed", "fail_closed", "deny"):
            _emit("deny", "Empty stdin payload in fail-closed mode")
        else:
            _emit("allow", "Empty stdin payload (fail-open)")
        return

    if len(raw) > MAX_STDIN_BYTES:
        _log("stdin_payload_too_large", size=len(raw), max_bytes=MAX_STDIN_BYTES)
        _emit(LOCKOUT_DECISION, "Payload exceeds maximum allowed size (2 MiB)")
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        _log("stdin_json_error", error=str(err))
        _emit(LOCKOUT_DECISION, "Invalid stdin JSON")
        return

    if not isinstance(payload, dict):
        _emit(LOCKOUT_DECISION, "Invalid stdin payload: expected JSON object")
        return

    conversation_id = str(payload.get("conversationId") or payload.get("conversation_id") or "")

    # PostToolUse handling (either --post flag or payload with stepIdx and no toolCall)
    if "--post" in sys.argv or ("stepIdx" in payload and not payload.get("toolCall")):
        err = payload.get("error")
        stdout, stderr = _get_latest_antigravity_tool_output(conversation_id)
        _report_tool_result_to_watcher(conversation_id, err, stdout, stderr)

        if _consume_force_ask(conversation_id):
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

    # 0) Provenance & Subagent Detection
    provenance = _detect_subagent_provenance(payload, tool_name, tool_args, conversation_id)
    _track_delegation_tool(tool_name, tool_args, conversation_id)

    _log(
        "pretool",
        tool_name=tool_name,
        cmd=cmd[:200],
        path=path[:200],
        conversation_id=conversation_id[:64],
        provenance=provenance,
    )

    # 1) Local deterministic deny (works offline). Still report to Watcher so UI
    # Live Stream / Sessions record the block.
    if cmd:
        for pattern, reason, tag in LOCAL_BLACKLIST:
            if re.search(pattern, cmd, re.IGNORECASE):
                _report_to_watcher(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    conversation_id=conversation_id,
                    cmd=cmd,
                    provenance=provenance,
                )
                reason_msg = (
                    f"[OPENEVAL INTERACTIVE LOCKOUT - {tag}]: {reason} "
                    "Execution frozen pending human operator approval."
                )
                consent_box = _format_consent_box(
                    decision=LOCKOUT_DECISION,
                    reason=f"[{tag}] {reason}",
                    tool_name=tool_name,
                    cmd=cmd,
                    path=path,
                )
                _emit(
                    LOCKOUT_DECISION,
                    reason_msg,
                    conversation_id=conversation_id,
                    consent_box=consent_box,
                )
                return

    # Protected credential path check (SSH keys, AWS credentials, shadow, .env, .secrets)
    is_cred_path = bool(
        path
        and re.search(
            r"(^|/)(id_rsa|id_ed25519|id_ecdsa|/etc/shadow|\.aws/credentials|\.secrets|\.env)",
            path,
        )
    )
    is_subagent = (
        provenance.get("call_origin") == "subagent" or provenance.get("delegation_depth", 0) > 0
    )

    if path and ("test_outputs.py" in path or "audit.log" in path or is_cred_path):
        is_write = tool_name in (
            "write_to_file",
            "replace_file_content",
            "edit_file",
            "modify_file",
        ) or bool(cmd and re.search(r"\b(rm|sed|mv|cp|truncate|dd|>|>>)\b", cmd))
        action_verb = "Modification of" if is_write else "Access / inspection of"

        if is_subagent and is_cred_path:
            tag = "RULE_SUBAGENT_CREDENTIAL_RESTRICTION"
            reason = "[SUBAGENT DELEGATE RESTRICTION]: Subagents strictly prohibited from credential access."
        elif "test_outputs.py" in path:
            tag = "RULE_REWARD_TAMPERING" if is_write else "RULE_SENSITIVE_PATH"
            reason = f"{action_verb} held-out evaluation verifier (test_outputs.py) prohibited without approval."
        elif "audit.log" in path:
            tag = "RULE_SENSITIVE_PATH"
            reason = f"{action_verb} security audit log trail prohibited without approval."
        else:
            tag = "RULE_SENSITIVE_PATH"
            reason = (
                f"{action_verb} protected credential / secret path prohibited without approval."
            )

        _report_to_watcher(
            tool_name=tool_name,
            tool_args=tool_args if tool_args else {"TargetFile": path, "path": path},
            conversation_id=conversation_id,
            cmd=path,
            provenance=provenance,
        )
        reason_msg = (
            f"[OPENEVAL INTERACTIVE LOCKOUT - {tag}]: {reason} "
            "Execution frozen pending human operator approval."
        )
        consent_box = _format_consent_box(
            decision=LOCKOUT_DECISION,
            reason=f"[{tag}] {reason}",
            tool_name=tool_name,
            cmd=cmd,
            path=path,
        )
        _emit(
            LOCKOUT_DECISION,
            reason_msg,
            conversation_id=conversation_id,
            consent_box=consent_box,
        )
        return

    thought_context = str(
        payload.get("thought_context")
        or payload.get("thoughtContext")
        or payload.get("thought")
        or payload.get("thinking")
        or payload.get("userPrompt")
        or payload.get("user_prompt")
        or conversation_id
    )

    # 1b) Offline negative constraint check (Reversibility ≠ Authorization)
    diff = ""
    if isinstance(tool_args, dict):
        diff = str(
            tool_args.get("diff")
            or tool_args.get("contents")
            or tool_args.get("content")
            or tool_args.get("CodeContent")
            or tool_args.get("ReplacementContent")
            or ""
        )
    target_input = cmd or path or str(tool_args)
    neg_constraints = _extract_negative_constraints_offline(thought_context)
    is_neg_viol, neg_desc = _check_negative_constraint_offline(
        neg_constraints, tool_name, target_input, diff=diff
    )
    if is_neg_viol:
        _report_to_watcher(
            tool_name=tool_name,
            tool_args=tool_args,
            conversation_id=conversation_id,
            cmd=cmd or path,
            provenance=provenance,
            thought_context=thought_context,
        )
        reason_msg = (
            f"[OPENEVAL INTERACTIVE LOCKOUT - REVERSIBILITY_NOT_AUTH]: "
            f"Explicit negative constraint '{neg_desc}' violated. "
            "Execution frozen pending human operator approval."
        )
        consent_box = _format_consent_box(
            decision=LOCKOUT_DECISION,
            reason=f"[REVERSIBILITY_NOT_AUTH] Explicit negative constraint '{neg_desc}' violated.",
            tool_name=tool_name,
            cmd=cmd,
            path=path,
        )
        _emit(
            LOCKOUT_DECISION,
            reason_msg,
            conversation_id=conversation_id,
            consent_box=consent_box,
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
                    "thought_context": thought_context,
                    "session_id": f"antigravity-{conversation_id[:8]}" if conversation_id else None,
                    "provenance": provenance,
                }
            ).encode("utf-8"),
            headers=_get_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
            verdict = json.loads(body)
        # Server query succeeded: reset circuit breaker failure records
        _reset_gateway_failures()
    except Exception as err:
        _log("server_unreachable", error=str(err), url=WATCHER_URL)
        recent_failures = _record_gateway_failure()
        if recent_failures >= MAX_CONSECUTIVE_FAILURES:
            reason_msg = (
                f"[OPENEVAL CIRCUIT BREAKER TRIPPED]: Watcher Gateway unreachable "
                f"({recent_failures} consecutive failures in {int(FAILURE_WINDOW_SEC)}s). "
                "Execution frozen pending human operator approval to prevent unmonitored actions."
            )
            consent_box = _format_consent_box(
                decision=LOCKOUT_DECISION,
                reason="[CIRCUIT_BREAKER_TRIPPED] Gateway unavailable; safety lockout engaged.",
                tool_name=tool_name,
                cmd=cmd,
                path=path,
            )
            _emit(
                LOCKOUT_DECISION,
                reason_msg,
                conversation_id=conversation_id,
                terminal_reason="circuit_breaker_tripped",
                consent_box=consent_box,
            )
            return

        # Fail-open only after local checks already passed and within failure budget
        _emit("allow", "Watcher gate: gateway timeout/unreachable (transient fail-open)")
        return

    decision = str(verdict.get("decision", "allow")).lower()
    risk_score = float(verdict.get("risk_score") or 0.0)
    reason = str(verdict.get("reason") or "Violates safety policy")
    shadow = verdict.get("shadow_decision")
    mode = str(verdict.get("mode_applied") or "")
    disclosure = verdict.get("consent_disclosure")

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
        reason_msg = (
            f"[OPENEVAL INTERACTIVE LOCKOUT]: {reason} (Risk: {int(risk_score * 100)}%). "
            "Execution frozen pending human operator approval."
        )
        consent_box = _format_consent_box(
            decision=LOCKOUT_DECISION,
            reason=reason,
            disclosure=disclosure,
            tool_name=tool_name,
            cmd=cmd,
            path=path,
        )
        _emit(
            LOCKOUT_DECISION,
            reason_msg,
            conversation_id=conversation_id,
            consent_box=consent_box,
            disclosure=disclosure,
        )
        return
    if decision in ("escalate", "ask", "warn"):
        reason_msg = f"[OPENEVAL ESCALATION]: {reason} (Risk: {int(risk_score * 100)}%)"
        consent_box = _format_consent_box(
            decision="force_ask",
            reason=reason,
            disclosure=disclosure,
            tool_name=tool_name,
            cmd=cmd,
            path=path,
        )
        _emit(
            "force_ask",
            reason_msg,
            conversation_id=conversation_id,
            consent_box=consent_box,
            disclosure=disclosure,
        )
        return

    _emit("allow")


def _setup_signal_handlers() -> None:
    def _sig_handler(signum: int, frame: Any) -> None:
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
        except Exception:
            pass
        sys.exit(0)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        _log("unhandled_exception", error=str(exc))
        if FAIL_MODE in ("closed", "fail-closed", "fail_closed", "deny"):
            _emit("deny", f"Unhandled error in gate: {exc}")
        else:
            _emit("allow", f"Unhandled error in gate (fail-open): {exc}")
