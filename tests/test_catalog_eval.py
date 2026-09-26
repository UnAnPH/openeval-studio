"""Tests for honest, genuine Inspect AI catalog evaluation worker."""

from unittest.mock import AsyncMock, patch

import pytest

from engine.judges import JudgeVerdict
from engine.llm_runner import LLMResponse, LLMUsage
from server.app import _run_catalog_eval_worker, resolve_catalog_task
from server.store import global_run_store


def test_resolve_catalog_tasks():
    """Verify catalog tasks resolve to real callable factories."""
    tasks = [
        "inspect_evals/humaneval",
        "inspect_evals/sycophancy",
        "inspect_evals/strong_reject",
        "inspect_evals/sec_qa",
        "inspect_evals/gdm_stealth",
    ]
    for task_id in tasks:
        fn = resolve_catalog_task(task_id)
        assert fn is not None, f"Failed resolving {task_id}"
        assert callable(fn)


@pytest.mark.asyncio
async def test_catalog_eval_fails_honestly_without_api_key(monkeypatch):
    """Verify that without API key, catalog evaluation fails honestly and never simulates a pass."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    record = global_run_store.create_run(
        task_id="inspect_evals/humaneval",
        model="gemini-3.1-flash-lite",
        provider="google",
    )

    await _run_catalog_eval_worker(
        run_id=record.run_id,
        task_id=record.task_id,
        model=record.model,
        provider="google",
        api_key=None,
    )

    updated = global_run_store.get_run(record.run_id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.passed is False
    assert "No API key" in (updated.failure_reason or "")
    assert len(updated.steps) == 0, "Steps must be empty on missing API key, no fake demo steps"


@pytest.mark.asyncio
async def test_catalog_eval_runs_genuine_llm_and_audits():
    """Verify genuine execution saves actual generated text and runs LLM audits."""
    record = global_run_store.create_run(
        task_id="inspect_evals/humaneval",
        model="gemini-3.1-flash-lite",
        provider="google",
    )

    mock_llm_res = LLMResponse(
        content="def solve():\n    return True\n",
        usage=LLMUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    )

    mock_audit = JudgeVerdict(
        metric_name="plan_adherence",
        score=0.9,
        passed=True,
        reasoning="Genuine LLM audit: model adhered to prompt.",
    )

    class _FakeSample:
        input = "Write a function solve() that returns True."

    class _FakeTask:
        dataset = [_FakeSample()]

    with (
        patch("server.app.resolve_catalog_task", return_value=lambda: _FakeTask()),
        patch("server.app.AsyncLLMRunner.generate", new_callable=AsyncMock) as mock_gen,
        patch(
            "server.app.TrajectoryJudges.audit_full_trajectory", new_callable=AsyncMock
        ) as mock_judges,
        patch("server.app.SchemingScanners.scan_all", new_callable=AsyncMock) as mock_scanners,
    ):
        mock_gen.return_value = mock_llm_res
        mock_judges.return_value = [mock_audit]
        mock_scanners.return_value = []

        await _run_catalog_eval_worker(
            run_id=record.run_id,
            task_id=record.task_id,
            model=record.model,
            provider="google",
            api_key="test-mock-key",
        )

    updated = global_run_store.get_run(record.run_id)
    assert updated is not None
    assert updated.status == "completed"
    assert len(updated.steps) == 1
    raw_obs = getattr(updated.steps[0], "observation", None)
    if raw_obs is None and isinstance(updated.steps[0], dict):
        raw_obs = updated.steps[0].get("observation")
    step_obs = str(raw_obs or "")
    assert "def solve():" in step_obs
    assert len(updated.audit_verdicts) >= 1
    raw_reason = getattr(updated.audit_verdicts[0], "reasoning", None)
    if raw_reason is None and isinstance(updated.audit_verdicts[0], dict):
        raw_reason = updated.audit_verdicts[0].get("reasoning")
    audit_reason = str(raw_reason or "")
    assert audit_reason == "Genuine LLM audit: model adhered to prompt."
    assert updated.total_tokens == 150
