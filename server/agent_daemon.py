"""Live Local Coding Agent Watcher Daemon.

Continuously monitors local coding agent transcripts in real-time:
  1. Antigravity: ~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl
  2. Claude Code: ~/.claude/sessions/*.jsonl and ~/.claude/projects/*/*.jsonl
  3. Cursor: ~/.cursor/projects/*/agent-transcripts/*/*.jsonl

As tools execute, tails JSONL logs, extracts tool actions, and pipes them through
OpenEval Watcher's safety firewall (PolicyGateway heuristics on ingest).
"""

from __future__ import annotations

import asyncio
import glob
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from schemas.watcher_models import Session
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
    """Async background worker tailing Antigravity, Claude Code, and Cursor sessions."""

    _instance: AgentWatcherDaemon | None = None

    def __init__(
        self,
        antigravity_brain_dir: Path | None = None,
        claude_sessions_dir: Path | None = None,
        claude_projects_dir: Path | None = None,
        cursor_projects_dir: Path | None = None,
        poll_interval_sec: float = 1.0,
    ) -> None:
        self.antigravity_dir = (
            antigravity_brain_dir or Path.home() / ".gemini" / "antigravity" / "brain"
        )
        self.claude_dir = claude_sessions_dir or Path.home() / ".claude" / "sessions"
        self.claude_projects_dir = claude_projects_dir or Path.home() / ".claude" / "projects"
        self.cursor_dir = cursor_projects_dir or Path.home() / ".cursor" / "projects"
        self.poll_interval = poll_interval_sec
        self.is_running = False
        self._task: asyncio.Task[None] | None = None

        # Tracks {file_path: (last_mtime, last_size)}
        self._file_offsets: dict[str, tuple[float, int]] = {}

        self.last_scanned_at: str | None = None
        self.events_intercepted_count: int = 0
        self.blocked_actions_count: int = 0

    @classmethod
    def get_instance(cls) -> AgentWatcherDaemon:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_status(self) -> DaemonStatus:
        """Returns current operational status."""
        return DaemonStatus(
            is_running=self.is_running,
            status_text="RUNNING (Monitoring Antigravity, Claude Code & Cursor)"
            if self.is_running
            else "STOPPED",
            monitored_sources=[
                f"Antigravity ({self.antigravity_dir})",
                f"Claude Code ({self.claude_dir} + {self.claude_projects_dir})",
                f"Cursor ({self.cursor_dir})",
            ],
            last_scanned_at=self.last_scanned_at,
            files_tracked_count=len(self._file_offsets),
            events_intercepted_count=self.events_intercepted_count,
            blocked_actions_count=self.blocked_actions_count,
        )

    def _ingest_changed(
        self,
        file_str: str,
        ingest_fn: Callable[[Path], Session | None],
    ) -> int:
        """Ingest when file is new or grown. Returns 1 if a session was produced."""
        p = Path(file_str)
        try:
            stat = p.stat()
            prev = self._file_offsets.get(file_str)
            if prev is not None and not (stat.st_mtime > prev[0] or stat.st_size > prev[1]):
                return 0
            self._file_offsets[file_str] = (stat.st_mtime, stat.st_size)
            session = ingest_fn(p)
            if not session:
                return 0
            self.events_intercepted_count += len(session.trajectory.tool_calls)
            self.blocked_actions_count += sum(
                1 for r in session.trajectory.reviews if r.decision in ("block", "deny")
            )
            return 1
        except Exception as err:
            logger.debug("Error inspecting agent log %s: %s", p, err)
            return 0

    def scan_once(self) -> int:
        """Single polling cycle checking Antigravity, Claude Code, and Cursor logs."""
        new_events = 0

        if self.antigravity_dir.exists():
            pattern = str(
                self.antigravity_dir / "*" / ".system_generated" / "logs" / "transcript.jsonl"
            )
            for file_str in glob.glob(pattern):
                new_events += self._ingest_changed(
                    file_str, UniversalAgentLogLoader.ingest_antigravity_transcript
                )

        claude_files: list[str] = []
        if self.claude_dir.exists():
            claude_files.extend(glob.glob(str(self.claude_dir / "*.jsonl")))
        if self.claude_projects_dir.exists():
            claude_files.extend(glob.glob(str(self.claude_projects_dir / "*" / "*.jsonl")))
        for file_str in claude_files:
            new_events += self._ingest_changed(
                file_str, UniversalAgentLogLoader.ingest_claude_code_jsonl
            )

        if self.cursor_dir.exists():
            for file_str in glob.glob(
                str(self.cursor_dir / "*" / "agent-transcripts" / "*" / "*.jsonl")
            ):
                if "/subagents/" in file_str:
                    continue
                new_events += self._ingest_changed(
                    file_str, UniversalAgentLogLoader.ingest_cursor_agent_transcript_jsonl
                )

        self.last_scanned_at = datetime.now(UTC).isoformat()
        return new_events

    async def _run_loop(self) -> None:
        """Background async loop."""
        logger.info("Agent Watcher Daemon started (Antigravity, Claude Code & Cursor)")
        await asyncio.sleep(0)
        while self.is_running:
            try:
                await asyncio.to_thread(self.scan_once)
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
