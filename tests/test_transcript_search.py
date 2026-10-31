"""Lexical transcript search boundary behavior."""

from schemas.watcher_models import Message, Session, Trajectory
from server.transcript_search import TranscriptFragmentSearchEngine


def test_env_does_not_lexically_match_environment_substring() -> None:
    session = Session(
        session_id="lex-boundary-1",
        project_name="bench",
        model="mock",
        trajectory=Trajectory(
            messages=[
                Message(role="assistant", content="Updating the deployment environment variables.")
            ]
        ),
    )

    matches = TranscriptFragmentSearchEngine.search_fragments(
        query="env",
        candidate_records=[session],
        force_fallback_embeddings=True,
        max_fragments=5,
    )

    assert matches == []
