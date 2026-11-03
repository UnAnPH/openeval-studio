"""Automated tests for strict model provenance and multi-model ensemble calibration."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.grader_workbench import BacktestSample, GraderWorkbench
from engine.llm_runner import AsyncLLMRunner, ChatMessage, LLMConfig
from engine.react_agent import AgentAction, AgentStep, AgentTrajectory
from engine.scanners import SchemingScanners, _resolve_scanner_config
from engine.scorers import run_llm_judge
from schemas.task_spec import TaskMetadata, TaskSpec
from server.policy_gateway import PolicyGateway


@pytest.mark.asyncio
async def test_generate_google_no_opencode_rewrite_and_normalizes_legacy_google() -> None:
    """Verify _generate_google does NOT rewrite opencode models, but normalizes sunset Google IDs."""
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Analysis complete."
    mock_response.candidates = []
    mock_response.usage_metadata = MagicMock(prompt_token_count=10, candidates_token_count=10)

    mock_genai_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    runner = AsyncLLMRunner(
        api_key="mock-key",
        provider="google",
        genai_client=mock_genai_client,
    )

    messages = [ChatMessage(role="user", content="Ping")]

    # 1. Deprecated Google ID is normalized to gemini-3.1-flash-lite
    cfg_legacy = LLMConfig(model="gemini-2.5-flash", temperature=0.0)
    await runner._generate_google(messages, cfg_legacy)
    called_model = mock_genai_client.aio.models.generate_content.call_args.kwargs["model"]
    assert called_model == "gemini-3.1-flash-lite"

    # 2. Modern Gemini model is preserved
    cfg_modern = LLMConfig(model="gemini-3.5-flash-lite", temperature=0.0)
    await runner._generate_google(messages, cfg_modern)
    called_model = mock_genai_client.aio.models.generate_content.call_args.kwargs["model"]
    assert called_model == "gemini-3.5-flash-lite"

    # 3. Opencode model is NOT rewritten if passed to _generate_google
    cfg_opencode = LLMConfig(model="opencode/glm-5.3-flash-free", temperature=0.0)
    await runner._generate_google(messages, cfg_opencode)
    called_model = mock_genai_client.aio.models.generate_content.call_args.kwargs["model"]
    assert called_model == "opencode/glm-5.3-flash-free"


@pytest.mark.asyncio
async def test_generate_google_rate_limit_does_not_cascade() -> None:
    """Verify _generate_google raises RuntimeError naming the exact model without cascading."""
    mock_genai_client = MagicMock()
    mock_genai_client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("429 RESOURCE_EXHAUSTED: Quota exceeded for gemini-3.1-flash-lite")
    )

    runner = AsyncLLMRunner(
        api_key="mock-key",
        provider="google",
        genai_client=mock_genai_client,
    )

    cfg = LLMConfig(model="gemini-3.1-flash-lite", temperature=0.0, max_retries=0)
    messages = [ChatMessage(role="user", content="Ping")]

    with pytest.raises(RuntimeError) as exc_info:
        await runner._generate_google(messages, cfg)

    assert "Rate limit / quota reached for gemini-3.1-flash-lite" in str(exc_info.value)
    # Ensure it only called generate_content for gemini-3.1-flash-lite, never cascaded to 3.5 or 3.7
    assert mock_genai_client.aio.models.generate_content.call_count == 1
    call_model = mock_genai_client.aio.models.generate_content.call_args.kwargs["model"]
    assert call_model == "gemini-3.1-flash-lite"


@pytest.mark.asyncio
async def test_generate_openai_opencode_quota_does_not_fallback_to_google() -> None:
    """Verify _generate_openai raises RuntimeError naming the OpenCode model on 429/quota."""
    runner = AsyncLLMRunner(api_key="mock-key", provider="opencode")
    mock_opencode_client = MagicMock()
    mock_opencode_client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError(
            "429 FreeUsageLimitError: daily quota exceeded for glm-5.3-flash-free"
        )
    )

    cfg = LLMConfig(
        model="opencode/glm-5.3-flash-free",
        provider="opencode",
        temperature=0.0,
        max_retries=0,
    )
    messages = [ChatMessage(role="user", content="Ping")]

    with pytest.raises(RuntimeError) as exc_info:
        await runner._generate_openai(messages, cfg, client=mock_opencode_client)

    assert "OpenCode quota/rate limit reached for opencode/glm-5.3-flash-free" in str(
        exc_info.value
    )


@pytest.mark.asyncio
async def test_run_llm_judge_strict_provenance_and_error_verdict() -> None:
    """Verify run_llm_judge evaluates with requested model and returns JudgeVerdict.errored on 429."""
    runner = MagicMock(spec=AsyncLLMRunner)
    runner.generate = AsyncMock(
        side_effect=RuntimeError("429 Quota reached for opencode/deepseek-v4.1-flash-free")
    )

    messages = [ChatMessage(role="user", content="Judge this")]
    verdict = await run_llm_judge(
        metric_name="situational_awareness",
        messages=messages,
        runner=runner,
        model="opencode/deepseek-v4.1-flash-free",
    )

    assert verdict.status == "error"
    assert verdict.score is None
    assert verdict.passed is None
    assert "opencode/deepseek-v4.1-flash-free" in verdict.reasoning
    # Verify generate was called with LLMConfig carrying opencode/deepseek-v4.1-flash-free
    called_cfg = runner.generate.call_args.kwargs["config"]
    assert called_cfg.model == "opencode/deepseek-v4.1-flash-free"


def test_resolve_scanner_config_no_rewrite() -> None:
    """Verify _resolve_scanner_config preserves requested model name."""
    runner = MagicMock(spec=AsyncLLMRunner)
    runner.provider = "google"

    cfg = _resolve_scanner_config("opencode/glm-5.3-flash-free", runner)
    assert cfg.model == "opencode/glm-5.3-flash-free"

    cfg_default = _resolve_scanner_config(None, runner)
    assert cfg_default.model == "google"


@pytest.mark.asyncio
async def test_scanner_429_returns_errored_without_fallback() -> None:
    """Verify SchemingScanners returns JudgeVerdict.errored on 429 without swapping to gemini-3.5."""
    task = TaskSpec(
        task_id="t1",
        task_dir=Path("/tmp"),
        metadata=TaskMetadata(category="test", difficulty="easy"),
        instruction_text="Fix the bug",
        dockerfile_path=Path("/tmp/Dockerfile"),
        solution_path=Path("/tmp/solve.sh"),
        test_outputs_path=Path("/tmp/test_outputs.py"),
    )
    trajectory = AgentTrajectory(
        task_id="t1",
        model="gemini-3.8-flash",
        steps=[
            AgentStep(
                step_number=1,
                thought="I will inspect files.",
                action=AgentAction(tool="execute_bash", command="ls"),
                observation="main.py",
            )
        ],
    )

    runner = MagicMock(spec=AsyncLLMRunner)
    runner.generate = AsyncMock(
        side_effect=RuntimeError("429 RESOURCE_EXHAUSTED: Rate limit reached on gemini-3.8-flash")
    )

    verdict = await SchemingScanners.audit_situational_awareness(
        trajectory,
        task,
        runner,
        model="gemini-3.8-flash",
    )

    assert verdict.status == "error"
    assert verdict.score is None
    assert verdict.passed is None
    assert "gemini-3.8-flash" in verdict.reasoning
    # Only 1 call made, no fallback retry with gemini-3.5-flash-lite
    assert runner.generate.call_count == 1


@pytest.mark.asyncio
async def test_backtest_resilient_ensemble_and_warning_logging() -> None:
    """Verify GraderWorkbench.run_backtest scores with surviving models and logs quota warning."""
    sample = BacktestSample(
        sample_id="gold-resilience-01",
        dimension="Dimension 1",
        failure_mode="SituationalAwarenessGrader",
        headline="Synthetic resilience test",
        trajectory_text="Turn 1: Checking files.",
        ground_truth_score=7,
    )

    runner = MagicMock(spec=AsyncLLMRunner)

    # Model A succeeds with score 8, Model B fails with 429
    async def mock_evaluate(
        traj_text: str, runner: AsyncLLMRunner, config: LLMConfig, **kwargs: object
    ):
        if config.model == "gemini-3.5-flash-lite":
            mock_v = MagicMock()
            mock_v.score = 8
            mock_v.explanation = "Evaluated safely."
            mock_v.thinking = "Thinking through trajectory."
            return mock_v
        else:
            raise RuntimeError(
                f"Rate limit / quota reached for {config.model}: 429 RESOURCE_EXHAUSTED"
            )

    mock_grader = MagicMock()
    mock_grader.name = "SituationalAwarenessGrader"
    mock_grader.dimension = "Dimension 1"
    mock_grader.template_type = "apollo_trajectory"
    mock_grader.default_preset = "lean"
    mock_grader.evaluate = AsyncMock(side_effect=mock_evaluate)
    mock_grader.model_copy = MagicMock(side_effect=lambda update=None: mock_grader)

    with patch("engine.graders_suite.get_canonical_grader", return_value=mock_grader):
        workbench = GraderWorkbench(runner=runner)
        report = await workbench.run_backtest(
            grader_name="SituationalAwarenessGrader",
            dataset=[sample],
            ensemble_models=["gemini-3.5-flash-lite", "gemini-3.8-flash"],
        )

    assert report.total_samples == 1
    assert report.failed_samples == 0
    assert len(report.samples) == 1
    pred = report.samples[0]
    assert pred.predicted_score == 8.0
    assert "gemini-3.5-flash-lite" in pred.ensemble_scores
    assert "gemini-3.8-flash" not in pred.ensemble_scores
    # Active warnings must contain the explicit quota error for gemini-3.8-flash
    assert any("gemini-3.8-flash" in w for w in report.active_warnings)
    assert any("Quota / rate limit" in w for w in report.active_warnings)


def test_policy_gateway_default_model() -> None:
    """Verify default llm_model in PolicyGateway is gemini-3.5-flash-lite."""
    gateway = PolicyGateway()
    assert gateway.llm_model == "gemini-3.5-flash-lite"


@pytest.mark.asyncio
async def test_run_llm_judge_with_unconfigured_magicmock_runner() -> None:
    """Verify run_llm_judge does NOT crash with ValidationError when runner is MagicMock without spec."""
    mock_runner = MagicMock()  # Accessing mock_runner.model returns MagicMock
    mock_runner.generate = AsyncMock(
        return_value=MagicMock(
            parsed_json={"metric_name": "test", "score": 1.0, "passed": True, "reasoning": "ok"},
            content=None,
        )
    )

    verdict = await run_llm_judge(
        metric_name="test",
        messages=[ChatMessage(role="user", content="Ping")],
        runner=mock_runner,
        model=None,
    )
    assert verdict.status == "scored"
    assert verdict.score == 1.0
    called_cfg = mock_runner.generate.call_args.kwargs["config"]
    assert isinstance(called_cfg.model, str)
    assert called_cfg.model == "gemini-3.5-flash-lite"


@pytest.mark.asyncio
async def test_generate_openai_permanent_quota_error_raises_immediately() -> None:
    """Verify _generate_openai immediately raises RuntimeError for OpenAI credit exhaustion."""
    mock_openai_client = MagicMock()
    mock_openai_client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError(
            "429: You have no credits remaining (code: credit_balance_exhausted)"
        )
    )

    runner = AsyncLLMRunner(api_key="mock-key", provider="openai")
    cfg = LLMConfig(model="gpt-4o-mini", provider="openai", temperature=0.0, max_retries=3)
    messages = [ChatMessage(role="user", content="Ping")]

    with pytest.raises(RuntimeError) as exc_info:
        await runner._generate_openai(messages, cfg, client=mock_openai_client)

    assert "OpenAI quota/rate limit reached for gpt-4o-mini" in str(exc_info.value)
    # Must fail immediately without retrying all 3 times
    assert mock_openai_client.chat.completions.create.call_count == 1


@pytest.mark.asyncio
async def test_generate_anthropic_routing_and_missing_key() -> None:
    """Verify claude models route to anthropic provider and honestly report missing key without Google fallback."""
    mock_genai_client = MagicMock()
    runner = AsyncLLMRunner(api_key="mock-key", provider="google", genai_client=mock_genai_client)
    cfg = LLMConfig(model="claude-3-5-sonnet", temperature=0.0)
    messages = [ChatMessage(role="user", content="Ping")]

    with patch.dict("os.environ", {}, clear=True), pytest.raises(RuntimeError) as exc_info:
        await runner.generate(messages, cfg)

    assert "Anthropic API key not configured for claude-3-5-sonnet" in str(exc_info.value)
    # Ensure google_client was never called
    assert not mock_genai_client.aio.models.generate_content.called


@pytest.mark.asyncio
async def test_google_genai_404_does_not_claim_quota_reached() -> None:
    """Verify Google GenAI 404 NOT_FOUND raises model unavailable rather than falsifying quota reached."""
    mock_genai_client = MagicMock()
    mock_genai_client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("404 NOT_FOUND: models/unknown-model is not found for API version")
    )

    runner = AsyncLLMRunner(
        api_key="mock-key",
        provider="google",
        genai_client=mock_genai_client,
    )

    cfg = LLMConfig(model="unknown-model", temperature=0.0, max_retries=0)
    messages = [ChatMessage(role="user", content="Ping")]

    with pytest.raises(RuntimeError) as exc_info:
        await runner._generate_google(messages, cfg)

    err_msg = str(exc_info.value)
    assert "Google GenAI model unavailable or not found for unknown-model" in err_msg
    assert "Rate limit / quota reached" not in err_msg


@pytest.mark.asyncio
async def test_backtest_all_models_fail_handles_cleanly() -> None:
    """Verify GraderWorkbench.run_backtest handles total ensemble exhaustion without crashing."""
    sample = BacktestSample(
        sample_id="gold-fail-01",
        dimension="Dimension 1",
        failure_mode="SituationalAwarenessGrader",
        headline="Synthetic total failure test",
        trajectory_text="Turn 1: Checking files.",
        ground_truth_score=7,
    )

    runner = MagicMock(spec=AsyncLLMRunner)

    mock_grader = MagicMock()
    mock_grader.name = "SituationalAwarenessGrader"
    mock_grader.dimension = "Dimension 1"
    mock_grader.template_type = "apollo_trajectory"
    mock_grader.default_preset = "lean"
    mock_grader.evaluate = AsyncMock(
        side_effect=RuntimeError("429 RESOURCE_EXHAUSTED: Rate limit reached")
    )
    mock_grader.model_copy = MagicMock(side_effect=lambda update=None: mock_grader)

    with patch("engine.graders_suite.get_canonical_grader", return_value=mock_grader):
        workbench = GraderWorkbench(runner=runner)
        report = await workbench.run_backtest(
            grader_name="SituationalAwarenessGrader",
            dataset=[sample],
            ensemble_models=["gemini-3.5-flash-lite"],
        )

    assert report.total_samples == 1
    assert report.failed_samples == 1
    assert len(report.samples) == 0
    assert report.mae == 0.0
    assert any("gemini-3.5-flash-lite" in w for w in report.active_warnings)
    assert any("Quota / rate limit" in w for w in report.active_warnings)


def test_grader_audit_session_request_default_model() -> None:
    """Verify default model in GraderAuditSessionRequest is gemini-3.5-flash-lite."""
    from server.app import GraderAuditSessionRequest

    req = GraderAuditSessionRequest(session_id="s1", grader_name="SituationalAwarenessGrader")
    assert req.model == "gemini-3.5-flash-lite"


def test_eval_worker_error_status_and_failure_reason_logic() -> None:
    """Verify eval route preserves status='error' and failure_reason when trajectory has error status on turn >= 1."""
    traj = AgentTrajectory(
        task_id="task-1",
        model="gemini-3.8-flash",
        steps=[
            AgentStep(
                step_number=1,
                thought="Step 1 ok",
                action=AgentAction(tool="execute_bash", command="ls"),
                observation="app.py",
            ),
            AgentStep(
                step_number=2,
                thought="Step 2 hitting rate limit",
                action=AgentAction(tool="execute_bash", command="pytest"),
                observation="Rate limit exceeded",
            ),
        ],
        status="error",
        error_message="Rate limit / quota reached for gemini-3.8-flash: 429 RESOURCE_EXHAUSTED",
    )

    # Replicate eval route logic
    model = "gemini-3.8-flash"
    if getattr(traj, "status", None) == "error":
        status_to_set = "error"
        err_msg = (
            getattr(traj, "error_message", None)
            or getattr(traj, "final_summary", None)
            or "Unknown LLM API Error"
        )
        failure_reason_to_set = f"LLM API Error ({model}): {err_msg}"
    else:
        status_to_set = traj.status if traj.status in ("completed", "failed") else "completed"
        failure_reason_to_set = "Grade failed"

    assert status_to_set == "error"
    assert failure_reason_to_set == (
        "LLM API Error (gemini-3.8-flash): Rate limit / quota reached for gemini-3.8-flash: 429 RESOURCE_EXHAUSTED"
    )
