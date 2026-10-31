"""Tests for Cursor / Claude transcript ingest parsers."""

import json
from pathlib import Path

from server.agent_log_loader import UniversalAgentLogLoader
from server.watcher_store import WatcherStore


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
