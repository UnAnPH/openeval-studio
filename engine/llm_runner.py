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
from google.genai import types
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from engine.gemini_key_pool import (
    GeminiKeyPool,
    get_global_gemini_key_pool,
    is_gemini_rate_limit_error,
    is_permanent_quota_error,
)
from schemas.models import parse_model_variant

load_dotenv()

logger = logging.getLogger("openeval.engine.llm")


class QuotaTierRestrictedError(RuntimeError):
    """Raised when a requested model is unavailable or rate-limited to 0 on the caller's account tier."""


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

    model: str = Field(default="gemini-3.5-flash-lite", description="Target model identifier")
    provider: Literal["google", "openai", "opencode", "anthropic", "custom"] = Field(
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
    thinking_level: Literal["high", "minimal", "none"] | None = Field(
        default=None,
        description="Thinking level for models that support it. "
        "'high' enables thinking; 'minimal' or 'none' minimizes or disables it.",
    )
    thinking_budget: int | None = Field(
        default=None,
        description="Explicit thinking token budget (0 to disable thinking for sub-800ms fast gen).",
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


def get_opencode_headers() -> dict[str, str]:
    """Mandatory default headers for all OpenCode API calls."""
    return {
        "x-opencode-session": f"ses_openeval_{int(time.time())}",
        "User-Agent": "openeval-studio/1.0",
    }


OPENCODE_MANDATORY_HEADERS: dict[str, str] = get_opencode_headers()


def parse_structured_json(
    raw_text: str,
    response_schema: type[BaseModel] | None = None,
) -> dict[str, Any] | None:
    """Extract and optionally validate a JSON object from model output.

    Handles `<think>` blocks, partial or full markdown code fences (including
    trailing ``` without an opening fence), and surrounding prose via JSONDecoder.
    """
    if not raw_text or not raw_text.strip():
        return None

    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw_text, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned).strip()

    candidates: list[dict[str, Any]] = []
    try:
        loaded = json.loads(cleaned)
        if isinstance(loaded, dict):
            candidates.append(loaded)
    except Exception:
        pass

    if not candidates:
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(cleaned):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(cleaned, idx)
                if isinstance(obj, dict):
                    candidates.append(obj)
            except Exception:
                continue

    if not candidates:
        logger.warning("JSON parse error: could not extract valid JSON object from output")
        return None

    for candidate in candidates:
        if response_schema is None:
            return candidate
        try:
            validated = response_schema.model_validate(candidate)
            return validated.model_dump()
        except Exception as err:
            logger.warning("JSON schema validation error: %s", err)

    return None


def resolve_opencode_base_url(model: str = "", base_url: str | None = None) -> str:
    """Resolve OpenCode inference endpoint URL.

    Zen endpoint: https://opencode.ai/inference/openai/v1 (for *-free models and opencode/big-pickle)
    Go endpoint:  https://opencode.ai/inference/go/openai/v1 (for OpenCode Go subscription models)
    Respects OPENCODE_BASE_URL if explicitly set to a custom or Go endpoint.
    """
    if base_url:
        return base_url
    env_base = os.getenv("OPENCODE_BASE_URL", "").strip()
    base_m, _ = parse_model_variant(model)
    clean_m = base_m.removeprefix("opencode/").lower()
    is_go_model = not clean_m.endswith("-free") and clean_m != "big-pickle"

    # If env_base is explicitly set to Go endpoint or a custom proxy, respect it
    if env_base and env_base != "https://opencode.ai/inference/openai/v1":
        return env_base

    if is_go_model:
        return "https://opencode.ai/inference/go/openai/v1"
    return "https://opencode.ai/inference/openai/v1"


class AsyncLLMRunner:
    """Production-grade async runner for Google GenAI SDK and OpenAI evaluation requests."""

    def __init__(
        self,
        api_key: str | None = None,
        provider: Literal["google", "openai", "opencode", "anthropic", "custom"] = "google",
        base_url: str | None = None,
        openai_client: AsyncOpenAI | None = None,
        genai_client: Any = None,
        client: Any = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url
        self._custom_genai_client = genai_client is not None
        # Always keep a Gemini key pool available: generate() can route a gemini-* model to
        # Google even when the runner was constructed for another provider.
        # When no explicit api_key or custom genai_client is passed, share the global pool so
        # per-request callers (e.g., Live Session Audit, Golden Incident Benchmark) retain
        # sticky rotation and cooldown state across invocations.
        if (api_key is None and genai_client is None) or provider != "google":
            self._gemini_key_pool = get_global_gemini_key_pool()
        else:
            self._gemini_key_pool = GeminiKeyPool(
                explicit_key=api_key,
                include_env_backups=(genai_client is None and api_key != "mock-key"),
            )

        if provider == "google":
            self.api_key = self._gemini_key_pool.get_active_key()
        elif api_key:
            self.api_key = api_key
        elif provider == "opencode":
            self.api_key = os.getenv("OPENCODE_API_KEY") or "mock-key"
        elif provider == "anthropic":
            self.api_key = os.getenv("ANTHROPIC_API_KEY") or "mock-key"
        else:
            self.api_key = os.getenv("OPENAI_API_KEY") or "mock-key"

        self.google_client = genai_client if provider != "google" else None
        self.openai_client = None
        self.opencode_client = None
        self._opencode_clients: dict[str, AsyncOpenAI] = {}
        self._opencode_go_client: AsyncOpenAI | None = None
        self._opencode_zen_client: AsyncOpenAI | None = None

        # Official Google GenAI SDK client
        if provider == "google":
            self.google_client = genai_client or self._gemini_key_pool.get_client(self.api_key)[1]
        elif provider == "opencode":
            opencode_base = resolve_opencode_base_url("", base_url)
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
                        default_headers=get_opencode_headers(),
                    )
            else:
                self._auto_created_opencode = True
                self.opencode_client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=opencode_base,
                    default_headers=get_opencode_headers(),
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

    @property
    def opencode_go_client(self) -> AsyncOpenAI:
        """Cached AsyncOpenAI client targeting OpenCode Go inference endpoint."""
        if self._opencode_go_client is None:
            opencode_key = (
                (self.api_key if self.provider == "opencode" else None)
                or os.getenv("OPENCODE_API_KEY")
                or "mock-key"
            )
            self._opencode_go_client = AsyncOpenAI(
                api_key=opencode_key,
                base_url="https://opencode.ai/inference/go/openai/v1",
                default_headers=get_opencode_headers(),
            )
        return self._opencode_go_client

    @opencode_go_client.setter
    def opencode_go_client(self, client: AsyncOpenAI | None) -> None:
        self._opencode_go_client = client

    @property
    def opencode_zen_client(self) -> AsyncOpenAI:
        """Cached AsyncOpenAI client targeting OpenCode Zen inference endpoint."""
        if self._opencode_zen_client is None:
            opencode_key = (
                (self.api_key if self.provider == "opencode" else None)
                or os.getenv("OPENCODE_API_KEY")
                or "mock-key"
            )
            self._opencode_zen_client = AsyncOpenAI(
                api_key=opencode_key,
                base_url="https://opencode.ai/inference/openai/v1",
                default_headers=get_opencode_headers(),
            )
        return self._opencode_zen_client

    @opencode_zen_client.setter
    def opencode_zen_client(self, client: AsyncOpenAI | None) -> None:
        self._opencode_zen_client = client

    async def close(self) -> None:
        """Close OpenAI and OpenCode client sessions if open."""
        if self.openai_client is not None:
            await self.openai_client.close()
        if self.opencode_client is not None:
            await self.opencode_client.close()
        if self._opencode_go_client is not None:
            await self._opencode_go_client.close()
        if self._opencode_zen_client is not None:
            await self._opencode_zen_client.close()
        if hasattr(self, "_opencode_clients"):
            for cl in self._opencode_clients.values():
                await cl.close()

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
            and self.provider not in ("openai", "opencode", "anthropic")
            and cfg.provider not in ("openai", "opencode", "anthropic")
        ):
            provider = "google"
        elif cfg.model.startswith("claude-") or cfg.provider == "anthropic":
            provider = "anthropic"
        elif cfg.provider:
            provider = cfg.provider
        else:
            provider = self.provider

        if provider == "google":
            if self.google_client is None:
                current_key, client = self._gemini_key_pool.get_client(model=cfg.model)
                self.api_key = current_key
                self.google_client = client
            if "live" in cfg.model.lower():
                return await self._call_google_live(messages, cfg, response_schema)
            return await self._generate_google(messages, cfg, response_schema)
        elif provider == "opencode":
            opencode_base = resolve_opencode_base_url(cfg.model, self.base_url)
            if self.opencode_client is not None and not getattr(
                self, "_auto_created_opencode", False
            ):
                target_client = self.opencode_client
            elif opencode_base == "https://opencode.ai/inference/go/openai/v1":
                target_client = self.opencode_go_client
            elif opencode_base == "https://opencode.ai/inference/openai/v1":
                target_client = self.opencode_zen_client
            else:
                opencode_key = (
                    (self.api_key if self.provider == "opencode" else None)
                    or os.getenv("OPENCODE_API_KEY")
                    or "mock-key"
                )
                if not hasattr(self, "_opencode_clients"):
                    self._opencode_clients = {}
                if opencode_base not in self._opencode_clients:
                    self._opencode_clients[opencode_base] = AsyncOpenAI(
                        api_key=opencode_key,
                        base_url=opencode_base,
                        default_headers=get_opencode_headers(),
                    )
                target_client = self._opencode_clients[opencode_base]
            return await self._generate_openai(messages, cfg, response_schema, client=target_client)
        elif provider == "anthropic":
            anthropic_key = (self.api_key if self.provider == "anthropic" else None) or os.getenv(
                "ANTHROPIC_API_KEY"
            )
            if not anthropic_key:
                raise RuntimeError(
                    f"Anthropic API key not configured for {cfg.model}. Set ANTHROPIC_API_KEY."
                )
            return await self._generate_anthropic(
                messages, cfg, response_schema, api_key=anthropic_key
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

        base_model, _ = parse_model_variant(cfg.model)
        target_model = base_model.replace("google/", "")
        if any(
            legacy in target_model
            for legacy in (
                "2.5-flash-lite",
                "2.5-flash",
                "2.5-pro",
                "2.0-flash",
                "1.5-flash",
                "1.5-pro",
            )
        ):
            target_model = "gemini-3.5-flash-lite"
        elif target_model == "gemini-3-flash":
            target_model = "gemini-3-flash-preview"

        _afc_config = (
            None
            if "gemma" in target_model.lower()
            else types.AutomaticFunctionCallingConfig(disable=True)
        )

        # Inject ThinkingConfig when explicitly requested (e.g. Gemma 4 / Gemini fast generation)
        _thinking_config: types.ThinkingConfig | None = None
        # Gemini 3.5+, Gemini 3.6+, and Gemma models reject thinking_budget=0 with 400 INVALID_ARGUMENT.
        # For these models, fast generation is achieved by omitting thinking_config (None).
        _rejects_budget_zero = (
            "gemini-3.5" in target_model
            or "gemini-3.6" in target_model
            or "gemma" in target_model.lower()
        )
        if cfg.thinking_level == "none" or (
            cfg.thinking_budget is not None and cfg.thinking_budget == 0
        ):
            if _rejects_budget_zero:
                _thinking_config = None
            else:
                _thinking_config = types.ThinkingConfig(thinking_budget=0)
        elif cfg.thinking_level is not None:
            _level = cfg.thinking_level
            _thinking_config = types.ThinkingConfig(thinking_level=cast(Any, _level))
        elif cfg.thinking_budget is not None:
            if cfg.thinking_budget == 0 and _rejects_budget_zero:
                _thinking_config = None
            else:
                _thinking_config = types.ThinkingConfig(thinking_budget=cfg.thinking_budget)

        gen_config = types.GenerateContentConfig(
            temperature=cfg.temperature,
            top_p=cfg.top_p,
            max_output_tokens=cfg.max_tokens,
            system_instruction=system_prompt,
            thinking_config=_thinking_config,
            response_mime_type="application/json" if response_schema is not None else None,
            response_schema=response_schema if response_schema is not None else None,
            automatic_function_calling=_afc_config,
        )

        attempt = 0
        tried_keys_for_model: set[str] = set()
        current_key = self.api_key
        while attempt <= cfg.max_retries:
            try:
                if not self._custom_genai_client:
                    current_key, client = self._gemini_key_pool.get_client(
                        model=target_model,
                        exclude_keys=tried_keys_for_model,
                    )
                    self.api_key = current_key
                    self.google_client = client
                assert self.google_client is not None
                timeout_val = getattr(cfg, "timeout_sec", 60.0) or 60.0
                response = await asyncio.wait_for(
                    self.google_client.aio.models.generate_content(
                        model=target_model,
                        contents=cast(Any, contents),
                        config=gen_config,
                    ),
                    timeout=timeout_val,
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
                if (
                    response_schema is not None
                    or raw_text.strip().startswith("{")
                    or "{" in raw_text
                ):
                    parsed_json = parse_structured_json(raw_text, response_schema=response_schema)

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
                    "INVALID_ARGUMENT" in err_str or "400" in err_str
                ) and gen_config.thinking_config is not None:
                    logger.warning(
                        "Google GenAI rejected thinking_config for %s (%s). Retrying immediately with thinking_config=None.",
                        target_model,
                        exc,
                    )
                    gen_config.thinking_config = None
                    continue

                is_rate_limit = (
                    "429" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str
                    or "Quota exceeded" in err_str
                    or "rate limit" in err_str.lower()
                )

                # Zero-delay immediate failover to a backup key on the SAME requested model.
                if is_gemini_rate_limit_error(err_str) and not self._custom_genai_client:
                    tried_keys_for_model.add(current_key)
                    next_key = self._gemini_key_pool.report_rate_limit(
                        current_key,
                        err_str=err_str,
                        model=target_model,
                        tried_keys=tried_keys_for_model,
                    )
                    if next_key is not None:
                        self.api_key = next_key
                        _, self.google_client = self._gemini_key_pool.get_client(
                            api_key=next_key,
                            model=target_model,
                        )
                        continue

                # Check for Free Tier limit: 0 restriction on Gemini 4 Argon / Carbon.
                # Only downgrade the model once every key has failed on target_model.
                is_tier_restricted = (
                    target_model in ("gemini-4-argon", "gemini-4-carbon")
                    and not cfg.model.startswith("opencode/")
                    and is_rate_limit
                    and (
                        "limit: 0" in err_str.lower()
                        or "limit:0" in err_str.lower()
                        or "freetier" in err_str.lower()
                        or "free_tier" in err_str.lower()
                        or "resource_exhausted" in err_str
                    )
                )
                if is_tier_restricted:
                    logger.warning(
                        "Quota tier restricted on %s (limit: 0 on Free Tier: %s). "
                        "Automatically falling back to gemini-4-hydrogen.",
                        target_model,
                        exc,
                    )
                    target_model = "gemini-4-hydrogen"
                    tried_keys_for_model.clear()
                    continue

                # Permanent failures (billing, model deprecation, daily cap)
                if is_permanent_quota_error(err_str) or "Daily" in err_str or "PerDay" in err_str:
                    raise RuntimeError(
                        f"Rate limit / quota reached for {target_model}: {exc}"
                    ) from exc

                if "404 NOT_FOUND" in err_str or "no longer available" in err_str:
                    raise RuntimeError(
                        f"Google GenAI model unavailable or not found for {target_model}: {exc}"
                    ) from exc

                attempt += 1
                tried_keys_for_model.clear()
                if attempt > cfg.max_retries:
                    if is_rate_limit:
                        raise RuntimeError(
                            f"Rate limit / quota reached for {target_model}: {exc}"
                        ) from exc
                    raise RuntimeError(
                        f"Google GenAI request failed for {target_model} after {cfg.max_retries} retries: {exc}"
                    ) from exc

                if is_rate_limit:
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
                        "Google API rate limit (429/RESOURCE_EXHAUSTED) on %s. Retrying in %.1fs (attempt %d/%d)...",
                        target_model,
                        backoff,
                        attempt,
                        cfg.max_retries,
                    )
                else:
                    backoff = min(2.0**attempt, 10.0)
                    logger.warning(
                        "Google API error on %s: %s. Retrying in %.1fs...",
                        target_model,
                        exc,
                        backoff,
                    )

                await asyncio.sleep(backoff)

        raise RuntimeError(f"Exhausted retries without response for {target_model}")

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

        base_model, _ = parse_model_variant(cfg.model)
        target_model = base_model.replace("google/", "")
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
            or cfg.thinking_budget is not None
        )
        thinking_config: types.ThinkingConfig | None = None
        if is_thinking:
            if cfg.thinking_level == "none" or (
                cfg.thinking_budget is not None and cfg.thinking_budget == 0
            ):
                thinking_config = types.ThinkingConfig(thinking_budget=0)
            elif cfg.thinking_level is not None:
                level = cfg.thinking_level
                if level == "minimal":
                    level = "low"
                thinking_config = types.ThinkingConfig(
                    thinking_level=cast(Any, level), include_thoughts=True
                )
            elif cfg.thinking_budget is not None:
                thinking_config = types.ThinkingConfig(thinking_budget=cfg.thinking_budget)

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
        tried_keys_for_model: set[str] = set()
        current_key = self.api_key
        while attempt <= cfg.max_retries:
            try:
                if not self._custom_genai_client:
                    current_key, client = self._gemini_key_pool.get_client(
                        model=target_model,
                        exclude_keys=tried_keys_for_model,
                    )
                    self.api_key = current_key
                    self.google_client = client
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

                # Zero-delay immediate failover to a backup key on the SAME model.
                if is_gemini_rate_limit_error(err_str) and not self._custom_genai_client:
                    tried_keys_for_model.add(current_key)
                    next_key = self._gemini_key_pool.report_rate_limit(
                        current_key,
                        err_str=err_str,
                        model=target_model,
                        tried_keys=tried_keys_for_model,
                    )
                    if next_key is not None:
                        self.api_key = next_key
                        _, self.google_client = self._gemini_key_pool.get_client(
                            api_key=next_key,
                            model=target_model,
                        )
                        continue

                if (
                    is_permanent_quota_error(err_str)
                    or "credit_balance_exhausted" in err_str
                    or "insufficient_quota" in err_str
                    or "404 NOT_FOUND" in err_str
                    or "no longer available" in err_str
                ):
                    raise RuntimeError(f"Google GenAI Live API unavailable: {exc}") from exc

                attempt += 1
                tried_keys_for_model.clear()
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

        # CRITICAL: Strip the "opencode/" prefix and any #variant from model name for OpenCode API calls
        base_model, variant = parse_model_variant(cfg.model)
        target_model = base_model.removeprefix("opencode/")

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

        # Ensure max_tokens sent to _call_openai_compatible() is clamped to at least
        # max(16, cfg.max_tokens) when calling gpt-*-luna / OpenAI models (since gpt-6-luna returns 400 if max_tokens < 16)
        effective_max_tokens = cfg.max_tokens
        if (
            "luna" in target_model.lower()
            or target_model.startswith("gpt-")
            or "openai" in str(getattr(target_client, "base_url", ""))
        ):
            effective_max_tokens = max(16, cfg.max_tokens)

        kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": formatted_messages,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "max_tokens": effective_max_tokens,
        }

        # Map OpenCode #variant (#low, #medium, #high, #max) to reasoning_effort
        if is_opencode and variant:
            variant_norm = variant.lower()
            if variant_norm in ("low", "medium", "high"):
                kwargs["reasoning_effort"] = variant_norm
            elif variant_norm == "max":
                kwargs["reasoning_effort"] = "high"

        # OpenCode Go reasoning models (DeepSeek/GLM/Qwen/Kimi/MiniMax) spend the
        # entire max_tokens budget on hidden reasoning unless thinking is
        # explicitly disabled in the request body.
        if (
            is_opencode
            and not variant
            and (cfg.thinking_budget == 0 or cfg.thinking_level == "none")
            and any(
                marker in target_model.lower()
                for marker in ("deepseek", "glm", "qwen", "kimi", "minimax")
            )
        ):
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        if cfg.seed is not None:
            kwargs["seed"] = cfg.seed

        if response_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        if is_opencode:
            kwargs["extra_headers"] = get_opencode_headers()

        request_timeout = float(getattr(cfg, "timeout_sec", 60.0) or 60.0)
        attempt = 0
        response = None
        while attempt <= cfg.max_retries:
            try:
                response = await asyncio.wait_for(
                    target_client.chat.completions.create(**kwargs),
                    timeout=request_timeout,
                )
                break
            except Exception as exc:
                err_str = str(exc).strip()
                if not err_str:
                    err_str = (
                        f"Timed out after {request_timeout:.1f}s"
                        if isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                        else type(exc).__name__
                    )

                is_rate_limit = (
                    "429" in err_str
                    or "rate_limit" in err_str.lower()
                    or "insufficient_quota" in err_str.lower()
                    or "credit_balance_exhausted" in err_str.lower()
                )

                # Strict provenance: surface quota/rate limit errors honestly without cross-model fallback
                if (
                    "insufficient_quota" in err_str.lower()
                    or "credit_balance_exhausted" in err_str.lower()
                    or (
                        is_opencode
                        and (
                            "freetiererror" in err_str.lower()
                            or "freeusagelimiterror" in err_str.lower()
                            or "free_usage_limit" in err_str.lower()
                            or "only be used from within opencode" in err_str.lower()
                            or "model is unavailable" in err_str.lower()
                            or "upstream request failed" in err_str.lower()
                            or "429" in err_str
                            or "rate_limit" in err_str.lower()
                        )
                    )
                ):
                    prefix = "OpenCode" if is_opencode else "OpenAI"
                    raise RuntimeError(
                        f"{prefix} quota/rate limit reached for {cfg.model}: {err_str}"
                    ) from exc

                # If the gateway rejects the OpenCode `thinking` body extension,
                # retry without extra_body (the model then uses its default).
                if "extra_body" in kwargs and (
                    "400" in err_str
                    or "extra_body" in err_str.lower()
                    or "thinking" in err_str.lower()
                    or "unknown" in err_str.lower()
                    or "unrecognized" in err_str.lower()
                    or "invalid" in err_str.lower()
                ):
                    logger.warning(
                        "Gateway rejected extra_body thinking disable: %s. Retrying without extra_body...",
                        err_str,
                    )
                    kwargs.pop("extra_body", None)
                    continue

                # If gateway / model fails because response_format is not supported, retry without response_format
                if "response_format" in kwargs and (
                    "response_format" in err_str.lower()
                    or "json_object" in err_str.lower()
                    or "schema" in err_str.lower()
                    or "400" in err_str
                ):
                    logger.warning(
                        "Gateway rejected response_format: %s. Retrying without response_format...",
                        err_str,
                    )
                    kwargs.pop("response_format", None)
                    continue

                # If gateway / model fails because reasoning_effort is not supported, retry without it
                if "reasoning_effort" in kwargs and (
                    "reasoning_effort" in err_str.lower() or "unknown parameter" in err_str.lower()
                ):
                    logger.warning(
                        "Gateway rejected reasoning_effort: %s. Retrying without reasoning_effort...",
                        err_str,
                    )
                    kwargs.pop("reasoning_effort", None)
                    continue

                attempt += 1
                if attempt > cfg.max_retries:
                    if is_rate_limit:
                        prefix = "OpenCode" if is_opencode else "OpenAI"
                        raise RuntimeError(
                            f"{prefix} quota/rate limit reached for {cfg.model}: {err_str}"
                        ) from exc
                    raise RuntimeError(
                        f"OpenAI/OpenCode request failed for {cfg.model} after {cfg.max_retries} retries: {err_str}"
                    ) from exc

                is_rate_limit = "429" in err_str or "rate_limit" in err_str.lower()
                backoff = min(2.0**attempt, 10.0) if not is_rate_limit else (5.0 + attempt * 2.0)
                logger.warning(
                    "API error on %s: %s. Retrying in %.1fs...", cfg.model, err_str, backoff
                )
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
            parsed_json = parse_structured_json(raw_text, response_schema=response_schema)

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

    _call_openai_compatible = _generate_openai

    async def _generate_anthropic(
        self,
        messages: list[ChatMessage],
        cfg: LLMConfig,
        response_schema: type[BaseModel] | None = None,
        api_key: str | None = None,
    ) -> LLMResponse:
        """Execute async inference via Anthropic / cross-family frontier provider using litellm."""
        import litellm

        start_time = time.perf_counter()
        formatted_msgs = [{"role": m.role, "content": m.content} for m in messages]
        target_model = cfg.model if "claude" in cfg.model else f"anthropic/{cfg.model}"

        attempt = 0
        while attempt <= cfg.max_retries:
            try:
                resp = await litellm.acompletion(
                    model=target_model,
                    messages=formatted_msgs,
                    api_key=api_key,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                    timeout=cfg.timeout_sec,
                )
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                resp_obj: Any = resp
                raw_text = resp_obj.choices[0].message.content or ""
                usage_obj = getattr(resp_obj, "usage", None)
                prompt_toks = (getattr(usage_obj, "prompt_tokens", 0) or 0) if usage_obj else 0
                comp_toks = (getattr(usage_obj, "completion_tokens", 0) or 0) if usage_obj else 0
                tot_toks = (
                    (getattr(usage_obj, "total_tokens", 0) or 0)
                    if usage_obj
                    else (prompt_toks + comp_toks)
                )

                parsed_json = None
                if (
                    response_schema is not None
                    or raw_text.strip().startswith("{")
                    or "{" in raw_text
                ):
                    parsed_json = parse_structured_json(raw_text, response_schema=response_schema)

                return LLMResponse(
                    content=raw_text,
                    role="assistant",
                    finish_reason="stop",
                    usage=LLMUsage(
                        prompt_tokens=prompt_toks,
                        completion_tokens=comp_toks,
                        total_tokens=tot_toks,
                        latency_ms=round(elapsed_ms, 2),
                    ),
                    parsed_json=parsed_json,
                )
            except Exception as exc:
                err_str = str(exc)
                is_rate_limit = (
                    "429" in err_str
                    or "rate_limit" in err_str.lower()
                    or "insufficient_quota" in err_str.lower()
                    or "credit" in err_str.lower()
                )
                if (
                    "authentication" in err_str.lower()
                    or "invalid_api_key" in err_str.lower()
                    or "permission" in err_str.lower()
                    or "credit" in err_str.lower()
                    or "insufficient_quota" in err_str.lower()
                ):
                    raise RuntimeError(f"Anthropic API error for {cfg.model}: {exc}") from exc

                attempt += 1
                if attempt > cfg.max_retries:
                    if is_rate_limit:
                        raise RuntimeError(
                            f"Anthropic quota/rate limit reached for {cfg.model}: {exc}"
                        ) from exc
                    raise RuntimeError(
                        f"Anthropic request failed for {cfg.model} after {cfg.max_retries} retries: {exc}"
                    ) from exc

                backoff = min(2.0**attempt, 10.0) if not is_rate_limit else (5.0 + attempt * 2.0)
                await asyncio.sleep(backoff)

        raise RuntimeError(f"Exhausted retries without response for {cfg.model}")
