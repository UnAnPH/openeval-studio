"""Distributed Sliding-Window Token Bucket Rate Limiter for OpenEval Studio & Watcher.

Protects the real-time blocking path and LLM triage layer against runaway agent retry loops.
Operates with Redis when REDIS_URL is configured, falling back to a thread-safe
in-memory sliding token bucket for local single-node development.
"""

import contextlib
import os
import threading
import time
from typing import NamedTuple

try:
    import redis
except ImportError:
    redis = None  # type: ignore[assignment]


class RateLimitResult(NamedTuple):
    allowed: bool
    retry_after_ms: int
    remaining_tokens: int


class TokenBucketRateLimiter:
    """Sliding-window token bucket rate limiter for autonomous coding agent sessions."""

    def __init__(
        self,
        capacity: int = 30,
        refill_rate_per_sec: float = 5.0,
        redis_url: str | None = None,
    ) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        self._lock = threading.Lock()
        self._local_buckets: dict[str, tuple[float, float]] = {}  # {key: (tokens, last_refill_time)}

        self._redis_client: redis.Redis[bytes] | None = None
        target_redis_url = redis_url or os.getenv("REDIS_URL")
        if target_redis_url and redis is not None:
            try:
                self._redis_client = redis.from_url(target_redis_url)  # type: ignore[no-untyped-call]
                # Ping test
                self._redis_client.ping()
            except Exception:  # noqa: BLE001
                self._redis_client = None

    def check(self, key: str) -> RateLimitResult:
        """Check if an action is permitted under the token bucket budget.

        Deducts 1 token if allowed. If bucket is empty, returns allowed=False
        with the exact millisecond duration the caller should pause.
        """
        if self._redis_client is not None:
            return self._check_redis(key)
        return self._check_local(key)

    def _check_local(self, key: str) -> RateLimitResult:
        now = time.monotonic()
        with self._lock:
            tokens, last_time = self._local_buckets.get(key, (float(self.capacity), now))

            # Refill tokens proportional to elapsed time
            elapsed = now - last_time
            tokens = min(float(self.capacity), tokens + elapsed * self.refill_rate)

            if tokens >= 1.0:
                tokens -= 1.0
                self._local_buckets[key] = (tokens, now)
                return RateLimitResult(
                    allowed=True,
                    retry_after_ms=0,
                    remaining_tokens=int(tokens),
                )

            # Bucket exhausted
            wait_sec = (1.0 - tokens) / self.refill_rate
            self._local_buckets[key] = (tokens, now)
            return RateLimitResult(
                allowed=False,
                retry_after_ms=max(1, int(wait_sec * 1000)),
                remaining_tokens=0,
            )

    def _check_redis(self, key: str) -> RateLimitResult:
        """Atomic Redis Lua token bucket implementation."""
        assert self._redis_client is not None
        lua_script = """
        local key = KEYS[1]
        local cap = tonumber(ARGV[1])
        local rate = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])

        local data = redis.call('HMGET', key, 'tokens', 'last_time')
        local tokens = tonumber(data[1])
        local last_time = tonumber(data[2])

        if not tokens then
            tokens = cap
            last_time = now
        else
            local elapsed = math.max(0, now - last_time)
            tokens = math.min(cap, tokens + elapsed * rate)
        end

        if tokens >= 1.0 then
            tokens = tokens - 1.0
            redis.call('HMSET', key, 'tokens', tokens, 'last_time', now)
            redis.call('EXPIRE', key, math.ceil(cap / rate) * 2)
            return {1, 0, math.floor(tokens)}
        else
            local wait_sec = (1.0 - tokens) / rate
            return {0, math.ceil(wait_sec * 1000), 0}
        end
        """
        try:
            now = time.time()
            res = self._redis_client.eval(lua_script, 1, f"ratelimit:{key}", self.capacity, self.refill_rate, now)  # type: ignore[no-untyped-call]
            allowed = bool(res[0] == 1)
            retry_ms = int(res[1])
            remaining = int(res[2])
            return RateLimitResult(allowed=allowed, retry_after_ms=retry_ms, remaining_tokens=remaining)
        except Exception:  # noqa: BLE001
            # Fall back to local on transient Redis failure
            return self._check_local(key)

    def reset(self, key: str) -> None:
        """Reset a rate limit bucket for testing or manual operator override."""
        with self._lock:
            self._local_buckets.pop(key, None)
        if self._redis_client is not None:
            with contextlib.suppress(Exception):
                self._redis_client.delete(f"ratelimit:{key}")


_GLOBAL_LIMITER: TokenBucketRateLimiter | None = None


def get_rate_limiter() -> TokenBucketRateLimiter:
    """Retrieve shared singleton TokenBucketRateLimiter."""
    global _GLOBAL_LIMITER
    if _GLOBAL_LIMITER is None:
        _GLOBAL_LIMITER = TokenBucketRateLimiter()
    return _GLOBAL_LIMITER
