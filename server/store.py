"""Unified Session & Run Store for OpenEval Studio.

Re-exports canonical Watcher Session as RunRecord and delegates run tracking
to the high-performance DuckDB WatcherStore.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from schemas.watcher_models import Session
from server.watcher_store import WatcherStore, get_watcher_store


class RunStore:
    """Delegating store that bridges legacy RunStore calls into WatcherStore."""

    def __init__(self, watcher_store: WatcherStore | None = None) -> None:
        self._watcher = watcher_store or get_watcher_store()
        self._runs: dict[str, Session] = self._watcher._sessions
        self._deleted_ids: set[str] = set()

    def create_run(
        self,
        task_id: str,
        model: str,
        provider: str = "google",
        run_id: str | None = None,
    ) -> Session:
        """Initialize a new pending evaluation session with a canonical eval-{task_id}-{...} ID."""
        safe_task_id = task_id.replace("/", "--").replace(" ", "-")
        if not run_id:
            now_str = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            short_id = uuid4().hex[:6]
            run_id = f"eval-{safe_task_id}-{now_str}-{short_id}"
        else:
            run_id = run_id.replace("/", "--").replace(" ", "-")

        session = Session(
            session_id=run_id,
            run_id=run_id,
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

    def _candidates(self, run_id: str) -> list[str]:
        cands = [run_id]
        if "/" in run_id:
            cands.append(run_id.replace("/", "--"))
        if "--" in run_id:
            cands.append(run_id.replace("--", "/"))
        return cands

    def get_run(self, run_id: str) -> Session | None:
        """Look up a run/session by its ID (session_id or run_id field)."""
        candidates = self._candidates(run_id)
        for cid in candidates:
            if cid in self._deleted_ids:
                return None
            scoped = self._watcher.memory_key(cid)
            if scoped in self._runs and self._watcher._cache_visible(scoped, self._runs[scoped]):
                return self._runs[scoped]
            if cid in self._runs and self._watcher._cache_visible(cid, self._runs[cid]):
                return self._runs[cid]
            session = self._watcher.get_session(cid)
            if session and session.agent_type not in ("antigravity", "claude_code", "cursor"):
                return session

        # Fall back: linear scan for run_id or session_id field
        for s in self._watcher.list_sessions():
            if (s.run_id in candidates or s.session_id in candidates) and s.agent_type not in (
                "antigravity",
                "claude_code",
                "cursor",
            ):
                return s
        return None

    def is_deleted(self, run_id: str) -> bool:
        """Check if a run ID (by session_id or run_id field) has been deleted."""
        return any(cid in self._deleted_ids for cid in self._candidates(run_id))

    def list_runs(self) -> list[Session]:
        """Return all historical evaluation runs sorted by created_at descending."""
        db_sessions = self._watcher.list_sessions()
        seen = {s.session_id for s in db_sessions}
        all_sessions = list(db_sessions)
        for key, s in self._runs.items():
            if not self._watcher._cache_visible(key, s):
                continue
            if s.session_id not in seen:
                all_sessions.append(s)
                seen.add(s.session_id)
        return [
            s
            for s in all_sessions
            if s.session_id not in self._deleted_ids
            and (s.run_id is None or s.run_id not in self._deleted_ids)
            and s.agent_type not in ("antigravity", "claude_code", "cursor")
        ]

    def update_run(self, run_id: str, **kwargs: Any) -> Session | None:
        """Update fields on an existing session record."""
        for cid in self._candidates(run_id):
            if cid in self._deleted_ids:
                return None
            updated = self._watcher.update_session(cid, kwargs)
            if updated:
                return updated
        return None

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Register a subscriber queue for real-time SSE streaming."""
        return self._watcher.subscribe(run_id)

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue."""
        self._watcher.unsubscribe(run_id, queue)

    def publish_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast an event payload to all active SSE subscribers."""
        for cid in self._candidates(run_id):
            self._watcher.broadcast_sync(cid, event_type, data)

    def delete_run(self, run_id: str) -> bool:
        """Delete a run from the store and record tombstone."""
        found = False
        for cid in self._candidates(run_id):
            self._deleted_ids.add(cid)
            self._runs.pop(cid, None)
            session = self._watcher.get_session(cid)
            if session:
                session.status = "cancelled"
                found = True
        return found

    def clear_runs(self) -> None:
        """Clear all runs from the store and record tombstones."""
        for s in self._watcher.list_sessions():
            self._deleted_ids.add(s.session_id)


# Global singleton instance for the server process
global_run_store = RunStore()
