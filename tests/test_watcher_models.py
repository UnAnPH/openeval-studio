"""Unit tests for official Watcher canonical models."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from schemas.watcher_models import (
    DEFAULT_COMMAND_RULES,
    DEFAULT_TOOL_THRESHOLDS,
    Message,
    Policy,
    RawToolCall,
    ReviewRecord,
    Session,
    SessionEventRecord,
    ToolCall,
    ToolCallEventPayload,
    ToolResult,
    Trajectory,
    review_from_row,
)


def test_message_and_trajectory_models() -> None:
    """Verify Message, ToolCall, and Trajectory initialization."""
    msg = Message(role="assistant", content="Hello world", thinking="Internal thought")
    assert msg.role == "assistant"
    assert msg.content == "Hello world"
    assert msg.thinking == "Internal thought"

    tc = ToolCall(tool_name="execute_bash", arguments={"command": "ls -la"})
    assert tc.tool_name == "execute_bash"
    assert tc.arguments["command"] == "ls -la"

    tr = ToolResult(
        tool_id=tc.tool_id, tool_name="execute_bash", stdout="file1\nfile2", exit_code=0
    )
    assert tr.exit_code == 0
    assert not tr.is_error

    traj = Trajectory(session_id="test_sess")
    traj.messages.append(msg)
    traj.tool_calls.append(tc)
    traj.tool_results.append(tr)

    assert len(traj.messages) == 1
    assert len(traj.tool_calls) == 1
    assert len(traj.tool_results) == 1


def test_review_record_validation() -> None:
    """Verify ReviewRecord severity score boundaries (1-10) and fields."""
    rec = ReviewRecord(
        session_id="s123",
        tool_name="execute_bash",
        tool_input="rm -rf /",
        decision="block",
        score=10,
        stage="rule",
        rule_name="Destructive filesystem removal",
        explanation="Blocked destructive root wipe.",
        diff=None,
        latency_ms=12.5,
    )
    assert rec.score == 10
    assert rec.decision == "block"
    assert rec.stage == "rule"

    # Score > 10 should fail validation
    with pytest.raises(ValidationError):
        ReviewRecord(session_id="s123", tool_name="bash", score=11)

    # Score < 1 should fail validation
    with pytest.raises(ValidationError):
        ReviewRecord(session_id="s123", tool_name="bash", score=0)


def test_session_unified_identity() -> None:
    """Verify Session handles both live agents and benchmark runs seamlessly."""
    # Benchmark session
    sess_bench = Session(
        task_id="delivery-routing",
        model="google/gemini-2.5-flash",
        agent_type="inspect_eval",
    )
    assert sess_bench.project_name == "delivery-routing"
    assert sess_bench.task_id == "delivery-routing"
    assert sess_bench.run_id == sess_bench.session_id
    assert sess_bench.agent_type == "inspect_eval"

    # Claude Code session
    sess_claude = Session(
        project_name="openeval-studio",
        agent_type="claude_code",
        model="claude-3-7-sonnet-latest",
        provider="anthropic",
        status="working",
    )
    assert sess_claude.project_name == "openeval-studio"
    assert sess_claude.agent_type == "claude_code"
    assert sess_claude.status == "working"


def test_command_rules_matching() -> None:
    """Verify seeded 63 command rules accurately detect risky vs safe commands."""
    rule_map = {r.name: r for r in DEFAULT_COMMAND_RULES}

    # Git force push
    force_rule = rule_map["Force push"]
    assert force_rule.matches("git push origin main --force")
    assert force_rule.matches("git push --force-with-lease")
    assert not force_rule.matches("git push origin main")

    # Sensitive files
    sec_rule = rule_map["Sensitive files"]
    assert sec_rule.matches("cat .env")
    assert sec_rule.matches("vim ~/.aws/credentials")
    assert sec_rule.matches("nano ~/.ssh/id_rsa")
    assert sec_rule.matches("openssl req -key server.key")
    assert not sec_rule.matches("cat src/main.py")

    # Git read only
    read_rule = rule_map["Git read-only"]
    assert read_rule.matches("git status")
    assert read_rule.matches("git diff HEAD~1")
    assert read_rule.matches("git log -n 5")
    assert not read_rule.matches("git commit -m 'test'")


def test_policy_and_tool_thresholds() -> None:
    """Verify Policy container and ToolThreshold lookups."""
    policy = Policy(
        policy_id="test_pol",
        command_rules=list(DEFAULT_COMMAND_RULES),
        tool_thresholds=list(DEFAULT_TOOL_THRESHOLDS),
    )
    assert len(policy.command_rules) >= 10
    assert len(policy.tool_thresholds) >= 8

    bash_thresh = next(t for t in policy.tool_thresholds if t.tool_name == "execute_bash")
    assert bash_thresh.auto_approve_le == 3
    assert bash_thresh.auto_deny_ge == 8


# ---------------------------------------------------------------------------
# Canonical ReviewRecord row mapper
# ---------------------------------------------------------------------------


def _review_row(*extra: object) -> tuple[object, ...]:
    """13-column row matching the canonical ReviewRecord SELECT projection."""
    return (
        "rev-1",
        "sess-1",
        "Bash",
        "rm -rf /tmp/x",
        "block",
        9,
        "rule",
        "Destructive wipe",
        "Blocked destructive removal.",
        "--- a\n+++ b",
        12.5,
        datetime(2026, 10, 1, 12, 30, tzinfo=UTC),
        *extra,
    )


def test_review_from_row_full_projection() -> None:
    """13-column rows map every field, including datetime normalization."""
    rec = review_from_row(_review_row("req-abc"))
    assert rec.id == "rev-1"
    assert rec.session_id == "sess-1"
    assert rec.tool_name == "Bash"
    assert rec.decision == "block"
    assert rec.score == 9
    assert rec.stage == "rule"
    assert rec.rule_name == "Destructive wipe"
    assert rec.rule_id == "Destructive wipe"  # model_post_init alias
    assert rec.diff == "--- a\n+++ b"
    assert rec.latency_ms == 12.5
    assert rec.timestamp == "2026-10-01T12:30:00+00:00"
    assert rec.request_id == "req-abc"


def test_review_from_row_legacy_11_columns_ends_with_request_id() -> None:
    """Legacy 11-column rows ending in a string treat it as request_id."""
    row = (
        "rev-legacy",
        "sess-legacy",
        "Edit",
        "src/app.py",
        "escalate",
        6,
        "triage",
        None,
        "Needs review",
        None,
        "req-legacy",
    )
    rec = review_from_row(row)
    assert rec.request_id == "req-legacy"
    assert rec.latency_ms == 0.0
    assert rec.timestamp  # normalized fallback, never an empty string
    assert rec.rule_name is None


def test_review_from_row_ten_column_prefix_uses_defaults() -> None:
    """Short rows are treated as a prefix of the canonical column order."""
    row = (
        "rev-short",
        "sess-short",
        "Read",
        "README.md",
        "allow",
        1,
        "threshold",
        None,
        "",
        None,
    )
    rec = review_from_row(row)
    assert rec.decision == "allow"
    assert rec.score == 1
    assert rec.latency_ms == 0.0
    assert rec.request_id is None
    assert rec.diff is None


@pytest.mark.parametrize("as_json_string", [False, True])
def test_review_from_row_overlays_payload_snapshot(as_json_string: bool) -> None:
    """JSONB payloads (parsed dict or JSON string) overlay typed telemetry."""
    snapshot = {
        "id": "rev-1",
        "session_id": "sess-1",
        "tool_name": "Bash",
        "tool_input": "rm -rf /tmp/x",
        "decision": "block",
        "score": 10,
        "stage": "deep_review",
        "explanation": "Deep review confirmed exfiltration.",
        "provenance": {"call_origin": "subagent", "delegation_depth": 2},
        "calibrated_telemetry": {
            "expected_severity": 9.5,
            "top_threat_category": "data_exfiltration",
        },
    }
    payload_blob: object = (
        json.dumps({"trajectory": {"reviews": [snapshot]}})
        if as_json_string
        else {"trajectory": {"reviews": [snapshot]}}
    )

    rec = review_from_row(_review_row("req-keep"), payload=payload_blob)
    assert rec.score == 10
    assert rec.stage == "deep_review"
    assert rec.provenance.call_origin == "subagent"
    assert rec.provenance.delegation_depth == 2
    assert rec.calibrated_telemetry is not None
    assert rec.calibrated_telemetry.expected_severity == 9.5
    # The relational request_id survives a snapshot that does not carry one.
    assert rec.request_id == "req-keep"


def test_review_from_row_payload_in_last_column_is_not_request_id() -> None:
    """get_session_decisions selects the JSONB payload in the request_id slot."""
    row = (
        "rev-db",
        "sess-db",
        "Bash",
        "ls",
        "allow",
        1,
        "rule",
        None,
        "",
        None,
        1.0,
        "2026-10-01T00:00:00+00:00",
        {"trajectory": {"reviews": [{"id": "rev-db", "score": 4, "stage": "triage"}]}},
    )
    rec = review_from_row(row)
    assert rec.request_id is None
    assert rec.score == 4
    assert rec.stage == "triage"


# ---------------------------------------------------------------------------
# Typed hook payloads
# ---------------------------------------------------------------------------


def test_raw_tool_call_normalizes_hook_shapes() -> None:
    """OpenAI/Anthropic/Cursor style payloads map onto one typed shape."""
    openai_style = RawToolCall.model_validate(
        {
            "id": "call-1",
            "function": {"name": "Bash", "arguments": {"cmd": "ls -la"}},
            "tool_input": "ls -la",
        }
    )
    assert openai_style.tool_id == "call-1"
    assert openai_style.tool_name == "Bash"
    assert openai_style.arguments == {"cmd": "ls -la"}
    assert openai_style.raw_input == "ls -la"

    cursor_style = RawToolCall.model_validate({"tool": "Edit", "args": {"path": "a.py"}})
    assert cursor_style.tool_name == "Edit"
    assert cursor_style.arguments == {"path": "a.py"}
    assert cursor_style.raw_input is None


def test_session_event_payload_stays_backward_compatible() -> None:
    """Dict payloads still work; typed views are additive."""
    event = SessionEventRecord(
        session_id="sess-ev",
        event_type="tool_call",
        payload={"tool_id": "t1", "tool_name": "Bash", "arguments": {"cmd": "ls"}},
    )
    assert isinstance(event.payload, dict)
    assert event.model_dump()["payload"]["tool_name"] == "Bash"

    typed = event.typed_payload()
    assert isinstance(typed, ToolCallEventPayload)
    assert typed.tool_name == "Bash"
    raw_call = event.raw_tool_call()
    assert raw_call is not None
    assert raw_call.arguments == {"cmd": "ls"}

    # A typed payload model is accepted where a dict used to be required.
    from_model = SessionEventRecord(
        session_id="sess-ev",
        event_type="tool_call",
        payload=ToolCallEventPayload(tool_name="Edit", arguments={"path": "a.py"}),  # type: ignore[arg-type]
    )
    assert from_model.payload["tool_name"] == "Edit"

    unknown = SessionEventRecord(
        session_id="sess-ev", event_type="materialize", payload={"any": "shape"}
    )
    assert unknown.typed_payload() is None


# ---------------------------------------------------------------------------
# Relational row coercion
# ---------------------------------------------------------------------------


def test_review_from_row_coerces_unknown_values_cleanly() -> None:
    """Untyped DB values narrow to canonical literals instead of raising."""
    null_columns = review_from_row(
        (
            "rev-null",
            "sess-null",
            "Bash",
            "echo hi",
            None,
            None,
            None,
            None,
            "",
            None,
            None,
            None,
            None,
        )
    )
    assert null_columns.decision == "allow"
    assert null_columns.stage == "rule"
    assert null_columns.score == 1
    assert null_columns.latency_ms == 0.0

    unknown_strings = review_from_row(
        ("rev-unknown", "sess-unknown", "Bash", "ls", "totally-unknown", "not-a-number", 42)
    )
    assert unknown_strings.decision == "allow"
    assert unknown_strings.stage == "rule"
    assert unknown_strings.score == 1

    numeric_strings = review_from_row(
        (
            "rev-num",
            "sess-num",
            "Bash",
            "ls",
            "deny",
            "7",
            "deep_review",
            None,
            "",
            None,
            "3.5",
            None,
            None,
        )
    )
    assert numeric_strings.decision == "deny"
    assert numeric_strings.score == 7
    assert numeric_strings.stage == "deep_review"
    assert numeric_strings.latency_ms == 3.5


# ---------------------------------------------------------------------------
# Live canonical grader reset
# ---------------------------------------------------------------------------


def test_reset_canonical_graders_restores_exact_defaults() -> None:
    """reset_canonical_graders() restores the immutable snapshot between tests."""
    from engine.graders_suite import (
        _DEFAULT_CANONICAL_GRADERS,
        CANONICAL_GRADERS,
        reset_canonical_graders,
        update_live_canonical_grader,
    )

    default_role = _DEFAULT_CANONICAL_GRADERS["PolicyGatewayDeepGrader"].c2_role
    try:
        updated = update_live_canonical_grader("PolicyGatewayDeepGrader", {"c2_role": "OVERRIDE"})
        assert updated is not None
        assert CANONICAL_GRADERS["PolicyGatewayDeepGrader"].c2_role == "OVERRIDE"

        CANONICAL_GRADERS["RuntimeInjectedGrader"] = updated.model_copy()
        restored = reset_canonical_graders()

        assert "PolicyGatewayDeepGrader" in restored
        assert "RuntimeInjectedGrader" not in CANONICAL_GRADERS
        assert CANONICAL_GRADERS["PolicyGatewayDeepGrader"].c2_role == default_role
        assert set(CANONICAL_GRADERS) == set(_DEFAULT_CANONICAL_GRADERS)
    finally:
        reset_canonical_graders()
