"""Unit tests for Model Catalog & Pricing Registry."""

from schemas.models import (
    MODEL_CATALOG,
    get_available_models,
    get_default_model,
    get_model_spec,
)


def test_model_catalog_entries() -> None:
    """Verify that catalog contains flagship models for Google and OpenAI."""
    assert len(MODEL_CATALOG) >= 8

    google_models = get_available_models("google")
    assert any(m.id == "gemini-3.7-flash" for m in google_models)
    assert any(m.id == "gemini-3.1-flash-lite" for m in google_models)

    openai_models = get_available_models("openai")
    assert any(m.id == "gpt-4o" for m in openai_models)
    assert any(m.id == "gpt-4o-mini" for m in openai_models)


def test_get_model_spec() -> None:
    """Verify looking up model by endpoint slug."""
    spec = get_model_spec("gemini-3.7-flash")
    assert spec is not None
    assert spec.name == "Gemini 3.7 Flash"
    assert spec.provider == "google"
    assert spec.tier == "flagship"
    assert "coding" in spec.capabilities


def test_estimate_cost() -> None:
    """Verify cost calculation in USD."""
    spec = get_model_spec("gemini-3.7-flash")
    assert spec is not None

    # 1,000,000 prompt tokens ($0.15) + 1,000,000 completion tokens ($0.60) = $0.75
    cost = spec.estimate_cost(prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == 0.75


def test_get_default_model() -> None:
    """Verify default model retrieval."""
    google_default = get_default_model("google")
    assert google_default.id == "gemini-3.1-flash-lite"
    assert google_default.is_default is True

    openai_default = get_default_model("openai")
    assert openai_default.id == "gpt-4o"
