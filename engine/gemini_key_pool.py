"""Centralized Google AI Studio (Gemini) API Key Pool with Automatic Failover.

Supports multiple Google AI Studio API keys with:
- Sticky active-key selection (ping-pong rotation)
- Zero-delay immediate failover to backup keys on HTTP 429 / RESOURCE_EXHAUSTED / Daily quota limits
- Per-key and per-(key, model) cooldown tracking (parsed from Google's retryDelay or sensible defaults)
- Masked key logging so secrets are never leaked in logs
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Mapping

from google import genai

logger = logging.getLogger("openeval.engine.gemini_key_pool")

BACKUP_ENV_VARS: tuple[str, ...] = (
    "GEMINI_API_KEYS",
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_BACKUP",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
    "GOOGLE_API_KEY",
    "GOOGLE_API_KEY_BACKUP",
    "GOOGLE_API_KEY_2",
)


def mask_api_key(key: str) -> str:
    """Return a safe, masked representation of an API key for logging."""
    clean = (key or "").strip()
    if not clean or clean == "mock-key":
        return clean or "<empty>"
    if len(clean) <= 8:
        return f"...{clean[-3:]}"
    return f"{clean[:4]}...{clean[-4:]}"


def is_gemini_rate_limit_error(err_str: str, status_code: int | None = None) -> bool:
    """Return True if an exception message or HTTP status indicates a rate limit or quota error."""
    if status_code == 429:
        return True
    lower = err_str.lower()
    return (
        "429" in err_str
        or "resource_exhausted" in lower
        or "quota exceeded" in lower
        or "rate limit" in lower
        or "rate_limit" in lower
        or "too many requests" in lower
        or "insufficient_quota" in lower
        or "credit_balance_exhausted" in lower
        or "perday" in lower
        or "daily" in lower
    )


def is_permanent_quota_error(err_str: str) -> bool:
    """Return True if the quota error is a daily cap or exhausted billing balance for that key."""
    lower = err_str.lower()
    return (
        "credit_balance_exhausted" in lower
        or "insufficient_quota" in lower
        or "daily" in err_str
        or "perday" in lower
    )


def parse_retry_delay_seconds(err_str: str) -> float:
    """Extract Google's suggested retry delay from an error string, or return a default cooldown."""
    if is_permanent_quota_error(err_str):
        return 3600.0
    delay_match = re.search(
        r"retry\s+(?:in|Delay['\"]?:\s*['\"])(\d+(?:\.\d+)?)s?",
        err_str,
        re.IGNORECASE,
    )
    if delay_match:
        return float(delay_match.group(1)) + 1.0
    return 60.0


def _split_keys(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def discover_gemini_keys(
    explicit_key: str | None = None,
    env: Mapping[str, str] | None = None,
    include_env_backups: bool = True,
) -> list[str]:
    """Discover all configured Gemini API keys in priority order, deduplicated."""
    env_map = env if env is not None else os.environ
    keys: list[str] = []

    def _add(candidate: str) -> None:
        if candidate and candidate != "mock-key" and candidate not in keys:
            keys.append(candidate)

    for k in _split_keys(explicit_key):
        _add(k)

    # If the caller explicitly passed "mock-key", keep offline mock mode isolated
    if explicit_key == "mock-key":
        return ["mock-key"]

    if include_env_backups:
        for var_name in BACKUP_ENV_VARS:
            for k in _split_keys(env_map.get(var_name)):
                _add(k)

    if not keys and explicit_key:
        return [explicit_key.strip()]

    return keys


class GeminiKeyPool:
    """Thread-safe and async-safe pool managing primary and backup Gemini API keys."""

    def __init__(
        self,
        explicit_key: str | None = None,
        env: Mapping[str, str] | None = None,
        include_env_backups: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._explicit_key = explicit_key
        self._env = env
        self._include_env_backups = include_env_backups
        self.keys: list[str] = discover_gemini_keys(
            explicit_key=explicit_key,
            env=env,
            include_env_backups=include_env_backups,
        )
        if not self.keys:
            self.keys = ["mock-key"]
        self._active_index: int = 0
        self._cooldown_until: dict[str, float] = {}
        self._model_cooldown_until: dict[tuple[str, str], float] = {}
        self._clients: dict[str, genai.Client] = {}

    def refresh_from_env(self) -> None:
        """Re-read environment variables to pick up newly set keys dynamically."""
        with self._lock:
            discovered = discover_gemini_keys(
                explicit_key=self._explicit_key,
                env=self._env,
                include_env_backups=self._include_env_backups,
            )
            if self._env is None and self._explicit_key is None:
                new_keys = discovered if discovered else ["mock-key"]
                if new_keys != self.keys:
                    current_active = (
                        self.keys[self._active_index % len(self.keys)] if self.keys else None
                    )
                    self.keys = new_keys
                    if current_active and current_active in self.keys:
                        self._active_index = self.keys.index(current_active)
                    else:
                        self._active_index = 0
                return
            if discovered:
                for k in discovered:
                    if k not in self.keys:
                        if self.keys == ["mock-key"]:
                            self.keys = [k]
                            self._active_index = 0
                        else:
                            self.keys.append(k)

    @property
    def has_real_key(self) -> bool:
        """Return True if at least one non-mock API key is available."""
        self.refresh_from_env()
        return any(k and k != "mock-key" for k in self.keys)

    @property
    def size(self) -> int:
        """Return total number of keys in the pool."""
        return len(self.keys)

    def _is_key_available(self, key: str, model: str | None, now: float) -> bool:
        if self._cooldown_until.get(key, 0.0) > now:
            return False
        return not (model and self._model_cooldown_until.get((key, model), 0.0) > now)

    def get_active_key(
        self,
        model: str | None = None,
        exclude_keys: set[str] | None = None,
    ) -> str:
        """Return the current active API key, rotating if on cooldown or excluded."""
        with self._lock:
            self.refresh_from_env()
            n = len(self.keys)
            if n == 0:
                return "mock-key"
            if n == 1:
                return self.keys[0]

            now = time.monotonic()
            excluded = exclude_keys or set()

            # 1. Prefer keys that are neither excluded nor on cooldown, starting at _active_index
            for offset in range(n):
                idx = (self._active_index + offset) % n
                candidate = self.keys[idx]
                if candidate not in excluded and self._is_key_available(candidate, model, now):
                    self._active_index = idx
                    return candidate

            # 2. If all non-excluded keys are on cooldown, pick the non-excluded key whose cooldown expires soonest
            non_excluded_indices = [
                (self._active_index + offset) % n
                for offset in range(n)
                if self.keys[(self._active_index + offset) % n] not in excluded
            ]
            if non_excluded_indices:
                best_idx = min(
                    non_excluded_indices,
                    key=lambda i: max(
                        self._cooldown_until.get(self.keys[i], 0.0),
                        self._model_cooldown_until.get((self.keys[i], model), 0.0)
                        if model
                        else 0.0,
                    ),
                )
                self._active_index = best_idx
                return self.keys[best_idx]

            # 3. Fallback to current active index
            return self.keys[self._active_index % n]

    def get_client(
        self,
        api_key: str | None = None,
        model: str | None = None,
        exclude_keys: set[str] | None = None,
    ) -> tuple[str, genai.Client]:
        """Return (api_key, genai.Client) for the active or requested key."""
        with self._lock:
            key = api_key or self.get_active_key(model=model, exclude_keys=exclude_keys)
            client = self._clients.get(key)
            if client is None:
                client = genai.Client(api_key=key)
                self._clients[key] = client
            return key, client

    def report_rate_limit(
        self,
        failed_key: str,
        err_str: str = "",
        model: str | None = None,
        tried_keys: set[str] | None = None,
    ) -> str | None:
        """Record a rate-limit/quota failure on `failed_key` and rotate to a backup key if available.

        Returns the next untried backup key if one exists, or None if all keys in the pool
        have been tried in the current cycle.
        """
        with self._lock:
            self.refresh_from_env()
            now = time.monotonic()
            cooldown_sec = parse_retry_delay_seconds(err_str)

            # Check if this is a model-specific tier restriction (e.g. limit: 0 on Free Tier)
            lower_err = err_str.lower()
            is_model_specific = model is not None and (
                "limit: 0" in lower_err or "limit:0" in lower_err
            )
            if is_model_specific and model is not None:
                self._model_cooldown_until[(failed_key, model)] = now + 3600.0
            else:
                self._cooldown_until[failed_key] = now + cooldown_sec

            failed_idx = self.keys.index(failed_key) if failed_key in self.keys else 0
            excluded = set(tried_keys or set())
            excluded.add(failed_key)

            # Find if there is another key not yet tried in this request
            n = len(self.keys)
            next_key: str | None = None
            next_idx: int | None = None

            # First look for an untried key that is not on cooldown
            for offset in range(1, n + 1):
                idx = (failed_idx + offset) % n
                candidate = self.keys[idx]
                if candidate not in excluded and self._is_key_available(candidate, model, now):
                    next_key = candidate
                    next_idx = idx
                    break

            # Otherwise, look for any untried key even if it had an older cooldown
            if next_key is None:
                for offset in range(1, n + 1):
                    idx = (failed_idx + offset) % n
                    candidate = self.keys[idx]
                    if candidate not in excluded:
                        next_key = candidate
                        next_idx = idx
                        break

            if next_key is not None and next_idx is not None:
                self._active_index = next_idx

                logger.warning(
                    "Gemini API limit hit on key[%d] (%s)%s. "
                    "Immediately failing over to backup key[%d] (%s) (key[%d] cooldown=%.1fs).",
                    failed_idx,
                    mask_api_key(failed_key),
                    f" for {model}" if model else "",
                    next_idx,
                    mask_api_key(next_key),
                    failed_idx,
                    cooldown_sec,
                )
                return next_key

            # Even if all keys were tried in this call, advance _active_index for future calls
            if n > 1:
                self._active_index = (failed_idx + 1) % n
            return None


_GLOBAL_KEY_POOL: GeminiKeyPool | None = None
_GLOBAL_LOCK = threading.Lock()


def get_global_gemini_key_pool() -> GeminiKeyPool:
    """Return the shared process-wide GeminiKeyPool singleton."""
    global _GLOBAL_KEY_POOL
    with _GLOBAL_LOCK:
        if _GLOBAL_KEY_POOL is None:
            _GLOBAL_KEY_POOL = GeminiKeyPool()
        else:
            _GLOBAL_KEY_POOL.refresh_from_env()
        return _GLOBAL_KEY_POOL


def reset_global_gemini_key_pool() -> None:
    """Reset the global key pool singleton (useful for tests)."""
    global _GLOBAL_KEY_POOL
    with _GLOBAL_LOCK:
        _GLOBAL_KEY_POOL = None
