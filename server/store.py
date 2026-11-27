"""Unified Session & Run Store for OpenEval Studio.

Re-exports canonical Watcher Session as RunRecord and delegates run tracking
to the high-performance DuckDB WatcherStore.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from schemas.watcher_models import Session
from server.db import SessionLocal
from server.watcher_store import WatcherStore, get_watcher_store

logger = logging.getLogger("openeval.server.store")

WATCHER_SESSION_AGENT_TYPES = ("antigravity", "claude_code", "cursor", "codex", "opencode")
DEMO_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "demo"

_cached_valid_demo_eval_ids: set[str] | None = None


def is_corrupt_completed_run(run: Session) -> bool:
    """Return True only for zero-duration completed runs with no recorded work.

    Newly launched ``running``/``pending`` runs, failed launches (which carry a
    non-negative duration), and runs with steps or turns must stay visible.
    """
    return (
        run.status == "completed"
        and (run.total_duration_sec or 0) <= 0
        and (run.total_steps or 0) <= 0
        and not run.steps
        and not run.turns
    )


def get_valid_demo_eval_ids() -> set[str]:
    """Return canonical set of active demo evaluation run IDs from fixtures."""
    global _cached_valid_demo_eval_ids
    if _cached_valid_demo_eval_ids is not None:
        return _cached_valid_demo_eval_ids
    evals_dir = DEMO_FIXTURES_DIR / "evals"
    ids: set[str] = set()
    legacy_filenames = {
        "regex-log-gemini-3-5-flash-lite.json",
        "eval-regex-log-gemini-3-5-flash-lite.json",
    }
    legacy_ids = {
        "eval-regex-log-gemini-3-5-flash-lite",
        "regex-log-gemini-3-5-flash-lite",
    }
    if evals_dir.exists():
        for f in evals_dir.glob("*.json"):
            if f.name in legacy_filenames:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sid = data.get("session_id") or data.get("run_id")
                if sid and str(sid) not in legacy_ids:
                    ids.add(str(sid))
            except Exception:
                pass
    if ids:
        _cached_valid_demo_eval_ids = ids
    return ids


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

    def save_run(self, session: Session, *, is_seeded: bool = False) -> Session:
        """Persist an evaluation run/session in memory, DuckDB, and disk cache."""
        self._deleted_ids.discard(session.session_id)
        if not session.agent_type or session.agent_type in ("antigravity", "claude_code", "cursor"):
            session.agent_type = "inspect_eval"
        return self._watcher.record_session(session, is_seeded=is_seeded)

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
            action = (
                last_step.get("action", {})
                if isinstance(last_step, dict)
                else getattr(last_step, "action", None)
            )
            tool = action.get("tool") if isinstance(action, dict) else getattr(action, "tool", None)
            obs = str(
                last_step.get("observation", "")
                if isinstance(last_step, dict)
                else getattr(last_step, "observation", "")
            )
            if (
                tool == "finish"
                or "Task marked as finished by agent." in obs
                or "finished by agent" in obs.lower()
            ):
                s.status = "completed"
                reconciled = True
                if s.total_steps == 0 or s.total_steps is None:
                    s.total_steps = len(s.steps)
                if s.total_duration_sec is None or s.total_duration_sec <= 0:
                    try:
                        t0 = float(
                            s.steps[0].get("timestamp", 0)
                            if isinstance(s.steps[0], dict)
                            else getattr(s.steps[0], "timestamp", 0)
                        )
                        t1 = float(
                            s.steps[-1].get("timestamp", 0)
                            if isinstance(s.steps[-1], dict)
                            else getattr(s.steps[-1], "timestamp", 0)
                        )
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
            if session and session.agent_type not in WATCHER_SESSION_AGENT_TYPES:
                return self._reconcile_and_persist(session)

        # 2. In-memory check across scoped, raw, or any tenant-scoped key
        for key, s in self._runs.items():
            if not self._watcher._cache_visible(key, s):
                continue
            if s.agent_type in WATCHER_SESSION_AGENT_TYPES:
                continue
            for cid in candidates:
                if (key == cid or key.endswith(f":{cid}")) or (
                    s.run_id in candidates or s.session_id in candidates
                ):
                    return self._reconcile_and_persist(s)

        # 3. Direct PostgreSQL lookup for the active tenant
        from server.db import get_current_user_id

        with SessionLocal() as db:
            user_id = get_current_user_id(db)
            for cid in candidates:
                row = db.execute(
                    text("SELECT payload FROM sessions WHERE id = :id AND user_id = :uid"),
                    {"id": cid, "uid": user_id},
                ).fetchone()
                if row and row[0]:
                    try:
                        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                        sess = Session.model_validate(payload)
                        if sess.agent_type not in WATCHER_SESSION_AGENT_TYPES:
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
                    if sess.agent_type not in WATCHER_SESSION_AGENT_TYPES:
                        return self._reconcile_and_persist(sess)
                except Exception as e:
                    logger.warning("Error reading session from disk for %s: %s", cid, e)

        # 5. Linear scan over watcher list_sessions
        for s in self._watcher.list_sessions():
            if (s.run_id in candidates or s.session_id in candidates) and (
                s.agent_type not in WATCHER_SESSION_AGENT_TYPES
            ):
                return self._reconcile_and_persist(s)
        return None

    def is_deleted(self, run_id: str) -> bool:
        """Check if a run ID (by session_id or run_id field) has been deleted."""
        return any(cid in self._deleted_ids for cid in self._candidates(run_id))

    def list_runs(self) -> list[Session]:
        """Return all historical evaluation runs sorted by created_at descending."""
        all_sessions: list[Session] = []
        seen: set[str] = set()

        # Fetch eval runs for the active user in DB
        from server.db import current_user_slug, get_current_user_id

        is_demo_user = current_user_slug.get() == "demo"
        # Demo runs launched live (in-memory / DB) are not part of the static fixture
        # allowlist, so track their IDs to keep them visible alongside seeded fixtures.
        live_demo_run_ids: set[str] = set()

        db_query_failed = False
        db_row_count = 0
        try:
            with SessionLocal() as db:
                user_id = get_current_user_id(db)
                eval_rows = db.execute(
                    text(
                        "SELECT payload FROM sessions WHERE user_id = :uid "
                        "AND (agent_type = 'inspect_eval' OR id LIKE 'eval-%') "
                        "ORDER BY created_at DESC LIMIT 100"
                    ),
                    {"uid": user_id},
                ).fetchall()
                db_row_count = len(eval_rows)
                for r in eval_rows:
                    if r[0]:
                        try:
                            payload = r[0] if isinstance(r[0], dict) else json.loads(r[0])
                            sess = Session.model_validate(payload)
                            if is_demo_user:
                                live_demo_run_ids.add(sess.session_id)
                                if sess.run_id:
                                    live_demo_run_ids.add(sess.run_id)
                            if sess.session_id not in seen:
                                all_sessions.append(self._reconcile_and_persist(sess))
                                seen.add(sess.session_id)
                        except Exception:
                            pass
        except Exception as e:
            db_query_failed = True
            logger.debug("Database list_runs query failed (offline mode): %s", e)

        # Only fall back to the watcher store when the DB is unavailable or empty:
        # ``list_sessions()`` loads 100 live-gate sessions that are filtered out below.
        if db_query_failed or db_row_count == 0:
            for s in self._watcher.list_sessions():
                if s.agent_type in WATCHER_SESSION_AGENT_TYPES or s.session_id in seen:
                    continue
                all_sessions.append(self._reconcile_and_persist(s))
                seen.add(s.session_id)

        # Include in-memory sessions visible to the active tenant
        for key, s in self._runs.items():
            if not self._watcher._cache_visible(key, s):
                continue
            if is_demo_user:
                live_demo_run_ids.add(s.session_id)
                if s.run_id:
                    live_demo_run_ids.add(s.run_id)
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
                        live_demo_run_ids.add(sess.session_id)
                        if sess.run_id:
                            live_demo_run_ids.add(sess.run_id)
                        all_sessions.append(self._reconcile_and_persist(sess))
                        seen.add(sid)
                    except Exception:
                        pass

        zombie_patterns = ("0d57b4", "regex-log-gemini-flash-lite", "20261019")
        zombie_models = {
            "big-pickle",
            "gemini-flash-lite-latest",
            "gemini-3.7-flash",
            "gemini-3.5-flash",
        }
        zombie_ids = {
            "d8fb2be2",
            "eval-regex-log-gemini-3-5-flash-lite",
            "regex-log-gemini-3-5-flash-lite",
        }
        valid_demo_eval_ids = get_valid_demo_eval_ids() if is_demo_user else None

        filtered = [
            s
            for s in all_sessions
            if s.session_id not in self._deleted_ids
            and (s.run_id is None or s.run_id not in self._deleted_ids)
            and s.agent_type not in WATCHER_SESSION_AGENT_TYPES
            and not is_corrupt_completed_run(s)
            and not any(
                pat in (s.session_id or "") or pat in (s.run_id or "") for pat in zombie_patterns
            )
            and s.session_id not in zombie_ids
            and (s.run_id is None or s.run_id not in zombie_ids)
            and (s.model or "") not in zombie_models
            and not (s.model or "").endswith("-free")
            and (
                not is_demo_user
                or not valid_demo_eval_ids
                or s.session_id in valid_demo_eval_ids
                or (s.run_id is not None and s.run_id in valid_demo_eval_ids)
                or s.session_id in live_demo_run_ids
                or (s.run_id is not None and s.run_id in live_demo_run_ids)
            )
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
