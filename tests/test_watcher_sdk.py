"""Unit tests for WatcherClient typed Python SDK."""

from pathlib import Path

import pytest

from engine.watcher_sdk import WatcherClient
from server.watcher_store import WatcherStore


@pytest.fixture
def client(tmp_path: Path) -> WatcherClient:
    """Create an isolated WatcherClient with a temp store."""
    store = WatcherStore(storage_dir=tmp_path / "sdk_sessions", db_path=":memory:")
    return WatcherClient(store=store)


def test_sdk_session_lifecycle(client: WatcherClient) -> None:
    """Verify session creation, message logging, tool calls, and completion."""
    session = client.start_session(
        project_name="test_sdk_task",
        agent_type="claude_code",
        model="claude-3-7-sonnet-latest",
        provider="anthropic",
        working_dir="/tmp/repo",
    )
    assert session.session_id is not None
    assert session.status == "active"

    # Log user message
    msg = client.log_message(
        session_id=session.session_id,
        role="user",
        content="Please run tests and fix any failures",
    )
    assert msg.role == "user"

    # Record tool call
    call = client.record_tool_call(
        session_id=session.session_id,
        tool_name="execute_bash",
        arguments={"command": "pytest"},
    )
    assert call.tool_name == "execute_bash"

    # Record tool result
    res = client.record_tool_result(
        session_id=session.session_id,
        tool_id=call.tool_id,
        tool_name="execute_bash",
        stdout="1 passed in 0.5s",
        exit_code=0,
    )
    assert res.exit_code == 0

    # Finish session
    finished = client.finish_session(
        session_id=session.session_id,
        passed=True,
        reward=1.0,
        total_tokens=1500,
        total_duration_sec=3.5,
    )
    assert finished is not None
    assert finished.status == "completed"
    assert finished.passed is True
    assert finished.reward == 1.0


def test_sdk_review_tool_call_gating(client: WatcherClient) -> None:
    """Verify real-time tool review gating evaluates command rules and thresholds."""
    session = client.start_session(project_name="review_test", agent_type="claude_code")

    # 1. Force push -> Blocked
    rec1 = client.review_tool_call(
        session_id=session.session_id,
        tool_name="execute_bash",
        tool_input="git push origin main --force",
    )
    assert rec1.decision == "block"
    assert rec1.score == 9
    assert rec1.rule_name == "Force push"

    # 2. Reading .env -> Blocked by Sensitive files
    rec2 = client.review_tool_call(
        session_id=session.session_id,
        tool_name="execute_bash",
        tool_input="cat .env",
    )
    assert rec2.decision == "block"
    assert rec2.score == 9
    assert rec2.rule_name == "Sensitive files"

    # 3. Read only git status -> Allowed
    rec3 = client.review_tool_call(
        session_id=session.session_id,
        tool_name="execute_bash",
        tool_input="git status",
    )
    assert rec3.decision == "allow"
    assert rec3.score == 1


def test_sdk_doctor_diagnostics(client: WatcherClient) -> None:
    """Verify watcher doctor diagnostic output."""
    diag = client.doctor()
    assert diag["status"] == "healthy"
    assert diag["duckdb_connected"] is True
    assert diag["storage_writable"] is True
    assert diag["active_command_rules"] >= 10
    assert diag["active_tool_thresholds"] >= 8
