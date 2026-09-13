"""Unified Session & Run Store for OpenEval Studio.

Re-exports canonical Watcher Session as RunRecord and delegates run tracking
to the high-performance DuckDB WatcherStore.
"""

import asyncio
from typing import Any

from schemas.watcher_models import Session
from server.watcher_store import WatcherStore, get_watcher_store

RunRecord = Session


class RunStore:
    """Delegating store that bridges legacy RunStore calls into WatcherStore."""

    def __init__(self, watcher_store: WatcherStore | None = None) -> None:
        self._watcher = watcher_store or get_watcher_store()
        self._runs: dict[str, Session] = self._watcher._sessions
        self._deleted_ids: set[str] = set()

    def create_run(self, task_id: str, model: str, provider: str = "google") -> Session:
        """Initialize a new pending evaluation session."""
        session = Session(
            project_name=task_id,
            task_id=task_id,
            model=model,
            provider=provider,
            agent_type="inspect_eval",
            status="pending",
        )
        self._deleted_ids.discard(session.session_id)
        return self._watcher.create_session(session)

    def save_run(self, session: Session) -> Session:
        """Persist an evaluation run/session in memory, DuckDB, and disk cache."""
        self._deleted_ids.discard(session.session_id)
        if not session.agent_type or session.agent_type in ("antigravity", "claude_code", "cursor"):
            session.agent_type = "inspect_eval"
        return self._watcher.record_session(session)

    def get_run(self, run_id: str) -> Session | None:
        """Look up a run/session by its ID (session_id or run_id field)."""
        if run_id in self._deleted_ids:
            return None
        # First try by session_id (fastest path)
        session = self._watcher.get_session(run_id)
        if session and session.agent_type not in ("antigravity", "claude_code", "cursor"):
            return session
        # Fall back: linear scan for run_id field (fixture records may differ from session_id)
        for s in self._watcher.list_sessions():
            if s.run_id == run_id and s.agent_type not in ("antigravity", "claude_code", "cursor"):
                return s
        return None

    def is_deleted(self, run_id: str) -> bool:
        """Check if a run ID (by session_id or run_id field) has been deleted."""
        return run_id in self._deleted_ids

    def list_runs(self) -> list[Session]:
        """Return all historical evaluation runs sorted by created_at descending."""
        sessions = self._watcher.list_sessions()
        return [
            s
            for s in sessions
            if s.session_id not in self._deleted_ids
            and (s.run_id is None or s.run_id not in self._deleted_ids)
            and s.agent_type not in ("antigravity", "claude_code", "cursor")
        ]

    def update_run(self, run_id: str, **kwargs: Any) -> Session | None:
        """Update fields on an existing session record."""
        if run_id in self._deleted_ids:
            return None
        return self._watcher.update_session(run_id, kwargs)

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Register a subscriber queue for real-time SSE streaming."""
        return self._watcher.subscribe(run_id)

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue."""
        self._watcher.unsubscribe(run_id, queue)

    def publish_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast an event payload to all active SSE subscribers."""
        self._watcher.broadcast_sync(run_id, event_type, data)

    def delete_run(self, run_id: str) -> bool:
        """Delete a run from the store and record tombstone."""
        self._deleted_ids.add(run_id)
        session = self._watcher.get_session(run_id)
        if session:
            session.status = "cancelled"
            return True
        return False

    def clear_runs(self) -> None:
        """Clear all runs from the store and record tombstones."""
        for s in self._watcher.list_sessions():
            self._deleted_ids.add(s.session_id)


# Global singleton instance for the server process
global_run_store = RunStore()
