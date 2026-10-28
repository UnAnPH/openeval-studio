"""Unified Session & Run Store for OpenEval Studio.

Re-exports canonical Watcher Session as RunRecord and delegates run tracking
to the high-performance DuckDB WatcherStore.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from schemas.watcher_models import Session
from server.db import SessionLocal
from server.watcher_store import WatcherStore, get_watcher_store

logger = logging.getLogger("openeval.server.store")


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
        chaos_mode: bool = False,
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
            chaos_mode=chaos_mode,
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

    def _reconcile_and_persist(self, s: Session) -> Session:
        """Automatically reconcile runs whose steps indicate completion."""
        reconciled = False
        if (s.total_steps == 0 or s.total_steps is None) and s.steps:
            s.total_steps = len(s.steps)
            reconciled = True
        if s.status in ("running", "pending") and s.steps:
            last_step = s.steps[-1]
            action = last_step.get("action", {}) if isinstance(last_step, dict) else getattr(last_step, "action", None)
            tool = action.get("tool") if isinstance(action, dict) else getattr(action, "tool", None)
            obs = str(last_step.get("observation", "") if isinstance(last_step, dict) else getattr(last_step, "observation", ""))
            if tool == "finish" or "Task marked as finished by agent." in obs or "finished by agent" in obs.lower():
                s.status = "completed"
                reconciled = True
                if s.total_steps == 0 or s.total_steps is None:
                    s.total_steps = len(s.steps)
                if s.total_duration_sec is None or s.total_duration_sec <= 0:
                    try:
                        t0 = float(s.steps[0].get("timestamp", 0) if isinstance(s.steps[0], dict) else getattr(s.steps[0], "timestamp", 0))
                        t1 = float(s.steps[-1].get("timestamp", 0) if isinstance(s.steps[-1], dict) else getattr(s.steps[-1], "timestamp", 0))
                        if t1 > t0 > 0:
                            s.total_duration_sec = round(t1 - t0, 2)
                        else:
                            s.total_duration_sec = 30.0
                    except Exception:
                        s.total_duration_sec = 30.0

        if reconciled:
            # Persist across in-memory cache, DB, and disk
            try:
                self._watcher._write_session_to_db(s)
            except Exception as e:
                logger.debug("Reconciled session DB write: %s", e)
            try:
                disk_path = self._watcher.storage_dir / f"{s.session_id}.json"
                disk_path.write_text(json.dumps(s.model_dump(), indent=2), encoding="utf-8")
            except Exception as e:
                logger.debug("Reconciled session disk write: %s", e)
        return s

    def get_run(self, run_id: str) -> Session | None:
        """Look up a run/session by its ID (session_id or run_id field)."""
        candidates = self._candidates(run_id)
        if any(cid in self._deleted_ids for cid in candidates):
            return None

        # 1. Watcher store get_session check (canonical tenant-scoped or eval cross-tenant)
        for cid in candidates:
            session = self._watcher.get_session(cid)
            if session and session.agent_type not in ("antigravity", "claude_code", "cursor"):
                return self._reconcile_and_persist(session)

        # 2. In-memory check across scoped, raw, or any tenant-scoped key
        for key, s in self._runs.items():
            for cid in candidates:
                if (key == cid or key.endswith(f":{cid}")) or (s.run_id in candidates or s.session_id in candidates):
                    return self._reconcile_and_persist(s)

        # 3. Direct PostgreSQL lookup across all tenants
        with SessionLocal() as db:
            for cid in candidates:
                row = db.execute(
                    text("SELECT payload FROM sessions WHERE id = :id"),
                    {"id": cid},
                ).fetchone()
                if row and row[0]:
                    try:
                        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                        sess = Session.model_validate(payload)
                        if sess.agent_type not in ("antigravity", "claude_code", "cursor"):
                            return self._reconcile_and_persist(sess)
                    except Exception as e:
                        logger.warning("Error reading session from DB for %s: %s", cid, e)

        # 4. Direct disk lookup in storage_dir
        for cid in candidates:
            disk_path = self._watcher.storage_dir / f"{cid}.json"
            if disk_path.exists():
                try:
                    payload = json.loads(disk_path.read_text(encoding="utf-8"))
                    sess = Session.model_validate(payload)
                    if sess.agent_type not in ("antigravity", "claude_code", "cursor"):
                        return self._reconcile_and_persist(sess)
                except Exception as e:
                    logger.warning("Error reading session from disk for %s: %s", cid, e)

        # 5. Linear scan over watcher list_sessions
        for s in self._watcher.list_sessions():
            if (s.run_id in candidates or s.session_id in candidates) and s.agent_type not in (
                "antigravity",
                "claude_code",
                "cursor",
            ):
                return self._reconcile_and_persist(s)
        return None

    def is_deleted(self, run_id: str) -> bool:
        """Check if a run ID (by session_id or run_id field) has been deleted."""
        return any(cid in self._deleted_ids for cid in self._candidates(run_id))

    def list_runs(self) -> list[Session]:
        """Return all historical evaluation runs sorted by created_at descending."""
        db_sessions = self._watcher.list_sessions()
        seen = {s.session_id for s in db_sessions}
        all_sessions = [self._reconcile_and_persist(s) for s in db_sessions]

        # Fetch eval runs for the active user in DB
        from server.db import current_user_slug, get_current_user_id
        is_demo_user = current_user_slug.get() == "demo"

        with SessionLocal() as db:
            if is_demo_user:
                eval_rows = db.execute(
                    text("SELECT payload FROM sessions WHERE (agent_type = 'inspect_eval' OR id LIKE 'eval-%') ORDER BY created_at DESC LIMIT 100")
                ).fetchall()
            else:
                user_id = get_current_user_id(db)
                eval_rows = db.execute(
                    text("SELECT payload FROM sessions WHERE user_id = :uid AND (agent_type = 'inspect_eval' OR id LIKE 'eval-%') ORDER BY created_at DESC LIMIT 100"),
                    {"uid": user_id}
                ).fetchall()
            for r in eval_rows:
                if r[0]:
                    try:
                        payload = r[0] if isinstance(r[0], dict) else json.loads(r[0])
                        sess = Session.model_validate(payload)
                        if sess.session_id not in seen:
                            all_sessions.append(self._reconcile_and_persist(sess))
                            seen.add(sess.session_id)
                    except Exception:
                        pass

        # Include in-memory sessions (demo runs only visible in demo mode)
        for s in self._runs.values():
            if not is_demo_user and getattr(s, "org_id", "") == "demo":
                continue
            if s.session_id not in seen:
                all_sessions.append(self._reconcile_and_persist(s))
                seen.add(s.session_id)

        # Include disk .runs/watcher/eval-*.json (demo surface only)
        if is_demo_user and self._watcher.storage_dir.exists():
            for p in self._watcher.storage_dir.glob("eval-*.json"):
                sid = p.stem
                if sid not in seen and sid not in self._deleted_ids:
                    try:
                        payload = json.loads(p.read_text(encoding="utf-8"))
                        sess = Session.model_validate(payload)
                        all_sessions.append(self._reconcile_and_persist(sess))
                        seen.add(sid)
                    except Exception:
                        pass

        filtered = [
            s
            for s in all_sessions
            if s.session_id not in self._deleted_ids
            and (s.run_id is None or s.run_id not in self._deleted_ids)
            and s.agent_type not in ("antigravity", "claude_code", "cursor")
            and not ((s.total_duration_sec or 0) <= 0 and s.status not in ("running", "pending"))
        ]
        filtered.sort(key=lambda s: str(s.created_at or ""), reverse=True)
        return filtered

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
        for s in self.list_runs():
            self._deleted_ids.add(s.session_id)
            if s.run_id:
                self._deleted_ids.add(s.run_id)
        for s in self._watcher.list_sessions():
            self._deleted_ids.add(s.session_id)
            if s.run_id:
                self._deleted_ids.add(s.run_id)
        self._runs.clear()


# Global singleton instance for the server process
global_run_store = RunStore()
