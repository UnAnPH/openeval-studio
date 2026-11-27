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

# Load fallback config from ~/.openeval/config.json if not present in environment (e.g. GUI app launch)
_CONFIG_FILE = Path.home() / ".openeval" / "config.json"
if _CONFIG_FILE.is_file():
    try:
        _cfg = json.loads(_CONFIG_FILE.read_text())
        if not OPENEVAL_API_KEY and _cfg.get("api_key"):
            OPENEVAL_API_KEY = str(_cfg["api_key"]).strip()
        if "OPENEVAL_WATCHER_URL" not in os.environ and _cfg.get("watcher_url"):
            WATCHER_URL = str(_cfg["watcher_url"]).strip()
    except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError):
        pass
TIMEOUT_SEC = _safe_float(os.environ.get("OPENEVAL_WATCHER_TIMEOUT"), 1.5)
# Local-blacklist reporting must never stall the hook: keep it fast (default 0.35s).
REPORT_TIMEOUT_SEC = min(
    TIMEOUT_SEC, _safe_float(os.environ.get("OPENEVAL_WATCHER_REPORT_TIMEOUT"), 0.35)
)
# For beforeShellExecution, default to "ask" so Cursor prompts the user
SHELL_LOCKOUT_PERMISSION = os.environ.get("OPENEVAL_LOCKOUT_PERMISSION", "ask")
FAIL_MODE = (
    os.environ.get(
        "OPENEVAL_GATE_FAIL_MODE",
        os.environ.get("OPENEVAL_FAIL_MODE", os.environ.get("OPENEVAL_ON_FAILURE", "open")),
    )
    .strip()
    .lower()
)
LOG_PATH = Path.home() / ".openeval" / "cursor-watcher-gate.log"


def _fail_open_or_closed_permission(
    open_permission: str,
    *,
    pre_tool_use: bool = False,
) -> str:
    """Map fail-open allow to fail-closed deny/lockout when gateway transport fails."""
    if FAIL_MODE in ("closed", "fail-closed", "fail_closed", "deny"):
        if pre_tool_use:
            return "deny"
        return SHELL_LOCKOUT_PERMISSION
    return open_permission


def _emit_gateway_auth_error(
    status_code: int,
    *,
    pre_tool_use: bool,
    tool_name: str,
    cmd: str = "",
    path: str = "",
) -> None:
    """Handle HTTP 401/403 auth rejection while honoring fail-open vs fail-closed mode."""
    _log(
        "auth_error",
        code=status_code,
        cmd=cmd[:120] if cmd else None,
        path=path or None,
    )
    closed_perm = _fail_open_or_closed_permission("allow", pre_tool_use=pre_tool_use)
    if closed_perm == "allow":
        if pre_tool_use:
            _emit("allow")
        else:
            _emit(
                "allow",
                f"Watcher gate: gateway auth rejected (HTTP {status_code}); "
                "fail-open applied — verify api_key in ~/.openeval/config.json.",
            )
        return

    prompt_msg = (
        f"[OPENEVAL AUTH ERROR]: Watcher Gateway rejected request (HTTP {status_code}). "
        "Verify api_key in ~/.openeval/config.json."
    )
    consent_box = _format_consent_box(
        decision=closed_perm,
        reason=f"[AUTH_ERROR] Gateway returned HTTP {status_code}; check api_key.",
        tool_name=tool_name,
        cmd=cmd,
        path=path,
    )
    _emit(closed_perm, prompt_msg, consent_box=consent_box)


def _get_headers(req_id: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-ID": req_id or REQUEST_ID,
    }
    if OPENEVAL_API_KEY:
        headers["X-OpenEval-Key"] = OPENEVAL_API_KEY
        headers["Authorization"] = f"Bearer {OPENEVAL_API_KEY}"
    return headers


# Cursor's hook protocol consumes (pattern, reason) pairs.
LOCAL_BLACKLIST = LOCAL_BLACKLIST_REASONS

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
        _rotate_log_file_if_needed(LOG_PATH)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        redacted = {k: redact_payload(v) for k, v in fields.items()}
        row = {"ts": time.time(), "request_id": REQUEST_ID, "event": event, **redacted}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
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
            lock_file = SUBAGENT_REGISTRY_PATH.with_suffix(".lock")
            data = {}
            if lock_file.exists() or SUBAGENT_REGISTRY_PATH.exists():
                try:
                    with open(lock_file, "a+", encoding="utf-8") as lf:
                        try:
                            import fcntl

                            fcntl.flock(lf.fileno(), fcntl.LOCK_SH)
                        except (OSError, ValueError):
                            pass
                        if SUBAGENT_REGISTRY_PATH.exists():
                            data = json.loads(SUBAGENT_REGISTRY_PATH.read_text(encoding="utf-8"))
                        try:
                            import fcntl

                            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                        except (OSError, ValueError):
                            pass
                except (OSError, ValueError):
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
        except (OSError, ValueError):
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
            except OSError:
                pass
            current_data = {}
            if SUBAGENT_REGISTRY_PATH.exists():
                with contextlib.suppress(OSError, ValueError):
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
            except OSError:
                pass
    except OSError:
        pass


def _emit(
    permission: str,
    message: str = "",
    consent_box: str = "",
    disclosure: dict | None = None,
) -> None:
    try:
        if consent_box:
            redacted_box = redact_text(consent_box)
            sys.stderr.write(f"\n{redacted_box}\n\n")
            sys.stderr.flush()
            display_message = (
                f"{message}\n\n{redacted_box}"
                if message and message not in redacted_box
                else redacted_box
            )
        else:
            display_message = redact_text(message)

        out = {
            "permission": permission,
            "continue": permission == "allow",
            "user_message": display_message,
            "agent_message": display_message,
        }
        if disclosure:
            out["consent_disclosure"] = redact_payload(disclosure)

        print(json.dumps(out, ensure_ascii=False))
        sys.stdout.flush()
    except BrokenPipeError:
        pass
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


def _extract_cursor_thought_context(payload: dict, conversation_id: str) -> str:
    """Extract real prompt/thought context (never bare conversation UUIDs)."""
    direct = str(
        payload.get("thought_context")
        or payload.get("thoughtContext")
        or payload.get("thought")
        or payload.get("thinking")
        or payload.get("userPrompt")
        or payload.get("user_prompt")
        or payload.get("prompt")
        or ""
    ).strip()
    if direct and not re.fullmatch(r"[0-9a-fA-F-]{8,64}", direct):
        return direct[:1500]

    transcript_candidate: Path | None = None
    raw_tp = payload.get("transcript_path") or payload.get("transcriptPath")
    if isinstance(raw_tp, str) and raw_tp.strip():
        p = Path(raw_tp.strip())
        if p.is_file():
            transcript_candidate = p
    if transcript_candidate is None and conversation_id:
        clean_cid = re.sub(r"[^a-zA-Z0-9_-]", "", conversation_id)
        if clean_cid:
            for found in (Path.home() / ".cursor" / "projects").glob(
                f"*/agent-transcripts/{clean_cid}/{clean_cid}.jsonl"
            ):
                if found.is_file():
                    transcript_candidate = found
                    break

    if transcript_candidate is None:
        return ""

    try:
        with transcript_candidate.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 16384), os.SEEK_SET)
            lines = fh.read().decode("utf-8", errors="ignore").splitlines()
        latest_user = ""
        latest_assistant = ""
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            role = str(obj.get("role") or "").lower()
            content = (obj.get("message") or {}).get("content")
            text_parts: list[str] = []
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                        text_parts.append(str(item["text"]))
            joined = re.sub(r"</?user_query>", "", "\n".join(text_parts)).strip()
            if not joined:
                continue
            if role == "user" and not latest_user:
                latest_user = joined[:800]
            elif role == "assistant" and not latest_assistant:
                latest_assistant = joined[:600]
            if latest_user and latest_assistant:
                break
        if latest_user and latest_assistant:
            return f"[user]: {latest_user}\n[assistant]: {latest_assistant}"
        if latest_user:
            return f"[user]: {latest_user}"
        if latest_assistant:
            return f"[assistant]: {latest_assistant}"
    except OSError:
        pass
    return ""


def _report_to_watcher(
    *,
    tool_name: str,
    args: dict,
    conversation_id: str,
    cmd: str,
    provenance: dict | None = None,
    thought_context: str = "",
) -> dict[str, Any] | None:
    """Best-effort POST so Live Stream / Sessions see blocked actions.

    Returns the gateway verdict when the report succeeds so callers can honor
    observe-mode (recorded but allowed) decisions; ``None`` on any failure.
    """
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
        with urllib.request.urlopen(req, timeout=REPORT_TIMEOUT_SEC) as resp:
            verdict = json.loads(resp.read().decode("utf-8"))
            _log(
                "server_verdict",
                decision=str(verdict.get("decision", "")).lower(),
                risk_score=float(verdict.get("risk_score") or 0.0),
                shadow_decision=verdict.get("shadow_decision"),
                mode_applied=verdict.get("mode_applied"),
                source="report_to_watcher",
            )
            return verdict if isinstance(verdict, dict) else None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as err:
        _log("server_unreachable", error=str(err), source="report_to_watcher")
        return None


def _report_tool_result(
    payload: dict,
    conversation_id: str,
    tool_name: str,
    hook_event: str,
) -> None:
    """Report post-execution output from afterShellExecution / postToolUse to /api/v1/watcher/result."""
    result_url = os.environ.get(
        "OPENEVAL_WATCHER_RESULT_URL",
        WATCHER_URL.replace("/api/watcher/evaluate", "/api/v1/watcher/result")
        .replace("/api/v1/watcher/evaluate", "/api/v1/watcher/result")
        .replace("/api/v1/watcher/gate", "/api/v1/watcher/result"),
    )
    tool_result = (
        payload.get("tool_output")
        or payload.get("toolOutput")
        or payload.get("tool_result")
        or payload.get("toolResult")
        or payload.get("result")
        or {}
    )
    stdout = ""
    stderr = ""
    exit_code = 0
    if isinstance(tool_result, str):
        stdout = tool_result
    elif isinstance(tool_result, dict):
        stdout = str(
            tool_result.get("stdout")
            or tool_result.get("output")
            or tool_result.get("content")
            or ""
        )
        stderr = str(tool_result.get("stderr") or "")
        exit_code = _safe_int(
            tool_result.get("exitCode") or tool_result.get("exit_code") or payload.get("exit_code"),
            0,
        )

    if not stdout and payload.get("output") is not None:
        stdout = str(payload.get("output") or "")
    if not stderr and payload.get("stderr") is not None:
        stderr = str(payload.get("stderr") or "")

    err = str(payload.get("error") or payload.get("error_message") or "")
    if not err and hook_event == "postToolUseFailure":
        err = stderr or stdout or f"{tool_name or 'Tool'} execution failed"
    if err and not stderr:
        stderr = err
    if payload.get("exit_code") is not None or payload.get("exitCode") is not None:
        exit_code = _safe_int(payload.get("exit_code") or payload.get("exitCode"), exit_code)
    if err and exit_code == 0:
        exit_code = 1

    if not stdout and not stderr and not err:
        label = tool_name or ("Shell" if hook_event == "afterShellExecution" else "Tool")
        stdout = f"{label} completed"

    req_body = {
        "session_id": _session_id(conversation_id),
        "agent_id": "cursor",
        "tool_name": tool_name or ("Shell" if hook_event == "afterShellExecution" else "unknown"),
        "stdout": redact_text(stdout)[:4000],
        "stderr": redact_text(stderr)[:4000],
        "exit_code": exit_code,
        "error": redact_text(err)[:1000] if err else None,
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
        _log("posttool_result_reported", hook_event=hook_event, session_id=req_body["session_id"])
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as post_err:
        _log("posttool_result_error", hook_event=hook_event, error=str(post_err))


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
            verdict = _report_to_watcher(
                tool_name="Shell",
                args={"command": cmd, "CommandLine": cmd},
                conversation_id=conversation_id,
                cmd=cmd,
                provenance=provenance,
                thought_context=thought_context,
            )
            if _verdict_allows_in_observe(verdict):
                _emit("allow")
                return
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
        verdict = _report_to_watcher(
            tool_name="Shell",
            args={"command": cmd, "CommandLine": cmd},
            conversation_id=conversation_id,
            cmd=cmd,
            provenance=provenance,
            thought_context=thought_context,
        )
        if _verdict_allows_in_observe(verdict):
            _emit("allow")
            return
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
        verdict = _report_to_watcher(
            tool_name="Shell",
            args={"command": cmd, "CommandLine": cmd},
            conversation_id=conversation_id,
            cmd=cmd,
            provenance=provenance,
            thought_context=thought_context,
        )
        if _verdict_allows_in_observe(verdict):
            _emit("allow")
            return
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
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as err:
        if isinstance(err, urllib.error.HTTPError) and err.code == 422:
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
            prompt_msg = (
                f"[OPENEVAL SCHEMA DRIFT]: {drift_msg}. Execution frozen pending operator review."
            )
            consent_box = _format_consent_box(
                decision=SHELL_LOCKOUT_PERMISSION,
                reason="[SCHEMA_DRIFT] Watcher gate payload schema drift detected.",
                tool_name="Shell",
                cmd=cmd,
            )
            _emit(SHELL_LOCKOUT_PERMISSION, prompt_msg, consent_box=consent_box)
            return
        if isinstance(err, urllib.error.HTTPError) and err.code in (401, 403):
            _emit_gateway_auth_error(
                err.code,
                pre_tool_use=False,
                tool_name="Shell",
                cmd=cmd,
            )
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
        closed_perm = _fail_open_or_closed_permission("allow", pre_tool_use=False)
        if closed_perm == "allow":
            _emit("allow", "Watcher gate: gateway timeout/unreachable (transient fail-open)")
        else:
            _emit(
                closed_perm,
                "Watcher gate: gateway unreachable (fail-closed mode)",
            )
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
        verdict = _report_to_watcher(
            tool_name=tool_name,
            args={"path": path} if path else {},
            conversation_id=conversation_id,
            cmd="",
            provenance=provenance,
            thought_context=thought_context,
        )
        if _verdict_allows_in_observe(verdict):
            _emit("allow")
            return
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
    if is_sub and path and _is_protected_credential_read_path(path):
        verdict = _report_to_watcher(
            tool_name=tool_name,
            args={"path": path},
            conversation_id=conversation_id,
            cmd="",
            provenance=provenance,
            thought_context=thought_context,
        )
        if _verdict_allows_in_observe(verdict):
            _emit("allow")
            return
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
        if path and _is_protected_credential_read_path(path):
            verdict = _report_to_watcher(
                tool_name=tool_name,
                args={"path": path},
                conversation_id=conversation_id,
                cmd="",
                provenance=provenance,
                thought_context=thought_context,
            )
            if _verdict_allows_in_observe(verdict):
                _emit("allow")
                return
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
        verdict = _report_to_watcher(
            tool_name=tool_name,
            args={"path": path},
            conversation_id=conversation_id,
            cmd="",
            provenance=provenance,
            thought_context=thought_context,
        )
        if _verdict_allows_in_observe(verdict):
            _emit("allow")
            return
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
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as err:
            if isinstance(err, urllib.error.HTTPError) and err.code in (401, 403):
                _emit_gateway_auth_error(
                    err.code,
                    pre_tool_use=True,
                    tool_name=tool_name,
                    path=path,
                )
                return
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
            closed_perm = _fail_open_or_closed_permission("allow", pre_tool_use=True)
            if closed_perm == "allow":
                _emit("allow")
            else:
                _emit("deny", "Watcher gate: gateway unreachable (fail-closed mode)")
            return

    # All standard project file edits and benign tools are allowed
    _emit("allow")


def main() -> None:
    try:
        raw = sys.stdin.read(MAX_STDIN_BYTES + 1)
    except Exception as err:  # noqa: BLE001
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
        _emit(SHELL_LOCKOUT_PERMISSION, "Payload exceeds maximum allowed size (2 MiB)")
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        _log("stdin_json_error", error=str(err))
        _emit(SHELL_LOCKOUT_PERMISSION, "Invalid stdin JSON")
        return

    if not isinstance(payload, dict):
        _emit(SHELL_LOCKOUT_PERMISSION, "Invalid stdin payload: expected JSON object")
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
    thought_context = _extract_cursor_thought_context(payload, conversation_id)

    # Route based on hook event
    if hook_event in ("afterShellExecution", "postToolUse", "postToolUseFailure"):
        _report_tool_result(payload, conversation_id, tool_name, hook_event)
        print("{}")
        sys.stdout.flush()
    elif hook_event == "beforeShellExecution" or (cmd and hook_event != "preToolUse"):
        _handle_shell_execution(cmd, conversation_id, provenance, thought_context)
    elif hook_event == "preToolUse":
        if not tool_name:
            tool_name = "Shell" if cmd else "unknown"
        _handle_pre_tool_use(tool_name, tool_input, conversation_id, provenance, thought_context)
    else:
        # Unknown hook event: fail-open
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
        except Exception:  # noqa: BLE001
            pass
        sys.exit(0)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        _log("unhandled_exception", error=str(exc))
        if FAIL_MODE in ("closed", "fail-closed", "fail_closed", "deny"):
            _emit("deny", f"Unhandled error in gate: {exc}")
        else:
            _emit("allow", f"Unhandled error in gate (fail-open): {exc}")
