"""Unit tests for official Watcher canonical models."""

import pytest
from pydantic import ValidationError

from schemas.watcher_models import (
    DEFAULT_COMMAND_RULES,
    DEFAULT_TOOL_THRESHOLDS,
    Message,
    Policy,
    ReviewRecord,
    Session,
    ToolCall,
    ToolResult,
    Trajectory,
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
