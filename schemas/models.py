"""Model Catalog & Pricing Registry for OpenEval Studio.

Defines metadata, token limits, pricing specifications, and capabilities
for frontier LLM providers (Google Gemini, OpenAI).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ModelProvider = Literal["google", "openai", "anthropic", "custom"]
ModelTier = Literal["flagship", "balanced", "fast", "preview", "specialized"]


class ModelSpec(BaseModel):
    """Specification and metadata for an evaluation model."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="API model endpoint identifier (e.g. gemini-3.7-flash)")
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

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate estimated inference cost in USD for a given token usage."""
        input_cost = (prompt_tokens / 1_000_000.0) * self.input_cost_per_m
        output_cost = (completion_tokens / 1_000_000.0) * self.output_cost_per_m
        return round(input_cost + output_cost, 6)


# Complete registry of active Gemini & OpenAI evaluation models
MODEL_CATALOG: list[ModelSpec] = [
    # --- GOOGLE GEMINI 3 SERIES ---
    ModelSpec(
        id="gemini-3.7-flash",
        name="Gemini 3.7 Flash",
        provider="google",
        tier="flagship",
        description="Most capable Flash model, built for complex coding and agentic loops.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.15,
        output_cost_per_m=0.60,
        capabilities=["coding", "agentic", "tool_use", "reasoning"],
        is_default=False,
    ),
    ModelSpec(
        id="gemini-3.1-flash-lite",
        name="Gemini 3.1 Flash-Lite",
        provider="google",
        tier="fast",
        description="Ultra-fast, cost-effective frontier performance for high-throughput sweeps.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.075,
        output_cost_per_m=0.30,
        capabilities=["fast", "agentic", "tool_use", "high_throughput"],
        is_default=True,
    ),
    ModelSpec(
        id="gemini-3.6-flash",
        name="Gemini 3.6 Flash",
        provider="google",
        tier="balanced",
        description="Previous-gen stable Flash model balancing speed and multimodal tasks.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.10,
        output_cost_per_m=0.40,
        capabilities=["balanced", "agentic", "tool_use"],
    ),
    ModelSpec(
        id="gemini-3.1-pro-preview",
        name="Gemini 3.1 Pro (Preview)",
        provider="google",
        tier="preview",
        description="Advanced intelligence and complex problem-solving for hard benchmark tasks.",
        context_window=2_097_152,
        max_output_tokens=8192,
        input_cost_per_m=1.25,
        output_cost_per_m=5.00,
        capabilities=["deep_reasoning", "coding", "agentic"],
    ),
    ModelSpec(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        provider="google",
        tier="balanced",
        description="Stable 2.5 workhorse model with high reasoning capacity and low latency.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.10,
        output_cost_per_m=0.40,
        capabilities=["balanced", "tool_use"],
    ),
    ModelSpec(
        id="gemini-2.5-pro",
        name="Gemini 2.5 Pro",
        provider="google",
        tier="flagship",
        description="Deep reasoning and coding capabilities across the 2.5 family.",
        context_window=2_097_152,
        max_output_tokens=8192,
        input_cost_per_m=1.25,
        output_cost_per_m=5.00,
        capabilities=["deep_reasoning", "coding"],
    ),

    # --- OPENAI SERIES ---
    ModelSpec(
        id="gpt-4o",
        name="GPT-4o",
        provider="openai",
        tier="flagship",
        description="Flagship multimodal omni model for complex technical workflows.",
        context_window=128000,
        max_output_tokens=16384,
        input_cost_per_m=2.50,
        output_cost_per_m=10.00,
        capabilities=["coding", "agentic", "tool_use", "reasoning"],
        is_default=True,
    ),
    ModelSpec(
        id="gpt-4o-mini",
        name="GPT-4o Mini",
        provider="openai",
        tier="fast",
        description="Affordable, low-latency small model for fast benchmark iterations.",
        context_window=128000,
        max_output_tokens=16384,
        input_cost_per_m=0.15,
        output_cost_per_m=0.60,
        capabilities=["fast", "tool_use"],
    ),
    ModelSpec(
        id="o3-mini",
        name="o3-mini",
        provider="openai",
        tier="specialized",
        description="Reasoning-specialized model with native chain-of-thought search.",
        context_window=200000,
        max_output_tokens=100000,
        input_cost_per_m=1.10,
        output_cost_per_m=4.40,
        capabilities=["deep_reasoning", "math", "coding"],
    ),
]


def get_available_models(provider: ModelProvider | None = None) -> list[ModelSpec]:
    """Retrieve filtered list of available models for evaluation suites."""
    if provider is None:
        return MODEL_CATALOG
    return [m for m in MODEL_CATALOG if m.provider == provider]


def get_model_spec(model_id: str) -> ModelSpec | None:
    """Find a specific model specification by its endpoint slug."""
    for m in MODEL_CATALOG:
        if m.id == model_id:
            return m
    return None


def get_default_model(provider: ModelProvider = "google") -> ModelSpec:
    """Retrieve the primary default model for a given provider."""
    for m in MODEL_CATALOG:
        if m.provider == provider and m.is_default:
            return m
    models = get_available_models(provider)
    if models:
        return models[0]
    return MODEL_CATALOG[0]
