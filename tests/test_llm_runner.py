"""Unit tests for Async LLM Execution Runner with Google GenAI SDK & OpenAI."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from engine.llm_runner import (
    AsyncLLMRunner,
    ChatMessage,
    LLMConfig,
)


class ToolCallSchema(BaseModel):
    tool: str = Field(..., description="Action name")
    argument: str = Field(..., description="Command or path")


def test_google_ai_studio_default_initialization() -> None:
    """Verify that runner defaults to Google provider and gemini model."""
    runner = AsyncLLMRunner(api_key="test-gemini-key", provider="google")
    assert runner.provider == "google"
    assert runner.api_key == "test-gemini-key"
    assert runner.google_client is not None


@pytest.mark.asyncio
async def test_successful_google_genai_generation() -> None:
    """Verify that generate handles standard Gemini chat completions via Google GenAI SDK."""
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "I will inspect the feed-sync codebase."
    mock_response.usage_metadata = MagicMock(
        prompt_token_count=20,
        candidates_token_count=10,
        total_token_count=30,
    )

    mock_genai_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    runner = AsyncLLMRunner(
        api_key="mock-key",
        provider="google",
        genai_client=mock_genai_client,
    )
    messages = [
        ChatMessage(role="system", content="You are an evals agent."),
        ChatMessage(role="user", content="Analyze the repository."),
    ]

    config = LLMConfig(model="gemini-2.0-flash", temperature=0.0)
    response = await runner.generate(messages, config=config)

    assert response.content == "I will inspect the feed-sync codebase."
    assert response.role == "assistant"
    assert response.finish_reason == "stop"
    assert response.usage.prompt_tokens == 20
    assert response.usage.completion_tokens == 10
    assert response.usage.total_tokens == 30


@pytest.mark.asyncio
async def test_structured_json_validation_google() -> None:
    """Verify structured JSON output parsing with Google GenAI SDK."""
    tool_payload = {"tool": "run_bash", "argument": "pytest tests/"}
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps(tool_payload)
    mock_response.usage_metadata = MagicMock(
        prompt_token_count=15,
        candidates_token_count=8,
        total_token_count=23,
    )
    mock_genai_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    runner = AsyncLLMRunner(
        api_key="mock-key",
        provider="google",
        genai_client=mock_genai_client,
    )
    messages = [ChatMessage(role="user", content="Run tests")]

    response = await runner.generate(
        messages,
        response_schema=ToolCallSchema,
    )

    assert response.parsed_json is not None
    assert response.parsed_json["tool"] == "run_bash"
    assert response.parsed_json["argument"] == "pytest tests/"


@pytest.mark.asyncio
async def test_openai_endpoint_generation() -> None:
    """Verify that OpenAI provider path works with httpx client."""
    mock_response_data = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "OpenAI answer"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"total_tokens": 12},
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_response_data)

    transport = httpx.MockTransport(mock_handler)
    base_url = "https://api.openai.com/v1"
    async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
        runner = AsyncLLMRunner(client=client, provider="openai")
        messages = [ChatMessage(role="user", content="Hello")]

        response = await runner.generate(
            messages, config=LLMConfig(model="gpt-4o-mini", provider="openai")
        )
        assert response.content == "OpenAI answer"
        assert response.usage.total_tokens == 12


@pytest.mark.asyncio
async def test_opencode_endpoint_generation_and_prefix_stripping() -> None:
    """Verify OpenCode provider routes requests, strips 'opencode/' prefix, and tracks usage."""
    captured_payloads = []

    mock_response_data = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "OpenCode GLM answer"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 18, "completion_tokens": 9, "total_tokens": 27},
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_payloads.append(body)
        return httpx.Response(200, json=mock_response_data)

    transport = httpx.MockTransport(mock_handler)
    base_url = "https://opencode.ai/inference/openai/v1"
    async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
        runner = AsyncLLMRunner(
            api_key="oc_sk_test_key_12345678",
            provider="opencode",
            base_url=base_url,
            client=client,
        )
        assert runner.provider == "opencode"
        assert runner.opencode_client is not None

        messages = [ChatMessage(role="user", content="Analyze task")]

        # Calling with prefixed model name opencode/glm-5.3-flash
        response = await runner.generate(
            messages,
            config=LLMConfig(model="opencode/glm-5.3-flash", provider="opencode"),
        )

        assert response.content == "OpenCode GLM answer"
        assert response.usage.prompt_tokens == 18
        assert response.usage.completion_tokens == 9
        assert response.usage.total_tokens == 27

        # Verify that opencode/ prefix was stripped before hitting the API
        assert len(captured_payloads) == 1
        assert captured_payloads[0]["model"] == "glm-5.3-flash"


@pytest.mark.asyncio
async def test_opencode_auto_detection_and_schema_parsing() -> None:
    """Verify OpenCode model auto-detection from default runner and response_schema JSON validation."""
    captured_payloads = []

    tool_payload = {"tool": "execute_bash", "argument": "ls -la"}
    mock_response_data = {
        "choices": [
            {
                "message": {"role": "assistant", "content": json.dumps(tool_payload)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 25, "completion_tokens": 12, "total_tokens": 37},
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_payloads.append(body)
        return httpx.Response(200, json=mock_response_data)

    transport = httpx.MockTransport(mock_handler)
    base_url = "https://opencode.ai/inference/openai/v1"
    async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
        # Default runner initializes with provider="google"
        mock_genai_client = MagicMock()
        runner = AsyncLLMRunner(
            api_key="mock-key",
            provider="google",
            genai_client=mock_genai_client,
        )
        # Inject test http client for opencode
        runner.opencode_client = runner.opencode_client or AsyncOpenAI(
            api_key="oc_sk_mock_test",
            base_url=base_url,
            http_client=client,
        )

        messages = [ChatMessage(role="user", content="List directory contents")]

        # Invoking with opencode free model ID should auto-detect opencode provider
        response = await runner.generate(
            messages,
            config=LLMConfig(model="opencode/big-pickle"),
            response_schema=ToolCallSchema,
        )

        assert response.parsed_json is not None
        assert response.parsed_json["tool"] == "execute_bash"
        assert response.parsed_json["argument"] == "ls -la"

        assert len(captured_payloads) == 1
        assert captured_payloads[0]["model"] == "big-pickle"
        # Verify schema instructions were injected
        system_msgs = [m for m in captured_payloads[0]["messages"] if m["role"] == "system"]
        assert len(system_msgs) > 0
        assert (
            "ToolCallSchema" in system_msgs[0]["content"] or "schema" in system_msgs[0]["content"]
        )


def test_opencode_go_routing_and_headers() -> None:
    """Verify OpenCode Go routing to https://opencode.ai/inference/go/openai/v1 and mandatory headers."""
    from engine.llm_runner import OPENCODE_MANDATORY_HEADERS, resolve_opencode_base_url

    # Free models and big-pickle route to Zen
    assert (
        resolve_opencode_base_url("opencode/big-pickle")
        == "https://opencode.ai/inference/openai/v1"
    )
    assert (
        resolve_opencode_base_url("opencode/glm-5.3-flash-free")
        == "https://opencode.ai/inference/openai/v1"
    )

    # OpenCode Go models route to Go endpoint
    assert (
        resolve_opencode_base_url("opencode/deepseek-v4.1-flash")
        == "https://opencode.ai/inference/go/openai/v1"
    )
    assert (
        resolve_opencode_base_url("opencode/kimi-k3")
        == "https://opencode.ai/inference/go/openai/v1"
    )
    assert (
        resolve_opencode_base_url("opencode/qwen3.8-max")
        == "https://opencode.ai/inference/go/openai/v1"
    )

    # Respects explicit OPENCODE_BASE_URL env var
    with patch.dict(os.environ, {"OPENCODE_BASE_URL": "https://custom.opencode/v1"}):
        assert (
            resolve_opencode_base_url("opencode/deepseek-v4.1-flash")
            == "https://custom.opencode/v1"
        )

    # Mandatory headers
    assert OPENCODE_MANDATORY_HEADERS["x-opencode-session"].startswith("ses_openeval_")
    assert OPENCODE_MANDATORY_HEADERS["User-Agent"] == "openeval-studio/1.0"

    # Cached clients
    runner = AsyncLLMRunner(api_key="oc_test_key", provider="opencode")
    go_cl = runner.opencode_go_client
    assert str(go_cl.base_url) == "https://opencode.ai/inference/go/openai/v1/"
    assert runner.opencode_go_client is go_cl
    zen_cl = runner.opencode_zen_client
    assert str(zen_cl.base_url) == "https://opencode.ai/inference/openai/v1/"
    assert runner.opencode_zen_client is zen_cl


@pytest.mark.asyncio
async def test_opencode_timeout_surfaces_explicit_reason_and_audit_session_budget() -> None:
    """TimeoutError with empty str(exc) reports explicit timeout seconds and audit_session gives >=30s + thinking_budget=0."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from server.app import GraderAuditSessionRequest, audit_session_with_grader

    class SlowCompletions:
        async def create(self, **kwargs):  # type: ignore[no-untyped-def]
            raise TimeoutError()

    slow_client = SimpleNamespace(
        base_url="https://opencode.ai/inference/go/openai/v1",
        chat=SimpleNamespace(completions=SlowCompletions()),
    )
    runner = AsyncLLMRunner(api_key="oc_test", provider="opencode")
    runner.opencode_go_client = slow_client  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="Timed out after 12.0s"):
        await runner.generate(
            [ChatMessage(role="user", content="hi")],
            config=LLMConfig(
                model="opencode/glm-5.3-flash",
                provider="opencode",
                timeout_sec=12.0,
                max_retries=0,
            ),
        )

    # Verify audit_session_with_grader uses adequate timeout and thinking_budget=0 for OpenCode models
    captured_cfgs: list[LLMConfig] = []

    class FakeGrader:
        def model_copy(self, update=None):  # type: ignore[no-untyped-def]
            return self

        def to_compressed(self):  # type: ignore[no-untyped-def]
            return self

        async def evaluate(  # type: ignore[no-untyped-def]
            self,
            trajectory_text: str,
            runner_arg: object,
            config: LLMConfig | None = None,
            preset_mode: str | None = None,
        ):
            assert config is not None
            captured_cfgs.append(config)
            return SimpleNamespace(model_dump=lambda: {"score": 1, "explanation": "ok"})

    fake_session = SimpleNamespace(
        session_id="sess-audit-1", working_dir="/tmp", trajectory=None, steps=[]
    )
    grader_name = "SituationalAwarenessGrader"
    fake_store = SimpleNamespace(
        get_session=lambda sid: fake_session,
        get_grader_definition=lambda name: FakeGrader() if name == grader_name else None,
    )
    with (
        patch("server.app.get_watcher_store", return_value=fake_store),
        patch("server.app.AsyncLLMRunner", return_value=SimpleNamespace(close=AsyncMock())),
    ):
        res = await audit_session_with_grader(
            GraderAuditSessionRequest(
                session_id="sess-audit-1",
                grader_name=grader_name,
                model="opencode/glm-5.3-flash",
            )
        )
    assert res["score"] == 1
    assert len(captured_cfgs) == 1
    assert captured_cfgs[0].timeout_sec >= 30.0
    assert captured_cfgs[0].thinking_budget == 0
