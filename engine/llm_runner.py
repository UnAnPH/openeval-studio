"""Core Async LLM Execution Engine for OpenEval Studio.

Supports the official Google GenAI SDK (for Gemini models) and OpenAI Async SDK
with strict Pydantic validation, token usage telemetry, and deterministic sampling configurations.
"""

import asyncio
import json
import logging
import os
import re
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
    provider: Literal["google", "openai", "opencode", "custom"] = Field(
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
    thinking_level: Literal["high", "minimal"] | None = Field(
        default=None,
        description="Thinking level for models that support it. "  # (e.g. Gemma 4)
        "'high' enables the thinking process; 'minimal' disables it.",
    )


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
        provider: Literal["google", "openai", "opencode", "custom"] = "google",
        base_url: str | None = None,
        openai_client: AsyncOpenAI | None = None,
        genai_client: Any = None,
        client: Any = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url
        if api_key:
            self.api_key = api_key
        elif provider == "google":
            self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "mock-key"
        elif provider == "opencode":
            self.api_key = os.getenv("OPENCODE_API_KEY") or "mock-key"
        else:
            self.api_key = os.getenv("OPENAI_API_KEY") or "mock-key"

        self.google_client = None
        self.openai_client = None
        self.opencode_client = None

        # Official Google GenAI SDK client
        if provider == "google":
            self.google_client = genai_client or genai.Client(api_key=self.api_key)
        elif provider == "opencode":
            opencode_base = base_url or os.getenv(
                "OPENCODE_BASE_URL", "https://opencode.ai/inference/openai/v1"
            )
            if openai_client is not None:
                self.opencode_client = openai_client
            elif client is not None:
                if isinstance(client, AsyncOpenAI):
                    self.opencode_client = client
                else:
                    self.opencode_client = AsyncOpenAI(
                        api_key=self.api_key,
                        base_url=opencode_base,
                        http_client=client,
                    )
            else:
                self.opencode_client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=opencode_base,
                )
        else:
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
        """Close OpenAI and OpenCode client sessions if open."""
        if self.openai_client is not None:
            await self.openai_client.close()
        if self.opencode_client is not None:
            await self.opencode_client.close()

    async def generate(
        self,
        messages: list[ChatMessage],
        config: LLMConfig | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        """Issue an async completion request with retries, latency tracking, and JSON parsing."""
        cfg = config or LLMConfig(provider=cast(Any, self.provider))

        provider = cfg.provider if cfg.provider != "google" else self.provider
        if (
            cfg.provider == "opencode"
            or cfg.model.startswith("opencode/")
            or cfg.model == "big-pickle"
            or cfg.model.endswith("-free")
        ):
            provider = "opencode"
        elif (
            cfg.model.startswith("gpt-") or cfg.model.startswith("o1") or cfg.model.startswith("o3")
        ):
            provider = "openai"
        elif (
            (cfg.model.startswith("gemini-") or "live" in cfg.model)
            and self.provider not in ("openai", "opencode")
            and cfg.provider not in ("openai", "opencode")
        ):
            provider = "google"
        elif cfg.provider:
            provider = cfg.provider
        else:
            provider = self.provider

        if provider == "google":
            if self.google_client is None:
                self.google_client = genai.Client(
                    api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "mock-key"
                )
            if "live" in cfg.model.lower():
                return await self._call_google_live(messages, cfg, response_schema)
            return await self._generate_google(messages, cfg, response_schema)
        elif provider == "opencode":
            if self.opencode_client is None:
                opencode_key = (
                    (self.api_key if self.provider == "opencode" else None)
                    or os.getenv("OPENCODE_API_KEY")
                    or "mock-key"
                )
                opencode_base = self.base_url or os.getenv(
                    "OPENCODE_BASE_URL", "https://opencode.ai/inference/openai/v1"
                )
                self.opencode_client = AsyncOpenAI(api_key=opencode_key, base_url=opencode_base)
            return await self._generate_openai(
                messages, cfg, response_schema, client=self.opencode_client
            )
        else:
            if self.openai_client is None:
                self.openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY") or "mock-key")
            return await self._generate_openai(
                messages, cfg, response_schema, client=self.openai_client
            )

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

        # Inject ThinkingConfig when explicitly requested  # (e.g. Gemma 4)
        _is_thinking_model = (
            # cfg.model.startswith("gemma-4-") or
            cfg.thinking_level is not None
        )
        _thinking_config: types.ThinkingConfig | None = None
        if _is_thinking_model:
            _level = cfg.thinking_level or "high"
            _thinking_config = types.ThinkingConfig(thinking_level=cast(Any, _level))

        gen_config = types.GenerateContentConfig(
            temperature=cfg.temperature,
            top_p=cfg.top_p,
            max_output_tokens=cfg.max_tokens,
            system_instruction=system_prompt,
            thinking_config=_thinking_config,
            response_mime_type="application/json" if response_schema is not None else None,
            response_schema=response_schema if response_schema is not None else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        target_model = cfg.model.replace("google/", "")
        if (
            target_model.startswith("opencode/")
            or target_model
            in (
                "glm-5.3-flash-free",
                "deepseek-v4.1-flash-free",
                "big-pickle",
                "nemotron-3-ultra-free",
            )
            or target_model.endswith("-free")
            or any(legacy in target_model for legacy in ("2.0-flash", "1.5-flash", "1.5-pro"))
        ):
            target_model = "gemini-3.1-flash-lite"
        elif target_model == "gemini-3-flash":
            target_model = "gemini-3-flash-preview"

        attempt = 0
        while attempt <= cfg.max_retries:
            try:
                assert self.google_client is not None
                response = await self.google_client.aio.models.generate_content(
                    model=target_model,
                    contents=cast(Any, contents),
                    config=gen_config,
                )

                elapsed_ms = (time.perf_counter() - start_time) * 1000.0

                # For thinking models the SDK may return thought parts + answer parts.
                # Extract only the non-thought (answer) text or function calls; fall back to response.text.
                raw_text = ""
                if response.candidates:
                    candidate = response.candidates[0]
                    if candidate.content and candidate.content.parts:
                        parts_list: list[str] = []
                        for p in candidate.content.parts:
                            if getattr(p, "thought", False):
                                continue
                            if p.text:
                                parts_list.append(p.text)
                            elif (fc := getattr(p, "function_call", None)) is not None:
                                fc_args = getattr(fc, "args", None)
                                args_repr = str(fc_args) if fc_args else ""
                                fc_name = getattr(fc, "name", "") or "unknown"
                                parts_list.append(f"Tool Call: {fc_name}({args_repr})")
                        raw_text = "".join(parts_list)
                if not raw_text:
                    raw_text = response.text or ""

                prompt_toks = 0
                comp_toks = 0
                tot_toks = 0
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    prompt_toks = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                    comp_toks = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
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
                err_str = str(exc)

                # Permanent failures (billing, model deprecation, daily cap)
                if (
                    "credit_balance_exhausted" in err_str
                    or "insufficient_quota" in err_str
                    or "404 NOT_FOUND" in err_str
                    or "no longer available" in err_str
                    or "Daily" in err_str
                    or "PerDay" in err_str
                ):
                    raise RuntimeError(f"Google GenAI quota or model unavailable: {exc}") from exc

                attempt += 1
                if attempt > cfg.max_retries:
                    raise RuntimeError(
                        f"Google GenAI request failed after {cfg.max_retries} retries: {exc}"
                    ) from exc

                is_rate_limit = (
                    "429" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str
                    or "Quota exceeded" in err_str
                )
                if is_rate_limit:
                    if target_model == "gemini-3.1-flash-lite":
                        target_model = "gemini-3.5-flash-lite"
                        logger.warning(
                            "Google API rate limit on gemini-3.1-flash-lite. Cascading to gemini-3.5-flash-lite..."
                        )
                        continue
                    elif target_model == "gemini-3.5-flash-lite":
                        target_model = "gemini-3.7-flash"
                        logger.warning(
                            "Google API rate limit on gemini-3.5-flash-lite. Cascading to gemini-3.7-flash..."
                        )
                        continue

                    delay_match = re.search(
                        r"retry\s+(?:in|Delay['\"]?:\s*['\"])(\d+(?:\.\d+)?)s?",
                        err_str,
                        re.IGNORECASE,
                    )
                    if delay_match:
                        backoff = float(delay_match.group(1)) + 1.0
                    else:
                        backoff = 10.0 + (attempt * 4.0)
                    logger.warning(
                        "Google API rate limit (429/RESOURCE_EXHAUSTED). Retrying in %.1fs (attempt %d/%d)...",
                        backoff,
                        attempt,
                        cfg.max_retries,
                    )
                else:
                    backoff = min(2.0**attempt, 10.0)
                    logger.warning("Google API error: %s. Retrying in %.1fs...", exc, backoff)

                await asyncio.sleep(backoff)

        raise RuntimeError("Exhausted retries without response")

    @staticmethod
    def _fallback_parse_agent_action(
        combined_text: str,
        thoughts: str,
        messages: list[ChatMessage],
    ) -> tuple[str, dict[str, Any]]:
        """Parse or infer an AgentAction payload when no native tool call was issued."""
        clean_text = combined_text.strip()
        # 1. Check if model spoke or transcribed a JSON object
        json_match = re.search(r"\{[\s\S]*\}", clean_text)
        if json_match:
            try:
                candidate = json.loads(json_match.group(0))
                if isinstance(candidate, dict) and "tool" in candidate:
                    return json.dumps(candidate), candidate
            except Exception:
                pass

        # 2. Check for completion or finish indicators
        finish_indicators = [
            "finish",
            "finished",
            "completed",
            "done",
            "wrap up",
            "wrap things up",
            "conclude",
            "solved",
            "verified",
        ]
        is_finish = any(ind in clean_text.lower() for ind in finish_indicators)

        if is_finish:
            action: dict[str, Any] = {
                "thought": thoughts or clean_text or "Task verified and completed.",
                "tool": "finish",
                "command": None,
                "path": None,
                "content": None,
                "summary": clean_text or "Evaluation task marked finished.",
            }
            return json.dumps(action), action

        # 3. Check for shell command in backticks or quotes
        cmd_match = re.search(r"`([^`]+)`", clean_text)
        command = cmd_match.group(1) if cmd_match else None

        # 4. Check if last user message requested a specific command
        if not command and messages:
            last_user_msg = next(
                (m.content for m in reversed(messages) if m.role in ("user", "tool")), ""
            )
            user_cmd_match = re.search(r"`([^`]+)`", last_user_msg)
            if user_cmd_match:
                command = user_cmd_match.group(1)

        # 5. Default fallback to bash exploration
        action = {
            "thought": thoughts or clean_text or "Investigating workspace files.",
            "tool": "execute_bash",
            "command": command or "ls -la",
            "path": None,
            "content": None,
            "summary": None,
        }
        return json.dumps(action), action

    @staticmethod
    def _fallback_parse_schema(
        combined_text: str,
        response_schema: type[BaseModel],
    ) -> tuple[str, dict[str, Any] | None]:
        """Parse structured output from text when no native tool call was issued."""
        clean_text = combined_text.strip()
        json_match = re.search(r"\{[\s\S]*\}", clean_text)
        if json_match:
            try:
                candidate = json.loads(json_match.group(0))
                if isinstance(candidate, dict):
                    validated = response_schema.model_validate(candidate)
                    dumped = validated.model_dump()
                    return json.dumps(dumped), dumped
            except Exception:
                pass
        return clean_text, None

    async def _call_google_live(
        self,
        messages: list[ChatMessage],
        cfg: LLMConfig,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        """Execute async inference via the official Google GenAI Live API WebSocket."""
        start_time = time.perf_counter()

        target_model = cfg.model.replace("google/", "")
        if target_model == "gemini-3-flash":
            target_model = "gemini-3-flash-preview"

        system_prompt: str | None = None
        turns: list[types.Content] = []

        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
            elif msg.role in ("user", "tool"):
                turns.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=msg.content)],
                    )
                )
            elif msg.role == "assistant":
                turns.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=msg.content)],
                    )
                )

        if not turns:
            turns.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="Please proceed.")],
                )
            )

        is_thinking = (
            "extended-thinking" in target_model.lower()
            or "thinking" in target_model.lower()
            or cfg.thinking_level is not None
        )
        thinking_config: types.ThinkingConfig | None = None
        if is_thinking:
            level = cfg.thinking_level or "high"
            if level == "minimal":
                level = "low"
            thinking_config = types.ThinkingConfig(
                thinking_level=cast(Any, level), include_thoughts=True
            )

        is_agent_action = (
            response_schema is not None
            and getattr(response_schema, "__name__", "") == "AgentAction"
        )
        tools: list[types.Tool] | None = None

        if is_agent_action:
            tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name="execute_bash",
                            description="Run terminal commands in the sandboxed workspace. Input: command.",
                            parameters=cast(
                                Any,
                                {
                                    "type": "OBJECT",
                                    "properties": {
                                        "command": {
                                            "type": "STRING",
                                            "description": "Shell command to execute in bash",
                                        }
                                    },
                                    "required": ["command"],
                                },
                            ),
                        ),
                        types.FunctionDeclaration(
                            name="view_file",
                            description="Read contents of a file in the workspace. Input: path.",
                            parameters=cast(
                                Any,
                                {
                                    "type": "OBJECT",
                                    "properties": {
                                        "path": {
                                            "type": "STRING",
                                            "description": "Target file path to view",
                                        }
                                    },
                                    "required": ["path"],
                                },
                            ),
                        ),
                        types.FunctionDeclaration(
                            name="write_file",
                            description="Write content to a file in the workspace. Input: path, content.",
                            parameters=cast(
                                Any,
                                {
                                    "type": "OBJECT",
                                    "properties": {
                                        "path": {
                                            "type": "STRING",
                                            "description": "Target file path to write",
                                        },
                                        "content": {
                                            "type": "STRING",
                                            "description": "Content to write into the file",
                                        },
                                    },
                                    "required": ["path", "content"],
                                },
                            ),
                        ),
                        types.FunctionDeclaration(
                            name="finish",
                            description="Conclude evaluation work once verified. Input: summary.",
                            parameters=cast(
                                Any,
                                {
                                    "type": "OBJECT",
                                    "properties": {
                                        "summary": {
                                            "type": "STRING",
                                            "description": "Summary of final benchmark resolution",
                                        }
                                    },
                                    "required": ["summary"],
                                },
                            ),
                        ),
                    ]
                )
            ]
        elif response_schema is not None:
            schema_dict = (
                response_schema.model_json_schema()
                if hasattr(response_schema, "model_json_schema")
                else {}
            )
            tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name="submit_structured_output",
                            description="Submit the structured evaluation verdict or response.",
                            parameters=cast(Any, schema_dict),
                        )
                    ]
                )
            ]

        attempt = 0
        while attempt <= cfg.max_retries:
            try:
                assert self.google_client is not None
                live_config = types.LiveConnectConfig(
                    response_modalities=cast(Any, ["AUDIO"]),
                    output_audio_transcription=types.AudioTranscriptionConfig(),
                    tools=tools,
                    system_instruction=system_prompt,
                    thinking_config=thinking_config,
                    temperature=cfg.temperature,
                    top_p=cfg.top_p,
                )

                async with self.google_client.aio.live.connect(
                    model=target_model, config=live_config
                ) as session:
                    await session.send_client_content(turns=cast(Any, turns), turn_complete=True)

                    tool_call = None
                    transcription_chunks: list[str] = []
                    thought_chunks: list[str] = []
                    text_chunks: list[str] = []
                    prompt_toks = 0
                    comp_toks = 0
                    tot_toks = 0

                    async for msg in session.receive():
                        if msg.tool_call and not tool_call:
                            tool_call = msg.tool_call
                        if msg.usage_metadata:
                            prompt_toks = (
                                getattr(msg.usage_metadata, "prompt_token_count", 0) or prompt_toks
                            )
                            c_toks = (
                                getattr(msg.usage_metadata, "response_token_count", 0)
                                or getattr(msg.usage_metadata, "candidates_token_count", 0)
                                or comp_toks
                            )
                            th_toks = getattr(msg.usage_metadata, "thoughts_token_count", 0) or 0
                            comp_toks = c_toks + th_toks
                            tot_toks = getattr(msg.usage_metadata, "total_token_count", 0) or (
                                prompt_toks + comp_toks
                            )

                        if msg.server_content:
                            if (
                                msg.server_content.output_transcription
                                and msg.server_content.output_transcription.text
                            ):
                                transcription_chunks.append(
                                    msg.server_content.output_transcription.text
                                )
                            if (
                                msg.server_content.model_turn
                                and msg.server_content.model_turn.parts
                            ):
                                for p in msg.server_content.model_turn.parts:
                                    if getattr(p, "thought", False) and p.text:
                                        thought_chunks.append(p.text)
                                    elif p.text:
                                        text_chunks.append(p.text)
                            if msg.server_content.turn_complete:
                                break

                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                transcription = "".join(transcription_chunks).strip()
                thoughts = "".join(thought_chunks).strip()
                text_parts = "".join(text_chunks).strip()
                combined_text = text_parts or transcription

                raw_text = ""
                parsed_json: dict[str, Any] | None = None

                if is_agent_action:
                    if tool_call and tool_call.function_calls:
                        fc = tool_call.function_calls[0]
                        fc_args = fc.args if isinstance(fc.args, dict) else {}
                        thought_val = thoughts or transcription or f"Calling tool {fc.name}"
                        action_dict = {
                            "thought": thought_val,
                            "tool": fc.name,
                            "command": fc_args.get("command"),
                            "path": fc_args.get("path"),
                            "content": fc_args.get("content"),
                            "summary": fc_args.get("summary"),
                        }
                        raw_text = json.dumps(action_dict)
                        parsed_json = action_dict
                    else:
                        raw_text, parsed_json = self._fallback_parse_agent_action(
                            combined_text=combined_text,
                            thoughts=thoughts,
                            messages=messages,
                        )
                elif response_schema is not None:
                    if tool_call and tool_call.function_calls:
                        fc = tool_call.function_calls[0]
                        fc_args = fc.args if isinstance(fc.args, dict) else {}
                        try:
                            validated = response_schema.model_validate(fc_args)
                            parsed_json = validated.model_dump()
                        except Exception:
                            parsed_json = fc_args
                        raw_text = json.dumps(parsed_json)
                    else:
                        raw_text, parsed_json = self._fallback_parse_schema(
                            combined_text=combined_text,
                            response_schema=response_schema,
                        )
                else:
                    raw_text = combined_text
                    if raw_text.strip().startswith("{"):
                        try:
                            parsed_json = json.loads(raw_text)
                        except Exception:
                            parsed_json = None

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
                err_str = str(exc)
                if (
                    "credit_balance_exhausted" in err_str
                    or "insufficient_quota" in err_str
                    or "404 NOT_FOUND" in err_str
                    or "no longer available" in err_str
                ):
                    raise RuntimeError(f"Google GenAI Live API unavailable: {exc}") from exc

                attempt += 1
                if attempt > cfg.max_retries:
                    raise RuntimeError(
                        f"Google GenAI Live API request failed after {cfg.max_retries} retries: {exc}"
                    ) from exc

                backoff = min(2.0**attempt, 10.0)
                logger.warning(
                    "Google GenAI Live API error: %s. Retrying in %.1fs...", exc, backoff
                )
                await asyncio.sleep(backoff)

        raise RuntimeError("Exhausted retries without response from Live API")

    async def _generate_openai(
        self,
        messages: list[ChatMessage],
        cfg: LLMConfig,
        response_schema: type[BaseModel] | None = None,
        client: AsyncOpenAI | None = None,
    ) -> LLMResponse:
        """Execute async inference via the official OpenAI SDK (or OpenAI-compatible gateway)."""
        start_time = time.perf_counter()

        target_client = client or self.openai_client
        assert target_client is not None, "OpenAI or OpenCode client must be initialized"

        is_opencode = (
            self.provider == "opencode"
            or cfg.provider == "opencode"
            or cfg.model.startswith("opencode/")
            or cfg.model == "big-pickle"
            or cfg.model.endswith("-free")
            or "opencode" in str(getattr(target_client, "base_url", ""))
        )

        # CRITICAL: Strip the "opencode/" prefix from model name for OpenCode API calls
        target_model = cfg.model.removeprefix("opencode/")

        formatted_messages: list[dict[str, Any]] = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        # For OpenAI-compatible gateways and open-weight models, ensure JSON schema instructions
        # are injected into the prompt if response_schema is required
        if response_schema is not None:
            schema_json = json.dumps(
                response_schema.model_json_schema()
                if hasattr(response_schema, "model_json_schema")
                else {}
            )
            schema_instruction = f"\n\nYou MUST respond ONLY with a valid JSON object matching this schema:\n{schema_json}"
            if formatted_messages and formatted_messages[0]["role"] == "system":
                formatted_messages[0] = {
                    "role": "system",
                    "content": formatted_messages[0]["content"] + schema_instruction,
                }
            else:
                formatted_messages.insert(
                    0,
                    {
                        "role": "system",
                        "content": f"You are a helpful assistant.{schema_instruction}",
                    },
                )

        kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": formatted_messages,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "max_tokens": cfg.max_tokens,
        }

        if cfg.seed is not None:
            kwargs["seed"] = cfg.seed

        if response_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        attempt = 0
        response = None
        while attempt <= cfg.max_retries:
            try:
                response = await target_client.chat.completions.create(**kwargs)
                break
            except Exception as exc:
                err_str = str(exc)

                # Seamless fallback for OpenCode free tier or upstream availability issues
                if is_opencode and (
                    "freetiererror" in err_str.lower()
                    or "only be used from within opencode" in err_str.lower()
                    or "model is unavailable" in err_str.lower()
                    or "upstream request failed" in err_str.lower()
                    or "401" in err_str
                    or "403" in err_str
                    or "404" in err_str
                    or "429" in err_str
                    or "connection" in err_str.lower()
                    or "permissiondenied" in err_str.lower()
                ):
                    logger.warning(
                        "OpenCode API error for %s (%s). Seamlessly falling back to Google GenAI backend...",
                        cfg.model,
                        exc,
                    )
                    if self.google_client is None:
                        self.google_client = genai.Client(
                            api_key=os.getenv("GEMINI_API_KEY")
                            or os.getenv("GOOGLE_API_KEY")
                            or "mock-key"
                        )
                    fallback_cfg = cfg.model_copy(
                        update={"model": "gemini-3.1-flash-lite", "provider": "google"}
                    )
                    return await self._generate_google(messages, fallback_cfg, response_schema)

                # If gateway / model fails because response_format is not supported, retry without response_format
                if "response_format" in kwargs and (
                    "response_format" in err_str.lower()
                    or "json_object" in err_str.lower()
                    or "schema" in err_str.lower()
                    or "400" in err_str
                ):
                    logger.warning(
                        "Gateway rejected response_format: %s. Retrying without response_format...",
                        exc,
                    )
                    kwargs.pop("response_format", None)
                    continue

                attempt += 1
                if attempt > cfg.max_retries:
                    if is_opencode:
                        logger.warning(
                            "OpenCode request failed after retries: %s. Seamlessly falling back to Google GenAI backend...",
                            exc,
                        )
                        if self.google_client is None:
                            self.google_client = genai.Client(
                                api_key=os.getenv("GEMINI_API_KEY")
                                or os.getenv("GOOGLE_API_KEY")
                                or "mock-key"
                            )
                        fallback_cfg = cfg.model_copy(
                            update={"model": "gemini-3.1-flash-lite", "provider": "google"}
                        )
                        return await self._generate_google(messages, fallback_cfg, response_schema)
                    raise RuntimeError(
                        f"OpenAI/OpenCode request failed after {cfg.max_retries} retries: {exc}"
                    ) from exc

                is_rate_limit = "429" in err_str or "rate_limit" in err_str.lower()
                backoff = min(2.0**attempt, 10.0) if not is_rate_limit else (5.0 + attempt * 2.0)
                logger.warning("API error: %s. Retrying in %.1fs...", exc, backoff)
                await asyncio.sleep(backoff)

        if response is None:
            raise RuntimeError("Exhausted retries without response")

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        choice = response.choices[0]
        raw_text = choice.message.content or ""
        finish_reason = choice.finish_reason or "stop"

        usage_data = response.usage
        prompt_toks = (getattr(usage_data, "prompt_tokens", 0) or 0) if usage_data else 0
        comp_toks = (getattr(usage_data, "completion_tokens", 0) or 0) if usage_data else 0
        tot_toks = (getattr(usage_data, "total_tokens", 0) or 0) if usage_data else 0

        parsed_json = None
        if response_schema is not None or raw_text.strip().startswith("{") or "{" in raw_text:
            clean_str = raw_text.strip()
            if clean_str.startswith("```"):
                clean_str = re.sub(r"^```(?:json)?\s*\n?", "", clean_str)
                clean_str = re.sub(r"\n?```$", "", clean_str).strip()
            try:
                raw_dict = json.loads(clean_str)
                if response_schema is not None:
                    validated = response_schema.model_validate(raw_dict)
                    parsed_json = validated.model_dump()
                else:
                    parsed_json = raw_dict
            except Exception:
                json_match = re.search(r"\{[\s\S]*\}", raw_text)
                if json_match:
                    try:
                        raw_dict = json.loads(json_match.group(0))
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
