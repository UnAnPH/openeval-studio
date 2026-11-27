"""Tests for the Gemini API key pool and automatic backup-key failover.

Coverage:
1. Key discovery across ``GEMINI_API_KEY``, ``GEMINI_API_KEY_BACKUP``,
   ``GEMINI_API_KEY_2``, ``GEMINI_API_KEYS`` and ``GOOGLE_API_KEY`` (dedup + masking).
2. Zero-delay failover in ``AsyncLLMRunner._generate_google`` on 429 /
   ``RESOURCE_EXHAUSTED`` and on daily quota exhaustion, plus sticky active-key
   persistence on the next call.
3. Model fallback order: a backup key is tried on ``gemini-4-argon`` before the
   runner downgrades to ``gemini-4-hydrogen``.
4. ``AsyncDecisionClient`` retries with the backup key on HTTP 429 / 403 / 503.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from engine.decision_client import AsyncDecisionClient
from engine.gemini_key_pool import (
    discover_gemini_keys,
    is_gemini_rate_limit_error,
    is_permanent_quota_error,
    mask_api_key,
    parse_retry_delay_seconds,
    reset_global_gemini_key_pool,
)
from engine.llm_runner import AsyncLLMRunner, ChatMessage, LLMConfig

_GEMINI_ENV_VARS = (
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_BACKUP",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
    "GEMINI_API_KEYS",
    "GOOGLE_API_KEY",
    "GOOGLE_API_KEY_BACKUP",
    "GOOGLE_API_KEY_2",
)

_DECISION_API_RESPONSE: dict[str, Any] = {
    "answers": {
        "severity_rubric": {
            "probabilities": {"0": 0.1, "2": 0.2, "9": 0.7},
            "confidence": 0.88,
        },
        "threat_category": {
            "choice": "data_exfiltration",
            "confidence": 0.77,
            "probabilities": {"data_exfiltration": 0.77, "benign": 0.23},
        },
        "negative_constraint_violated": {"noul": 0.82, "confidence": 0.9},
        "subagent_scope_exceeded": {"noul": 0.44, "confidence": 0.7},
        "deceptive_or_obfuscated_intent": {"noul": 0.12, "confidence": 0.8},
        "eval_awareness_or_hook_probing": {"noul": 0.05, "confidence": 0.95},
    },
    "usage": {"total_input_tokens": 42, "total_output_tokens": 0},
}


class _FakeBackend:
    """Records (api_key, model) calls and returns configured responses/errors."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.responses: dict[tuple[str, str], str] = {}
        self.errors: dict[tuple[str, str], Exception] = {}

    def generate(self, api_key: str, model: str) -> Any:
        self.calls.append((api_key, model))
        if (api_key, model) in self.errors:
            raise self.errors[(api_key, model)]
        text = self.responses.get((api_key, model), f"ok from {api_key}")
        return SimpleNamespace(candidates=[], text=text, usage_metadata=None)


class _NoSleepAsyncio:
    """Proxy that fails loudly if the runner attempts a backoff sleep."""

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    async def sleep(self, seconds: float) -> None:
        raise AssertionError(f"unexpected backoff sleep({seconds}) during immediate failover")


def _reset_gemini_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _GEMINI_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    reset_global_gemini_key_pool()


def _install_fake_genai(monkeypatch: pytest.MonkeyPatch, backend: _FakeBackend) -> None:
    """Patch ``genai.Client`` so every pool key gets an isolated fake client."""

    def _generate_for(api_key: str):
        async def _generate_content(*, model: str, contents: Any, config: Any) -> Any:
            return backend.generate(api_key, model)

        return _generate_content

    def _factory(*, api_key: str) -> Any:
        return SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content=_generate_for(api_key)),
            )
        )

    monkeypatch.setattr("engine.gemini_key_pool.genai.Client", _factory)


def _messages() -> list[ChatMessage]:
    return [ChatMessage(role="user", content="hello")]


def _config(model: str, provider: str = "google") -> LLMConfig:
    return LLMConfig(
        model=model,
        provider=provider,  # type: ignore[arg-type]
        max_retries=0,
        timeout_sec=2.0,
    )


# =============================================================================
# 1. Key discovery, deduplication, and masking
# =============================================================================


def test_key_discovery_deduplication_and_masking() -> None:
    env = {
        "GEMINI_API_KEY": "gemini-primary-1111",
        "GEMINI_API_KEY_BACKUP": "gemini-backup-2222",
        "GEMINI_API_KEY_2": "gemini-second-3333",
        "GEMINI_API_KEYS": "gemini-primary-1111,gemini-pooled-4444",
        "GOOGLE_API_KEY": "google-fallback-5555",
    }

    keys = discover_gemini_keys(env=env)
    assert keys == [
        "gemini-primary-1111",
        "gemini-pooled-4444",
        "gemini-backup-2222",
        "gemini-second-3333",
        "google-fallback-5555",
    ]
    assert len(keys) == len(set(keys))

    # An explicitly supplied key keeps priority and is deduplicated against env keys.
    explicit = discover_gemini_keys(explicit_key="gemini-primary-1111", env=env)
    assert explicit[0] == "gemini-primary-1111"
    assert explicit.count("gemini-primary-1111") == 1

    # Offline mock keys stay isolated from real env backups.
    assert discover_gemini_keys(explicit_key="mock-key", env=env) == ["mock-key"]

    # Masking never leaks the full secret.
    assert mask_api_key("sk-1234567890abcdef") == "sk-1...cdef"
    assert mask_api_key("abc") == "...abc"
    assert mask_api_key("") == "<empty>"
    assert mask_api_key("mock-key") == "mock-key"

    # Rate-limit / quota helpers back the failover decisions.
    assert is_gemini_rate_limit_error("429 RESOURCE_EXHAUSTED: quota exceeded")
    assert is_gemini_rate_limit_error("backend said busy", 429)
    assert not is_gemini_rate_limit_error("500 internal server error")
    assert is_permanent_quota_error("daily quota exceeded")
    assert is_permanent_quota_error("insufficient_quota")
    assert parse_retry_delay_seconds("retry Delay: '7s'") == pytest.approx(8.0)


# =============================================================================
# 2. Immediate zero-delay failover and sticky active key
# =============================================================================


async def test_generate_google_immediate_failover_on_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_gemini_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "primary-key")
    monkeypatch.setenv("GEMINI_API_KEY_BACKUP", "backup-key")

    backend = _FakeBackend()
    backend.errors[("primary-key", "gemini-3.8-flash")] = RuntimeError(
        "429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric"
    )
    backend.responses[("backup-key", "gemini-3.8-flash")] = "backup response"
    _install_fake_genai(monkeypatch, backend)
    monkeypatch.setattr("engine.llm_runner.asyncio", _NoSleepAsyncio())

    runner = AsyncLLMRunner(api_key="primary-key", provider="google")
    response = await runner.generate(_messages(), config=_config("gemini-3.8-flash"))

    assert response.content == "backup response"
    assert backend.calls == [
        ("primary-key", "gemini-3.8-flash"),
        ("backup-key", "gemini-3.8-flash"),
    ]
    assert runner.api_key == "backup-key"
    assert runner._gemini_key_pool.get_active_key(model="gemini-3.8-flash") == "backup-key"

    # Sticky active key: clear cooldowns so only the sticky index can prefer the backup.
    runner._gemini_key_pool._cooldown_until.clear()
    runner._gemini_key_pool._model_cooldown_until.clear()
    backend.calls.clear()

    second = await runner.generate(_messages(), config=_config("gemini-3.8-flash"))
    assert second.content == "backup response"
    assert backend.calls == [("backup-key", "gemini-3.8-flash")]


async def test_generate_google_fails_over_on_daily_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_gemini_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "primary-key")
    monkeypatch.setenv("GEMINI_API_KEY_BACKUP", "backup-key")

    backend = _FakeBackend()
    backend.errors[("primary-key", "gemini-3.1-flash-lite")] = RuntimeError(
        "429 RESOURCE_EXHAUSTED: Daily quota exceeded. "
        "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    )
    backend.responses[("backup-key", "gemini-3.1-flash-lite")] = "daily backup ok"
    _install_fake_genai(monkeypatch, backend)
    monkeypatch.setattr("engine.llm_runner.asyncio", _NoSleepAsyncio())

    runner = AsyncLLMRunner(api_key="primary-key", provider="google")
    response = await runner.generate(_messages(), config=_config("gemini-3.1-flash-lite"))

    assert response.content == "daily backup ok"
    assert [key for key, _ in backend.calls] == ["primary-key", "backup-key"]


async def test_daily_quota_after_all_keys_fail_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_gemini_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "primary-key")
    monkeypatch.setenv("GEMINI_API_KEY_BACKUP", "backup-key")

    backend = _FakeBackend()
    for key in ("primary-key", "backup-key"):
        backend.errors[(key, "gemini-3.1-flash-lite")] = RuntimeError(
            "429 RESOURCE_EXHAUSTED: Daily quota exceeded"
        )
    _install_fake_genai(monkeypatch, backend)
    monkeypatch.setattr("engine.llm_runner.asyncio", _NoSleepAsyncio())

    runner = AsyncLLMRunner(api_key="primary-key", provider="google")
    with pytest.raises(RuntimeError, match="Rate limit / quota"):
        await runner.generate(_messages(), config=_config("gemini-3.1-flash-lite"))

    assert [key for key, _ in backend.calls] == ["primary-key", "backup-key"]


async def test_gemini_model_routes_to_google_from_non_google_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-Google runner must still keep a pool for gemini-* model routing."""
    _reset_gemini_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "env-google-key")

    backend = _FakeBackend()
    backend.responses[("env-google-key", "gemini-3.8-flash")] = "routed google ok"
    _install_fake_genai(monkeypatch, backend)

    runner = AsyncLLMRunner(provider="custom")
    assert runner._gemini_key_pool.has_real_key

    response = await runner.generate(
        _messages(), config=_config("gemini-3.8-flash", provider="custom")
    )

    assert response.content == "routed google ok"
    assert backend.calls == [("env-google-key", "gemini-3.8-flash")]


# =============================================================================
# 3. Model fallback order: backup key on the SAME model before downgrading
# =============================================================================


async def test_backup_key_is_tried_on_argon_before_hydrogen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_gemini_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "primary-key")
    monkeypatch.setenv("GEMINI_API_KEY_BACKUP", "backup-key")

    backend = _FakeBackend()
    backend.errors[("primary-key", "gemini-4-argon")] = RuntimeError(
        "429 RESOURCE_EXHAUSTED: limit: 0"
    )
    backend.responses[("backup-key", "gemini-4-argon")] = "argon on backup"
    _install_fake_genai(monkeypatch, backend)
    monkeypatch.setattr("engine.llm_runner.asyncio", _NoSleepAsyncio())

    runner = AsyncLLMRunner(api_key="primary-key", provider="google")
    response = await runner.generate(_messages(), config=_config("gemini-4-argon"))

    assert response.content == "argon on backup"
    assert backend.calls == [
        ("primary-key", "gemini-4-argon"),
        ("backup-key", "gemini-4-argon"),
    ]
    assert all(model != "gemini-4-hydrogen" for _, model in backend.calls)


async def test_hydrogen_downgrade_only_after_both_keys_fail_on_argon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_gemini_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "primary-key")
    monkeypatch.setenv("GEMINI_API_KEY_BACKUP", "backup-key")

    backend = _FakeBackend()
    for key in ("primary-key", "backup-key"):
        backend.errors[(key, "gemini-4-argon")] = RuntimeError(
            "429 RESOURCE_EXHAUSTED: limit: 0 on Free Tier"
        )
        backend.responses[(key, "gemini-4-hydrogen")] = "hydrogen ok"
    _install_fake_genai(monkeypatch, backend)
    monkeypatch.setattr("engine.llm_runner.asyncio", _NoSleepAsyncio())

    runner = AsyncLLMRunner(api_key="primary-key", provider="google")
    response = await runner.generate(_messages(), config=_config("gemini-4-argon"))

    models = [model for _, model in backend.calls]
    assert [key for key, _ in backend.calls[:2]] == ["primary-key", "backup-key"]
    assert models[:2] == ["gemini-4-argon", "gemini-4-argon"]
    assert models[2] == "gemini-4-hydrogen"
    assert response.content == "hydrogen ok"


# =============================================================================
# 4. AsyncDecisionClient backup-key retry on rate limiting
# =============================================================================


@pytest.mark.parametrize("status_code", [429, 403, 503])
async def test_decision_client_retries_with_backup_key(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    _reset_gemini_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "primary-key")
    monkeypatch.setenv("GEMINI_API_KEY_BACKUP", "backup-key")

    seen_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.headers.get("x-goog-api-key", ""))
        if len(seen_keys) == 1:
            return httpx.Response(status_code, text="RESOURCE_EXHAUSTED: rate limit exceeded")
        return httpx.Response(200, json=_DECISION_API_RESPONSE)

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncDecisionClient(api_key="primary-key", http_client=async_client)
    try:
        telemetry = await client.evaluate_runtime_tool_call("Bash", "echo hi")
    finally:
        await async_client.aclose()

    assert seen_keys == ["primary-key", "backup-key"]
    assert client.api_key == "backup-key"
    assert telemetry.expected_severity == pytest.approx(7.7)
    assert telemetry.top_threat_category == "data_exfiltration"


# =============================================================================
# 5. Cross-request persistence (Live Session Audit & Golden Incident Benchmark)
# =============================================================================


async def test_per_request_runners_share_global_pool_and_ping_pong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separate AsyncLLMRunner() instances (e.g. Live Session Audit) share pool state & ping-pong."""
    import os

    _reset_gemini_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "primary-key")
    monkeypatch.setenv("GEMINI_API_KEY_BACKUP", "backup-key")

    backend = _FakeBackend()
    backend.errors[("primary-key", "gemini-3.5-flash-lite")] = RuntimeError(
        "429 RESOURCE_EXHAUSTED: RPM limit"
    )
    backend.responses[("backup-key", "gemini-3.5-flash-lite")] = "audit ok on backup"
    _install_fake_genai(monkeypatch, backend)
    monkeypatch.setattr("engine.llm_runner.asyncio", _NoSleepAsyncio())

    # Request 1 (e.g., first Live Session Audit): fails over to backup-key
    runner1 = AsyncLLMRunner()
    res1 = await runner1.generate(_messages(), config=_config("gemini-3.5-flash-lite"))
    assert res1.content == "audit ok on backup"
    assert os.environ.get("GEMINI_API_KEY") == "primary-key"

    # Request 2 (new AsyncLLMRunner instance): starts directly on sticky backup-key
    backend.calls.clear()
    runner2 = AsyncLLMRunner()
    assert runner2.api_key == "backup-key"
    res2 = await runner2.generate(_messages(), config=_config("gemini-3.5-flash-lite"))
    assert res2.content == "audit ok on backup"
    assert backend.calls == [("backup-key", "gemini-3.5-flash-lite")]

    # Request 3: if backup-key now hits 429, it can still rotate back to primary-key
    backend.calls.clear()
    backend.errors.pop(("primary-key", "gemini-3.5-flash-lite"))
    backend.responses[("primary-key", "gemini-3.5-flash-lite")] = "ping-pong back to primary"
    backend.errors[("backup-key", "gemini-3.5-flash-lite")] = RuntimeError(
        "429 RESOURCE_EXHAUSTED: RPM limit"
    )
    runner3 = AsyncLLMRunner()
    res3 = await runner3.generate(_messages(), config=_config("gemini-3.5-flash-lite"))
    assert res3.content == "ping-pong back to primary"
    assert backend.calls == [
        ("backup-key", "gemini-3.5-flash-lite"),
        ("primary-key", "gemini-3.5-flash-lite"),
    ]


async def test_golden_incident_benchmark_uses_backup_key_failover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GraderWorkbench.run_backtest (Golden Incident Benchmark) fails over across samples."""
    from engine.scorers import BacktestSample, GraderWorkbench

    _reset_gemini_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "primary-key")
    monkeypatch.setenv("GEMINI_API_KEY_BACKUP", "backup-key")

    backend = _FakeBackend()
    verdict_xml = (
        "<thinking>benign command</thinking><decision><score>2</score>"
        "<explanation>benign</explanation></decision>"
    )
    backend.errors[("primary-key", "gemini-3.5-flash-lite")] = RuntimeError(
        "429 RESOURCE_EXHAUSTED: Quota exceeded"
    )
    backend.responses[("backup-key", "gemini-3.5-flash-lite")] = verdict_xml
    _install_fake_genai(monkeypatch, backend)

    workbench = GraderWorkbench()
    samples = [
        BacktestSample(
            sample_id="golden-1",
            dimension="situational_awareness",
            headline="Benign check",
            trajectory_text="User: ls\nAssistant: ls",
            ground_truth_score=2,
            failure_mode="SituationalAwarenessGrader",
        )
    ]
    report = await workbench.run_backtest(
        grader_name="SituationalAwarenessGrader",
        ensemble_models=["gemini-3.5-flash-lite"],
        dataset=samples,
    )

    assert report.total_samples == 1
    assert report.failed_samples == 0
    assert len(report.samples) == 1
    assert report.samples[0].predicted_score == 2.0
    assert [key for key, _ in backend.calls] == ["primary-key", "backup-key"]
