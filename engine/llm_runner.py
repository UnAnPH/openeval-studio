"""Core Async LLM Execution Engine for OpenEval Studio.

Supports the official Google GenAI SDK (for Gemini models) and OpenAI Async SDK
with strict Pydantic validation, token usage telemetry, and deterministic sampling configurations.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Literal, cast

from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()

logger = logging.getLogger("openeval.engine.llm")


class ChatMessage(BaseModel):
    """Represents a single message turn in an evaluation conversation."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["system", "user", "assistant", "tool"] = Field(
        ..., description="Message author role"
    )
    content: str = Field(..., description="Text content of the message")
    tool_call_id: str | None = Field(default=None, description="ID of tool call if role is 'tool'")
    name: str | None = Field(default=None, description="Optional author or function name")


class LLMConfig(BaseModel):
    """Inference hyperparameters and sampling configuration."""

    model_config = ConfigDict(extra="ignore")

    model: str = Field(default="gemini-3.1-flash-lite", description="Target model identifier")
    provider: Literal["google", "openai", "custom"] = Field(
        default="google", description="Target LLM API provider"
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="Sampling temperature (0.0 for deterministic evals)",
    )
    top_p: float = Field(default=1.0, ge=0.0, le=1.0, description="Nucleus sampling threshold")
    max_tokens: int = Field(default=4096, ge=1, description="Maximum completion token budget")
    timeout_sec: float = Field(default=60.0, ge=1.0, description="HTTP request timeout (seconds)")
    max_retries: int = Field(
        default=4, ge=0, description="Maximum retry attempts on 429/5xx errors"
    )
    seed: int | None = Field(default=42, description="Random seed for reproducible completions")


class LLMUsage(BaseModel):
    """Token consumption and latency metrics for an inference call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0


class LLMResponse(BaseModel):
    """Structured response from the LLM execution runner."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: str = Field(..., description="Raw generated text from the assistant")
    role: str = "assistant"
    finish_reason: str = "stop"
    usage: LLMUsage = Field(default_factory=LLMUsage)
    parsed_json: dict[str, Any] | None = Field(
        default=None, description="Extracted JSON payload if output contains structured data"
    )


class AsyncLLMRunner:
    """Production-grade async runner for Google GenAI SDK and OpenAI evaluation requests."""

    def __init__(
        self,
        api_key: str | None = None,
        provider: Literal["google", "openai", "custom"] = "google",
        base_url: str | None = None,
        openai_client: AsyncOpenAI | None = None,
        genai_client: Any = None,
        client: Any = None,
    ) -> None:
        self.provider = provider
        if api_key:
            self.api_key = api_key
        elif provider == "google":
            self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "mock-key"
        else:
            self.api_key = os.getenv("OPENAI_API_KEY") or "mock-key"

        # Official Google GenAI SDK client
        if provider == "google":
            self.google_client = genai_client or genai.Client(api_key=self.api_key)
            self.openai_client = None
        else:
            self.google_client = None
            if openai_client is not None:
                self.openai_client = openai_client
            elif client is not None:
                if isinstance(client, AsyncOpenAI):
                    self.openai_client = client
                else:
                    self.openai_client = AsyncOpenAI(
                        api_key=self.api_key,
                        base_url=base_url,
                        http_client=client,
                    )
            else:
                self.openai_client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=base_url,
                )

    async def close(self) -> None:
        """Close OpenAI client session if open."""
        if self.openai_client is not None:
            await self.openai_client.close()

    async def generate(
        self,
        messages: list[ChatMessage],
        config: LLMConfig | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        """Issue an async completion request with retries, latency tracking, and JSON parsing."""
        cfg = config or LLMConfig(provider=self.provider)

        provider = cfg.provider if cfg.provider != "google" else self.provider
        if cfg.model.startswith("gpt-") or cfg.model.startswith("o1") or cfg.model.startswith("o3"):
            provider = "openai"

        if provider == "google":
            if self.google_client is None:
                self.google_client = genai.Client(
                    api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "mock-key"
                )
            return await self._generate_google(messages, cfg, response_schema)
        else:
            if self.openai_client is None:
                self.openai_client = AsyncOpenAI(
                    api_key=os.getenv("OPENAI_API_KEY") or "mock-key"
                )
            return await self._generate_openai(messages, cfg, response_schema)

    async def _generate_google(
        self,
        messages: list[ChatMessage],
        cfg: LLMConfig,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        """Execute async inference via the official Google GenAI SDK."""
        start_time = time.perf_counter()

        system_prompt: str | None = None
        contents: list[types.Content] = []

        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
            elif msg.role in ("user", "tool"):
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=msg.content)],
                    )
                )
            elif msg.role == "assistant":
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=msg.content)],
                    )
                )

        gen_config = types.GenerateContentConfig(
            temperature=cfg.temperature,
            top_p=cfg.top_p,
            max_output_tokens=cfg.max_tokens,
            system_instruction=system_prompt,
            response_mime_type="application/json" if response_schema is not None else None,
            response_schema=response_schema if response_schema is not None else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        attempt = 0
        while attempt <= cfg.max_retries:
            try:
                response = await self.google_client.aio.models.generate_content(
                    model=cfg.model,
                    contents=cast(Any, contents),
                    config=gen_config,
                )

                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                raw_text = response.text or ""

                prompt_toks = 0
                comp_toks = 0
                tot_toks = 0
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    prompt_toks = (
                        getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                    )
                    comp_toks = (
                        getattr(response.usage_metadata, "candidates_token_count", 0) or 0
                    )
                    tot_toks = getattr(response.usage_metadata, "total_token_count", 0) or 0

                parsed_json = None
                if response_schema is not None or raw_text.strip().startswith("{"):
                    try:
                        raw_dict = json.loads(raw_text)
                        if response_schema is not None:
                            validated = response_schema.model_validate(raw_dict)
                            parsed_json = validated.model_dump()
                        else:
                            parsed_json = raw_dict
                    except Exception as parse_err:
                        logger.warning("JSON parse error: %s", parse_err)

                return LLMResponse(
                    content=raw_text,
                    role="assistant",
                    finish_reason="stop",
                    usage=LLMUsage(
                        prompt_tokens=prompt_toks,
                        completion_tokens=comp_toks,
                        total_tokens=tot_toks or (prompt_toks + comp_toks),
                        latency_ms=round(elapsed_ms, 2),
                    ),
                    parsed_json=parsed_json,
                )

            except Exception as exc:
                attempt += 1
                if attempt > cfg.max_retries:
                    raise RuntimeError(
                        f"Google GenAI request failed after {cfg.max_retries} retries: {exc}"
                    ) from exc

                err_str = str(exc)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "Quota" in err_str:
                    backoff = 5.0 + (attempt * 2.0)
                    logger.warning(
                        "Google API quota limit (429). Retrying in %.1fs (attempt %d/%d)...",
                        backoff,
                        attempt,
                        cfg.max_retries,
                    )
                else:
                    backoff = min(2.0 ** attempt, 10.0)
                    logger.warning("Google API error: %s. Retrying in %.1fs...", exc, backoff)

                await asyncio.sleep(backoff)

        raise RuntimeError("Exhausted retries without response")

    async def _generate_openai(
        self,
        messages: list[ChatMessage],
        cfg: LLMConfig,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        """Execute async inference via the official OpenAI SDK."""
        start_time = time.perf_counter()

        formatted_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
        ]

        kwargs: dict[str, Any] = {
            "model": cfg.model,
            "messages": formatted_messages,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "max_tokens": cfg.max_tokens,
        }

        if cfg.seed is not None:
            kwargs["seed"] = cfg.seed

        if response_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        assert self.openai_client is not None
        response = await self.openai_client.chat.completions.create(**kwargs)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        choice = response.choices[0]
        raw_text = choice.message.content or ""
        finish_reason = choice.finish_reason or "stop"

        usage_data = response.usage
        prompt_toks = (getattr(usage_data, "prompt_tokens", 0) or 0) if usage_data else 0
        comp_toks = (getattr(usage_data, "completion_tokens", 0) or 0) if usage_data else 0
        tot_toks = (getattr(usage_data, "total_tokens", 0) or 0) if usage_data else 0

        parsed_json = None
        if response_schema is not None or raw_text.strip().startswith("{"):
            try:
                raw_dict = json.loads(raw_text)
                if response_schema is not None:
                    validated = response_schema.model_validate(raw_dict)
                    parsed_json = validated.model_dump()
                else:
                    parsed_json = raw_dict
            except Exception as parse_err:
                logger.warning("JSON parse error: %s", parse_err)

        return LLMResponse(
            content=raw_text,
            role="assistant",
            finish_reason=finish_reason,
            usage=LLMUsage(
                prompt_tokens=prompt_toks,
                completion_tokens=comp_toks,
                total_tokens=tot_toks,
                latency_ms=round(elapsed_ms, 2),
            ),
            parsed_json=parsed_json,
        )
