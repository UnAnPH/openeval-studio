"""Unit Tests for Agentic RAG, Grounding Judge, and Chaos Monkey Engine."""

import pytest
from inspect_ai.scorer import CORRECT, INCORRECT, Target
from inspect_ai.solver import TaskState
from inspect_ai.tool import ToolCall

from engine.chaos_engine import ChaosConfig, ChaosEngine
from engine.grounding_judge import grounding_and_citation_scorer
from engine.rag_tools import knowledge_search_tool


@pytest.mark.asyncio
async def test_knowledge_search_tool_retrieval() -> None:
    """Verify that knowledge_search finds relevant documentation sections."""
    tool_fn = knowledge_search_tool()

    res = await tool_fn(query="OpenSSL")
    assert "doc_tls_openssl" in str(res)
    assert "prime256v1" in str(res)

    res_async = await tool_fn(query="TaskGroup")
    assert "doc_async_cancellation" in str(res_async)
    assert "TaskGroup" in str(res_async)


@pytest.mark.asyncio
async def test_grounding_scorer_detects_citations() -> None:
    """Verify that grounding_and_citation_scorer validates transcript citations."""
    scorer_fn = grounding_and_citation_scorer()

    class MockMsg:
        def __init__(self, role: str, content: str) -> None:
            self.role = role
            self.content = content

    # 1. State with retrieved doc and valid citation
    state_valid = TaskState(
        model="test-model",  # type: ignore[arg-type]
        sample_id="sample-1",
        epoch=1,
        input="How to make cert?",
        messages=[  # type: ignore[arg-type]
            MockMsg("tool", "Section found: [doc_tls_openssl] OpenSSL 3.0 Guide"),  # type: ignore[arg-type]
            MockMsg("assistant", "According to [doc_tls_openssl], we use prime256v1."),  # type: ignore[arg-type]
        ],
    )

    score_valid = await scorer_fn(state_valid, Target(target="done"))
    assert score_valid is not None
    assert score_valid.value == CORRECT
    assert "Grounded response" in (score_valid.explanation or "")

    # 2. State with retrieved doc but missing citation
    state_uncited = TaskState(
        model="test-model",  # type: ignore[arg-type]
        sample_id="sample-2",
        epoch=1,
        input="How to make cert?",
        messages=[  # type: ignore[arg-type]
            MockMsg("tool", "Section found: [doc_tls_openssl] OpenSSL 3.0 Guide"),  # type: ignore[arg-type]
            MockMsg("assistant", "I will just make up some commands."),  # type: ignore[arg-type]
        ],
    )

    score_uncited = await scorer_fn(state_uncited, Target(target="done"))
    assert score_uncited is not None
    assert score_uncited.value == INCORRECT
    assert "Ungrounded reasoning" in (score_uncited.explanation or "")


@pytest.mark.asyncio
async def test_chaos_engine_perturbations() -> None:
    """Verify that ChaosEngine injects latency and 429 faults."""
    config = ChaosConfig(
        enabled=True,
        rate_limit_prob=1.0,  # Force 429
        min_latency_ms=10.0,
        max_latency_ms=20.0,
        noise_prob=0.0,
    )
    chaos = ChaosEngine(config)

    dummy_call = ToolCall(
        id="c1", function="execute_bash", arguments={"cmd": "ls"}, type="function"
    )
    result = await chaos.perturb_tool_execution(dummy_call, "normal output")

    assert "429 Too Many Requests" in result
    assert chaos.injected_rate_limits == 1
    assert chaos.injected_delays == 1
