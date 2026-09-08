"""Unit tests for Live Local Coding Agent Watcher Daemon (Antigravity & Claude Code)."""

import json
from pathlib import Path

from server.agent_daemon import AgentWatcherDaemon


def test_daemon_status_initial():
    """Verify daemon reports initial status correctly."""
    daemon = AgentWatcherDaemon()
    status = daemon.get_status()

    assert status.is_running is False
    assert (
        "Monitoring Antigravity & Claude Code" in status.status_text
        or status.status_text == "STOPPED"
    )
    assert len(status.monitored_sources) == 2
    assert any("Antigravity" in s for s in status.monitored_sources)
    assert any("Claude Code" in s for s in status.monitored_sources)


def test_daemon_scan_antigravity_logs(tmp_path: Path):
    """Verify daemon discovers and scans Antigravity transcripts."""
    # Create simulated Antigravity brain dir
    brain_dir = tmp_path / "brain"
    conv_dir = brain_dir / "conv_abc123" / ".system_generated" / "logs"
    conv_dir.mkdir(parents=True, exist_ok=True)
    transcript_file = conv_dir / "transcript.jsonl"

    sample_step = {
        "step_index": 1,
        "type": "PLANNER_RESPONSE",
        "content": "Executing command",
        "tool_calls": [
            {
                "name": "run_command",
                "args": {"CommandLine": "ls -la"},
            }
        ],
    }
    transcript_file.write_text(json.dumps(sample_step) + "\n", encoding="utf-8")

    daemon = AgentWatcherDaemon(
        antigravity_brain_dir=brain_dir,
        claude_sessions_dir=tmp_path / "claude",
    )

    events = daemon.scan_once()
    assert events == 1
    assert daemon.events_intercepted_count >= 1

    status = daemon.get_status()
    assert status.files_tracked_count >= 1


def test_daemon_scan_claude_code_logs_blocked_action(tmp_path: Path):
    """Verify daemon intercepts Claude Code tools and increments blocked count on dangerous commands."""
    claude_dir = tmp_path / "claude" / "sessions"
    claude_dir.mkdir(parents=True, exist_ok=True)
    session_file = claude_dir / "claude_sess_01.jsonl"

    dangerous_step = {
        "type": "tool_call",
        "tool": "run_command",
        "arguments": {"CommandLine": "rm -rf /"},
    }
    session_file.write_text(json.dumps(dangerous_step) + "\n", encoding="utf-8")

    daemon = AgentWatcherDaemon(
        antigravity_brain_dir=tmp_path / "brain",
        claude_sessions_dir=claude_dir,
    )

    events = daemon.scan_once()
    assert events == 1
    assert daemon.events_intercepted_count >= 1
    assert daemon.blocked_actions_count >= 1
