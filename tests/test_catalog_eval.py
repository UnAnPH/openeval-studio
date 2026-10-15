"""Tests for honest, genuine Inspect AI catalog evaluation worker."""

import pytest

from engine.judges import JudgeVerdict
from engine.llm_runner import ChatMessage, LLMConfig, LLMResponse, LLMUsage
from server.routes.eval import _run_catalog_eval_worker, resolve_catalog_task
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
async def test_catalog_eval_fails_honestly_without_api_key():
    """Verify that without API key, catalog evaluation fails honestly and never simulates a pass."""
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
        env={},
    )

    updated = global_run_store.get_run(record.run_id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.passed is False
    assert "No API key" in (updated.failure_reason or "")
    assert len(updated.steps) == 0, "Steps must be empty on missing API key, no fake demo steps"


@pytest.mark.asyncio
async def test_catalog_eval_runs_genuine_llm_and_audits():
    """Verify genuine execution saves actual generated text and runs LLM audits.

    Dropping the injected completion text would fail the observation assertion.
    """
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

    class _Runner:
        async def generate(
            self,
            messages: list[ChatMessage],
            config: LLMConfig | None = None,
        ) -> LLMResponse:
            return mock_llm_res

    async def _audit(*_args: object, **_kwargs: object) -> list[JudgeVerdict]:
        return [mock_audit]

    async def _scan(*_args: object, **_kwargs: object) -> list[JudgeVerdict]:
        return []

    await _run_catalog_eval_worker(
        run_id=record.run_id,
        task_id=record.task_id,
        model=record.model,
        provider="google",
        api_key="test-mock-key",
        task_resolver=lambda _task_id: lambda: _FakeTask(),
        runner=_Runner(),
        audit_trajectory=_audit,
        scan_trajectory=_scan,
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
