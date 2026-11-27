#!/usr/bin/env python3
"""Canonical shared core for the OpenEval runtime gate hook scripts.

This file is the single source of truth for the code that is embedded between
the ``OPENEVAL_SHARED_HOOK_CORE`` markers in every hook script. The hooks stay
100% standalone (single-file, curl-installable): ``scripts/build_hooks.py``
copies the marked block into each hook, so nothing imports this module at
runtime.

Edit the shared helpers here, then run ``python3 scripts/build_hooks.py``.
``python3 scripts/build_hooks.py --check`` (wired into ``make check``) fails if
any hook block has drifted from this file.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path

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
