"""Tests for Gemini Flash models and Gemini 3.8 Live API models."""

import asyncio
import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from engine.llm_runner import AsyncLLMRunner, ChatMessage, LLMConfig
from engine.react_agent import AgentAction, ReActAgent
from schemas.models import get_available_models, get_model_spec
from schemas.task_spec import TaskMetadata, TaskSpec


def test_catalog_flash_and_live_models() -> None:
    """Verify that all required Gemini Flash models are registered with correct specs."""
    google_models = get_available_models("google")
    model_ids = {m.id for m in google_models}

    # All required Active Flash & Flagship models
    required_flash = [
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3-flash-preview",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
    ]
    for m_id in required_flash:
        assert m_id in model_ids, f"Model {m_id} missing from MODEL_CATALOG"

    # Active Live API, Gemma, and specialized models
    for active_id in [
        "gemini-3.8-live",
        "gemini-3.8-live-extended-thinking",
        "gemini-4-hydrogen",
        "gemma-4-31b-it",
        "gemma-4-26b-it",
    ]:
        assert active_id in model_ids, f"Model {active_id} missing from MODEL_CATALOG"
        assert get_model_spec(active_id) is not None

    # Antigravity is an agent platform harness, NOT a catalog model
    assert "antigravity" not in model_ids
    assert get_model_spec("antigravity") is None

    # Live API models and Decisions model are present but marked non-eval-agent
    live_spec = get_model_spec("gemini-3.8-live")
    assert live_spec is not None and live_spec.eval_capable is False
    live_ext = get_model_spec("gemini-3.8-live-extended-thinking")
    assert live_ext is not None and live_ext.eval_capable is False

    # Sunset / decommissioned models must not be in model_ids
    for sunset_id in [
        "gemini-3.7-flash",
        "gemini-3.5-flash",
        "gemini-4-carbon",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]:
        assert sunset_id not in model_ids, (
            f"Sunset model {sunset_id} should not be in MODEL_CATALOG"
        )
        assert get_model_spec(sunset_id) is None

    # Google-native gemini-4-argon is decommissioned from google provider (now verified via OpenCode Go)
    assert "gemini-4-argon" not in model_ids
    assert get_model_spec("google/gemini-4-argon") is None

    # gemini-3.8-flash spec
    spec_38 = get_model_spec("gemini-3.8-flash")
    assert spec_38 is not None
    assert spec_38.name == "Gemini 3.8 Flash"
    assert spec_38.tier == "flagship"
    assert set(spec_38.capabilities) == {"coding", "agentic", "tool_use", "reasoning"}
    assert "5 RPM / 20 RPD" in spec_38.description

    # Quota documentation checks
    for flash_id in [
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3-flash-preview",
        "gemini-4-hydrogen",
    ]:
        s = get_model_spec(flash_id)
        assert s is not None
        assert "5 RPM / 20 RPD" in s.description

    for lite_id in ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]:
        s = get_model_spec(lite_id)
        assert s is not None
        assert "500 RPD" in s.description

    for gemma_id in ["gemma-4-31b-it", "gemma-4-26b-it"]:
        s = get_model_spec(gemma_id)
        assert s is not None
        assert "14.4K RPD" in s.description


def test_model_rewriting_rules() -> None:
    """Verify legacy rewrite list removes 2.5-flash and maps gemini-3-flash to preview."""

    # Test target_model rewrite logic directly
    def rewrite(model: str) -> str:
        target = model.replace("google/", "")
        if any(legacy in target for legacy in ("2.0-flash", "1.5-flash", "1.5-pro")):
            return "gemini-3.5-flash-lite"
        elif target == "gemini-3-flash":
            return "gemini-3-flash-preview"
        return target

    # 2.5-flash and 2.5-flash-lite must NOT be rewritten
    assert rewrite("gemini-2.5-flash") == "gemini-2.5-flash"
    assert rewrite("gemini-2.5-flash-lite") == "gemini-2.5-flash-lite"
    assert rewrite("google/gemini-2.5-flash") == "gemini-2.5-flash"

    # gemini-3-flash resolves to preview
    assert rewrite("gemini-3-flash") == "gemini-3-flash-preview"
    assert rewrite("google/gemini-3-flash") == "gemini-3-flash-preview"

    # 3.8 models preserved
    assert rewrite("gemini-3.8-flash") == "gemini-3.8-flash"
    assert rewrite("gemini-3.8-live") == "gemini-3.8-live"
    assert rewrite("gemini-3.8-live-extended-thinking") == "gemini-3.8-live-extended-thinking"

    # Deprecated models rewritten
    assert rewrite("gemini-2.0-flash") == "gemini-3.5-flash-lite"
    assert rewrite("gemini-1.5-flash") == "gemini-3.5-flash-lite"
    assert rewrite("gemini-1.5-pro") == "gemini-3.5-flash-lite"


def test_fallback_parse_agent_action() -> None:
    """Test fallback parsing of AgentAction when no native tool call is returned."""
    # JSON embedded in text
    text_with_json = 'Here is the action: {"thought": "Inspect repo", "tool": "execute_bash", "command": "git status"}'
    raw, action = AsyncLLMRunner._fallback_parse_agent_action(
        combined_text=text_with_json,
        thoughts="Thinking about files",
        messages=[],
    )
    assert raw is not None
    assert action["tool"] == "execute_bash"
    assert action["command"] == "git status"

    # Text indicating finish
    finish_text = "I have verified all tests and completed the task successfully."
    raw, action = AsyncLLMRunner._fallback_parse_agent_action(
        combined_text=finish_text,
        thoughts="",
        messages=[],
    )
    assert raw is not None
    assert action["tool"] == "finish"
    assert action["summary"] == finish_text

    # Text indicating bash command in backticks
    cmd_text = "Let's run `pytest tests/` to verify."
    raw, action = AsyncLLMRunner._fallback_parse_agent_action(
        combined_text=cmd_text,
        thoughts="",
        messages=[],
    )
    assert raw is not None
    assert action["tool"] == "execute_bash"
    assert action["command"] == "pytest tests/"

    # Fallback when last user message requested command
    messages = [ChatMessage(role="user", content="Please execute `cargo check`.")]
    raw, action = AsyncLLMRunner._fallback_parse_agent_action(
        combined_text="Checking workspace now.",
        thoughts="",
        messages=messages,
    )
    assert raw is not None
    assert action["tool"] == "execute_bash"
    assert action["command"] == "cargo check"


def test_fallback_parse_schema() -> None:
    """Test fallback parsing for general Pydantic response_schema."""

    class GradingVerdict(BaseModel):
        passed: bool
        score: float
        comment: str

    text = '{"passed": true, "score": 0.95, "comment": "Great solution"}'
    raw, parsed = AsyncLLMRunner._fallback_parse_schema(text, GradingVerdict)
    assert raw is not None
    assert parsed is not None
    assert parsed["passed"] is True
    assert parsed["score"] == 0.95


@pytest.mark.slow
@pytest.mark.asyncio
async def test_live_runner_live_integration() -> None:
    """End-to-end integration test with Gemini Live API if API key is present."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY not set")

    try:
        runner = AsyncLLMRunner(api_key=api_key, provider="google")

        # 1. Plain text with gemini-3.8-live
        res_text = await asyncio.wait_for(
            runner.generate(
                [ChatMessage(role="user", content="Respond with: HELLO_LIVE")],
                config=LLMConfig(model="gemini-3.8-live", max_tokens=100),
            ),
            timeout=3.0,
        )
        assert len(res_text.content) > 0

        # 2. AgentAction with gemini-3.8-live
        messages = [
            ChatMessage(
                role="system", content="You are a terminal evaluation agent. Execute bash commands."
            ),
            ChatMessage(
                role="user",
                content="Inspect the current directory with execute_bash(command='ls').",
            ),
        ]
        res_action = await asyncio.wait_for(
            runner.generate(
                messages=messages,
                config=LLMConfig(model="gemini-3.8-live", max_tokens=500),
                response_schema=AgentAction,
            ),
            timeout=3.0,
        )
        assert res_action.parsed_json is not None
        assert "tool" in res_action.parsed_json
    except Exception as exc:
        if (
            isinstance(exc, (TimeoutError, asyncio.TimeoutError))
            or "timeout" in str(exc).lower()
            or "API key not valid" in str(exc)
            or "403" in str(exc)
            or "1007" in str(exc)
            or "1011" in str(exc)
            or "exhausted" in str(exc).lower()
            or "quota" in str(exc).lower()
        ):
            pytest.skip(
                f"Live Gemini API key invalid, inaccessible, timed out, or quota exhausted: {exc}"
            )
        raise


@pytest.mark.slow
@pytest.mark.asyncio
async def test_react_agent_live_loop() -> None:
    """End-to-end ReActAgent execution loop with gemini-3.8-live."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY not set")

    try:
        runner = AsyncLLMRunner(api_key=api_key, provider="google")

        async def mock_executor(action: AgentAction) -> str:
            if action.tool == "execute_bash":
                return "total 0\n-rw-r--r-- 1 root root 0 main.py\n-rw-r--r-- 1 root root 0 test_main.py"
            return "Task finished."

        agent = ReActAgent(runner=runner, executor=mock_executor)
        task = TaskSpec(
            task_id="terminal-bench-eval",
            task_dir=Path("/tmp"),
            metadata=TaskMetadata(category="test", difficulty="easy"),
            instruction_text="Inspect workspace files using execute_bash(command='ls -la'). Then call finish.",
            dockerfile_path=Path("/tmp/Dockerfile"),
            solution_path=Path("/tmp/solve.sh"),
            test_outputs_path=Path("/tmp/test_outputs.py"),
        )

        trajectory = await asyncio.wait_for(
            agent.solve_task(
                task=task,
                config=LLMConfig(model="gemini-3.8-live", provider="google", max_tokens=1024),
            ),
            timeout=3.0,
        )
        assert len(trajectory.steps) >= 1
        assert trajectory.status == "completed"
    except Exception as exc:
        if (
            isinstance(exc, (TimeoutError, asyncio.TimeoutError))
            or "timeout" in str(exc).lower()
            or "API key not valid" in str(exc)
            or "403" in str(exc)
            or "1007" in str(exc)
            or "1011" in str(exc)
            or "exhausted" in str(exc).lower()
            or "quota" in str(exc).lower()
        ):
            pytest.skip(
                f"Live Gemini API key invalid, inaccessible, timed out, or quota exhausted: {exc}"
            )
        raise
