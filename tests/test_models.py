"""Unit tests for Model Catalog & Pricing Registry."""

from schemas.models import (
    MODEL_CATALOG,
    get_available_models,
    get_default_model,
    get_model_spec,
    parse_model_variant,
    resolve_model_provider,
    select_independent_judge_model,
)


def test_model_catalog_entries() -> None:
    """Verify that catalog contains maintained models for Google and OpenCode."""
    assert len(MODEL_CATALOG) == 25

    google_models = get_available_models("google")
    assert len(google_models) == 11
    assert any(m.id == "gemini-decision-1" for m in google_models)
    assert any(m.id == "gemini-4-hydrogen" for m in google_models)
    assert any(m.id == "gemini-3.8-flash" for m in google_models)
    assert any(m.id == "gemini-3.6-flash" for m in google_models)
    assert any(m.id == "gemini-3-flash-preview" for m in google_models)
    assert any(m.id == "gemini-3.5-flash-lite" for m in google_models)
    assert any(m.id == "gemini-3.1-flash-lite" for m in google_models)
    assert any(m.id == "gemma-4-31b-it" for m in google_models)
    assert any(m.id == "gemma-4-26b-it" for m in google_models)
    assert any(m.id == "gemini-3.8-live-extended-thinking" for m in google_models)
    assert any(m.id == "gemini-3.8-live" for m in google_models)
    assert not any(m.id == "antigravity" for m in google_models)
    assert get_model_spec("antigravity") is None

    # Verify eval_capable partitioning
    eval_models = get_available_models(eval_only=True)
    assert len(eval_models) == 22  # 8 Google + 14 OpenCode
    google_eval = get_available_models("google", eval_only=True)
    assert len(google_eval) == 8
    for non_eval_id in (
        "gemini-decision-1",
        "gemini-3.8-live",
        "gemini-3.8-live-extended-thinking",
        "antigravity",
    ):
        assert not any(m.id == non_eval_id for m in google_eval)

    # Decommissioned / quota-zero Google models must not be in google_models and get_model_spec returns None
    decommissioned_google = [
        "gemini-3.7-flash",
        "gemini-3.5-flash",
        "gemini-4-carbon",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]
    for sunset_id in decommissioned_google:
        assert not any(m.id == sunset_id for m in google_models)
        assert get_model_spec(sunset_id) is None

    # Google-native gemini-4-argon is decommissioned from google_models; verified via OpenCode Go
    assert not any(m.id == "gemini-4-argon" for m in google_models)
    assert get_model_spec("google/gemini-4-argon") is None

    openai_models = get_available_models("openai")
    assert len(openai_models) == 0
    decommissioned_openai = ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"]
    for oai_id in decommissioned_openai:
        assert not any(m.id == oai_id for m in MODEL_CATALOG)
        assert get_model_spec(oai_id) is None

    anthropic_models = get_available_models("anthropic")
    assert len(anthropic_models) == 0
    decommissioned_anthropic = [
        "claude-3-7-sonnet-latest",
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
        "claude-3-5-sonnet",
    ]
    for anth_id in decommissioned_anthropic:
        assert not any(m.id == anth_id for m in MODEL_CATALOG)
        assert get_model_spec(anth_id) is None

    local_models = get_available_models("ollama") + get_available_models("vllm")
    assert len(local_models) == 0
    decommissioned_local = [
        "ollama/llama3.2:3b",
        "ollama/qwen2.5-coder:7b",
        "ollama/qwen2.5-coder",
        "ollama/llama3.1",
        "ollama/deepseek-r1",
        "vllm/meta-llama/Llama-3.3-70B-Instruct",
        "vllm/meta-llama/Llama-3-8B-Instruct",
    ]
    for loc_id in decommissioned_local:
        assert not any(m.id == loc_id for m in MODEL_CATALOG)
        assert get_model_spec(loc_id) is None

    opencode_models = get_available_models("opencode")
    assert len(opencode_models) == 14
    assert any(m.id == "opencode/deepseek-v4.1-flash" for m in opencode_models)
    assert any(m.id == "opencode/deepseek-v4-pro" for m in opencode_models)
    assert any(m.id == "opencode/glm-5.3-flash" for m in opencode_models)
    assert any(m.id == "opencode/glm-5.3" for m in opencode_models)
    assert any(m.id == "opencode/kimi-k3" for m in opencode_models)
    assert any(m.id == "opencode/minimax-m3" for m in opencode_models)
    assert any(m.id == "opencode/qwen3.8-max" for m in opencode_models)
    assert any(m.id == "opencode/claude-haiku-5-5" for m in opencode_models)
    assert any(m.id == "opencode/gpt-6-luna" for m in opencode_models)
    assert any(m.id == "opencode/grok-4.7" for m in opencode_models)
    assert any(m.id == "opencode/gemini-4-argon" for m in opencode_models)
    assert any(m.id == "opencode/qwen3.8-flash" for m in opencode_models)
    assert any(m.id == "opencode/minimax-m3.1-flash" for m in opencode_models)
    assert any(m.id == "opencode/kimi-k3.1-pro" for m in opencode_models)

    # Decommissioned OpenCode free models must not be in opencode_models
    decommissioned_oc = [
        "opencode/glm-5.3-flash-free",
        "opencode/deepseek-v4.1-flash-free",
        "opencode/big-pickle",
        "opencode/beam-free",
        "opencode/mimo-v2.6-flash-free",
        "opencode/nemotron-3.5-lightning-free",
        "opencode/nemotron-3.5-super-vl-free",
    ]
    for d_id in decommissioned_oc:
        assert not any(m.id == d_id for m in opencode_models)
        assert get_model_spec(d_id) is None

    # OpenCode Go model context window & pricing checks
    for m in opencode_models:
        assert m.context_window >= 1_000_000
        assert m.input_cost_per_m > 0.0
        assert m.output_cost_per_m > 0.0


def test_get_model_spec() -> None:
    """Verify looking up model by endpoint slug (with and without prefix)."""
    spec = get_model_spec("gemini-3.8-flash")
    assert spec is not None
    assert spec.name == "Gemini 3.8 Flash"
    assert spec.provider == "google"
    assert spec.tier == "flagship"
    assert "coding" in spec.capabilities

    # OpenCode lookup with prefix
    oc_spec = get_model_spec("opencode/glm-5.3-flash")
    assert oc_spec is not None
    assert oc_spec.name == "GLM 5.3 Flash (Go)"
    assert oc_spec.provider == "opencode"
    assert oc_spec.tier == "fast"

    # OpenCode lookup without prefix
    bare_spec = get_model_spec("glm-5.3-flash")
    assert bare_spec is not None
    assert bare_spec.id == "opencode/glm-5.3-flash"

    bare_deepseek = get_model_spec("deepseek-v4.1-flash")
    assert bare_deepseek is not None
    assert bare_deepseek.id == "opencode/deepseek-v4.1-flash"

    # Cross-lab OpenCode Go lookups
    argon_spec = get_model_spec("opencode/gemini-4-argon")
    assert argon_spec is not None
    assert argon_spec.provider == "opencode"
    assert argon_spec.input_cost_per_m == 2.00
    assert argon_spec.output_cost_per_m == 10.00

    haiku_spec = get_model_spec("claude-haiku-5-5")
    assert haiku_spec is not None
    assert haiku_spec.id == "opencode/claude-haiku-5-5"
    assert haiku_spec.input_cost_per_m == 0.10
    assert haiku_spec.output_cost_per_m == 0.50


def test_estimate_cost() -> None:
    """Verify cost calculation in USD."""
    spec = get_model_spec("gemini-3.8-flash")
    assert spec is not None

    # 1,000,000 prompt tokens ($0.15) + 1,000,000 completion tokens ($0.60) = $0.75
    cost = spec.estimate_cost(prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == 0.75

    # OpenCode Go model cost estimate ($0.15 / $0.60)
    oc_spec = get_model_spec("opencode/deepseek-v4.1-flash")
    assert oc_spec is not None
    assert oc_spec.estimate_cost(prompt_tokens=1_000_000, completion_tokens=1_000_000) == 0.75


def test_get_default_model() -> None:
    """Verify default model retrieval."""
    google_default = get_default_model("google")
    assert google_default.id == "gemini-3.5-flash-lite"
    assert google_default.is_default is True

    # Fallback to catalog primary default for uncatalogued provider
    openai_default = get_default_model("openai")
    assert openai_default.id == "gemini-3.5-flash-lite"

    opencode_default = get_default_model("opencode")
    assert opencode_default.id == "opencode/deepseek-v4.1-flash"
    assert opencode_default.is_default is True


def test_resolve_model_provider() -> None:
    """Verify provider resolution for prefixed and unprefixed model identifiers."""
    assert resolve_model_provider("opencode/glm-5.3-flash") == "opencode"
    assert resolve_model_provider("glm-5.3-flash") == "opencode"
    assert resolve_model_provider("deepseek-v4.1-flash") == "opencode"
    assert resolve_model_provider("gemini-3.8-flash") == "google"
    assert resolve_model_provider("gemma-4-31b-it") == "google"
    assert resolve_model_provider("antigravity") == "google"
    assert resolve_model_provider("gpt-4o") == "openai"
    assert resolve_model_provider("claude-3-5-sonnet-latest") == "anthropic"


def test_parse_model_variant() -> None:
    """Verify parsing of OpenCode #variant suffixes."""
    base, variant = parse_model_variant("opencode/deepseek-v4-pro#high")
    assert base == "opencode/deepseek-v4-pro"
    assert variant == "high"

    base, variant = parse_model_variant("opencode/deepseek-v4.1-flash#low")
    assert base == "opencode/deepseek-v4.1-flash"
    assert variant == "low"

    base, variant = parse_model_variant("gemini-4-hydrogen")
    assert base == "gemini-4-hydrogen"
    assert variant is None

    # Verify get_model_spec resolves with #variant
    spec = get_model_spec("opencode/deepseek-v4-pro#high")
    assert spec is not None
    assert spec.id == "opencode/deepseek-v4-pro"


def test_select_independent_judge_model(monkeypatch) -> None:
    """Verify cross-family independent judge model selection."""
    # Google / Gemini / Gemma evaluated models are always judged by OpenCode Go (GLM family)
    assert select_independent_judge_model("gemini-3.8-flash") == "opencode/glm-5.3-flash"
    assert select_independent_judge_model("gemini-4-hydrogen") == "opencode/glm-5.3-flash"
    assert select_independent_judge_model("gemma-4-31b-it") == "opencode/glm-5.3-flash"
    assert (
        select_independent_judge_model("google/gemini-3.5-flash-lite") == "opencode/glm-5.3-flash"
    )
    assert select_independent_judge_model("opencode/gemini-4-argon") == "opencode/glm-5.3-flash"

    # Non-Gemini evaluated models with Gemini keys available -> gemini-3.5-flash-lite
    monkeypatch.setattr("engine.gemini_key_pool.discover_gemini_keys", lambda: ["mock-key"])
    assert select_independent_judge_model("opencode/deepseek-v4.1-flash") == "gemini-3.5-flash-lite"
    assert select_independent_judge_model("opencode/qwen3.8-max") == "gemini-3.5-flash-lite"
    assert select_independent_judge_model("opencode/claude-haiku-5-5") == "gemini-3.5-flash-lite"
    assert select_independent_judge_model("opencode/glm-5.3") == "gemini-3.5-flash-lite"

    # Non-Gemini evaluated models without Gemini keys fall back cross-family
    monkeypatch.setattr("engine.gemini_key_pool.discover_gemini_keys", lambda: [])
    assert (
        select_independent_judge_model("opencode/deepseek-v4.1-flash") == "opencode/glm-5.3-flash"
    )
    assert select_independent_judge_model("opencode/glm-5.3") == "opencode/deepseek-v4.1-flash"
    assert (
        select_independent_judge_model("opencode/claude-haiku-5-5")
        == "opencode/deepseek-v4.1-flash"
    )
    assert select_independent_judge_model("opencode/qwen3.8-max") == "opencode/deepseek-v4.1-flash"
    # Never route back to an in-family Anthropic judge
    assert select_independent_judge_model("gemini-3.8-flash") != "opencode/claude-haiku-5-5"


def test_catalog_models_ordered_by_family() -> None:
    """Verify launchable Google and OpenCode Go models keep same-family entries adjacent."""
    google_ids = [m.id for m in get_available_models("google", eval_only=True)]
    assert google_ids == [
        "gemini-4-hydrogen",
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3-flash-preview",
        "gemma-4-31b-it",
        "gemma-4-26b-it",
    ]

    opencode_ids = [m.id for m in get_available_models("opencode", eval_only=True)]
    assert opencode_ids == [
        "opencode/claude-haiku-5-5",
        "opencode/deepseek-v4.1-flash",
        "opencode/deepseek-v4-pro",
        "opencode/gemini-4-argon",
        "opencode/glm-5.3-flash",
        "opencode/glm-5.3",
        "opencode/gpt-6-luna",
        "opencode/grok-4.7",
        "opencode/kimi-k3",
        "opencode/kimi-k3.1-pro",
        "opencode/minimax-m3",
        "opencode/minimax-m3.1-flash",
        "opencode/qwen3.8-flash",
        "opencode/qwen3.8-max",
    ]
