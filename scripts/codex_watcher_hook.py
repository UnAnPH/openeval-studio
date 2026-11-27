#!/usr/bin/env python3
"""OpenAI Codex CLI PreToolUse hook → OpenEval Watcher `/api/v1/watcher/gate`.

Supports the function-call style payloads emitted by Codex CLI wrappers:

    {"name": "exec_command", "arguments": {"cmd": "ls -la"}}
    {"tool_name": "apply_patch", "tool_input": {"patch": "..."}}

The hook performs a sub-millisecond local blacklist check, then forwards the
call to the Watcher gate. Output includes both ``decision`` (allow | deny |
ask | force_ask) and ``permission`` (allow | deny | ask) so Codex wrappers can
consume either contract.
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


# === BEGIN OPENEVAL_SHARED_HOOK_CORE ===
# Canonical shared core for the OpenEval runtime gate hooks.
# Generated from scripts/_hook_core.py; edit that file and run build_hooks.py.

MAX_STDIN_BYTES = 2 * 1024 * 1024  # 2 MiB cap
MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MiB cap


def _safe_int(val: object, default: int = 0) -> int:
    """Best-effort integer coercion that never raises on hostile payloads."""
    if val is None:
        return default
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        try:
            return int(float(val.strip()))
        except ValueError:
            return default
    return default


def _safe_float(val: object, default: float = 0.0) -> float:
    """Best-effort float coercion that never raises on hostile payloads."""
    if val is None:
        return default
    if isinstance(val, bool):
        return float(val)
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.strip())
        except ValueError:
            return default
    return default


_PROTECTED_CREDENTIAL_TEMPLATE_RE = re.compile(
    r"\.env\.(example|sample|template|defaults|dist)$", re.IGNORECASE
)
_PROTECTED_CREDENTIAL_BASENAME_RE = re.compile(
    r"(^|/)("
    r"\.env(\.[a-zA-Z0-9_-]+)?|"
    r"\.secrets|"
    r"id_rsa|"
    r"id_ed25519|"
    r"id_ecdsa|"
    r"\.aws/credentials|"
    r"\.npmrc|"
    r"\.pypirc|"
    r"\.netrc"
    r")$"
)
_PROTECTED_CREDENTIAL_SUFFIX_RE = re.compile(r"\.(pem|key)$")
_PROTECTED_CREDENTIAL_ABSOLUTE_RE = re.compile(r"/etc/shadow")


def _is_protected_credential_read_path(path: str) -> bool:
    """Return True for real credential material, False for templates and code files.

    Shared by the read-only tool guards in every hook so ``.env``,
    ``~/.aws/credentials``, ``.secrets``, ``id_rsa``, ``*.pem``/``*.key`` and
    ``/etc/shadow`` are protected while ``.env.example``-style templates stay
    readable.
    """
    if not path:
        return False
    normalized = path.replace("\\", "/")
    if _PROTECTED_CREDENTIAL_TEMPLATE_RE.search(normalized):
        return False
    if _PROTECTED_CREDENTIAL_BASENAME_RE.search(normalized):
        return True
    if _PROTECTED_CREDENTIAL_SUFFIX_RE.search(normalized):
        return True
    return bool(_PROTECTED_CREDENTIAL_ABSOLUTE_RE.search(normalized))


def _verdict_allows_in_observe(verdict: object) -> bool:
    """True when the gateway recorded the hit but observe/paused mode allows it."""
    return (
        isinstance(verdict, dict)
        and str(verdict.get("mode_applied") or "").lower() in ("observe", "paused")
        and str(verdict.get("decision") or "").lower() == "allow"
    )


# Stateful Circuit Breaker Configuration (shared across every hook so one
# gateway outage trips the breaker consistently for all clients).
CIRCUIT_STATE_FILE = Path(
    os.environ.get(
        "OPENEVAL_CIRCUIT_STATE_FILE",
        str(Path.home() / ".openeval" / "circuit_breaker.json"),
    )
)
MAX_CONSECUTIVE_FAILURES = _safe_int(os.environ.get("OPENEVAL_MAX_GATEWAY_FAILURES"), 3)
FAILURE_WINDOW_SEC = _safe_float(os.environ.get("OPENEVAL_FAILURE_WINDOW_SEC"), 60.0)


def _record_gateway_failure() -> int:
    """Record a gateway failure timestamp and return active failures in the window."""
    now = time.time()
    failures: list[float] = []
    lock_file = CIRCUIT_STATE_FILE.with_suffix(".lock")
    try:
        CIRCUIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "w", encoding="utf-8") as lf:
            with contextlib.suppress(ImportError, OSError):
                import fcntl

                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                if CIRCUIT_STATE_FILE.exists():
                    data = json.loads(CIRCUIT_STATE_FILE.read_text(encoding="utf-8"))
                    failures = [
                        _safe_float(ts)
                        for ts in data.get("failures", [])
                        if (now - _safe_float(ts)) < FAILURE_WINDOW_SEC
                    ]
            except (OSError, ValueError):
                failures = []
            failures.append(now)
            try:
                tmp = CIRCUIT_STATE_FILE.with_name(f"{CIRCUIT_STATE_FILE.name}.{os.getpid()}.tmp")
                tmp.write_text(
                    json.dumps({"failures": failures, "updated_at": now}), encoding="utf-8"
                )
                os.replace(tmp, CIRCUIT_STATE_FILE)
            except OSError:
                pass
            with contextlib.suppress(ImportError, OSError):
                import fcntl

                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    except OSError:
        failures.append(now)
    return len(failures)


def _reset_gateway_failures() -> None:
    """Reset circuit breaker failure records after a successful gate evaluation."""
    lock_file = CIRCUIT_STATE_FILE.with_suffix(".lock")
    try:
        if CIRCUIT_STATE_FILE.exists() or lock_file.exists():
            CIRCUIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_file, "w", encoding="utf-8") as lf:
                with contextlib.suppress(ImportError, OSError):
                    import fcntl

                    fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                if CIRCUIT_STATE_FILE.exists():
                    with contextlib.suppress(OSError):
                        CIRCUIT_STATE_FILE.unlink()
                with contextlib.suppress(ImportError, OSError):
                    import fcntl

                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    except Exception:  # noqa: BLE001  # Boundary: breaker reset must never crash a hook
        pass


def _rotate_log_file_if_needed(log_path: Path) -> None:
    """Keep hook logs bounded: rotate to ``<name>.1`` once MAX_LOG_BYTES is hit."""
    try:
        if log_path.exists() and log_path.stat().st_size >= MAX_LOG_BYTES:
            rotated = log_path.with_name(f"{log_path.name}.1")
            if rotated.exists():
                with contextlib.suppress(OSError):
                    rotated.unlink()
            os.replace(log_path, rotated)
    except Exception:  # noqa: BLE001  # Boundary: log rotation failure must not crash hook
        pass


# Canonical local blacklist shared by every runtime hook. Each entry is:
#   (regex, human reason, local rule id, watcher rule id)
# Hooks project each tuple into the shape their client protocol expects:
#   Codex / OpenCode -> (regex, reason, local rule id)
#   Antigravity      -> (regex, reason, watcher rule id)
#   Cursor / Claude  -> (regex, reason)
CANONICAL_BLACKLIST_PATTERNS: list[tuple[str, str, str, str]] = [
    # 1. Interpreter subprocess reflection (inline script wiping disks or reading secrets)
    (
        r"\b(?:python[23]?|perl|ruby|node)\s+(?:-e|-c)\s+[\"'].*?(?:shutil\.rmtree|os\.system\s*\(|open\s*\([^)]*(?:\.env|id_rsa|credentials)|\.unlink|\.rmdir|\.rmdirSync|\.rmSync|fs\.).*?[\"']",
        "Destructive filesystem or secret exfiltration via inline script interpreter.",
        "LOCAL_SUBPROCESS_REFLECTION",
        "RULE_SUBPROCESS_REFLECTION",
    ),
    # 2. Destructive filesystem wipe (combined -rf and split -r -f / --recursive --force)
    (
        r"\brm\s+(?:-[a-zA-Z0-9_-]+\s+)*"
        r"(?:-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*|"
        r"-[a-zA-Z]*r[a-zA-Z]*\s+-[a-zA-Z]*f[a-zA-Z]*|"
        r"-[a-zA-Z]*f[a-zA-Z]*\s+-[a-zA-Z]*r[a-zA-Z]*|--recursive\s+--force|--force\s+--recursive)"
        r"(?:\s+(?:/\S*|\*|\~|\$HOME)|\s+/\s*$)",
        "Recursive force-delete (rm -rf / separated flags) attempted.",
        "LOCAL_RM_RF_ROOT",
        "RULE_DESTRUCTIVE_WIPE",
    ),
    (
        r"\bmkfs(?:\.\w+)?\b",
        "Filesystem format (mkfs) attempted.",
        "LOCAL_MKFS",
        "RULE_FILESYSTEM_FORMAT",
    ),
    (
        r"\bsudo\b",
        "Unauthorized privilege escalation attempted (sudo).",
        "LOCAL_SUDO",
        "RULE_PRIVILEGE_ESCALATION",
    ),
    (
        r"\bsu\s+-\b",
        "Root user switch attempted (su -).",
        "LOCAL_SU",
        "RULE_PRIVILEGE_ESCALATION",
    ),
    (
        r"\bchmod\s+(-R\s+)?(777|a\+rwx)\b",
        "Security permissions weakening (chmod 777).",
        "LOCAL_CHMOD",
        "RULE_SECURITY_WEAKENING",
    ),
    (
        r"/var/run/docker\.sock",
        "Docker daemon socket access attempted (container escape).",
        "LOCAL_DOCKER_SOCK",
        "RULE_CONTAINER_ESCAPE",
    ),
    (
        r"169\.254\.169\.254",
        "Cloud IMDS metadata credential exfiltration attempted.",
        "LOCAL_IMDS",
        "RULE_CREDENTIAL_EXFILTRATION",
    ),
    (
        r"git\s+push\s+.*--(force|force-with-lease)\b",
        "git force push to remote attempted.",
        "LOCAL_GIT_FORCE_PUSH",
        "RULE_GIT_FORCE_PUSH",
    ),
    # 3. Obfuscated / encoded shell execution (base64/openssl/xxd pipe into shell)
    (
        r"(?:base64\s+(?:-d|--decode)|openssl\s+enc\s+-d|xxd\s+-r)\s*.*\|\s*(?:ba|z)?sh\b",
        "Obfuscated encoded payload piped to shell interpreter.",
        "LOCAL_ENCODED_PIPE_SHELL",
        "RULE_OBFUSCATED_EXECUTION",
    ),
    (
        r"curl\s+.*\|\s*(ba)?sh",
        "Remote script piped to shell (curl | bash).",
        "LOCAL_CURL_BASH",
        "RULE_CURL_BASH",
    ),
    (
        r"wget\s+.*\|\s*(ba)?sh",
        "Remote script piped to shell (wget | bash).",
        "LOCAL_WGET_BASH",
        "RULE_WGET_BASH",
    ),
    # 4. Credential exfiltration via network tools
    (
        r"\b(?:curl|wget)\s+[^\n]*(?:-d\s+@|-F\s+[^\s=]*=@|--post-file=)[^\s\"']*(?:\.env|\.secrets|id_rsa|id_ed25519|\.pem|/\.aws/credentials|/etc/shadow)",
        "Credential file exfiltration attempted via network upload.",
        "LOCAL_SECRET_EXFIL",
        "RULE_CREDENTIAL_EXFILTRATION",
    ),
    # 5. Expanded secret reading tools (cat, head, tail, grep, awk, sed, strings, xxd)
    (
        r"\b(?:cat|less|more|head|tail|bat|grep|awk|sed|strings|xxd|od)\s+[^\n]*(?:\.env\b|\.envrc\b|\.secrets\b|id_rsa|id_ed25519|\.pem\b|/etc/shadow|/\.aws/credentials)",
        "Reading secret/credential files via shell.",
        "LOCAL_SECRET_READ",
        "RULE_SECRET_READ",
    ),
    (
        r"terraform\s+destroy\s+-auto-approve",
        "Terraform destroy auto-approve attempted.",
        "LOCAL_TF_DESTROY",
        "RULE_TF_DESTROY",
    ),
    (
        r"kubectl\s+delete\s+(all|namespace)\b",
        "Destructive kubectl delete attempted.",
        "LOCAL_K8S_DELETE",
        "RULE_K8S_DELETE",
    ),
    (
        r"\bkillall\s+-9\s+watchdog\b",
        "Attempted sabotage of safety monitoring process.",
        "LOCAL_AUDIT_SABOTAGE",
        "RULE_AUDIT_SABOTAGE",
    ),
    (
        r'(api[_-]?key|secret|token|password)=["\']?[a-zA-Z0-9_\-]{16,}',
        "Inline credentials in command line.",
        "LOCAL_INLINE_CREDENTIALS",
        "RULE_CREDENTIAL_INLINE",
    ),
]

# Projections consumed by the individual hooks.
BLACKLIST_PATTERNS: list[tuple[str, str, str]] = [
    (pattern, reason, local_rule_id)
    for pattern, reason, local_rule_id, _watcher_rule_id in CANONICAL_BLACKLIST_PATTERNS
]
LOCAL_BLACKLIST_RULES = BLACKLIST_PATTERNS
LOCAL_BLACKLIST_WATCHER_RULES: list[tuple[str, str, str]] = [
    (pattern, reason, watcher_rule_id)
    for pattern, reason, _local_rule_id, watcher_rule_id in CANONICAL_BLACKLIST_PATTERNS
]
LOCAL_BLACKLIST_REASONS: list[tuple[str, str]] = [
    (pattern, reason)
    for pattern, reason, _local_rule_id, _watcher_rule_id in CANONICAL_BLACKLIST_PATTERNS
]


_DIRECTIVE_PREFIX = (
    r"(?:^|(?:[.!?\n;:]\s*)|(?:,\s+)|"
    r"\b(?:but|yet|also|please|you|agent|assistant|ensure|remember(?:\s+to)?|make\s+sure(?:\s+to)?)\s+)"
)
_CONSTRAINT_STOP = r"(?!\.\s|\.$|,\s*(?:and|but|then|so|while|which)\b)"

NEGATIVE_CONSTRAINT_PATTERNS = [
    re.compile(
        _DIRECTIVE_PREFIX
        + r"(?:do\s+not|don't|never|must\s+not|cannot|should\s+not)[ \t]+"
        + rf"((?:{_CONSTRAINT_STOP}[a-zA-Z0-9_\-./*`:, \t'\"]){{2,80}})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:under\s+no\s+circumstances(?:[ \t]+should[ \t]+you|[ \t]+shall[ \t]+you|[ \t]+can[ \t]+you|[ \t]+do)?)[ \t]+"
        + rf"((?:{_CONSTRAINT_STOP}[a-zA-Z0-9_\-./*`:, \t'\"]){{2,80}})",
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


def _stem_matches(stem: str, text: str) -> bool:
    """Match a constraint stem at token/path boundaries rather than inside unrelated words."""
    if not stem or not text:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(stem)}(?![a-z0-9])", text))


def _check_negative_constraint_offline(
    constraints: list[str], tool_name: str, tool_input: str, diff: str = ""
) -> tuple[bool, str]:
    """Offline negative-constraint matcher kept in lockstep with the server."""
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
            if exc_core and any(_stem_matches(t, full_combined) for t in exc_core):
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

            # Separate specific targets from generic targets (e.g. 'files', 'data').
            specific_targets = [ct for ct in core_targets if ct not in _GENERIC_TARGETS]
            # Must be computed before `matched`: a delete prohibition only fires for
            # delete-shaped invocations, even when the target noun appears in the command.
            is_delete_prohibition = any(a in _DELETE_ACTIONS for a in actions_in_sub)

            matched = False
            if specific_targets:
                all_found = True
                for ct in specific_targets:
                    stems = _get_target_stems(ct)
                    if not any(
                        _stem_matches(stem, combined_target) or _stem_matches(stem, full_combined)
                        for stem in stems
                    ):
                        all_found = False
                        break
                matched = (all_found and is_delete_tool) if is_delete_prohibition else all_found
            elif core_targets:
                # Only generic targets exist (e.g. 'delete files').
                if is_delete_prohibition:
                    matched = is_delete_tool
                else:
                    matched = any(
                        _stem_matches(stem, combined_target)
                        for ct in core_targets
                        for stem in _get_target_stems(ct)
                    )
            elif tokens:
                matched = any(_stem_matches(t, full_combined) for t in tokens)
            if matched:
                return True, constraint
    return False, ""


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
        # Generate local fallback disclosure.
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


# === END OPENEVAL_SHARED_HOOK_CORE ===

REQUEST_ID = uuid.uuid4().hex[:16]

_DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_KNOWN_ENDPOINT_SUFFIXES = (
    "/api/v1/watcher/evaluate",
    "/api/v1/watcher/gate/resolve",
    "/api/v1/watcher/gate",
    "/api/watcher/evaluate",
)

OPENEVAL_API_KEY = os.environ.get("OPENEVAL_API_KEY", "").strip()

# Load fallback config from ~/.openeval/config.json if not present in env (GUI launches).
_CONFIG_FILE = Path.home() / ".openeval" / "config.json"
_CONFIG_WATCHER_URL = ""
if _CONFIG_FILE.is_file():
    try:
        _cfg = json.loads(_CONFIG_FILE.read_text())
        if not OPENEVAL_API_KEY and _cfg.get("api_key"):
            OPENEVAL_API_KEY = str(_cfg["api_key"]).strip()
        if _cfg.get("watcher_url"):
            _CONFIG_WATCHER_URL = str(_cfg["watcher_url"]).strip()
    except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError):
        pass


def _base_url_from(raw: str) -> str:
    value = (raw or "").strip().rstrip("/")
    if not value:
        return _DEFAULT_BASE_URL
    for suffix in _KNOWN_ENDPOINT_SUFFIXES:
        if value.endswith(suffix):
            return value[: -len(suffix)].rstrip("/") or _DEFAULT_BASE_URL
    if value.endswith("/api/v1/watcher"):
        return value[: -len("/api/v1/watcher")].rstrip("/") or _DEFAULT_BASE_URL
    return value


WATCHER_BASE_URL = _base_url_from(
    os.environ.get("OPENEVAL_WATCHER_BASE_URL")
    or os.environ.get("OPENEVAL_WATCHER_URL")
    or _CONFIG_WATCHER_URL
)
WATCHER_URL = f"{WATCHER_BASE_URL}/api/v1/watcher/gate"
TIMEOUT_SEC = _safe_float(os.environ.get("OPENEVAL_WATCHER_TIMEOUT"), 1.5)
# Local-blacklist reporting must never stall the hook: keep it fast (default 0.35s).
REPORT_TIMEOUT_SEC = min(
    TIMEOUT_SEC, _safe_float(os.environ.get("OPENEVAL_WATCHER_REPORT_TIMEOUT"), 0.35)
)
LOCKOUT_DECISION = os.environ.get("OPENEVAL_LOCKOUT_DECISION", "force_ask")
FAIL_MODE = (
    os.environ.get(
        "OPENEVAL_GATE_FAIL_MODE",
        os.environ.get("OPENEVAL_FAIL_MODE", os.environ.get("OPENEVAL_ON_FAILURE", "open")),
    )
    .strip()
    .lower()
)

LOG_PATH = Path.home() / ".openeval" / "codex-watcher-gate.log"


def _fail_closed() -> bool:
    return FAIL_MODE in ("closed", "fail-closed", "fail_closed", "deny")


def _get_headers(req_id: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-ID": req_id or REQUEST_ID,
    }
    if OPENEVAL_API_KEY:
        headers["X-OpenEval-Key"] = OPENEVAL_API_KEY
        headers["Authorization"] = f"Bearer {OPENEVAL_API_KEY}"
    return headers


# Codex's hook protocol consumes (pattern, reason, local rule id) triples.
LOCAL_BLACKLIST = LOCAL_BLACKLIST_RULES

PROTECTED_PATH_PATTERNS: list[tuple[str, str]] = [
    (
        r"(^|/)(\.env|\.envrc|\.secrets|secrets?\.json)(\.|$)",
        "Environment / secret credentials (.env)",
    ),
    (r"(^|/)(id_rsa|id_ed25519|id_ecdsa|\.pem|\.key)$", "Private encryption key / credential file"),
    (r"^/etc/(shadow|passwd|sudoers)", "System authentication and security file"),
    (r"(^|/)\.git/hooks/", "Git hook script modification"),
    (r"(^|/)\.watcherignore$", "Watcher monitoring bypass (.watcherignore)"),
]


def _log(event: str, **fields: object) -> None:
    try:
        _rotate_log_file_if_needed(LOG_PATH)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        redacted = {k: redact_payload(v) for k, v in fields.items()}
        row = {"ts": time.time(), "request_id": REQUEST_ID, "event": event, **redacted}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


_CURRENT_HOOK_EVENT: str = "PreToolUse"


def _emit(
    decision: str,
    reason: str = "",
    rule_id: str | None = None,
    terminal_reason: str | None = None,
    hook_event_name: str | None = None,
) -> None:
    """Write the Codex hook verdict to stdout adhering strictly to Codex's Rust schema."""
    event = hook_event_name or _CURRENT_HOOK_EVENT or "PreToolUse"
    norm_event = event.lower().replace("_", "")
    is_blocked = str(decision).lower() in (
        "deny",
        "block",
        "ask",
        "force_ask",
        "reject",
        "escalate",
    )
    redacted_reason = redact_text(reason)

    if norm_event == "permissionrequest":
        if is_blocked:
            out: dict[str, Any] = {
                "continue": False,
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {
                        "behavior": "deny",
                        "message": redacted_reason,
                    },
                },
            }
        else:
            out = {
                "continue": True,
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {
                        "behavior": "allow",
                    },
                },
            }
    else:  # PreToolUse (default)
        if is_blocked:
            out = {
                "continue": False,
                "decision": "block",
                "reason": redacted_reason,
            }
        else:
            out = {
                "continue": True,
                "decision": "approve",
            }

    try:
        print(json.dumps(out, ensure_ascii=False))
        sys.stdout.flush()
    except BrokenPipeError:
        pass

    _log(
        "emit",
        decision=decision,
        rule_id=rule_id,
        reason=redacted_reason[:240],
        terminal_reason=terminal_reason,
        hook_event_name=event,
        out=out,
    )

    if is_blocked:
        try:
            if redacted_reason:
                sys.stderr.write(f"{redacted_reason}\n")
                sys.stderr.flush()
        except BrokenPipeError:
            pass
        sys.exit(2)
    else:
        sys.exit(0)


def _session_id(raw: str) -> str | None:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", (raw or "").strip())
    if not cleaned:
        return None
    if cleaned.startswith("codex-"):
        return cleaned
    return f"codex-{cleaned[:12]}"


def _first_string(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_cmd(tool_input: dict[str, Any]) -> str:
    cmd = _first_string(
        tool_input,
        ("command", "cmd", "CommandLine", "command_line", "script", "raw"),
    )
    if cmd:
        return cmd
    for nested_key in ("input", "params", "parameters", "arguments", "args"):
        nested = tool_input.get(nested_key)
        if isinstance(nested, dict):
            found = _extract_cmd(nested)
            if found:
                return found
    return ""


def _extract_path(tool_input: dict[str, Any]) -> str:
    path = _first_string(
        tool_input,
        (
            "path",
            "file_path",
            "filePath",
            "target_file",
            "TargetFile",
            "AbsolutePath",
            "file",
            "filename",
            "uri",
        ),
    )
    if path:
        return path
    for nested_key in ("input", "params", "parameters", "arguments", "args"):
        nested = tool_input.get(nested_key)
        if isinstance(nested, dict):
            found = _extract_path(nested)
            if found:
                return found
    return ""


def _detect_provenance(payload: dict[str, Any]) -> dict[str, Any]:
    """Multi-signal subagent provenance for Codex delegated calls."""
    raw = payload.get("provenance")
    if isinstance(raw, dict) and raw.get("call_origin"):
        return {
            "call_origin": raw.get("call_origin", "user_direct"),
            "parent_session_id": raw.get("parent_session_id"),
            "delegation_depth": _safe_int(raw.get("delegation_depth"), 0),
            "delegated_by": raw.get("delegated_by"),
        }

    subagent_id = os.environ.get("CODEX_SUBAGENT_ID") or os.environ.get("OPENEVAL_SUBAGENT_ID")
    origin = (os.environ.get("OPENEVAL_SUBAGENT_ORIGIN") or "").strip().lower()
    if subagent_id or origin == "subagent":
        return {
            "call_origin": "subagent",
            "parent_session_id": subagent_id or None,
            "delegation_depth": 1,
            "delegated_by": (
                "env:CODEX_SUBAGENT_ID" if subagent_id else "env:OPENEVAL_SUBAGENT_ORIGIN"
            ),
        }

    parent = payload.get("parent_session_id") or payload.get("parentSessionId")
    if parent:
        return {
            "call_origin": "subagent",
            "parent_session_id": str(parent),
            "delegation_depth": 1,
            "delegated_by": f"parent_session:{parent}",
        }

    return {
        "call_origin": "user_direct",
        "parent_session_id": None,
        "delegation_depth": 0,
        "delegated_by": None,
    }


def _is_sensitive_path(path: str) -> tuple[bool, str]:
    if not path:
        return False, ""
    normalized = path.replace("\\", "/")
    for pattern, desc in PROTECTED_PATH_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return True, desc
    return False, ""


def _report_to_watcher(
    *,
    tool_name: str,
    args: dict[str, Any],
    session_id: str | None,
    cmd: str,
    provenance: dict[str, Any],
    thought_context: str,
) -> dict[str, Any] | None:
    """Best-effort POST so local blacklist hits appear on the live dashboard.

    Returns the gateway verdict so observe-mode allows can be honored, or
    ``None`` on any transport/parse failure.
    """
    try:
        req = urllib.request.Request(
            WATCHER_URL,
            data=json.dumps(
                {
                    "agent_id": "codex",
                    "agent_type": "codex",
                    "tool_name": tool_name,
                    "arguments": args if args else ({"command": cmd} if cmd else {}),
                    "session_id": session_id,
                    "thought_context": thought_context or session_id,
                    "provenance": provenance,
                }
            ).encode("utf-8"),
            headers=_get_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=REPORT_TIMEOUT_SEC) as resp:
            verdict = json.loads(resp.read().decode("utf-8"))
            _log(
                "server_verdict",
                decision=str(verdict.get("decision", "")).lower(),
                source="local_blacklist_report",
                session_id=session_id,
            )
            return verdict if isinstance(verdict, dict) else None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as err:
        _log("server_unreachable", error=str(err), source="local_blacklist_report")
        return None


def _handle_transport_failure(cmd: str, tool_name: str) -> None:
    recent_failures = _record_gateway_failure()
    if recent_failures >= MAX_CONSECUTIVE_FAILURES:
        _emit(
            LOCKOUT_DECISION,
            f"[OPENEVAL CIRCUIT BREAKER TRIPPED]: Watcher Gateway unreachable "
            f"({recent_failures} consecutive failures in {int(FAILURE_WINDOW_SEC)}s).",
            rule_id="CIRCUIT_BREAKER_TRIPPED",
            terminal_reason="lockout",
        )
        return
    if _fail_closed():
        _emit(
            "deny",
            "Watcher gate: gateway unreachable (fail-closed mode)",
            rule_id="GATEWAY_UNREACHABLE",
        )
    else:
        _emit("allow", "Watcher gate: gateway timeout/unreachable (transient fail-open)")


def main() -> None:
    try:
        raw = sys.stdin.read(MAX_STDIN_BYTES + 1)
    except OSError as err:
        _log("stdin_read_error", error=str(err))
        raw = ""

    if not raw.strip():
        if _fail_closed():
            _emit("deny", "Empty stdin payload in fail-closed mode", rule_id="EMPTY_STDIN")
        else:
            _emit("allow")
        return

    if len(raw) > MAX_STDIN_BYTES:
        _log("stdin_payload_too_large", size=len(raw), max_bytes=MAX_STDIN_BYTES)
        _emit("deny", "Payload exceeds maximum allowed size (2 MiB)", rule_id="PAYLOAD_TOO_LARGE")
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        _log("stdin_json_error", error=str(err))
        _emit("deny", "Invalid stdin JSON", rule_id="INVALID_JSON")
        return

    if not isinstance(payload, dict):
        _emit("deny", "Invalid stdin payload: expected JSON object", rule_id="INVALID_PAYLOAD")
        return

    global _CURRENT_HOOK_EVENT
    _CURRENT_HOOK_EVENT = str(
        payload.get("hook_event_name")
        or payload.get("hookEventName")
        or payload.get("hook_name")
        or "PreToolUse"
    )

    tool_name = str(
        payload.get("tool_name")
        or payload.get("tool")
        or payload.get("name")
        or ("exec_command" if ("command" in payload or "cmd" in payload) else "unknown")
    )

    raw_input = (
        payload.get("tool_input")
        or payload.get("parameters")
        or payload.get("arguments")
        or payload
    )
    tool_input: dict[str, Any] = (
        dict(raw_input) if isinstance(raw_input, dict) else {"raw": str(raw_input)}
    )

    cmd = _extract_cmd(tool_input)
    path = _extract_path(tool_input)
    session_id = _session_id(
        str(
            payload.get("session_id")
            or payload.get("sessionId")
            or payload.get("conversation_id")
            or payload.get("conversationId")
            or payload.get("thread_id")
            or ""
        )
    )
    thought_context = str(
        payload.get("thought_context")
        or payload.get("thoughtContext")
        or payload.get("thought")
        or payload.get("thinking")
        or payload.get("userPrompt")
        or payload.get("user_prompt")
        or ""
    )
    provenance = _detect_provenance(payload)

    args = dict(tool_input)
    if cmd and not any(k in args for k in ("command", "cmd", "CommandLine")):
        args["command"] = cmd

    _log(
        "pretool",
        tool_name=tool_name,
        cmd=cmd[:200],
        path=path[:200],
        session_id=(session_id or "")[:64],
        provenance=provenance,
    )

    # 1. Sub-millisecond local blacklist
    for pattern, reason, rule_id in LOCAL_BLACKLIST:
        if cmd and re.search(pattern, cmd, re.IGNORECASE):
            verdict = _report_to_watcher(
                tool_name=tool_name,
                args=args,
                session_id=session_id,
                cmd=cmd,
                provenance=provenance,
                thought_context=thought_context,
            )
            if _verdict_allows_in_observe(verdict):
                _emit("allow")
                return
            _emit(
                "deny",
                f"[OPENEVAL LOCAL BLACKLIST]: {reason} detected (`{cmd[:120]}`).",
                rule_id=rule_id,
            )
            return

    # 2. Sensitive path mutation
    is_sensitive, sensitive_reason = _is_sensitive_path(path)
    if is_sensitive:
        verdict = _report_to_watcher(
            tool_name=tool_name,
            args=args,
            session_id=session_id,
            cmd=path,
            provenance=provenance,
            thought_context=thought_context,
        )
        if _verdict_allows_in_observe(verdict):
            _emit("allow")
            return
        _emit(
            "deny",
            f"[OPENEVAL LOCAL BLACKLIST]: Protected path ('{path}') blocked ({sensitive_reason}).",
            rule_id="LOCAL_PROTECTED_PATH",
        )
        return

    # 3. Subagent credential restriction
    is_sub = (
        provenance.get("call_origin") == "subagent" or provenance.get("delegation_depth", 0) > 0
    )
    if (
        is_sub
        and cmd
        and re.search(
            r"(?:\.env\b|\.secrets\b|id_rsa|id_ed25519|sudo\b|/etc/shadow|/\.aws/credentials)",
            cmd,
            re.IGNORECASE,
        )
    ):
        verdict = _report_to_watcher(
            tool_name=tool_name,
            args=args,
            session_id=session_id,
            cmd=cmd,
            provenance=provenance,
            thought_context=thought_context,
        )
        if _verdict_allows_in_observe(verdict):
            _emit("allow")
            return
        _emit(
            "deny",
            "[OPENEVAL SUBAGENT DELEGATE RESTRICTION]: Credential access or privilege "
            "escalation prohibited.",
            rule_id="SUBAGENT_CREDENTIAL_RESTRICTION",
        )
        return

    # 4. Query Watcher gate
    try:
        req = urllib.request.Request(
            WATCHER_URL,
            data=json.dumps(
                {
                    "agent_id": "codex",
                    "agent_type": "codex",
                    "tool_name": tool_name,
                    "arguments": args,
                    "session_id": session_id,
                    "thought_context": thought_context or session_id,
                    "provenance": provenance,
                }
            ).encode("utf-8"),
            headers=_get_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            verdict = json.loads(resp.read().decode("utf-8"))
        _reset_gateway_failures()
    except urllib.error.HTTPError as err:
        if err.code == 422:
            try:
                err_body = json.loads(err.read().decode("utf-8"))
                unknown_keys = [
                    str(d["loc"][-1])
                    for d in err_body.get("detail", [])
                    if isinstance(d, dict) and d.get("type") == "extra_forbidden" and "loc" in d
                ]
            except (OSError, ValueError):
                unknown_keys = []
            keys_str = ", ".join(unknown_keys) if unknown_keys else "unknown"
            drift_msg = (
                f"payload schema drift: unexpected fields [{keys_str}] rejected by Watcher gate"
            )
            sys.stderr.write(f"[OPENEVAL WARNING] {drift_msg}\n")
            _log("payload_schema_drift", unknown_keys=unknown_keys, error=str(err))
            _emit(
                LOCKOUT_DECISION,
                f"[OPENEVAL SCHEMA DRIFT]: {drift_msg}. Execution frozen pending operator review.",
                rule_id="SCHEMA_DRIFT",
                terminal_reason="lockout",
            )
            return
        _log("server_error", error=str(err), cmd=cmd[:120])
        _handle_transport_failure(cmd, tool_name)
        return
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as err:
        _log("server_unreachable", error=str(err), cmd=cmd[:120])
        _handle_transport_failure(cmd, tool_name)
        return

    decision = str(verdict.get("decision", "allow")).lower()
    risk = _safe_float(verdict.get("risk_score"), 0.0)
    reason = str(verdict.get("reason") or "Safety policy check")
    rule_id = verdict.get("rule_violation_tag")
    _log(
        "server_verdict",
        decision=decision,
        risk_score=risk,
        rule_id=rule_id,
        shadow_decision=verdict.get("shadow_decision"),
        mode_applied=verdict.get("mode_applied"),
    )

    if decision in ("deny", "reject", "block"):
        _emit(
            LOCKOUT_DECISION,
            f"[OPENEVAL INTERACTIVE LOCKOUT]: {reason} (Risk: {int(risk * 100)}%). "
            "Execution frozen pending human operator approval.",
            rule_id=str(rule_id) if rule_id else "GATEWAY_DENY",
            terminal_reason="lockout",
        )
        return
    if decision in ("escalate", "ask", "warn"):
        _emit("ask", f"[OPENEVAL ESCALATION] {reason}", rule_id=str(rule_id) if rule_id else None)
        return

    _emit("allow", rule_id=str(rule_id) if rule_id else None)


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
        except OSError:
            pass
        sys.exit(0)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001  # Boundary: top-level safety gate error handler
        _log("unhandled_exception", error=str(exc))
        if _fail_closed():
            _emit("deny", f"Unhandled error in gate: {exc}", rule_id="UNHANDLED_ERROR")
        else:
            _emit("allow", f"Unhandled error in gate (fail-open): {exc}")
