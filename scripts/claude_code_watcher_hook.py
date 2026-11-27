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

WATCHER_URL = os.environ.get(
    "OPENEVAL_WATCHER_URL", "http://127.0.0.1:8000/api/v1/watcher/evaluate"
)
OPENEVAL_API_KEY = os.environ.get("OPENEVAL_API_KEY", "").strip()

# Load fallback config from ~/.openeval/config.json if not present in env (GUI launches).
_CONFIG_FILE = Path.home() / ".openeval" / "config.json"
if _CONFIG_FILE.is_file():
    try:
        _cfg = json.loads(_CONFIG_FILE.read_text())
        if not OPENEVAL_API_KEY and _cfg.get("api_key"):
            OPENEVAL_API_KEY = str(_cfg["api_key"]).strip()
        if WATCHER_URL == "http://127.0.0.1:8000/api/v1/watcher/evaluate" and _cfg.get(
            "watcher_url"
        ):
            WATCHER_URL = str(_cfg["watcher_url"]).strip()
    except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError):
        pass
TIMEOUT_SEC = _safe_float(os.environ.get("OPENEVAL_WATCHER_TIMEOUT"), 1.5)
# Local-blacklist reporting must never stall the hook: keep it fast (default 0.35s).
REPORT_TIMEOUT_SEC = min(
    TIMEOUT_SEC, _safe_float(os.environ.get("OPENEVAL_WATCHER_REPORT_TIMEOUT"), 0.35)
)
LOCKOUT_PERMISSION = os.environ.get("OPENEVAL_LOCKOUT_PERMISSION", "ask")
FAIL_MODE = (
    os.environ.get(
        "OPENEVAL_GATE_FAIL_MODE",
        os.environ.get("OPENEVAL_FAIL_MODE", os.environ.get("OPENEVAL_ON_FAILURE", "open")),
    )
    .strip()
    .lower()
)


def _fail_open_or_closed_permission(open_permission: str = "allow") -> str:
    if FAIL_MODE in ("closed", "fail-closed", "fail_closed", "deny"):
        return "deny"
    return open_permission


def _handle_gateway_transport_failure() -> None:
    recent_failures = _record_gateway_failure()
    if recent_failures >= MAX_CONSECUTIVE_FAILURES:
        _emit(
            LOCKOUT_PERMISSION,
            f"[OPENEVAL CIRCUIT BREAKER TRIPPED]: Watcher Gateway unreachable "
            f"({recent_failures} consecutive failures in {int(FAILURE_WINDOW_SEC)}s).",
        )
        return
    closed_perm = _fail_open_or_closed_permission("allow")
    if closed_perm == "allow":
        _emit("allow")
    else:
        _emit(closed_perm, "Watcher gate: gateway unreachable (fail-closed mode)")


LOG_PATH = Path.home() / ".openeval" / "claude-watcher-gate.log"


def _get_headers(req_id: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-ID": req_id or REQUEST_ID,
    }
    if OPENEVAL_API_KEY:
        headers["X-OpenEval-Key"] = OPENEVAL_API_KEY
        headers["Authorization"] = f"Bearer {OPENEVAL_API_KEY}"
    return headers


# Claude Code's hook protocol consumes (pattern, reason) pairs.
LOCAL_BLACKLIST = LOCAL_BLACKLIST_REASONS


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


def _cursor_session_id(raw: str) -> str | None:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", (raw or "").strip())
    if not cleaned:
        return None
    if cleaned.startswith("cursor-"):
        return cleaned[:32]
    return f"cursor-{cleaned[:32]}"


def _is_invoked_by_cursor(payload: dict, tool_name: str) -> bool:
    """Detect Cursor IDE invoking ~/.claude/settings.json hooks via compatibility mode."""
    if (
        os.environ.get("CLAUDECODE")
        or os.environ.get("CLAUDE_CODE_ENTRYPOINT")
        or ".claude/" in str(payload.get("transcript_path") or "").replace("\\", "/").lower()
    ):
        return False
    if (
        "cursor_version" in payload
        or "conversation_id" in payload
        or "conversationId" in payload
        or ".cursor/" in str(payload.get("transcript_path") or "").replace("\\", "/").lower()
        or tool_name in ("Shell", "StrReplace", "EditNotebook", "ReadFile", "ListDir")
        or bool(os.environ.get("CURSOR_TRACE_ID") or os.environ.get("CURSOR_AGENT"))
    ):
        return True
    bundle_id = os.environ.get("__CFBUNDLEIDENTIFIER", "").lower()
    askpass = (
        os.environ.get("VSCODE_GIT_ASKPASS_NODE", "")
        + " "
        + os.environ.get("VSCODE_IPC_HOOK_CLI", "")
    ).lower()
    return "cursor" in bundle_id or "cursor.app" in askpass


def _cursor_gate_installed(cwd: str = "") -> bool:
    """Return True if Cursor's native hooks.json already delegates to cursor_watcher_gate."""
    candidates = [Path.home() / ".cursor" / "hooks.json"]
    if cwd:
        candidates.append(Path(cwd) / ".cursor" / "hooks.json")
    for cfg_path in candidates:
        if cfg_path.is_file():
            with contextlib.suppress(OSError):
                if "cursor_watcher_gate" in cfg_path.read_text(encoding="utf-8", errors="ignore"):
                    return True
    return False


def _is_uuid_like(val: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F-]{8,64}", val.strip()))


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
    thought_context: str = "",
) -> dict[str, Any] | None:
    """Best-effort POST so local blacklist hits show up on the live dashboard.

    Returns the gateway verdict (so observe-mode allows can be honored) or
    ``None`` when the report could not be delivered/parsed.
    """
    try:
        req = urllib.request.Request(
            WATCHER_URL,
            data=json.dumps(
                {
                    "agent_id": "claude_code",
                    "tool_name": tool_name,
                    "arguments": args if args else ({"command": cmd} if cmd else {}),
                    "session_id": session_id,
                    "thought_context": thought_context,
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
    except Exception as err:  # noqa: BLE001  # Boundary: best-effort report failure must not block execution
        _log("server_unreachable", error=str(err), source="local_blacklist_report")
        return None


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

    raw_session = str(
        payload.get("session_id")
        or payload.get("sessionId")
        or payload.get("conversation_id")
        or payload.get("conversationId")
        or ""
    )
    invoked_by_cursor = _is_invoked_by_cursor(payload, tool_name)
    if invoked_by_cursor:
        _log("cursor_compat_ignored", tool_name=tool_name, conversation_id=raw_session[:64])
        _emit("allow", "Claude Code hook skipping Cursor compatibility invocation")
        return

    agent_id = "claude_code"
    cmd = _cmd_from_input(tool_name, tool_input)
    session_id = _claude_session_id(raw_session)
    _log(
        "pretool",
        agent_id=agent_id,
        tool_name=tool_name,
        cmd=cmd[:200],
        session_id=(session_id or "")[:64],
    )

    # Thought/prompt context is used by local constraint checks and reporting; extract it
    # before any read-only / blacklist / sensitive-path early return.
    raw_thought = str(
        payload.get("thought_context")
        or payload.get("thoughtContext")
        or payload.get("thought")
        or payload.get("userPrompt")
        or payload.get("user_prompt")
        or payload.get("prompt")
        or ""
    ).strip()
    thought_context = raw_thought if raw_thought and not _is_uuid_like(raw_thought) else ""

    # Allow benign workspace reads/searches locally (matching Cursor/Antigravity gates)
    # while blocking direct reads of private SSH/shadow credentials and credential files.
    lower_tool = tool_name.lower()
    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
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
        if path and _is_protected_credential_read_path(path):
            args = dict(tool_input)
            if cmd and "command" not in args:
                args["command"] = cmd
            verdict = _report_to_watcher(
                tool_name=tool_name,
                args=args,
                session_id=session_id,
                cmd="",
                thought_context=thought_context,
            )
            if _verdict_allows_in_observe(verdict):
                _emit("allow")
                return
            _emit(
                LOCKOUT_PERMISSION,
                f"[OPENEVAL INTERACTIVE LOCKOUT]: Reading protected private credential file ('{path}') is blocked.",
            )
            return
        _emit("allow")
        return

    args = dict(tool_input)
    if cmd and "command" not in args:
        args["command"] = cmd

    for pattern, reason in LOCAL_BLACKLIST:
        if cmd and re.search(pattern, cmd, re.IGNORECASE):
            verdict = _report_to_watcher(
                tool_name=tool_name,
                args=args,
                session_id=session_id,
                cmd=cmd,
                thought_context=thought_context,
            )
            if _verdict_allows_in_observe(verdict):
                _emit("allow")
                return
            _emit(
                LOCKOUT_PERMISSION,
                f"[OPENEVAL INTERACTIVE LOCKOUT]: {reason}. Execution frozen pending human operator approval.",
            )
            return

    # Sensitive path writes
    if path.endswith(".env") or "id_rsa" in path or "/.aws/credentials" in path:
        verdict = _report_to_watcher(
            tool_name=tool_name,
            args=args,
            session_id=session_id,
            cmd=path,
            thought_context=thought_context,
        )
        if _verdict_allows_in_observe(verdict):
            _emit("allow")
            return
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
                    "agent_id": agent_id,
                    "tool_name": tool_name,
                    "arguments": args,
                    "session_id": session_id or None,
                    "thought_context": thought_context,
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
        if err.code in (401, 403):
            _log("auth_error", code=err.code, error=str(err))
            closed_perm = _fail_open_or_closed_permission("allow")
            if closed_perm == "allow":
                _emit(
                    "allow",
                    f"Watcher gate: gateway auth rejected (HTTP {err.code}); "
                    "fail-open applied — verify api_key in ~/.openeval/config.json.",
                )
            else:
                _emit(
                    LOCKOUT_PERMISSION,
                    f"[OPENEVAL AUTH ERROR]: Watcher Gateway rejected request (HTTP {err.code}). "
                    "Verify api_key in ~/.openeval/config.json.",
                )
            return
        _log("server_error", error=str(err))
        _handle_gateway_transport_failure()
        return
    except Exception as err:  # noqa: BLE001  # Boundary: network transport failure during evaluate call
        _log("server_unreachable", error=str(err))
        _handle_gateway_transport_failure()
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
