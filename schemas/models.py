"""Model Catalog & Pricing Registry for OpenEval Studio.

Defines metadata, token limits, pricing specifications, and capabilities
for frontier LLM providers (Google Gemini 3 / 2.5, OpenAI, Anthropic, Ollama, vLLM).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ModelProvider = Literal["google", "openai", "anthropic", "custom", "ollama", "vllm"]
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


# Complete registry of active Gemini 3, Gemini 2.5, OpenAI, Claude, and Local models
MODEL_CATALOG: list[ModelSpec] = [
    # =========================================================================
    # GOOGLE GEMINI & GEMMA ACTIVE TEXT-OUT MODELS (RPD > 0)
    # =========================================================================
    ModelSpec(
        id="gemini-3.1-flash-lite",
        name="Gemini 3.1 Flash-Lite",
        provider="google",
        tier="fast",
        description="High quota 500 RPD, 15 RPM. Frontier speed and cost-efficiency for evaluation benchmarks.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.075,
        output_cost_per_m=0.30,
        capabilities=["fast", "agentic", "tool_use", "high_throughput"],
        is_default=True,
    ),
    ModelSpec(
        id="gemini-3.5-flash-lite",
        name="Gemini 3.5 Flash-Lite",
        provider="google",
        tier="fast",
        description="High quota 500 RPD, 15 RPM. Fast, cost-effective 3.5 model for high-throughput evaluation loops.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.075,
        output_cost_per_m=0.30,
        capabilities=["fast", "agentic", "tool_use"],
    ),
    ModelSpec(
        id="gemini-3.7-flash",
        name="Gemini 3.7 Flash",
        provider="google",
        tier="flagship",
        description="Most capable Flash model, built for complex coding and agentic reasoning.",
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
        description="Balanced speed and agentic tool-use capabilities.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.10,
        output_cost_per_m=0.40,
        capabilities=["balanced", "agentic", "tool_use"],
    ),
    ModelSpec(
        id="gemini-3.5-flash",
        name="Gemini 3.5 Flash",
        provider="google",
        tier="balanced",
        description="Standard Flash model providing baseline speed for routine agentic tasks.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.10,
        output_cost_per_m=0.40,
        capabilities=["balanced", "fast", "tool_use"],
    ),
    ModelSpec(
        id="gemini-3-flash-preview",
        name="Gemini 3 Flash",
        provider="google",
        tier="preview",
        description="Preview Flash model with frontier-class speed and reasoning capabilities.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.10,
        output_cost_per_m=0.40,
        capabilities=["fast", "agentic", "preview"],
    ),
    ModelSpec(
        id="gemini-2.5-flash-lite",
        name="Gemini 2.5 Flash-Lite",
        provider="google",
        tier="fast",
        description="Fast budget-friendly model in the 2.5 family.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.075,
        output_cost_per_m=0.30,
        capabilities=["fast", "tool_use"],
    ),
    ModelSpec(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        provider="google",
        tier="balanced",
        description="Low-latency reasoning and tool use in the 2.5 generation.",
        context_window=1_048_576,
        max_output_tokens=8192,
        input_cost_per_m=0.10,
        output_cost_per_m=0.40,
        capabilities=["coding", "agentic", "tool_use", "fast"],
    ),
    ModelSpec(
        id="antigravity-preview-05-2026",
        name="Antigravity Agent",
        provider="google",
        tier="specialized",
        description="Managed agent that autonomously plans, codes, and executes in sandboxes.",
        context_window=2_097_152,
        max_output_tokens=65536,
        input_cost_per_m=2.00,
        output_cost_per_m=8.00,
        capabilities=["agentic", "coding", "sandbox_execution", "reasoning"],
    ),
    ModelSpec(
        id="gemma-4-26b",
        name="Gemma 4 26B",
        provider="google",
        tier="fast",
        description="Google open-weight 26B model hosted on AI Studio with high quota 14400 RPD.",
        context_window=128000,
        max_output_tokens=8192,
        input_cost_per_m=0.05,
        output_cost_per_m=0.20,
        capabilities=["fast", "coding", "open_weights"],
    ),
    ModelSpec(
        id="gemma-4-31b",
        name="Gemma 4 31B",
        provider="google",
        tier="balanced",
        description="Google open-weight 31B instruction-tuned model with high quota 14400 RPD.",
        context_window=128000,
        max_output_tokens=8192,
        input_cost_per_m=0.07,
        output_cost_per_m=0.25,
        capabilities=["balanced", "reasoning", "open_weights"],
    ),
    # =========================================================================
    # OPENAI FRONTIER MODELS
    # =========================================================================
    ModelSpec(
        id="gpt-4o",
        name="GPT-4o",
        provider="openai",
        tier="flagship",
        description="OpenAI flagship omni model for advanced reasoning and tool interaction.",
        context_window=128000,
        max_output_tokens=16384,
        input_cost_per_m=2.50,
        output_cost_per_m=10.00,
        capabilities=["reasoning", "agentic", "multimodal"],
        is_default=True,
    ),
    ModelSpec(
        id="gpt-4o-mini",
        name="GPT-4o Mini",
        provider="openai",
        tier="fast",
        description="Cost-efficient small model for rapid, lightweight evaluation testing.",
        context_window=128000,
        max_output_tokens=16384,
        input_cost_per_m=0.15,
        output_cost_per_m=0.60,
        capabilities=["fast", "tool_use"],
    ),
    ModelSpec(
        id="o1",
        name="o1",
        provider="openai",
        tier="specialized",
        description="Flagship chain-of-thought reasoning model for hard engineering and safety.",
        context_window=200000,
        max_output_tokens=100000,
        input_cost_per_m=15.00,
        output_cost_per_m=60.00,
        capabilities=["deep_reasoning", "math", "coding"],
    ),
    ModelSpec(
        id="o3-mini",
        name="o3-mini",
        provider="openai",
        tier="specialized",
        description="Fast reasoning-specialized model with native chain-of-thought search.",
        context_window=200000,
        max_output_tokens=100000,
        input_cost_per_m=1.10,
        output_cost_per_m=4.40,
        capabilities=["deep_reasoning", "math", "coding"],
    ),
    # =========================================================================
    # ANTHROPIC CLAUDE MODELS
    # =========================================================================
    ModelSpec(
        id="claude-3-7-sonnet-latest",
        name="Claude 3.7 Sonnet",
        provider="anthropic",
        tier="flagship",
        description="Hybrid reasoning and instant model with state-of-the-art coding abilities.",
        context_window=200000,
        max_output_tokens=8192,
        input_cost_per_m=3.00,
        output_cost_per_m=15.00,
        capabilities=["coding", "agentic", "hybrid_reasoning"],
    ),
    ModelSpec(
        id="claude-3-5-sonnet-latest",
        name="Claude 3.5 Sonnet",
        provider="anthropic",
        tier="flagship",
        description="Industry standard for coding agents and complex multi-step reasoning.",
        context_window=200000,
        max_output_tokens=8192,
        input_cost_per_m=3.00,
        output_cost_per_m=15.00,
        capabilities=["coding", "agentic", "tool_use"],
    ),
    ModelSpec(
        id="claude-3-5-haiku-latest",
        name="Claude 3.5 Haiku",
        provider="anthropic",
        tier="fast",
        description="Ultra-fast, high-intelligence model matching previous flagship capabilities.",
        context_window=200000,
        max_output_tokens=8192,
        input_cost_per_m=0.80,
        output_cost_per_m=4.00,
        capabilities=["fast", "agentic", "tool_use"],
    ),
    # =========================================================================
    # LOCAL OPEN-SOURCE MODELS
    # =========================================================================
    ModelSpec(
        id="ollama/llama3.2:3b",
        name="Llama 3.2 3B",
        provider="ollama",
        tier="fast",
        description="Lightweight local model with high speed and zero inference cost.",
        context_window=128000,
        max_output_tokens=4096,
        input_cost_per_m=0.0,
        output_cost_per_m=0.0,
        capabilities=["local", "fast", "privacy"],
    ),
    ModelSpec(
        id="ollama/qwen2.5-coder:7b",
        name="Qwen 2.5 Coder 7B",
        provider="ollama",
        tier="fast",
        description="Locally-hosted coding-specialized model with zero API latency and zero cost.",
        context_window=128000,
        max_output_tokens=8192,
        input_cost_per_m=0.0,
        output_cost_per_m=0.0,
        capabilities=["local", "coding", "agentic"],
    ),
    ModelSpec(
        id="ollama/qwen2.5-coder",
        name="Qwen 2.5 Coder",
        provider="ollama",
        tier="fast",
        description="Local coding-specialized model with zero API latency and zero cost.",
        context_window=128000,
        max_output_tokens=8192,
        input_cost_per_m=0.0,
        output_cost_per_m=0.0,
        capabilities=["local", "coding", "agentic"],
    ),
    ModelSpec(
        id="ollama/llama3.1",
        name="Llama 3.1 8B",
        provider="ollama",
        tier="fast",
        description="Locally-hosted Meta Llama 3.1 8B via Ollama with zero token cost.",
        context_window=128000,
        max_output_tokens=4096,
        input_cost_per_m=0.0,
        output_cost_per_m=0.0,
        capabilities=["local", "privacy", "tool_use"],
    ),
    ModelSpec(
        id="ollama/deepseek-r1",
        name="DeepSeek R1",
        provider="ollama",
        tier="specialized",
        description="Locally-hosted open-weights reasoning model with deep reasoning traces.",
        context_window=128000,
        max_output_tokens=8192,
        input_cost_per_m=0.0,
        output_cost_per_m=0.0,
        capabilities=["local", "reasoning", "privacy"],
    ),
    ModelSpec(
        id="vllm/meta-llama/Llama-3.3-70B-Instruct",
        name="Llama 3.3 70B",
        provider="vllm",
        tier="flagship",
        description="High-throughput self-hosted frontier-grade 70B model with full agentic capabilities.",
        context_window=128000,
        max_output_tokens=8192,
        input_cost_per_m=0.0,
        output_cost_per_m=0.0,
        capabilities=["local", "reasoning", "coding", "high_throughput"],
    ),
    ModelSpec(
        id="vllm/meta-llama/Llama-3-8B-Instruct",
        name="Llama 3 8B",
        provider="vllm",
        tier="specialized",
        description="High-throughput vLLM inference server supporting dynamic LoRA adapters.",
        context_window=128000,
        max_output_tokens=4096,
        input_cost_per_m=0.0,
        output_cost_per_m=0.0,
        capabilities=["local", "lora", "high_throughput"],
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
