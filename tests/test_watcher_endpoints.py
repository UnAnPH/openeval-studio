"""Integration tests for official Watcher REST endpoints."""

import pytest
from fastapi.testclient import TestClient

from schemas.watcher_models import Session
from server.app import app
from server.watcher_store import get_watcher_store


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_watcher_sessions_and_decisions_flow(client: TestClient) -> None:
    """Verify session creation, tool review, decision logging, and resolution."""
    store = get_watcher_store()
    session = Session(
        project_name="openeval-studio",
        agent_type="claude_code",
        model="claude-3-7-sonnet-latest",
        provider="anthropic",
        status="working",
    )
    store.create_session(session)

    # 1. List sessions
    res = client.get("/api/v1/watcher/sessions")
    assert res.status_code == 200
    sessions = res.json()
    assert any(s["session_id"] == session.session_id for s in sessions)

    # 2. Get session
    res_sess = client.get(f"/api/v1/watcher/sessions/{session.session_id}")
    assert res_sess.status_code == 200
    assert res_sess.json()["project_name"] == "openeval-studio"

    # 3. Review tool call (blocked by Sensitive files rule)
    review_res = client.post(
        "/api/v1/watcher/review",
        json={
            "session_id": session.session_id,
            "tool_name": "execute_bash",
            "tool_input": "cat .env",
            "diff": "- SECRET=123\n+ SECRET=456",
        },
    )
    assert review_res.status_code == 200
    review_data = review_res.json()
    assert review_data["decision"] == "block"
    assert review_data["score"] == 9
    assert review_data["rule_name"] == "Sensitive files"

    # 4. Get decisions
    dec_res = client.get(f"/api/v1/watcher/sessions/{session.session_id}/decisions")
    assert dec_res.status_code == 200
    decisions = dec_res.json()
    assert len(decisions) >= 1
    assert decisions[-1]["rule_name"] == "Sensitive files"

    # 5. Resolve decision (Human Override Allow Once)
    resolve_res = client.post(
        f"/api/v1/watcher/sessions/{session.session_id}/resolve",
        json={
            "review_id": review_data["id"],
            "action": "allow_once",
            "notes": "Allowed for developer debugging",
        },
    )
    assert resolve_res.status_code == 200
    assert resolve_res.json()["status"] == "resolved"
    assert resolve_res.json()["review"]["decision"] == "allow"


def test_watcher_policy_and_rules_endpoints(client: TestClient) -> None:
    """Verify policy retrieval and rule/threshold modification."""
    # 1. Get policy
    res = client.get("/api/v1/watcher/policy")
    assert res.status_code == 200
    policy = res.json()
    assert "command_rules" in policy
    assert len(policy["command_rules"]) >= 10
    assert "tool_thresholds" in policy

    # 2. Update a command rule
    rule_res = client.post(
        "/api/v1/watcher/policy/rule",
        json={"rule_name": "Force push", "action": "human"},
    )
    assert rule_res.status_code == 200
    assert rule_res.json()["action"] == "human"

    # 3. Update a tool threshold
    thresh_res = client.post(
        "/api/v1/watcher/policy/threshold",
        json={
            "tool_name": "execute_bash",
            "auto_approve_le": 2,
            "escalate_ge": 5,
            "auto_deny_ge": 8,
            "always_escalate": False,
        },
    )
    assert thresh_res.status_code == 200
    assert thresh_res.json()["status"] == "updated"

    # Reset rule and threshold back to defaults
    client.post("/api/v1/watcher/policy/rule", json={"rule_name": "Force push", "action": "deny"})
    client.post(
        "/api/v1/watcher/policy/threshold",
        json={
            "tool_name": "execute_bash",
            "auto_approve_le": 3,
            "escalate_ge": 5,
            "auto_deny_ge": 8,
            "always_escalate": False,
        },
    )


def test_policy_reset_and_evaluate_uses_store_thresholds(client: TestClient) -> None:
    """Saved tool thresholds must drive /api/watcher/evaluate deny decisions."""
    reset = client.post("/api/v1/watcher/policy/reset")
    assert reset.status_code == 200

    # Force Bash / execute_bash to always escalate (and auto-deny at 5+)
    for tool in ("Bash", "execute_bash", "run_command", "*"):
        client.post(
            "/api/v1/watcher/policy/threshold",
            json={
                "tool_name": tool,
                "auto_approve": False,
                "escalate_ge": 4,
                "auto_deny_ge": 5,
                "always_escalate": False,
            },
        )

    # Destructive command should be blocked by store-backed gateway
    res = client.post(
        "/api/watcher/evaluate",
        json={
            "agent_id": "claude_code",
            "tool_name": "Bash",
            "arguments": {"command": "rm -rf /tmp/dangerous-wipe"},
            "session_id": "test-threshold-session",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] in ("deny", "escalate")
    assert data["mode_applied"] == "enforce"

    # Always-escalate tool should escalate even for benign cmds
    client.post(
        "/api/v1/watcher/policy/threshold",
        json={
            "tool_name": "Bash",
            "auto_approve": False,
            "always_escalate": True,
            "escalate_ge": None,
            "auto_deny_ge": None,
        },
    )
    res2 = client.post(
        "/api/watcher/evaluate",
        json={
            "agent_id": "claude_code",
            "tool_name": "Bash",
            "arguments": {"command": "echo hello"},
            "session_id": "test-threshold-session-2",
        },
    )
    assert res2.status_code == 200
    assert res2.json()["decision"] == "escalate"

    client.post("/api/v1/watcher/policy/reset")


def test_watcher_doctor_endpoint(client: TestClient) -> None:
    """Verify watcher doctor diagnostic endpoint."""
    res = client.get("/api/v1/watcher/doctor")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["duckdb_connected"] is True
    assert data["storage_writable"] is True


def test_watcher_analyzer_lite(client: TestClient) -> None:
    """Analyzer-lite returns org blocked/escalated aggregates."""
    res = client.get("/api/v1/watcher/analyzer")
    assert res.status_code == 200
    data = res.json()
    assert "blocked_decisions" in data
    assert "escalated_decisions" in data
    assert "session_count" in data
    assert "fleet" in data


def test_analyzer_summary_endpoint(client: TestClient) -> None:
    """Verify organization-wide DuckDB Analyzer summary endpoint."""
    store = get_watcher_store()
    sess = Session(
        session_id="analyzer-test-sess",
        project_name="openeval-studio",
        agent_type="cursor",
        status="completed",
    )
    store.create_session(sess)

    res = client.get("/api/v1/analyzer/summary")
    assert res.status_code == 200
    data = res.json()
    assert "total_reviews" in data
    assert "total_blocked" in data
    assert "block_rate_pct" in data
    assert "top_threats" in data
    assert "agent_distribution" in data
    assert "recent_interventions" in data
    assert "p50_latency_ms" in data
    assert "p95_latency_ms" in data


def test_watcher_tool_result_endpoint(client: TestClient) -> None:
    """Verify tool execution result reporting endpoint."""
    store = get_watcher_store()
    sess = Session(
        session_id="test-result-sess-1",
        project_name="openeval-studio",
        agent_type="antigravity",
        status="working",
    )
    store.create_session(sess)

    # Report tool result
    res = client.post(
        "/api/watcher/result",
        json={
            "session_id": "test-result-sess-1",
            "agent_id": "antigravity",
            "tool_name": "run_command",
            "stdout": "total 42\n-rw-r--r-- file.txt",
            "stderr": "",
            "exit_code": 0,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "recorded"
    assert "total 42" in data["tool_result"]

    # Verify session in store was updated with ToolResult
    updated = store.get_session("test-result-sess-1")
    assert updated is not None
    assert len(updated.trajectory.tool_results) >= 1
    assert "total 42" in updated.trajectory.tool_results[-1].stdout
