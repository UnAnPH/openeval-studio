"""Model Catalog & Pricing Registry for OpenEval Studio.

Defines metadata, token limits, pricing specifications, and capabilities
for active evaluation models (Google Gemini and OpenCode).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ModelProvider = Literal["google", "openai", "anthropic", "custom", "ollama", "vllm", "opencode"]
ModelTier = Literal["flagship", "balanced", "fast", "preview", "specialized"]


class ModelSpec(BaseModel):
    """Specification and metadata for an evaluation model."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="API model endpoint identifier (e.g. gemini-3.8-flash)")
    name: str = Field(..., description="Human-readable display name")
    provider: ModelProvider = Field(..., description="Provider organization")
    tier: ModelTier = Field(..., description="Capability and latency tier")
    description: str = Field(..., description="Summary of architecture and recommended use cases")
    context_window: int = Field(default=128000, description="Max input context window in tokens")
    max_output_tokens: int = Field(default=8192, description="Max completion generation tokens")
    input_cost_per_m: float = Field(default=0.0, description="Cost in USD per 1M input tokens")
    output_cost_per_m: float = Field(default=0.0, description="Cost in USD per 1M output tokens")
    capabilities: list[str] = Field(default_factory=list, description="Supported capability tags")
    is_default: bool = Field(default=False, description="Default model for provider")
    eval_capable: bool = Field(
        default=True,
        description="Whether model is runnable as a general evaluation agent",
    )

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate estimated inference cost in USD for a given token usage."""
        input_cost = (prompt_tokens / 1_000_000.0) * self.input_cost_per_m
        output_cost = (completion_tokens / 1_000_000.0) * self.output_cost_per_m
        return round(input_cost + output_cost, 6)


# Complete registry of active Gemini, OpenCode, and Decision models
MODEL_CATALOG: list[ModelSpec] = [
    # =========================================================================
    # GOOGLE GEMINI SPECIALIZED DECISIONS API (0 OUTPUT TOKENS)
    # =========================================================================
    ModelSpec(
        id="gemini-decision-1",
        name="Gemini Decision 1 (Calibrated Prefill)",
        provider="google",
        tier="specialized",
        context_window=262144,
        max_output_tokens=0,
        input_cost_per_m=0.075,
        output_cost_per_m=0.0,
        capabilities=[
            "calibrated_decisions",
            "noul",
            "choice",
            "score",
            "multimodal",
            "zero_output_tokens",
        ],
        eval_capable=False,
        description="Single-pass prefill model for the Decisions API. Evaluates up to 1,000 typed questions (noul, choice, score) with calibrated probabilities and 0 output tokens (~184ms).",
    ),
    # =========================================================================
    # GOOGLE GEMINI ACTIVE TEXT-OUT MODELS (Ordered by family & version)
    # =========================================================================
    ModelSpec(
        id="gemini-4-hydrogen",
        name="Gemini 4 Hydrogen",
        provider="google",
        tier="balanced",
        context_window=1_048_576,
        max_output_tokens=65536,
        input_cost_per_m=0.10,
        output_cost_per_m=0.40,
        capabilities=["text", "code", "thinking", "multimodal", "free_tier_available"],
        description="Standard quota (5 RPM / 20 RPD). Balanced Gemini 4 reasoning & coding model. Live availability on Google AI Studio Free Tier (ideal for asynchronous Tier 3 Deep Review and offline C1–C15 audits).",
    ),
    ModelSpec(
        id="gemini-3.8-flash",
        name="Gemini 3.8 Flash",
        provider="google",
        tier="flagship",
        description="Standard quota (5 RPM / 20 RPD). Frontier Flash model built for complex coding and agentic reasoning.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.15,
        output_cost_per_m=0.60,
        capabilities=["coding", "agentic", "tool_use", "reasoning"],
    ),
    ModelSpec(
        id="gemini-3.6-flash",
        name="Gemini 3.6 Flash",
        provider="google",
        tier="balanced",
        description="Standard quota (5 RPM / 20 RPD). Balanced speed and agentic tool-use capabilities.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.10,
        output_cost_per_m=0.40,
        capabilities=["balanced", "agentic", "tool_use"],
    ),
    ModelSpec(
        id="gemini-3.5-flash-lite",
        name="Gemini 3.5 Flash-Lite",
        provider="google",
        tier="fast",
        description="High quota (500 RPD / 15 RPM). Fast, cost-effective 3.5 model for high-throughput evaluation loops.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.075,
        output_cost_per_m=0.30,
        capabilities=["fast", "agentic", "tool_use", "high_throughput"],
        is_default=True,
    ),
    ModelSpec(
        id="gemini-3.1-flash-lite",
        name="Gemini 3.1 Flash-Lite",
        provider="google",
        tier="fast",
        description="High quota (500 RPD / 15 RPM). Frontier speed and cost-efficiency for evaluation benchmarks.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.075,
        output_cost_per_m=0.30,
        capabilities=["fast", "agentic", "tool_use", "high_throughput"],
    ),
    ModelSpec(
        id="gemini-3-flash-preview",
        name="Gemini 3 Flash",
        provider="google",
        tier="preview",
        description="Standard quota (5 RPM / 20 RPD). Preview Flash model with frontier-class speed and reasoning capabilities.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.10,
        output_cost_per_m=0.40,
        capabilities=["fast", "agentic", "preview"],
    ),
    # =========================================================================
    # GOOGLE GEMMA ACTIVE OPEN-WEIGHT MODELS
    # =========================================================================
    ModelSpec(
        id="gemma-4-31b-it",
        name="Gemma 4 31B",
        provider="google",
        tier="balanced",
        description="High quota (30 RPM / 16K TPM / 14.4K RPD). Google open-weight 31B instruction-tuned model with thinking support.",
        context_window=131_072,
        max_output_tokens=8192,
        input_cost_per_m=0.07,
        output_cost_per_m=0.25,
        capabilities=["balanced", "reasoning", "open_weights", "thinking", "high_throughput"],
    ),
    ModelSpec(
        id="gemma-4-26b-it",
        name="Gemma 4 26B",
        provider="google",
        tier="fast",
        description="High quota (30 RPM / 16K TPM / 14.4K RPD). Google open-weight 26B instruction-tuned model with thinking support.",
        context_window=131_072,
        max_output_tokens=8192,
        input_cost_per_m=0.05,
        output_cost_per_m=0.20,
        capabilities=["fast", "coding", "open_weights", "thinking", "high_throughput"],
    ),
    # =========================================================================
    # GOOGLE GEMINI LIVE API STREAMING MODELS (NOT RUNNABLE AGENTS FOR #LAUNCH)
    # =========================================================================
    ModelSpec(
        id="gemini-3.8-live-extended-thinking",
        name="Gemini 3.8 Live Extended Thinking",
        provider="google",
        tier="flagship",
        description="Live API WebSocket model with extended thinking enabled for deep agentic evaluation runs.",
        context_window=131_072,
        max_output_tokens=65_536,
        input_cost_per_m=0.15,
        output_cost_per_m=0.60,
        capabilities=[
            "coding",
            "agentic",
            "tool_use",
            "reasoning",
            "live_api",
            "extended_thinking",
        ],
        eval_capable=False,
    ),
    ModelSpec(
        id="gemini-3.8-live",
        name="Gemini 3.8 Live",
        provider="google",
        tier="flagship",
        description="Live API WebSocket model for high-throughput agentic evaluation runs.",
        context_window=131_072,
        max_output_tokens=65_536,
        input_cost_per_m=0.15,
        output_cost_per_m=0.60,
        capabilities=["coding", "agentic", "tool_use", "reasoning", "live_api"],
        eval_capable=False,
    ),
    # =========================================================================
    # OPENCODE GO INFERENCE API MODELS ($10/mo Subscription, 1M context)
    # Grouped by model family (alphabetical), supports #variant tags
    # =========================================================================
    ModelSpec(
        id="opencode/claude-haiku-5-5",
        name="Claude Haiku 5.5",
        provider="opencode",
        tier="fast",
        description="Fast, cost-efficient Anthropic Claude Haiku 5.5 model via OpenCode Go subscription ($10/mo). Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_000_000,
        max_output_tokens=8192,
        input_cost_per_m=0.10,
        output_cost_per_m=0.50,
        capabilities=["fast", "coding", "agentic", "tool_use", "go", "variants_supported"],
    ),
    ModelSpec(
        id="opencode/deepseek-v4.1-flash",
        name="DeepSeek V4.1 Flash (Go)",
        provider="opencode",
        tier="fast",
        description="High-speed frontier coding model via OpenCode Go subscription ($10/mo). Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_000_000,
        max_output_tokens=8192,
        input_cost_per_m=0.15,
        output_cost_per_m=0.60,
        capabilities=["fast", "coding", "agentic", "tool_use", "go", "variants_supported"],
        is_default=True,
    ),
    ModelSpec(
        id="opencode/deepseek-v4-pro",
        name="DeepSeek V4 Pro (Go)",
        provider="opencode",
        tier="flagship",
        description="Frontier reasoning and code synthesis model via OpenCode Go subscription. Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_000_000,
        max_output_tokens=8192,
        input_cost_per_m=0.66,
        output_cost_per_m=1.98,
        capabilities=["coding", "agentic", "reasoning", "flagship", "go", "variants_supported"],
    ),
    ModelSpec(
        id="opencode/gemini-4-argon",
        name="Gemini 4 Argon (Go)",
        provider="opencode",
        tier="flagship",
        description="Google Gemini 4 Argon frontier reasoning and multimodal model via OpenCode Go subscription ($10/mo). Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=2.00,
        output_cost_per_m=10.00,
        capabilities=["flagship", "reasoning", "coding", "agentic", "go", "variants_supported"],
    ),
    ModelSpec(
        id="opencode/glm-5.3-flash",
        name="GLM 5.3 Flash (Go)",
        provider="opencode",
        tier="fast",
        description="High-throughput low-latency GLM 5.3 model via OpenCode Go subscription. Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_000_000,
        max_output_tokens=8192,
        input_cost_per_m=0.15,
        output_cost_per_m=0.50,
        capabilities=["fast", "agentic", "tool_use", "go", "variants_supported"],
    ),
    ModelSpec(
        id="opencode/glm-5.3",
        name="GLM 5.3 Flagship (Go)",
        provider="opencode",
        tier="flagship",
        description="Flagship GLM 5.3 reasoning and tool-use model via OpenCode Go subscription. Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_000_000,
        max_output_tokens=8192,
        input_cost_per_m=1.40,
        output_cost_per_m=4.40,
        capabilities=["flagship", "reasoning", "coding", "go", "variants_supported"],
    ),
    ModelSpec(
        id="opencode/gpt-6-luna",
        name="GPT-6 Luna",
        provider="opencode",
        tier="fast",
        description="Ultra-low latency GPT-6 Luna model via OpenCode Go subscription ($10/mo). Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_000_000,
        max_output_tokens=8192,
        input_cost_per_m=0.10,
        output_cost_per_m=0.50,
        capabilities=["fast", "coding", "agentic", "tool_use", "go", "variants_supported"],
    ),
    ModelSpec(
        id="opencode/grok-4.7",
        name="Grok 4.7",
        provider="opencode",
        tier="flagship",
        description="Frontier xAI Grok 4.7 reasoning and code generation model via OpenCode Go subscription ($10/mo). Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_000_000,
        max_output_tokens=8192,
        input_cost_per_m=2.00,
        output_cost_per_m=6.00,
        capabilities=["flagship", "reasoning", "coding", "agentic", "go", "variants_supported"],
    ),
    ModelSpec(
        id="opencode/kimi-k3",
        name="Kimi K3 (Go)",
        provider="opencode",
        tier="flagship",
        description="Massive context Kimi K3 reasoning model via OpenCode Go subscription. Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=3.00,
        output_cost_per_m=15.00,
        capabilities=["reasoning", "long_context", "agentic", "go", "variants_supported"],
    ),
    ModelSpec(
        id="opencode/kimi-k3.1-pro",
        name="Kimi K3.1 Pro",
        provider="opencode",
        tier="flagship",
        description="Massive context Kimi K3.1 Pro reasoning model via OpenCode Go subscription ($10/mo). Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=3.00,
        output_cost_per_m=15.00,
        capabilities=[
            "flagship",
            "reasoning",
            "long_context",
            "agentic",
            "go",
            "variants_supported",
        ],
    ),
    ModelSpec(
        id="opencode/minimax-m3",
        name="MiniMax M3 (Go)",
        provider="opencode",
        tier="balanced",
        description="Balanced multi-turn conversational and coding model via OpenCode Go subscription. Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_000_000,
        max_output_tokens=8192,
        input_cost_per_m=0.30,
        output_cost_per_m=1.20,
        capabilities=["balanced", "agentic", "tool_use", "go", "variants_supported"],
    ),
    ModelSpec(
        id="opencode/minimax-m3.1-flash",
        name="MiniMax M3.1 Flash",
        provider="opencode",
        tier="fast",
        description="Low-latency MiniMax M3.1 Flash conversational and coding model via OpenCode Go subscription ($10/mo). Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_000_000,
        max_output_tokens=8192,
        input_cost_per_m=0.30,
        output_cost_per_m=1.20,
        capabilities=["fast", "balanced", "agentic", "tool_use", "go", "variants_supported"],
    ),
    ModelSpec(
        id="opencode/qwen3.8-flash",
        name="Qwen 3.8 Flash",
        provider="opencode",
        tier="fast",
        description="High-speed Alibaba Qwen 3.8 Flash model via OpenCode Go subscription ($10/mo). Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_000_000,
        max_output_tokens=8192,
        input_cost_per_m=0.15,
        output_cost_per_m=0.47,
        capabilities=["fast", "coding", "agentic", "tool_use", "go", "variants_supported"],
    ),
    ModelSpec(
        id="opencode/qwen3.8-max",
        name="Qwen 3.8 Max (Go)",
        provider="opencode",
        tier="flagship",
        description="Frontier Qwen 3.8 Max model via OpenCode Go subscription. Supports #variant suffixes (#low, #medium, #high, #max).",
        context_window=1_000_000,
        max_output_tokens=8192,
        input_cost_per_m=2.00,
        output_cost_per_m=6.00,
        capabilities=["flagship", "coding", "agentic", "reasoning", "go", "variants_supported"],
    ),
]


def parse_model_variant(model_id: str) -> tuple[str, str | None]:
    """Parse model identifier and optional variant suffix (e.g. #low, #medium, #high, #max).

    Example:
        parse_model_variant("opencode/deepseek-v4-pro#high") -> ("opencode/deepseek-v4-pro", "high")
        parse_model_variant("gemini-3.1-flash-lite") -> ("gemini-3.1-flash-lite", None)
    """
    if "#" in model_id:
        base, variant = model_id.split("#", 1)
        cleaned_variant = variant.strip()
        return base.strip(), cleaned_variant if cleaned_variant else None
    return model_id.strip(), None


def get_available_models(
    provider: ModelProvider | None = None,
    eval_only: bool = False,
) -> list[ModelSpec]:
    """Retrieve filtered list of available models for evaluation suites."""
    models = MODEL_CATALOG
    if provider is not None:
        models = [m for m in models if m.provider == provider]
    if eval_only:
        models = [m for m in models if m.eval_capable]
    return models


def infer_model_family(model_id: str | None) -> str:
    """Infer the canonical model family from a model identifier or slug.

    Returns one of: 'google', 'deepseek', 'glm', 'qwen', 'kimi', 'minimax',
    'anthropic', 'openai', or 'other'.
    """
    if not model_id:
        return "other"

    clean_id, _ = parse_model_variant(model_id)
    cid = clean_id.lower()
    if "deepseek" in cid:
        return "deepseek"
    if "glm" in cid:
        return "glm"
    if "qwen" in cid:
        return "qwen"
    if "kimi" in cid:
        return "kimi"
    if "minimax" in cid:
        return "minimax"
    if "gemini" in cid or "google" in cid or "gemma" in cid:
        return "google"
    if "claude" in cid or "anthropic" in cid:
        return "anthropic"
    if (
        "gpt" in cid
        or "openai" in cid
        or cid.startswith("o1")
        or cid.startswith("o3")
        or cid.startswith("o4")
    ):
        return "openai"
    return "other"


def get_model_spec(model_id: str) -> ModelSpec | None:
    """Find a specific model specification by its endpoint slug."""
    clean_id, _ = parse_model_variant(model_id)
    if clean_id == "gemma-4-26b-a4b-it":
        clean_id = "gemma-4-26b-it"
    for m in MODEL_CATALOG:
        if m.id == clean_id:
            return m
        # Support looking up models with or without provider prefix (e.g. opencode/ or google/)
        if m.provider == "opencode":
            if m.id.removeprefix("opencode/") == clean_id.removeprefix("opencode/"):
                return m
        elif m.id.removeprefix(f"{m.provider}/") == clean_id.removeprefix(f"{m.provider}/"):
            return m
    return None


def resolve_model_provider(model_id: str) -> ModelProvider:
    """Resolve the appropriate provider string for a given model ID or slug."""
    clean_id, _ = parse_model_variant(model_id)
    spec = get_model_spec(clean_id)
    if spec:
        return spec.provider
    if clean_id.startswith("opencode/") or clean_id == "big-pickle" or clean_id.endswith("-free"):
        return "opencode"
    if clean_id.startswith("gpt-") or clean_id.startswith("o1") or clean_id.startswith("o3"):
        return "openai"
    if clean_id.startswith("claude-"):
        return "anthropic"
    if clean_id.startswith("ollama/"):
        return "ollama"
    if clean_id.startswith("vllm/"):
        return "vllm"
    return "google"


def get_default_model(provider: ModelProvider = "google") -> ModelSpec:
    """Retrieve the primary default model for a given provider."""
    for m in MODEL_CATALOG:
        if m.provider == provider and m.is_default and m.eval_capable:
            return m
    models = get_available_models(provider, eval_only=True)
    if models:
        return models[0]
    for m in MODEL_CATALOG:
        if m.is_default and m.eval_capable:
            return m
    return MODEL_CATALOG[0]


def select_independent_judge_model(evaluated_model: str | None = None) -> str:
    """Select an independent, cross-family judge model ensuring evaluated models never judge themselves.

    Cross-family rule:
    1. For Google / Gemini / Gemma evaluated models (including opencode/gemini-4-argon):
       Use the cross-family OpenCode Go judge 'opencode/glm-5.3-flash'.
    2. For all other evaluated models (DeepSeek, GLM, Kimi, MiniMax, Qwen, Claude, GPT, Grok):
       Use the Google Gemini judge 'gemini-3.5-flash-lite' when a Gemini key is available.
       Otherwise fall back to a cross-family OpenCode Go judge:
       'opencode/glm-5.3-flash' for DeepSeek runs and 'opencode/deepseek-v4.1-flash' otherwise.
    """
    clean_eval = (evaluated_model or "").strip().lower()
    is_gemini_or_gemma = (
        "gemini" in clean_eval or "gemma" in clean_eval or clean_eval.startswith("google/")
    )

    if is_gemini_or_gemma:
        # Cross-family: Google/Gemini models are judged by OpenCode Go (GLM family)
        return "opencode/glm-5.3-flash"

    # Non-Gemini evaluated models: prefer Google Gemini when a key is available
    from engine.gemini_key_pool import discover_gemini_keys

    if discover_gemini_keys():
        return "gemini-3.5-flash-lite"

    # Fallback when GEMINI_API_KEY is not set
    if "deepseek" in clean_eval:
        return "opencode/glm-5.3-flash"
    return "opencode/deepseek-v4.1-flash"
