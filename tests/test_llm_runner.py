"""Unit tests for Async LLM Execution Runner with Google GenAI SDK & OpenAI."""

import json
from unittest.mock import AsyncMock, MagicMock

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

        # Calling with prefixed model name opencode/glm-5.3-flash-free
        response = await runner.generate(
            messages,
            config=LLMConfig(model="opencode/glm-5.3-flash-free", provider="opencode"),
        )

        assert response.content == "OpenCode GLM answer"
        assert response.usage.prompt_tokens == 18
        assert response.usage.completion_tokens == 9
        assert response.usage.total_tokens == 27

        # Verify that opencode/ prefix was stripped before hitting the API
        assert len(captured_payloads) == 1
        assert captured_payloads[0]["model"] == "glm-5.3-flash-free"


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
        assert "ToolCallSchema" in system_msgs[0]["content"] or "schema" in system_msgs[0]["content"]

