"""Unit tests for Model Catalog & Pricing Registry."""

from schemas.models import (
    MODEL_CATALOG,
    get_available_models,
    get_default_model,
    get_model_spec,
    resolve_model_provider,
)


def test_model_catalog_entries() -> None:
    """Verify that catalog contains flagship models for Google, OpenAI, and OpenCode."""
    assert len(MODEL_CATALOG) >= 8

    google_models = get_available_models("google")
    assert any(m.id == "gemini-3.8-flash" for m in google_models)
    assert any(m.id == "gemini-3.7-flash" for m in google_models)
    assert any(m.id == "gemini-3.6-flash" for m in google_models)
    assert any(m.id == "gemini-3.5-flash" for m in google_models)
    assert any(m.id == "gemini-3-flash-preview" for m in google_models)
    assert any(m.id == "gemini-2.5-flash" for m in google_models)
    assert any(m.id == "gemini-3.5-flash-lite" for m in google_models)
    assert any(m.id == "gemini-3.1-flash-lite" for m in google_models)
    assert any(m.id == "gemini-2.5-flash-lite" for m in google_models)
    assert any(m.id == "gemini-3.8-live-extended-thinking" for m in google_models)
    assert any(m.id == "gemini-3.8-live" for m in google_models)

    live_thinking = get_model_spec("gemini-3.8-live-extended-thinking")
    assert live_thinking is not None
    assert "live_api" in live_thinking.capabilities
    assert "extended_thinking" in live_thinking.capabilities
    assert live_thinking.context_window == 131_072
    assert live_thinking.max_output_tokens == 65_536

    live = get_model_spec("gemini-3.8-live")
    assert live is not None
    assert "live_api" in live.capabilities
    assert live.context_window == 131_072

    openai_models = get_available_models("openai")
    assert any(m.id == "gpt-4o" for m in openai_models)
    assert any(m.id == "gpt-4o-mini" for m in openai_models)

    opencode_models = get_available_models("opencode")
    assert len(opencode_models) == 13
    assert any(m.id == "opencode/glm-5.3-flash-free" for m in opencode_models)
    assert any(m.id == "opencode/deepseek-v4.1-flash-free" for m in opencode_models)
    assert any(m.id == "opencode/big-pickle" for m in opencode_models)
    assert any(m.id == "opencode/beam-free" for m in opencode_models)
    assert any(m.id == "opencode/mimo-v2.6-flash-free" for m in opencode_models)
    assert any(m.id == "opencode/nemotron-3.5-lightning-free" for m in opencode_models)
    assert any(m.id == "opencode/nemotron-3.5-super-vl-free" for m in opencode_models)
    assert any(m.id == "opencode/longcat-2.5-preview-free" for m in opencode_models)
    assert any(m.id == "opencode/space-bunny-free" for m in opencode_models)
    assert any(m.id == "opencode/nemotron-3-ultra-free" for m in opencode_models)
    assert any(m.id == "opencode/mimo-v2.5-free" for m in opencode_models)
    assert any(m.id == "opencode/ll-alpha-free" for m in opencode_models)
    assert any(m.id == "opencode/exo-free" for m in opencode_models)

    # Free model pricing checks
    for m in opencode_models:
        assert m.input_cost_per_m == 0.0
        assert m.output_cost_per_m == 0.0
        assert m.context_window == 128000


def test_get_model_spec() -> None:
    """Verify looking up model by endpoint slug (with and without prefix)."""
    spec = get_model_spec("gemini-3.7-flash")
    assert spec is not None
    assert spec.name == "Gemini 3.7 Flash"
    assert spec.provider == "google"
    assert spec.tier == "flagship"
    assert "coding" in spec.capabilities

    # OpenCode lookup with prefix
    oc_spec = get_model_spec("opencode/glm-5.3-flash-free")
    assert oc_spec is not None
    assert oc_spec.name == "GLM-5.3-Flash (OpenCode Free)"
    assert oc_spec.provider == "opencode"
    assert oc_spec.tier == "fast"

    # OpenCode lookup without prefix
    bare_spec = get_model_spec("glm-5.3-flash-free")
    assert bare_spec is not None
    assert bare_spec.id == "opencode/glm-5.3-flash-free"

    bare_pickle = get_model_spec("big-pickle")
    assert bare_pickle is not None
    assert bare_pickle.id == "opencode/big-pickle"


def test_estimate_cost() -> None:
    """Verify cost calculation in USD."""
    spec = get_model_spec("gemini-3.7-flash")
    assert spec is not None

    # 1,000,000 prompt tokens ($0.15) + 1,000,000 completion tokens ($0.60) = $0.75
    cost = spec.estimate_cost(prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == 0.75

    # OpenCode free model cost estimate
    oc_spec = get_model_spec("opencode/glm-5.3-flash-free")
    assert oc_spec is not None
    assert oc_spec.estimate_cost(prompt_tokens=500_000, completion_tokens=100_000) == 0.0


def test_get_default_model() -> None:
    """Verify default model retrieval."""
    google_default = get_default_model("google")
    assert google_default.id == "gemini-3.1-flash-lite"
    assert google_default.is_default is True

    openai_default = get_default_model("openai")
    assert openai_default.id == "gpt-4o"

    opencode_default = get_default_model("opencode")
    assert opencode_default.id == "opencode/glm-5.3-flash-free"
    assert opencode_default.is_default is True


def test_resolve_model_provider() -> None:
    """Verify provider resolution for prefixed and unprefixed model identifiers."""
    assert resolve_model_provider("opencode/glm-5.3-flash-free") == "opencode"
    assert resolve_model_provider("glm-5.3-flash-free") == "opencode"
    assert resolve_model_provider("big-pickle") == "opencode"
    assert resolve_model_provider("deepseek-v4.1-flash-free") == "opencode"
    assert resolve_model_provider("gemini-3.7-flash") == "google"
    assert resolve_model_provider("gpt-4o") == "openai"
    assert resolve_model_provider("claude-3-5-sonnet-latest") == "anthropic"

