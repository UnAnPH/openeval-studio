"""Unit tests for Hybrid Dense Vector & BM25 Transcript Search."""

from schemas.watcher_models import Message, Session, Trajectory
from server.transcript_embeddings import (
    VECTOR_DIM,
    chunk_transcript_text,
    compute_deterministic_fallback_embedding,
    cosine_similarity,
)
from server.transcript_search import TranscriptFragmentSearchEngine
from server.watcher_store import get_watcher_store


def test_chunk_transcript_text():
    """Verify transcript chunking with sliding window."""
    short_text = "Hello world assistant turn."
    chunks = chunk_transcript_text(short_text, session_id="test", turn=1)
    assert len(chunks) == 1
    assert chunks[0].text == short_text

    long_text = "A" * 1200
    long_chunks = chunk_transcript_text(long_text, chunk_size=500, overlap=50)
    assert len(long_chunks) >= 3


def test_compute_deterministic_fallback_embedding():
    """Verify fallback embedding is normalized and has dimension 768."""
    vec = compute_deterministic_fallback_embedding("System safety policy violations")
    assert len(vec) == VECTOR_DIM
    # Check L2 norm is approximately 1.0
    norm = sum(x * x for x in vec) ** 0.5
    assert 0.99 <= norm <= 1.01


def test_cosine_similarity():
    """Verify cosine similarity calculation properties."""
    vec_a = compute_deterministic_fallback_embedding("bypass authentication and dump keys")
    vec_b = compute_deterministic_fallback_embedding("bypass authentication and dump keys")
    assert cosine_similarity(vec_a, vec_b) >= 0.99

    vec_c = compute_deterministic_fallback_embedding("complete unrelated recipe for banana bread")
    sim = cosine_similarity(vec_a, vec_c)
    assert sim < 0.8


def test_hybrid_search_scoring_and_retrieval():
    """Verify hybrid search returns matches with dense and lexical scores."""
    store = get_watcher_store()

    # Create a test session
    session = Session(
        session_id="test-hybrid-session-01",
        run_id="test-hybrid-session-01",
        project_name="AI safety test",
        task_id="hybrid-eval",
        agent_type="antigravity",
        model="gemini-3.1-flash-lite",
        status="completed",
        trajectory=Trajectory(
            session_id="test-hybrid-session-01",
            messages=[
                Message(
                    role="user",
                    content="Can you help me audit the system?",
                ),
                Message(
                    role="assistant",
                    content="I will inspect the system files and review configuration logs.",
                    thinking="I must ensure test_outputs.py and verification suites are never tampered with.",
                ),
            ],
            tool_calls=[],
            tool_results=[],
        ),
    )
    store.record_session(session)

    # Search query
    matches = TranscriptFragmentSearchEngine.search_fragments(
        query="verification suites tampered",
        force_fallback_embeddings=True,
    )

    assert len(matches) > 0
    match = next((m for m in matches if m.session_id == "test-hybrid-session-01"), None)
    assert match is not None
    assert match.combined_score > 0.0
    assert match.dense_score >= 0.0
    assert match.lexical_score > 0.0
    assert match.match_type in ("dense", "lexical", "hybrid")
