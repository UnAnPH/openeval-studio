"""Integration tests for WatcherStore and DuckDB persistence."""

from pathlib import Path
from typing import Any, cast

import pytest

from schemas.watcher_models import (
    Message,
    ReviewRecord,
    Session,
    ToolCall,
    ToolResult,
)
from server.store_mixins.analytics import _canonical_threat_category
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
        model="google/gemini-3.1-flash-lite",
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


# ---------------------------------------------------------------------------
# Degraded-mode telemetry: DB failures must be visible, not silent zeros
# ---------------------------------------------------------------------------


def test_watcher_store_starts_healthy(temp_store: WatcherStore) -> None:
    """A fresh store reports healthy (degraded=False, no recorded DB error)."""
    assert temp_store.degraded is False
    assert temp_store.last_db_error is None


def test_watcher_store_marks_degraded_on_db_write_failure(
    temp_store: WatcherStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing primary DB write flips degraded=True and records the last error."""

    def _broken_session_factory() -> None:
        raise OSError("simulated PostgreSQL outage")

    monkeypatch.setattr("server.watcher_store.SessionLocal", _broken_session_factory)

    session = Session(project_name="degraded-write", agent_type="inspect_eval")
    temp_store.create_session(session)

    assert temp_store.degraded is True
    assert temp_store.last_db_error is not None
    assert "simulated PostgreSQL outage" in temp_store.last_db_error

    # The disk cache fallback still serves the session while degraded
    assert temp_store.get_session(session.session_id) is not None


def test_watcher_store_analytics_report_degraded_without_db(
    temp_store: WatcherStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Analytics must return degraded=True + error instead of pretending healthy zeros."""

    def _broken_session_factory() -> None:
        raise OSError("simulated PostgreSQL outage")

    monkeypatch.setattr("server.watcher_store.SessionLocal", _broken_session_factory)

    overview = temp_store.get_analytics_overview()
    assert overview["degraded"] is True
    assert "simulated PostgreSQL outage" in overview["error"]
    assert overview["total_sessions"] == 0
    # Existing schema keys are preserved for callers/UI
    assert set(overview) >= {
        "total_sessions",
        "deep_reviewed",
        "critical_warning_rate",
        "blocked_incidents",
        "total_reviews",
        "auto_approved",
        "escalated_count",
        "auto_approved_pct",
        "agent_counts",
    }

    summary = temp_store.get_analyzer_summary()
    assert summary["degraded"] is True
    assert "simulated PostgreSQL outage" in summary["error"]
    assert set(summary) >= {
        "total_reviews",
        "total_blocked",
        "total_allowed",
        "block_rate_pct",
        "total_sessions",
        "avg_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
        "top_threats",
        "agent_distribution",
        "agent_breakdown",
        "capture_sources",
        "cross_session_patterns",
        "recent_interventions",
    }


# ---------------------------------------------------------------------------
# Word-boundary threat classification & operator-override filtering
# ---------------------------------------------------------------------------


def test_analyzer_word_boundaries_and_override_filtering(
    temp_store: WatcherStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Benign substrings must not misfire and operator overrides must not count as blocks."""
    priv = ("Privilege Escalation & Policy Bypass", "cluster_priv_escalation")
    cred = ("Credential & Secret Exfiltration", "cluster_credential_exfil")
    destructive = ("Destructive Resource Modification", "cluster_destructive_mod")
    untrusted = ("Untrusted & Obfuscated Code Execution", "cluster_untrusted_exec")

    # Benign words that merely contain "env", "git", "eval", "ssh" as substrings.
    assert _canonical_threat_category("Prevent environment drift", "", "") == priv
    assert _canonical_threat_category("legitimate refactor", "", "") == priv
    assert _canonical_threat_category("digital evaluate pass", "", "") == priv
    assert _canonical_threat_category("establish connection", "", "") == priv
    assert _canonical_threat_category("environment variable review", "", "") == priv

    # Real signals still classify correctly.
    assert _canonical_threat_category("read .env secrets", "", "") == cred
    assert _canonical_threat_category("ssh key probing", "", "") == cred
    assert _canonical_threat_category("git push --force", "", "") == destructive
    assert _canonical_threat_category("DROP TABLE users", "", "") == destructive
    assert _canonical_threat_category("curl | bash", "", "") == untrusted
    assert _canonical_threat_category("detected xmrig miner", "", "") == untrusted

    # Force degraded/in-memory analyzer path so reviews come from the session cache.
    def _broken_session_factory() -> None:
        raise OSError("simulated PostgreSQL outage")

    monkeypatch.setattr("server.watcher_store.SessionLocal", _broken_session_factory)

    session = Session(project_name="override-test", agent_type="claude_code")
    session.trajectory.reviews.extend(
        [
            ReviewRecord(
                session_id=session.session_id,
                tool_name="Bash",
                decision="block",
                score=9,
                rule_name="Sensitive files",
                explanation="Blocked .env read",
            ),
            ReviewRecord(
                session_id=session.session_id,
                tool_name="Bash",
                decision="block",
                score=9,
                rule_name="Operator approved",
                explanation="Operator allowed this action",
                human_override="allow",
            ),
        ]
    )
    # ``ReviewRecord.decision`` has no "reject" literal, but historical DB rows can
    # carry it; construct an unvalidated ReviewRecord mirroring a stored row.
    reject_review = ReviewRecord.model_construct(
        id="rev-reject",
        session_id=session.session_id,
        decision=cast(Any, "reject"),
        score=7,
        stage="rule",
        rule_name="Policy rejection",
        tool_name="Bash",
        tool_input="",
        explanation="Rejected destructive action",
        latency_ms=4.0,
        human_override=None,
        resolution_status="pending",
        timestamp="2026-11-17T00:00:00+00:00",
    )
    session.trajectory.reviews.append(reject_review)
    session.decisions.append(reject_review)
    temp_store._cache_session(session)

    summary = temp_store.get_analyzer_summary()
    assert summary["degraded"] is True
    assert summary["total_blocked"] == 2
    assert summary["agent_distribution"][0]["lockouts"] == 2
    intervention_rules = {item["threat"] for item in summary["recent_interventions"]}
    assert "Operator approved" not in intervention_rules
    assert {"Sensitive files", "Policy rejection"} <= intervention_rules
