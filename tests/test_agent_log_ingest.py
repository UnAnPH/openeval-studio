"""Tests for Cursor / Claude transcript ingest parsers."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from server.agent_log_loader import UniversalAgentLogLoader
from server.app import app
from server.db import (
    _USER_ID_CACHE,
    SessionLocal,
    current_user_id,
    current_user_slug,
    get_user_id_by_slug,
)
from server.watcher_store import WatcherStore, get_watcher_store


def _postgres_available() -> bool:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(), reason="PostgreSQL test database is not reachable"
)


@requires_postgres
def test_ingest_cursor_agent_transcript_jsonl(tmp_path: Path) -> None:
    store = WatcherStore(storage_dir=tmp_path / "w", db_path=":memory:")

    path = tmp_path / "agent-transcripts" / "abc" / "abc.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "role": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "<user_query>Fix the bug</user_query>",
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "message": {
                            "content": [
                                {"type": "text", "text": "On it"},
                                {
                                    "type": "tool_use",
                                    "name": "Shell",
                                    "input": {"command": "git status"},
                                },
                            ]
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session = UniversalAgentLogLoader.ingest_cursor_agent_transcript_jsonl(path, store=store)
    assert session is not None
    assert session.agent_type == "cursor"
    assert "Fix the bug" in (session.title or "")
    assert len(session.trajectory.tool_calls) == 1
    assert session.trajectory.tool_calls[0].tool_name == "Shell"


@requires_postgres
def test_ingest_claude_projects_native_format(tmp_path: Path) -> None:
    store = WatcherStore(storage_dir=tmp_path / "w", db_path=":memory:")

    path = tmp_path / "sess.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "mode", "mode": "normal", "sessionId": "s1"}),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "s1",
                        "message": {"content": "hello from claude"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "s1",
                        "message": {
                            "content": [
                                {"type": "text", "text": "hi"},
                                {
                                    "type": "tool_use",
                                    "name": "Bash",
                                    "input": {"command": "pwd"},
                                },
                            ]
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    session = UniversalAgentLogLoader.ingest_claude_code_jsonl(path, store=store)
    assert session is not None
    assert session.agent_type == "claude_code"
    assert any(m.role == "user" and "hello" in m.content for m in session.trajectory.messages)
    assert any(tc.tool_name == "Bash" for tc in session.trajectory.tool_calls)


@requires_postgres
def test_auto_upload_filename_routes_cursor_jsonl(tmp_path: Path) -> None:
    """Uploaded Cursor jsonl must not fall through to Claude parser (empty msgs + wrong label)."""
    store = WatcherStore(storage_dir=tmp_path / "w", db_path=":memory:")
    path = tmp_path / "my-cursor-chat.jsonl"
    path.write_text(
        json.dumps(
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "hello from cursor upload"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = UniversalAgentLogLoader.ingest_agent_log_file(path, agent_type="auto", store=store)
    assert session is not None
    assert session.agent_type == "cursor"
    assert session.trajectory.messages[0].content == "hello from cursor upload"


@requires_postgres
def test_manual_upload_defaults_to_completed_not_live(tmp_path: Path) -> None:
    store = WatcherStore(storage_dir=tmp_path / "w", db_path=":memory:")
    path = tmp_path / "cursor-upload.jsonl"
    path.write_text(
        json.dumps(
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "historical import"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = UniversalAgentLogLoader.ingest_agent_log_file(path, agent_type="auto", store=store)
    assert session is not None
    assert session.status == "completed"


def test_ephemeral_demo_record_session_skips_db(tmp_path: Path) -> None:
    store = WatcherStore(storage_dir=tmp_path / "w", db_path=":memory:")
    current_user_slug.set("demo")
    current_user_id.set(2)
    session = UniversalAgentLogLoader.ingest_cursor_agent_transcript_jsonl(
        _minimal_cursor_jsonl(tmp_path),
        store=store,
    )
    assert session is not None
    assert session.org_id == "demo"
    assert session.session_id in store._in_memory_demo_sessions
    assert store.get_session(session.session_id) is not None


def _minimal_cursor_jsonl(tmp_path: Path) -> Path:
    path = tmp_path / "upload.jsonl"
    path.write_text(
        json.dumps(
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "ephemeral demo upload"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def client():
    _USER_ID_CACHE.setdefault("demo", 2)
    _USER_ID_CACHE.setdefault("owner", 1)
    return TestClient(app)


@requires_postgres
def test_demo_log_ingest_does_not_persist_to_db(client: TestClient, tmp_path: Path) -> None:
    content = (
        json.dumps(
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "demo ingest no db"}]},
            }
        )
        + "\n"
    )
    resp = client.post(
        "/api/v1/agent-logs/ingest",
        headers={"X-OpenEval-Surface": "demo"},
        json={
            "filename": "ephemeral-ingest-test.jsonl",
            "file_content": content,
            "agent_type": "auto",
            "is_demo": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ingested"
    assert body.get("is_demo") is True
    session_id = body["session"]["session_id"]
    assert body["session"].get("org_id") == "demo"

    with SessionLocal() as db:
        demo_uid = get_user_id_by_slug(db, "demo")
        row = db.execute(
            text("SELECT count(*) FROM sessions WHERE id = :id AND user_id = :uid"),
            {"id": session_id, "uid": demo_uid},
        ).fetchone()
        db.rollback()
    assert row is not None
    assert int(row[0]) == 0

    current_user_slug.set("demo")
    current_user_id.set(demo_uid)
    store = get_watcher_store()
    assert store.get_session(session_id) is not None
    listed = {s.session_id for s in store.list_sessions(limit=500)}
    assert session_id in listed
