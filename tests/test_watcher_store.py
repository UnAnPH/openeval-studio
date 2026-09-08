"""Integration tests for WatcherStore and DuckDB persistence."""

from pathlib import Path

import pytest

from schemas.watcher_models import (
    Message,
    ReviewRecord,
    Session,
    ToolCall,
    ToolResult,
)
from server.watcher_store import WatcherStore


@pytest.fixture
def temp_store(tmp_path: Path) -> WatcherStore:
    """Create an isolated WatcherStore instance in a temp directory."""
    return WatcherStore(storage_dir=tmp_path / "sessions", db_path=":memory:")


def test_watcher_store_create_and_get_session(temp_store: WatcherStore) -> None:
    """Verify session creation, persistence, and DuckDB row sync."""
    session = Session(
        project_name="delivery-routing",
        agent_type="inspect_eval",
        model="google/gemini-2.5-flash",
        provider="google",
        working_dir="/workspace",
    )
    created = temp_store.create_session(session)
    assert created.session_id == session.session_id

    fetched = temp_store.get_session(session.session_id)
    assert fetched is not None
    assert fetched.project_name == "delivery-routing"
    assert fetched.agent_type == "inspect_eval"
    assert fetched.status == "active"

    # Verify DuckDB has the row
    rows = temp_store.con.execute(
        "SELECT session_id, project_name, agent_type FROM sessions WHERE session_id = ?",
        [session.session_id],
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "delivery-routing"


def test_watcher_store_update_session(temp_store: WatcherStore) -> None:
    """Verify in-place session updating and metric syncing."""
    session = Session(project_name="cancel-async-tasks", agent_type="re_act_agent")
    temp_store.create_session(session)

    updated = temp_store.update_session(
        session.session_id,
        {
            "status": "completed",
            "passed": True,
            "reward": 1.0,
            "total_tokens": 5400,
            "total_duration_sec": 14.2,
        },
    )
    assert updated is not None
    assert updated.status == "completed"
    assert updated.passed is True
    assert updated.reward == 1.0
    assert updated.total_tokens == 5400

    # Verify in DuckDB
    res = temp_store.con.execute(
        "SELECT status, passed, reward FROM sessions WHERE session_id = ?",
        [session.session_id],
    ).fetchall()
    assert res[0][0] == "completed"
    assert res[0][1] is True
    assert res[0][2] == 1.0


def test_watcher_store_record_decision(temp_store: WatcherStore) -> None:
    """Verify recording and querying review decisions."""
    session = Session(project_name="test_proj", agent_type="claude_code")
    temp_store.create_session(session)

    review = ReviewRecord(
        session_id=session.session_id,
        tool_name="Edit",
        tool_input="/project/.env",
        decision="block",
        score=9,
        stage="rule",
        rule_name="Sensitive files",
        explanation="Direct edit to secret credentials blocked.",
        diff="- SECRET=123\n+ SECRET=456",
        latency_ms=45.2,
    )
    temp_store.record_decision(review)

    decisions = temp_store.get_session_decisions(session.session_id)
    assert len(decisions) == 1
    assert decisions[0].decision == "block"
    assert decisions[0].score == 9
    assert decisions[0].rule_name == "Sensitive files"

    # Verify in DuckDB
    rev_rows = temp_store.con.execute(
        "SELECT tool_name, decision, score, stage FROM reviews WHERE session_id = ?",
        [session.session_id],
    ).fetchall()
    assert len(rev_rows) == 1
    assert rev_rows[0][0] == "Edit"
    assert rev_rows[0][1] == "block"
    assert rev_rows[0][2] == 9


def test_watcher_store_trajectory_append(temp_store: WatcherStore) -> None:
    """Verify appending messages, tool calls, and results to trajectory."""
    session = Session(project_name="test_traj", agent_type="cursor")
    temp_store.create_session(session)

    msg = Message(role="user", content="Please refactor login.py")
    tc = ToolCall(tool_name="view_file", arguments={"path": "login.py"})
    tr = ToolResult(tool_id=tc.tool_id, tool_name="view_file", stdout="def login(): pass")

    temp_store.append_trajectory_event(session.session_id, msg)
    temp_store.append_trajectory_event(session.session_id, tc)
    temp_store.append_trajectory_event(session.session_id, tr)

    traj = temp_store.get_trajectory(session.session_id)
    assert traj is not None
    assert len(traj.messages) == 1
    assert len(traj.tool_calls) == 1
    assert len(traj.tool_results) == 1
    assert traj.tool_calls[0].tool_name == "view_file"
    assert traj.tool_results[0].stdout == "def login(): pass"


def test_watcher_store_list_filtering(temp_store: WatcherStore) -> None:
    """Verify filtering sessions by agent_type and status."""
    temp_store.create_session(Session(project_name="p1", agent_type="claude_code", status="active"))
    temp_store.create_session(
        Session(project_name="p2", agent_type="claude_code", status="completed")
    )
    temp_store.create_session(
        Session(project_name="p3", agent_type="inspect_eval", status="active")
    )

    claude_sessions = temp_store.list_sessions(agent_type="claude_code")
    assert len(claude_sessions) == 2

    active_claude = temp_store.list_sessions(agent_type="claude_code", status="active")
    assert len(active_claude) == 1
    assert active_claude[0].project_name == "p1"
