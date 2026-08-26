"""Unit tests for the ReAct Agent Scaffold Loop."""

import json
from pathlib import Path

import httpx
import pytest

from engine.llm_runner import AsyncLLMRunner
from engine.react_agent import AgentAction, AgentStep, ReActAgent
from schemas.task_spec import load_task_spec


@pytest.fixture
def sample_task(tmp_path: Path):
    """Create a temporary test task."""
    task_dir = tmp_path / "sample-eval"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text(
        "[metadata]\ncategory = 'sec'\n[agent]\nmax_steps = 5\n"
    )
    (task_dir / "instruction.md").write_text("Fix the auth bug")
    (task_dir / "environment").mkdir()
    (task_dir / "environment" / "Dockerfile").write_text("FROM alpine\n")
    (task_dir / "solution").mkdir()
    (task_dir / "solution" / "solve.sh").write_text("echo 1\n")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test_outputs.py").write_text("assert True\n")
    return load_task_spec(task_dir)


@pytest.mark.asyncio
async def test_react_agent_successful_resolution(sample_task) -> None:
    """Verify that ReActAgent loops through thoughts and tools until finish is called."""
    turn_responses = [
        # Turn 1: Explore workspace
        {
            "thought": "Let's check the current directory contents.",
            "tool": "execute_bash",
            "command": "ls -la",
        },
        # Turn 2: Write patch
        {
            "thought": "I will apply the fix to auth.py.",
            "tool": "write_file",
            "path": "/app/auth.py",
            "content": "def authenticate(): return True",
        },
        # Turn 3: Conclude
        {
            "thought": "The fix is applied and ready for verification.",
            "tool": "finish",
            "summary": "Fixed authentication bypass in auth.py",
        },
    ]

    current_turn = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal current_turn
        payload = turn_responses[current_turn]
        current_turn += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": json.dumps(payload)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            },
        )

    # Mock tool executor
    executed_tools: list[str] = []

    async def mock_executor(action: AgentAction) -> str:
        executed_tools.append(action.tool)
        if action.tool == "execute_bash":
            return "total 4\n-rw-r--r-- 1 root root auth.py"
        if action.tool == "write_file":
            return "File written successfully: /app/auth.py"
        return "Unknown tool"

    transport = httpx.MockTransport(mock_handler)
    base = "https://api.openai.com/v1"
    async with httpx.AsyncClient(transport=transport, base_url=base) as client:
        runner = AsyncLLMRunner(client=client, provider="openai")
        agent = ReActAgent(runner=runner, executor=mock_executor)

        streamed_steps: list[AgentStep] = []
        trajectory = await agent.solve_task(
            task=sample_task,
            on_step_callback=lambda step: streamed_steps.append(step),
        )

        assert trajectory.status == "completed"
        assert trajectory.total_steps == 3
        assert len(trajectory.steps) == 3
        assert trajectory.total_tokens == 210  # 70 tokens * 3 turns
        assert trajectory.final_summary == "Fixed authentication bypass in auth.py"
        assert executed_tools == ["execute_bash", "write_file"]
        assert len(streamed_steps) == 3


@pytest.mark.asyncio
async def test_react_agent_max_steps_exceeded(sample_task) -> None:
    """Verify that ReActAgent halts gracefully when max_steps is reached without finish."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "thought": "Still exploring files...",
            "tool": "execute_bash",
            "command": "pwd",
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": json.dumps(payload)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 30},
            },
        )

    async def mock_executor(action: AgentAction) -> str:
        return "/workspace"

    transport = httpx.MockTransport(mock_handler)
    base = "https://api.openai.com/v1"
    async with httpx.AsyncClient(transport=transport, base_url=base) as client:
        runner = AsyncLLMRunner(client=client, provider="openai")
        agent = ReActAgent(runner=runner, executor=mock_executor)

        trajectory = await agent.solve_task(task=sample_task)

        assert trajectory.status == "max_steps_exceeded"
        assert trajectory.total_steps == 5  # sample_task max_steps = 5
