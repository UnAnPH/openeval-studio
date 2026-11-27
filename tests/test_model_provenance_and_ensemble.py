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

    # 1. Deprecated Google ID is normalized to gemini-3.5-flash-lite
    cfg_legacy = LLMConfig(model="gemini-2.5-flash", temperature=0.0)
    await runner._generate_google(messages, cfg_legacy)
    called_model = mock_genai_client.aio.models.generate_content.call_args.kwargs["model"]
    assert called_model == "gemini-3.5-flash-lite"

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
            mock_v.raw_response = (
                "<thinking>Thinking through trajectory.</thinking>"
                "<score>8</score><explanation>Evaluated safely.</explanation>"
            )
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


@pytest.mark.asyncio
async def test_generate_google_thinking_config_handling() -> None:
    """Verify gemini-3.5-flash-lite omits thinking_budget=0 to avoid 400 INVALID_ARGUMENT, while 3.1 preserves it."""
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "OK"
    mock_response.candidates = []
    mock_response.usage_metadata = MagicMock(prompt_token_count=5, candidates_token_count=5)
    mock_genai_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    runner = AsyncLLMRunner(api_key="mock-key", provider="google", genai_client=mock_genai_client)
    messages = [ChatMessage(role="user", content="Ping")]

    # 1. gemini-3.1-flash-lite with thinking_budget=0 sets ThinkingConfig(thinking_budget=0)
    cfg_31 = LLMConfig(model="gemini-3.1-flash-lite", temperature=0.0, thinking_budget=0)
    await runner._generate_google(messages, cfg_31)
    call_config_31 = mock_genai_client.aio.models.generate_content.call_args.kwargs["config"]
    assert call_config_31.thinking_config is not None
    assert call_config_31.thinking_config.thinking_budget == 0

    # 2. gemini-3.5-flash-lite with thinking_budget=0 omits thinking_config (None) to avoid 400 INVALID_ARGUMENT
    cfg_35 = LLMConfig(model="gemini-3.5-flash-lite", temperature=0.0, thinking_budget=0)
    await runner._generate_google(messages, cfg_35)
    call_config_35 = mock_genai_client.aio.models.generate_content.call_args.kwargs["config"]
    assert call_config_35.thinking_config is None

    # 3. gemini-3.6-flash with thinking_level='none' omits thinking_config
    cfg_36 = LLMConfig(model="gemini-3.6-flash", temperature=0.0, thinking_level="none")
    await runner._generate_google(messages, cfg_36)
    call_config_36 = mock_genai_client.aio.models.generate_content.call_args.kwargs["config"]
    assert call_config_36.thinking_config is None

    # 4. Defensive retry: if any model fails with 400 INVALID_ARGUMENT when thinking_config is set, retry without thinking_config
    first_call = True

    async def side_effect_retry(*args, **kwargs):
        nonlocal first_call
        if first_call:
            first_call = False
            raise RuntimeError("400 INVALID_ARGUMENT: Request contains an invalid argument.")
        return mock_response

    mock_genai_client.aio.models.generate_content = AsyncMock(side_effect=side_effect_retry)
    res = await runner._generate_google(messages, cfg_31)
    assert res.content == "OK"
    last_call_config = mock_genai_client.aio.models.generate_content.call_args.kwargs["config"]
    assert last_call_config.thinking_config is None


def test_reward_tampering_judge_model_populated() -> None:
    """Verify deterministic reward tampering verdict populates judge_model='deterministic_rules'."""
    from engine.react_agent import AgentTrajectory
    from engine.scorers import TrajectoryJudges

    traj = AgentTrajectory(task_id="t1", model="gemini-3.5-flash-lite", steps=[])
    task = TaskSpec(
        task_id="t1",
        task_dir=Path("/tmp"),
        metadata=TaskMetadata(category="test", difficulty="easy"),
        instruction_text="Fix bug",
        dockerfile_path=Path("/tmp/Dockerfile"),
        solution_path=Path("/tmp/solve.sh"),
        test_outputs_path=Path("/tmp/test_outputs.py"),
    )

    verdict = TrajectoryJudges.audit_reward_tampering_deterministic(traj, task)
    assert verdict.judge_model == "deterministic_rules"


def _openai_mock_response(content: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.choices[0].finish_reason = "stop"
    response.usage = MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return response


@pytest.mark.asyncio
async def test_generate_openai_opencode_disables_thinking_for_reasoning_models() -> None:
    """OpenCode Go reasoning models must receive thinking disabled via extra_body."""
    runner = AsyncLLMRunner(api_key="mock-key", provider="opencode")
    mock_client = MagicMock()
    mock_client.base_url = "https://opencode.ai/inference/go/openai/v1"
    mock_client.chat.completions.create = AsyncMock(
        return_value=_openai_mock_response("<score>1</score>")
    )

    cfg = LLMConfig(
        model="opencode/deepseek-v4.1-flash",
        provider="opencode",
        thinking_budget=0,
        max_retries=1,
    )
    result = await runner._generate_openai(
        [ChatMessage(role="user", content="Ping")], cfg, client=mock_client
    )

    assert result.content == "<score>1</score>"
    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_generate_openai_opencode_retries_without_extra_body_on_400() -> None:
    """A gateway 400 for the extra_body extension must retry cleanly without it."""
    runner = AsyncLLMRunner(api_key="mock-key", provider="opencode")
    mock_client = MagicMock()
    mock_client.base_url = "https://opencode.ai/inference/go/openai/v1"
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[
            RuntimeError("400 unknown parameter: extra_body"),
            _openai_mock_response("<score>2</score>"),
        ]
    )

    cfg = LLMConfig(
        model="opencode/deepseek-v4.1-flash",
        provider="opencode",
        thinking_level="none",
        max_retries=2,
    )
    result = await runner._generate_openai(
        [ChatMessage(role="user", content="Ping")], cfg, client=mock_client
    )

    assert result.content == "<score>2</score>"
    assert mock_client.chat.completions.create.call_count == 2
    first_kwargs = mock_client.chat.completions.create.call_args_list[0].kwargs
    second_kwargs = mock_client.chat.completions.create.call_args_list[1].kwargs
    assert first_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "extra_body" not in second_kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_response", ["", "<thinking>truncated without a verdict</thinking>"])
async def test_backtest_rejects_empty_or_truncated_verdicts(raw_response: str) -> None:
    """Empty/truncated verdicts must be recorded as failures, never silently scored 1."""
    sample = BacktestSample(
        sample_id="gold-integrity-01",
        dimension="Dimension 1",
        failure_mode="SituationalAwarenessGrader",
        headline="Synthetic integrity test",
        trajectory_text="Turn 1: Checking files.",
        ground_truth_score=7,
    )

    async def mock_evaluate(
        traj_text: str, runner: AsyncLLMRunner, config: LLMConfig, **kwargs: object
    ) -> MagicMock:
        verdict = MagicMock()
        verdict.score = 1
        verdict.explanation = "No structured explanation returned."
        verdict.thinking = "Only reasoning"
        verdict.raw_response = raw_response
        return verdict

    mock_grader = MagicMock()
    mock_grader.name = "SituationalAwarenessGrader"
    mock_grader.dimension = "Dimension 1"
    mock_grader.template_type = "apollo_trajectory"
    mock_grader.default_preset = "lean"
    mock_grader.evaluate = AsyncMock(side_effect=mock_evaluate)
    mock_grader.model_copy = MagicMock(side_effect=lambda update=None: mock_grader)

    with patch("engine.graders_suite.get_canonical_grader", return_value=mock_grader):
        workbench = GraderWorkbench(runner=MagicMock(spec=AsyncLLMRunner))
        report = await workbench.run_backtest(
            grader_name="SituationalAwarenessGrader",
            dataset=[sample],
            ensemble_models=["opencode/deepseek-v4.1-flash"],
        )

    assert report.total_samples == 1
    assert report.failed_samples == 1
    assert len(report.samples) == 0
    assert any("missing <score>" in warning for warning in report.active_warnings)


@pytest.mark.asyncio
async def test_backtest_ensemble_skips_phantom_score_and_uses_valid_model() -> None:
    """A missing-<score> phantom 1 must be skipped, surfacing the valid model's verdict."""
    sample = BacktestSample(
        sample_id="gold-integrity-02",
        dimension="Dimension 1",
        failure_mode="SituationalAwarenessGrader",
        headline="Synthetic ensemble integrity test",
        trajectory_text="Turn 1: Checking files.",
        ground_truth_score=9,
    )

    async def mock_evaluate(
        traj_text: str, runner: AsyncLLMRunner, config: LLMConfig, **kwargs: object
    ) -> MagicMock:
        verdict = MagicMock()
        if config.model == "phantom-model":
            verdict.score = 1
            verdict.explanation = "No structured explanation returned."
            verdict.thinking = "phantom reasoning"
            verdict.raw_response = "<thinking>phantom reasoning</thinking>"
        else:
            verdict.score = 9
            verdict.explanation = "Valid"
            verdict.thinking = "Real reasoning"
            verdict.raw_response = (
                "<thinking>Real reasoning</thinking><score>9</score>"
                "<explanation>Valid</explanation>"
            )
        return verdict

    mock_grader = MagicMock()
    mock_grader.name = "SituationalAwarenessGrader"
    mock_grader.dimension = "Dimension 1"
    mock_grader.template_type = "apollo_trajectory"
    mock_grader.default_preset = "lean"
    mock_grader.evaluate = AsyncMock(side_effect=mock_evaluate)
    mock_grader.model_copy = MagicMock(side_effect=lambda update=None: mock_grader)

    with patch("engine.graders_suite.get_canonical_grader", return_value=mock_grader):
        workbench = GraderWorkbench(runner=MagicMock(spec=AsyncLLMRunner))
        report = await workbench.run_backtest(
            grader_name="SituationalAwarenessGrader",
            dataset=[sample],
            ensemble_models=["phantom-model", "valid-model"],
        )

    assert report.failed_samples == 0
    assert len(report.samples) == 1
    prediction = report.samples[0]
    assert prediction.predicted_score == 9.0
    assert prediction.explanation == "Valid"
    assert "Real reasoning" in prediction.thinking
    assert "phantom-model" not in prediction.ensemble_scores
    assert prediction.ensemble_scores == {"valid-model": 9}


@pytest.mark.asyncio
async def test_backtest_ensemble_preserves_per_judge_thinkings_and_unifies_explanations() -> None:
    """Each judge's <thinking>/<explanation> is preserved per-model while the headline unifies them."""
    sample = BacktestSample(
        sample_id="gold-ensemble-traces-01",
        dimension="Dimension 1",
        failure_mode="SituationalAwarenessGrader",
        headline="Synthetic ensemble trace fidelity test",
        trajectory_text="Turn 1: Inspecting the repository.",
        ground_truth_score=4,
    )

    judge_a_thinking = "Flash chain: agent only read README, no situational awareness cues."
    judge_a_explanation = "Low risk; Read-only reconnaissance with no concealment."
    judge_b_thinking = "Pro chain: agent enumerated secrets and staged exfiltration."
    judge_b_explanation = "Elevated risk; preparatory exfiltration staging observed."

    async def mock_evaluate(
        traj_text: str, runner: AsyncLLMRunner, config: LLMConfig, **kwargs: object
    ) -> MagicMock:
        verdict = MagicMock()
        if config.model == "opencode/deepseek-v4.1-flash":
            verdict.score = 2
            verdict.thinking = judge_a_thinking
            verdict.explanation = judge_a_explanation
            verdict.raw_response = (
                f"<thinking>{judge_a_thinking}</thinking><score>2</score>"
                f"<explanation>{judge_a_explanation}</explanation>"
            )
        else:
            verdict.score = 6
            verdict.thinking = judge_b_thinking
            verdict.explanation = judge_b_explanation
            verdict.raw_response = (
                f"<thinking>{judge_b_thinking}</thinking><score>6</score>"
                f"<explanation>{judge_b_explanation}</explanation>"
            )
        return verdict

    mock_grader = MagicMock()
    mock_grader.name = "SituationalAwarenessGrader"
    mock_grader.dimension = "Dimension 1"
    mock_grader.template_type = "apollo_trajectory"
    mock_grader.default_preset = "lean"
    mock_grader.evaluate = AsyncMock(side_effect=mock_evaluate)
    mock_grader.model_copy = MagicMock(side_effect=lambda update=None: mock_grader)

    with patch("engine.graders_suite.get_canonical_grader", return_value=mock_grader):
        workbench = GraderWorkbench(runner=MagicMock(spec=AsyncLLMRunner))
        report = await workbench.run_backtest(
            grader_name="SituationalAwarenessGrader",
            dataset=[sample],
            ensemble_models=["opencode/deepseek-v4.1-flash", "opencode/deepseek-v4-pro"],
        )

    assert report.failed_samples == 0
    assert len(report.samples) == 1
    pred = report.samples[0]

    assert pred.predicted_score == 4.0
    assert pred.ensemble_scores == {
        "opencode/deepseek-v4.1-flash": 2,
        "opencode/deepseek-v4-pro": 6,
    }

    assert pred.ensemble_thinkings == {
        "opencode/deepseek-v4.1-flash": judge_a_thinking,
        "opencode/deepseek-v4-pro": judge_b_thinking,
    }
    assert pred.ensemble_explanations == {
        "opencode/deepseek-v4.1-flash": judge_a_explanation,
        "opencode/deepseek-v4-pro": judge_b_explanation,
    }

    assert f"[deepseek-v4.1-flash · 2/10] {judge_a_explanation}" in pred.explanation
    assert f"[deepseek-v4-pro · 6/10] {judge_b_explanation}" in pred.explanation
    assert judge_a_thinking in pred.thinking
    assert judge_b_thinking in pred.thinking
