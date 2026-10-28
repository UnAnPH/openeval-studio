"""Secret & PII Redaction Pipeline for OpenEval Studio.

Redacts raw credentials and API secrets from text payloads, logs, database records,
and model prompts into typed replacement tokens:
- [REDACTED:OPENAI_API_KEY]
- [REDACTED:OPENCODE_API_KEY]
- [REDACTED:ANTHROPIC_API_KEY]
- [REDACTED:AWS_ACCESS_KEY]
- [REDACTED:GITHUB_TOKEN]
- [REDACTED:PRIVATE_KEY]
- [REDACTED:ENV_SECRET]
- [REDACTED:BEARER_TOKEN]
"""

from __future__ import annotations

import re
from typing import Any, TypeVar

# 1. Private keys (PEM blocks)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9_-]+ )?PRIVATE KEY-----",
    re.MULTILINE,
)

# 2. OpenCode API keys (starts with oc_sk_)
_OPENCODE_KEY_RE = re.compile(
    r"\boc_sk_[a-zA-Z0-9_-]+\b"
)

# 3. Anthropic API keys (must be evaluated before generic sk- to prevent collision)
_ANTHROPIC_KEY_RE = re.compile(
    r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b"
)

# 3. OpenAI API keys
_OPENAI_KEY_RE = re.compile(
    r"\b(?:sk-proj-[a-zA-Z0-9_-]{20,}|sk-[a-zA-Z0-9]{20,})\b"
)

# 4. GitHub tokens
_GITHUB_TOKEN_RE = re.compile(
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}\b|\bgithub_pat_[a-zA-Z0-9_]{82}\b"
)

# 5. AWS Access Key IDs
_AWS_KEY_ID_RE = re.compile(
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
)

# 6. AWS Secret Access Key assignments (with preserved quotes for shell/JSON)
_AWS_SECRET_RE = re.compile(
    r"(?i)(['\"]?AWS_SECRET_ACCESS_KEY['\"]?\s*[:=]\s*)(['\"]?)[a-zA-Z0-9/+=]{20,}\2"
)

# 7. Bearer Authorization Tokens
_BEARER_TOKEN_RE = re.compile(
    r"(?i)\b(Bearer\s+)[a-zA-Z0-9_\-\.]{16,}\b"
)

# 8. Generic sensitive environment variable assignments (with preserved quotes for shell/JSON)
_ENV_SECRET_RE = re.compile(
    r"(?i)(['\"]?(?:SECRET_KEY|API_SECRET|PRIVATE_KEY|DATABASE_URL|DB_PASSWORD|POSTGRES_PASSWORD|PASSWORD|PASSWD|AUTH_TOKEN|ACCESS_TOKEN)['\"]?\s*[:=]\s*)(['\"]?)(?!\[REDACTED:)[^\s'\"\n]{8,}\2"
)


def redact_text(text: str) -> str:
    """Redact sensitive credentials from text while preserving typed classification tokens."""
    if not text:
        return text

    # Apply in strict specificity order
    redacted = _PRIVATE_KEY_RE.sub("[REDACTED:PRIVATE_KEY]", text)
    redacted = _OPENCODE_KEY_RE.sub("[REDACTED:OPENCODE_API_KEY]", redacted)
    redacted = _ANTHROPIC_KEY_RE.sub("[REDACTED:ANTHROPIC_API_KEY]", redacted)
    redacted = _OPENAI_KEY_RE.sub("[REDACTED:OPENAI_API_KEY]", redacted)
    redacted = _GITHUB_TOKEN_RE.sub("[REDACTED:GITHUB_TOKEN]", redacted)
    redacted = _AWS_KEY_ID_RE.sub("[REDACTED:AWS_ACCESS_KEY]", redacted)
    redacted = _AWS_SECRET_RE.sub(r"\g<1>\g<2>[REDACTED:AWS_ACCESS_KEY]\g<2>", redacted)
    redacted = _BEARER_TOKEN_RE.sub(r"\g<1>[REDACTED:BEARER_TOKEN]", redacted)
    redacted = _ENV_SECRET_RE.sub(r"\g<1>\g<2>[REDACTED:ENV_SECRET]\g<2>", redacted)
    return redacted


T = TypeVar("T")

_SENSITIVE_DICT_KEYS = (
    "SECRET_KEY",
    "API_SECRET",
    "DATABASE_URL",
    "DB_PASSWORD",
    "POSTGRES_PASSWORD",
    "PASSWORD",
    "PASSWD",
    "AUTH_TOKEN",
    "ACCESS_TOKEN",
)


def redact_payload(obj: T) -> T:
    """Recursively scrub sensitive credential tokens from dicts, lists, and string payloads."""
    if isinstance(obj, str):
        return redact_text(obj)  # type: ignore[return-value]
    if isinstance(obj, dict):
        result: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(v, str):
                redacted_v = redact_text(v)
                if redacted_v != v:
                    result[k] = redacted_v
                    continue
                k_upper = str(k).upper()
                if any(kw in k_upper for kw in ("AWS_SECRET_ACCESS_KEY", "SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY")):
                    result[k] = "[REDACTED:AWS_ACCESS_KEY]"
                elif "GITHUB_TOKEN" in k_upper or "GH_TOKEN" in k_upper or "GITHUB_PAT" in k_upper:
                    result[k] = "[REDACTED:GITHUB_TOKEN]"
                elif "OPENCODE_API_KEY" in k_upper:
                    result[k] = "[REDACTED:OPENCODE_API_KEY]"
                elif "OPENAI_API_KEY" in k_upper:
                    result[k] = "[REDACTED:OPENAI_API_KEY]"
                elif "ANTHROPIC_API_KEY" in k_upper:
                    result[k] = "[REDACTED:ANTHROPIC_API_KEY]"
                elif "PRIVATE_KEY" in k_upper:
                    result[k] = "[REDACTED:PRIVATE_KEY]"
                elif "BEARER_TOKEN" in k_upper:
                    result[k] = "[REDACTED:BEARER_TOKEN]"
                elif any(s in k_upper for s in _SENSITIVE_DICT_KEYS):
                    result[k] = "[REDACTED:ENV_SECRET]"
                else:
                    result[k] = v
            else:
                result[k] = redact_payload(v)
        return result  # type: ignore[return-value]
    if isinstance(obj, list):
        return [redact_payload(v) for v in obj]  # type: ignore[return-value]
    if isinstance(obj, tuple):
        return tuple(redact_payload(v) for v in obj)  # type: ignore[return-value]
    return obj
