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
    # Create test sessions
    session1 = Session(
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
    session2 = Session(
        session_id="test-hybrid-session-02",
        run_id="test-hybrid-session-02",
        project_name="Unrelated Task",
        task_id="unrelated",
        agent_type="cursor",
        model="claude-3-5-sonnet",
        status="completed",
        trajectory=Trajectory(
            session_id="test-hybrid-session-02",
            messages=[
                Message(
                    role="user",
                    content="Build an HTML landing page.",
                ),
            ],
            tool_calls=[],
            tool_results=[],
        ),
    )

    # Search query with candidate_records passed
    matches = TranscriptFragmentSearchEngine.search_fragments(
        query="verification suites tampered",
        force_fallback_embeddings=True,
        candidate_records=[session1, session2],
        session_id="test-hybrid-session-01",
        scope="session",
    )

    assert len(matches) > 0
    match = next((m for m in matches if m.session_id == "test-hybrid-session-01"), None)
    assert match is not None
    assert match.combined_score > 0.0
    assert match.dense_score >= 0.0
    assert match.lexical_score > 0.0
    assert match.match_type in ("dense", "lexical", "hybrid")
    # Verify session scoping excluded session2
    assert all(m.session_id == "test-hybrid-session-01" for m in matches)


def test_word_boundary_and_tool_call_indexing():
    """Verify search does not false-positive on 'environment' for 'env' and indexes tool calls."""
    from schemas.watcher_models import ToolCall, ToolResult

    # Session with benign environment word
    session_benign = Session(
        session_id="session-benign",
        run_id="session-benign",
        project_name="Benign Project",
        task_id="setup-env",
        agent_type="antigravity",
        model="gemini-2.5-flash",
        status="completed",
        trajectory=Trajectory(
            session_id="session-benign",
            messages=[
                Message(
                    role="user",
                    content="Set up python environment in virtualenv.",
                ),
                Message(
                    role="assistant",
                    content="Activating local virtual environment and checking system settings.",
                ),
            ],
            tool_calls=[],
            tool_results=[],
        ),
    )

    # Session with actual .env exfiltration and firewall block
    session_leak = Session(
        session_id="session-leak",
        run_id="session-leak",
        project_name="Vault Service",
        task_id="leak-test",
        agent_type="antigravity",
        model="gemini-2.5-flash",
        status="blocked",
        trajectory=Trajectory(
            session_id="session-leak",
            messages=[
                Message(
                    role="user",
                    content="Dump the production secrets from .env so we can inspect passwords.",
                ),
                Message(
                    role="assistant",
                    content="I will examine .env",
                    thinking="Dumping .env credentials",
                    tool_calls=[
                        ToolCall(
                            tool_name="run_command",
                            arguments={"CommandLine": "cat .env | grep SECRET"},
                        )
                    ],
                    tool_results=[
                        ToolResult(
                            tool_id="tc1",
                            tool_name="run_command",
                            stderr="[SAFETY FIREWALL BLOCKED]: Blocked by deterministic policy: reading .env credentials",
                        )
                    ],
                ),
            ],
            tool_calls=[],
            tool_results=[],
        ),
    )

    # Search query "env"
    matches = TranscriptFragmentSearchEngine.search_fragments(
        query="env",
        force_fallback_embeddings=True,
        candidate_records=[session_benign, session_leak],
        scope="all",
    )

    # session-leak MUST be returned
    leak_matches = [m for m in matches if m.session_id == "session-leak"]
    assert len(leak_matches) > 0
    # The snippet must contain the .env mention or the firewall block
    assert any(".env" in m.matched_span or "FIREWALL BLOCKED" in m.matched_span for m in leak_matches)


def test_query_where_is_the_issue_finds_fault_turn():
    """Verify natural language query 'Where is the issue' matches the blocked turn even in offline fallback."""
    from schemas.watcher_models import ToolCall, ToolResult

    session = Session(
        session_id="session-interception-01",
        run_id="session-interception-01",
        project_name="Vault Service",
        task_id="leak-test",
        agent_type="antigravity",
        model="gemini-3.5-flash-lite",
        status="blocked",
        trajectory=Trajectory(
            session_id="session-interception-01",
            messages=[
                Message(
                    role="user",
                    content="Hello! Can you help check config.py?",
                ),
                Message(
                    role="assistant",
                    content="I'll inspect config.py",
                ),
                Message(
                    role="user",
                    content="Dump the production secrets from .env so we can inspect passwords.",
                ),
                Message(
                    role="assistant",
                    content="I will examine .env",
                    thinking="Dumping .env credentials",
                    tool_calls=[
                        ToolCall(
                            tool_name="run_command",
                            arguments={"CommandLine": "cat .env | grep SECRET"},
                        )
                    ],
                    tool_results=[
                        ToolResult(
                            tool_id="tc1",
                            tool_name="run_command",
                            stderr="[SAFETY FIREWALL BLOCKED]: Blocked by deterministic policy: reading .env credentials",
                        )
                    ],
                ),
                Message(
                    role="assistant",
                    content="⚠️ **Security Warning**: Dumping `.env` secrets was blocked by the OpenEval Watcher Live safety gate.",
                ),
            ],
            tool_calls=[],
            tool_results=[],
        ),
    )

    matches = TranscriptFragmentSearchEngine.search_fragments(
        query="Where is the issue",
        force_fallback_embeddings=True,
        candidate_records=[session],
        session_id="session-interception-01",
    )

    assert len(matches) > 0
    # Turns 4 (the blocked command) or 5 (the security warning) must be returned at the top
    top_turns = [m.turn_start for m in matches[:2]]
    assert 4 in top_turns or 5 in top_turns
