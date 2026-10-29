"""Unit tests for Distributed Sliding-Window Token Bucket Rate Limiter."""

import time

from server.rate_limiter import TokenBucketRateLimiter


def test_token_bucket_allows_within_capacity() -> None:
    limiter = TokenBucketRateLimiter(capacity=5, refill_rate_per_sec=2.0)
    session_id = "test-session-burst"

    # First 5 calls should succeed
    for i in range(5):
        res = limiter.check(session_id)
        assert res.allowed is True
        assert res.retry_after_ms == 0
        assert res.remaining_tokens == 4 - i

    # 6th call should be rejected
    res_rejected = limiter.check(session_id)
    assert res_rejected.allowed is False
    assert res_rejected.retry_after_ms > 0
    assert res_rejected.remaining_tokens == 0


def test_token_bucket_refills_over_time() -> None:
    limiter = TokenBucketRateLimiter(
        capacity=2, refill_rate_per_sec=10.0
    )  # 10 tokens per sec (1 every 100ms)
    session_id = "test-session-refill"

    # Drain bucket
    assert limiter.check(session_id).allowed is True
    assert limiter.check(session_id).allowed is True
    assert limiter.check(session_id).allowed is False

    # Sleep 150ms to allow at least 1 token refill
    time.sleep(0.15)
    res = limiter.check(session_id)
    assert res.allowed is True


def test_token_bucket_isolation_across_sessions() -> None:
    limiter = TokenBucketRateLimiter(capacity=2, refill_rate_per_sec=1.0)

    # Session A drained
    assert limiter.check("session-a").allowed is True
    assert limiter.check("session-a").allowed is True
    assert limiter.check("session-a").allowed is False

    # Session B is completely unaffected
    assert limiter.check("session-b").allowed is True
    assert limiter.check("session-b").allowed is True
