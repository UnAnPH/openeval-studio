"""Live Local Coding Agent Watcher Daemon.

Continuously monitors local coding agent transcripts in real-time:
  1. Antigravity: ~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl
  2. Claude Code: ~/.claude/sessions/*.jsonl

As tools execute, tails JSONL logs, extracts tool actions, and pipes them through
OpenEval Watcher's 4-stage safety firewall (evaluate_action_safety).
"""

import asyncio
import glob
import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from engine.approval_policy import (
    evaluate_action_safety,
)
from server.agent_log_loader import UniversalAgentLogLoader

logger = logging.getLogger("openeval.server.agent_daemon")


class DaemonStatus(BaseModel):
    """Runtime telemetry and health for the coding agent daemon."""

    is_running: bool
    status_text: str
    monitored_sources: list[str]
    last_scanned_at: str | None = None
    files_tracked_count: int = 0
    events_intercepted_count: int = 0
    blocked_actions_count: int = 0


class AgentWatcherDaemon:
    """Async background worker tailing Antigravity and Claude Code sessions."""

    _instance: "AgentWatcherDaemon | None" = None

    def __init__(
        self,
        antigravity_brain_dir: Path | None = None,
        claude_sessions_dir: Path | None = None,
        poll_interval_sec: float = 1.0,
    ) -> None:
        self.antigravity_dir = (
            antigravity_brain_dir or Path.home() / ".gemini" / "antigravity" / "brain"
        )
        self.claude_dir = claude_sessions_dir or Path.home() / ".claude" / "sessions"
        self.poll_interval = poll_interval_sec
        self.is_running = False
        self._task: asyncio.Task[None] | None = None

        # Tracks {file_path: (last_mtime, last_size)}
        self._file_offsets: dict[str, tuple[float, int]] = {}

        self.last_scanned_at: str | None = None
        self.events_intercepted_count: int = 0
        self.blocked_actions_count: int = 0

    @classmethod
    def get_instance(cls) -> "AgentWatcherDaemon":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_status(self) -> DaemonStatus:
        """Returns current operational status."""
        return DaemonStatus(
            is_running=self.is_running,
            status_text="RUNNING (Monitoring Antigravity & Claude Code)"
            if self.is_running
            else "STOPPED",
            monitored_sources=[
                f"Antigravity ({self.antigravity_dir})",
                f"Claude Code ({self.claude_dir})",
            ],
            last_scanned_at=self.last_scanned_at,
            files_tracked_count=len(self._file_offsets),
            events_intercepted_count=self.events_intercepted_count,
            blocked_actions_count=self.blocked_actions_count,
        )

    def scan_once(self) -> int:
        """Single polling cycle checking Antigravity and Claude Code logs for updates."""
        new_events = 0

        # 1. Antigravity transcript scanning
        if self.antigravity_dir.exists():
            # Match ~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl
            pattern = str(
                self.antigravity_dir / "*" / ".system_generated" / "logs" / "transcript.jsonl"
            )
            matched_files = glob.glob(pattern)

            for file_str in matched_files:
                p = Path(file_str)
                try:
                    stat = p.stat()
                    prev = self._file_offsets.get(file_str)
                    if prev is None or stat.st_mtime > prev[0] or stat.st_size > prev[1]:
                        self._file_offsets[file_str] = (stat.st_mtime, stat.st_size)
                        session = UniversalAgentLogLoader.ingest_antigravity_transcript(p)
                        if session:
                            new_events += 1
                            self.events_intercepted_count += len(session.trajectory.tool_calls)
                            # Count blocked decisions
                            blocked = sum(
                                1 for r in session.trajectory.reviews if r.decision == "deny"
                            )
                            self.blocked_actions_count += blocked
                except Exception as err:
                    logger.debug("Error inspecting Antigravity log %s: %s", p, err)

        # 2. Claude Code session scanning
        if self.claude_dir.exists():
            claude_files = glob.glob(str(self.claude_dir / "*.jsonl"))
            for file_str in claude_files:
                p = Path(file_str)
                try:
                    stat = p.stat()
                    prev = self._file_offsets.get(file_str)
                    if prev is None or stat.st_mtime > prev[0] or stat.st_size > prev[1]:
                        self._file_offsets[file_str] = (stat.st_mtime, stat.st_size)
                        session = UniversalAgentLogLoader.ingest_claude_code_jsonl(p)
                        if session:
                            new_events += 1
                            # Evaluate tool calls
                            for tc in session.trajectory.tool_calls:
                                self.events_intercepted_count += 1
                                v = evaluate_action_safety(
                                    tool_name=tc.tool_name,
                                    arguments=dict(tc.arguments)
                                    if isinstance(tc.arguments, dict)
                                    else {},
                                    thought_context="Claude Code coding session",
                                    agent_id="claude_code",
                                )
                                if v.decision == "deny":
                                    self.blocked_actions_count += 1
                except Exception as err:
                    logger.debug("Error inspecting Claude Code log %s: %s", p, err)

        self.last_scanned_at = datetime.now(UTC).isoformat()
        return new_events

    async def _run_loop(self) -> None:
        """Background async loop."""
        logger.info("Agent Watcher Daemon started (Antigravity & Claude Code)")
        while self.is_running:
            try:
                self.scan_once()
            except Exception as e:
                logger.error("Error in agent daemon polling cycle: %s", e)
            await asyncio.sleep(self.poll_interval)

    def start(self) -> None:
        """Start the background tailing daemon."""
        if self.is_running:
            return
        self.is_running = True
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._run_loop())
        except RuntimeError:
            pass

    def stop(self) -> None:
        """Stop the background daemon."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        logger.info("Agent Watcher Daemon stopped")
