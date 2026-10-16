"""OpenEval Watcher PostgreSQL Storage Engine & Session Store.

Provides thread-safe persistence in PostgreSQL 16, multi-tenant isolation
by user_id, real SQL analytical percentiles, and real-time SSE event broadcasting.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from engine.approval_policy import WatcherConfig
from engine.redaction import redact_payload, redact_text
from schemas.watcher_models import (
    DEFAULT_COMMAND_RULES,
    DEFAULT_TOOL_THRESHOLDS,
    AgentType,
    Message,
    Policy,
    ReviewDecision,
    ReviewRecord,
    ReviewStage,
    Session,
    SessionEventRecord,
    ToolCall,
    ToolResult,
    ToolThreshold,
    Trajectory,
)
from server.db import SessionLocal, current_user_id, current_user_slug, get_current_user_id
from server.request_context import request_id_ctx

logger = logging.getLogger("openeval.server.watcher_store")


def _to_jsonable(value: Any) -> Any:
    """Recursively convert Pydantic models / nested containers for JSON/SSE."""
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


class AwaitableReviewRecord(ReviewRecord):
    """Subclass of ReviewRecord that is also directly awaitable for backwards compatibility."""

    def __await__(self) -> Any:
        async def _ret():
            return self

        return _ret().__await__()


class CompatResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


class CompatDBConn:
    def execute(self, sql: str, params: list[Any] | None = None) -> CompatResult:
        p_dict: dict[str, Any] = {}
        if params:
            parts = sql.split("?")
            built: list[str] = []
            for i, part in enumerate(parts[:-1]):
                built.append(part)
                built.append(f":p{i}")
                p_dict[f"p{i}"] = params[i]
            built.append(parts[-1])
            sql = "".join(built)

        if "FROM sessions" in sql:
            sql = sql.replace("session_id,", "id AS session_id,")
            sql = sql.replace("WHERE session_id =", "WHERE id =")

        with SessionLocal() as db:
            result = db.execute(text(sql), p_dict)
            db.commit()
            try:
                rows = result.fetchall()
            except Exception:
                rows = []
            return CompatResult(list(rows))


class WatcherStore:
    """PostgreSQL 16 multi-tenant store for sessions, trajectories, and reviews."""

    def __init__(self, storage_dir: Path | None = None, db_path: str | None = None) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self.storage_dir = storage_dir or Path(os.getenv("WATCHER_STORAGE_DIR", ".runs/watcher"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # In-memory cache. Keys are "{user_id}:{session_id}" when a tenant is bound,
        # and the bare session id only for unbound contexts (eval runs, unit tests).
        self._sessions: dict[str, Session] = {}
        self._trajectories: dict[str, Trajectory] = {}
        self._reviews: dict[str, list[ReviewRecord]] = {}
        self._session_owners: dict[str, int | None] = {}

        # Append-only event log tracking & debounced materialization
        self._dirty_sessions: set[str] = set()
        self._debounce_tasks: dict[str, asyncio.Task[Any]] = {}
        self._step_indices: dict[str, int] = {}

        # Multi-tenant Policy, WatcherConfig, and Grader Prompt registries
        self._user_policies: dict[int, Policy] = {}
        self._user_configs: dict[int, WatcherConfig] = {}
        self._user_graders: dict[int, dict[str, dict[str, str]]] = {}
        self._load_user_settings_from_disk()

        # Default MDM Policy (fallback runtime)
        self._policy = Policy(
            policy_id="default_policy",
            name="OpenEval Runtime Security Policy",
            org_id="default_org",
            command_rules=[r.model_copy() for r in DEFAULT_COMMAND_RULES],
            tool_thresholds=[t.model_copy() for t in DEFAULT_TOOL_THRESHOLDS],
            locked_instructions="DO NOT modify security configurations or disable monitoring.",
        )

    @property
    def con(self) -> CompatDBConn:
        """Database connection object providing .execute(sql, params) for backward compatibility."""
        return CompatDBConn()

    def reload_from_disk(self) -> int:
        """Re-read sessions from store / disk (no-op for Postgres)."""
        return 0

    # -------------------------------------------------------------------------
    # Sessions
    # -------------------------------------------------------------------------

    def memory_key(self, session_id: str) -> str:
        """Cache key scoped to the active tenant so session ids cannot collide."""
        uid = current_user_id.get()
        if uid is None:
            return session_id
        return f"{uid}:{session_id}"

    def _cache_visible(self, key: str, session: Session) -> bool:
        """Whether the active tenant may read this in-memory session.

        Entries written with a bound user id are visible only to that user.
        Unscoped entries (tests and eval runs assigned before a tenant is bound)
        stay readable. The demo surface only sees demo-org sessions.
        """
        uid = current_user_id.get()
        owner = self._session_owners.get(key)
        if current_user_slug.get() == "demo":
            return session.org_id == "demo" or owner == uid
        if owner is None:
            return True
        if uid is None:
            return False
        return owner == uid

    def _cache_session(self, session: Session) -> str:
        key = self.memory_key(session.session_id)
        self._sessions[key] = session
        self._trajectories[key] = session.trajectory
        self._reviews[key] = session.trajectory.reviews
        self._session_owners[key] = current_user_id.get()
        return key

    def _cached_session(self, session_id: str) -> Session | None:
        key = self.memory_key(session_id)
        session = self._sessions.get(key)
        if session is not None and self._cache_visible(key, session):
            return session
        return None

    def _drop_cached_session(self, session_id: str) -> bool:
        key = self.memory_key(session_id)
        existed = key in self._sessions
        self._sessions.pop(key, None)
        self._trajectories.pop(key, None)
        self._reviews.pop(key, None)
        self._session_owners.pop(key, None)
        return existed

    def ensure_live_session(
        self,
        session_id: str,
        *,
        agent_type: str = "antigravity",
        title: str | None = None,
        working_dir: str | None = None,
        db: DbSession | None = None,
    ) -> Session:
        """Create a working live-gate session if missing; return existing otherwise."""
        existing = self.get_session(session_id)
        if existing:
            return existing

        agent: AgentType = "antigravity"
        lowered = (agent_type or "").lower()
        if "claude" in lowered:
            agent = "claude_code"
        elif "cursor" in lowered:
            agent = "cursor"
        elif "inspect" in lowered:
            agent = "inspect_eval"
        elif "red_team" in lowered or "redteam" in lowered:
            agent = "red_team"

        session = Session(
            session_id=session_id,
            title=title or f"Live gate ({session_id[:8]})",
            agent_type=agent,
            status="working",
            working_dir=working_dir or "/workspace",
            current_activity="awaiting tool call",
            trajectory=Trajectory(session_id=session_id),
        )
        return self.create_session(session, db=db)

    def _write_session_to_db(self, session: Session, db: DbSession | None = None) -> None:
        """Write session and trajectory reviews to PostgreSQL and disk cache."""
        with self._lock:
            self._cache_session(session)

        payload = redact_payload(session.model_dump())
        payload_json = json.dumps(_to_jsonable(payload))

        def _execute_write(active_db: DbSession) -> None:
            user_id = get_current_user_id(active_db)
            active_db.execute(
                text(
                    """
                    INSERT INTO sessions (
                        id, user_id, agent_type, project_name, model, provider,
                        status, title, working_dir, current_activity, failure_reason,
                        passed, reward, total_tokens, total_duration_sec,
                        estimated_cost_usd, payload, created_at, updated_at
                    ) VALUES (
                        :id, :user_id, :agent_type, :project_name, :model, :provider,
                        :status, :title, :working_dir, :current_activity, :failure_reason,
                        :passed, :reward, :total_tokens, :total_duration_sec,
                        :estimated_cost_usd, CAST(:payload AS jsonb), NOW(), NOW()
                    )
                    ON CONFLICT (user_id, id) DO UPDATE SET
                        status = EXCLUDED.status,
                        project_name = EXCLUDED.project_name,
                        model = EXCLUDED.model,
                        provider = EXCLUDED.provider,
                        title = EXCLUDED.title,
                        working_dir = EXCLUDED.working_dir,
                        current_activity = EXCLUDED.current_activity,
                        failure_reason = EXCLUDED.failure_reason,
                        passed = EXCLUDED.passed,
                        reward = EXCLUDED.reward,
                        total_tokens = EXCLUDED.total_tokens,
                        total_duration_sec = EXCLUDED.total_duration_sec,
                        estimated_cost_usd = EXCLUDED.estimated_cost_usd,
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                    WHERE sessions.user_id = EXCLUDED.user_id;
                    """
                ),
                {
                    "id": session.session_id,
                    "user_id": user_id,
                    "agent_type": session.agent_type,
                    "project_name": session.project_name or "",
                    "model": session.model or "",
                    "provider": session.provider or "",
                    "status": session.status,
                    "title": redact_text(session.title or ""),
                    "working_dir": session.working_dir or "",
                    "current_activity": redact_text(session.current_activity or ""),
                    "failure_reason": redact_text(session.failure_reason or ""),
                    "passed": session.passed,
                    "reward": session.reward,
                    "total_tokens": session.total_tokens,
                    "total_duration_sec": session.total_duration_sec,
                    "estimated_cost_usd": session.estimated_cost_usd,
                    "payload": payload_json,
                },
            )

            # Insert any reviews present in trajectory
            for rev in session.trajectory.reviews:
                rule_name = str(rev.rule_name or getattr(rev, "threat_category", "") or "")
                req_id = getattr(rev, "request_id", None)
                active_db.execute(
                    text(
                        """
                        INSERT INTO reviews (
                            id, user_id, session_id, tool_name, tool_input,
                            decision, score, stage, rule_name, explanation,
                            diff, latency_ms, request_id, created_at
                        ) VALUES (
                            :id, :user_id, :session_id, :tool_name, :tool_input,
                            :decision, :score, :stage, :rule_name, :explanation,
                            :diff, :latency_ms, :request_id, NOW()
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            decision = EXCLUDED.decision,
                            score = EXCLUDED.score,
                            explanation = EXCLUDED.explanation,
                            request_id = COALESCE(EXCLUDED.request_id, reviews.request_id);
                        """
                    ),
                    {
                        "id": rev.id,
                        "user_id": user_id,
                        "session_id": session.session_id,
                        "tool_name": rev.tool_name,
                        "tool_input": redact_text(rev.tool_input or ""),
                        "decision": rev.decision,
                        "score": rev.score,
                        "stage": rev.stage,
                        "rule_name": rule_name,
                        "explanation": redact_text(rev.explanation or ""),
                        "diff": redact_text(rev.diff or ""),
                        "latency_ms": rev.latency_ms,
                        "request_id": req_id,
                    },
                )

        if db is not None:
            _execute_write(db)
        else:
            with SessionLocal() as active_db:
                _execute_write(active_db)
                active_db.commit()

        # Update disk JSON cache for file-based tools / inspection
        try:
            target = self.storage_dir / f"{session.session_id}.json"
            tmp_target = self.storage_dir / f"{session.session_id}.tmp"
            tmp_target.write_text(session.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp_target, target)
        except Exception as e:
            logger.warning("Failed to write session file %s: %s", session.session_id, e)

    def create_session(self, session: Session, db: DbSession | None = None) -> Session:
        """Register a new session in PostgreSQL for the active user."""
        self._write_session_to_db(session, db=db)
        self.broadcast_sync(session.session_id, "session_created", session.model_dump())
        return session

    def record_session(self, session: Session) -> Session:
        """Register or update a session in PostgreSQL for the active user."""
        with self._lock:
            self._write_session_to_db(session)
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a session by ID scoped to the active tenant."""
        key = self.memory_key(session_id)
        with self._lock:
            is_dirty = key in self._dirty_sessions
            cached = self._cached_session(session_id)
            if cached is not None and not is_dirty:
                return cached
            if cached is not None and (
                current_user_slug.get() == "demo" or getattr(cached, "org_id", "") == "demo"
            ):
                return cached

        if is_dirty:
            return self.materialize_session(session_id)

        with SessionLocal() as db:
            user_id = get_current_user_id(db)
            row = db.execute(
                text("SELECT payload FROM sessions WHERE id = :id AND user_id = :uid"),
                {"id": session_id, "uid": user_id},
            ).fetchone()

            if not row or not row[0]:
                return None

            payload = row[0]
            if isinstance(payload, str):
                payload = json.loads(payload)
            try:
                sess = Session.model_validate(payload)
                with self._lock:
                    self._cache_session(sess)
                return sess
            except Exception as e:
                logger.warning("Error validating session model %s: %s", session_id, e)
                return None

    def update_session(self, session_id: str, updates: dict[str, Any]) -> Session | None:
        """Update fields on an existing session."""
        session = self.get_session(session_id)
        if not session:
            with self._lock:
                session = self._cached_session(session_id)
        if not session:
            return None

        for k, v in updates.items():
            if hasattr(session, k):
                setattr(session, k, v)

        session.updated_at = datetime.now(UTC).isoformat()
        self._write_session_to_db(session)

        terminal_statuses = {"completed", "failed", "aborted", "lockout", "error", "cancelled"}
        if getattr(session, "status", "") in terminal_statuses:
            self.materialize_session(session_id)

        self.broadcast_sync(session_id, "session_updated", _to_jsonable(updates))
        return session

    def list_sessions(
        self,
        agent_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Session]:
        """List sessions for the current user, optionally filtered."""
        if current_user_slug.get() == "demo":
            from server.demo_seed import ensure_demo_timeline_fresh

            ensure_demo_timeline_fresh(self)

        with SessionLocal() as db:
            user_id = get_current_user_id(db)
            query = "SELECT payload FROM sessions WHERE user_id = :uid"
            params: dict[str, Any] = {"uid": user_id, "limit": limit}

            if agent_type:
                query += " AND agent_type = :agent_type"
                params["agent_type"] = agent_type
            if status:
                query += " AND status = :status"
                params["status"] = status

            query += " ORDER BY created_at DESC LIMIT :limit"

            rows = db.execute(text(query), params).fetchall()
            results: list[Session] = []
            for r in rows:
                if r[0]:
                    payload = r[0] if isinstance(r[0], dict) else json.loads(r[0])
                    with contextlib.suppress(Exception):
                        results.append(Session.model_validate(payload))

            db_ids = {s.session_id for s in results}
            with self._lock:
                for key, s in self._sessions.items():
                    if not self._cache_visible(key, s):
                        continue
                    if s.session_id not in db_ids:
                        if agent_type and s.agent_type != agent_type:
                            continue
                        if status and s.status != status:
                            continue
                        results.append(s)
            results.sort(key=lambda x: str(x.created_at or ""), reverse=True)
            return results[:limit]

    def clear_all_sessions(self) -> None:
        """Purge all sessions and reviews for the active tenant."""
        if current_user_slug.get() != "demo":
            with SessionLocal() as db:
                user_id = get_current_user_id(db)
                db.execute(text("DELETE FROM session_events WHERE user_id = :uid"), {"uid": user_id})
                db.execute(text("DELETE FROM sessions WHERE user_id = :uid"), {"uid": user_id})
                db.commit()
        with self._lock:
            for key, session in list(self._sessions.items()):
                if not self._cache_visible(key, session):
                    continue
                target = self.storage_dir / f"{session.session_id}.json"
                if target.exists():
                    with contextlib.suppress(Exception):
                        target.unlink()
                self._sessions.pop(key, None)
                self._trajectories.pop(key, None)
                self._reviews.pop(key, None)
                self._session_owners.pop(key, None)

    def delete_session(self, session_id: str) -> bool:
        """Delete a single session for the active tenant."""
        with self._lock:
            mem_deleted = self._drop_cached_session(session_id)

        target = self.storage_dir / f"{session_id}.json"
        if target.exists():
            with contextlib.suppress(Exception):
                target.unlink()

        if current_user_slug.get() == "demo":
            return mem_deleted

        with SessionLocal() as db:
            user_id = get_current_user_id(db)
            db.execute(
                text("DELETE FROM session_events WHERE session_id = :id AND user_id = :uid"),
                {"id": session_id, "uid": user_id},
            )
            res = db.execute(
                text("DELETE FROM sessions WHERE id = :id AND user_id = :uid"),
                {"id": session_id, "uid": user_id},
            )
            db.commit()
            deleted = bool(getattr(res, "rowcount", 0) > 0)
        return deleted or mem_deleted

    def purge_empty_sessions(self, min_messages: int = 1) -> list[str]:
        """Remove sessions that have fewer than min_messages."""
        purged: list[str] = []
        for s in self.list_sessions(limit=500):
            msgs = s.trajectory.messages if s.trajectory else []
            if len(msgs) < min_messages and self.delete_session(s.session_id):
                purged.append(s.session_id)
        return purged

    def prune_expired_sessions(self, retention_days: int = 30) -> int:
        """Prune sessions older than retention_days along with their events and disk files."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        pruned_count = 0
        session_ids_to_prune: list[str] = []

        try:
            with SessionLocal() as db:
                user_id = get_current_user_id(db)
                rows = db.execute(
                    text(
                        "SELECT id FROM sessions WHERE user_id = :uid AND updated_at < :cutoff"
                    ),
                    {"uid": user_id, "cutoff": cutoff},
                ).fetchall()
                session_ids_to_prune = [str(r[0]) for r in rows]

                if session_ids_to_prune:
                    db.execute(
                        text("DELETE FROM session_events WHERE user_id = :uid AND session_id = ANY(:ids)"),
                        {"uid": user_id, "ids": session_ids_to_prune},
                    )
                    db.execute(
                        text("DELETE FROM reviews WHERE user_id = :uid AND session_id = ANY(:ids)"),
                        {"uid": user_id, "ids": session_ids_to_prune},
                    )
                    res = db.execute(
                        text("DELETE FROM sessions WHERE user_id = :uid AND id = ANY(:ids)"),
                        {"uid": user_id, "ids": session_ids_to_prune},
                    )
                    db.commit()
                    pruned_count = int(getattr(res, "rowcount", 0))
        except Exception as e:
            logger.debug("DB session pruning error: %s", e)

        with self._lock:
            for _key, session in list(self._sessions.items()):
                created_str = getattr(session, "created_at", None) or getattr(session, "updated_at", None)
                if created_str:
                    try:
                        created_dt = datetime.fromisoformat(str(created_str).replace("Z", "+00:00"))
                        if created_dt < cutoff:
                            session_ids_to_prune.append(session.session_id)
                    except Exception:
                        pass

        for sid in set(session_ids_to_prune):
            self._drop_cached_session(sid)
            target = self.storage_dir / f"{sid}.json"
            if target.exists():
                with contextlib.suppress(Exception):
                    target.unlink()

        # Clean up any orphaned session json files older than retention cutoff
        if self.storage_dir.exists():
            for f in self.storage_dir.glob("*.json"):
                if f.name in ("user_policies.json", "user_configs.json", "user_graders.json"):
                    continue
                try:
                    if f.stat().st_mtime < cutoff.timestamp():
                        f.unlink(missing_ok=True)
                        session_ids_to_prune.append(f.stem)
                except Exception:
                    pass

        return max(pruned_count, len(set(session_ids_to_prune)))

    # -------------------------------------------------------------------------
    # Reviews, Decisions & Append-Only Event Log
    # -------------------------------------------------------------------------

    def _get_next_step_index(self, session_id: str, db: DbSession | None = None) -> int:
        """Get the next sequential step_index for a session event."""
        with self._lock:
            key = self.memory_key(session_id)
            if key in self._step_indices:
                self._step_indices[key] += 1
                return self._step_indices[key]

        sql = (
            "SELECT COALESCE(MAX(step_index), -1) FROM session_events "
            "WHERE session_id = :session_id AND user_id = :uid"
        )
        if db is not None:
            user_id = get_current_user_id(db)
            row = db.execute(text(sql), {"session_id": session_id, "uid": user_id}).fetchone()
        else:
            with SessionLocal() as sdb:
                user_id = get_current_user_id(sdb)
                row = sdb.execute(text(sql), {"session_id": session_id, "uid": user_id}).fetchone()

        max_idx = int(row[0]) if row and row[0] is not None else -1
        next_idx = max_idx + 1
        with self._lock:
            self._step_indices[key] = next_idx
        return next_idx

    def append_event(self, event: SessionEventRecord, db: DbSession | None = None) -> None:
        """Insert an immutable event row into session_events for the active tenant."""
        def _execute_append(active_db: DbSession) -> None:
            user_id = current_user_id.get()
            if user_id is None:
                user_id = get_current_user_id(active_db)

            # Strictly enforce tenant isolation: event.user_id must match active tenant
            event.user_id = user_id

            payload_data = event.payload
            if hasattr(payload_data, "model_dump"):
                payload_data = cast(Any, payload_data).model_dump()
            payload_data = redact_payload(payload_data) if isinstance(payload_data, dict) else payload_data
            payload_json = json.dumps(_to_jsonable(payload_data))

            active_db.execute(
                text(
                    """
                    INSERT INTO session_events (
                        id, user_id, session_id, step_index, event_type, payload, created_at
                    ) VALUES (
                        :id, :user_id, :session_id, :step_index, :event_type,
                        CAST(:payload AS jsonb), NOW()
                    )
                    """
                ),
                {
                    "id": event.id,
                    "user_id": user_id,
                    "session_id": event.session_id,
                    "step_index": event.step_index,
                    "event_type": event.event_type,
                    "payload": payload_json,
                },
            )

        if db is not None:
            _execute_append(db)
        else:
            with SessionLocal() as active_db:
                _execute_append(active_db)
                active_db.commit()

        key = self.memory_key(event.session_id)
        with self._lock:
            self._dirty_sessions.add(key)

    async def arecord_decision(self, review: ReviewRecord) -> ReviewRecord:
        """Asynchronously record a security/policy review decision with Redis distributed mutex and atomic JSONB append."""
        from server.event_bus import get_event_bus

        user_id = current_user_id.get()
        if user_id is None:
            user_id = get_current_user_id()

        async with get_event_bus().session_lock(user_id, review.session_id, timeout=5.0):
            return self._apply_record_decision(review, user_id, publish_async=True)

    def record_decision_sync(self, review: ReviewRecord) -> ReviewRecord:
        """Synchronously record a security/policy review decision with Redis distributed mutex and atomic JSONB append."""
        from server.event_bus import get_event_bus

        user_id = current_user_id.get()
        if user_id is None:
            user_id = get_current_user_id()

        with get_event_bus().session_lock_sync(user_id, review.session_id, timeout=5.0):
            return self._apply_record_decision(review, user_id, publish_async=False)

    def record_decision(self, review: ReviewRecord) -> ReviewRecord:
        """Record a review decision with Redis distributed mutex and atomic JSONB append."""
        return self.record_decision_sync(review)

    def _apply_record_decision(
        self,
        review: ReviewRecord,
        user_id: int,
        publish_async: bool = False,
    ) -> ReviewRecord:
        from server.event_bus import get_event_bus

        redacted_review_dump = redact_payload(review.model_dump())
        tool_input_redacted = redact_text(review.tool_input or "")
        explanation_redacted = redact_text(review.explanation or "")
        diff_redacted = redact_text(review.diff or "")
        rule_name = str(review.rule_name or getattr(review, "threat_category", "") or "")
        req_id = getattr(review, "request_id", None)

        with SessionLocal() as db:
            uid = user_id if user_id is not None else get_current_user_id(db)

            # 1. Insert or update the normalized reviews relational row within the transaction
            db.execute(
                text(
                    """
                    INSERT INTO reviews (
                        id, user_id, session_id, tool_name, tool_input,
                        decision, score, stage, rule_name, explanation,
                        diff, latency_ms, request_id, created_at
                    ) VALUES (
                        :id, :user_id, :session_id, :tool_name, :tool_input,
                        :decision, :score, :stage, :rule_name, :explanation,
                        :diff, :latency_ms, :request_id, NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        decision = EXCLUDED.decision,
                        score = EXCLUDED.score,
                        explanation = EXCLUDED.explanation,
                        request_id = COALESCE(EXCLUDED.request_id, reviews.request_id)
                    WHERE reviews.user_id = EXCLUDED.user_id;
                    """
                ),
                {
                    "id": review.id,
                    "user_id": uid,
                    "session_id": review.session_id,
                    "tool_name": review.tool_name,
                    "tool_input": tool_input_redacted,
                    "decision": review.decision,
                    "score": review.score,
                    "stage": review.stage,
                    "rule_name": rule_name,
                    "explanation": explanation_redacted,
                    "diff": diff_redacted,
                    "latency_ms": review.latency_ms,
                    "request_id": req_id,
                },
            )

            # 2. Atomic PostgreSQL JSONB update on sessions.payload
            new_review_json = json.dumps(_to_jsonable([redacted_review_dump]))
            res = db.execute(
                text(
                    """
                    UPDATE sessions
                    SET payload = jsonb_set(
                        CASE
                            WHEN payload IS NULL THEN '{"trajectory": {}}'::jsonb
                            WHEN payload->'trajectory' IS NULL THEN jsonb_set(payload, '{trajectory}', '{}'::jsonb)
                            ELSE payload
                        END,
                        '{trajectory,reviews}',
                        COALESCE(payload->'trajectory'->'reviews', '[]'::jsonb) || CAST(:new_review_json AS jsonb)
                    ),
                    updated_at = NOW()
                    WHERE user_id = :user_id AND id = :session_id;
                    """
                ),
                {
                    "new_review_json": new_review_json,
                    "user_id": uid,
                    "session_id": review.session_id,
                },
            )
            if getattr(res, "rowcount", 0) == 0:
                self.ensure_live_session(review.session_id, db=db)
                db.execute(
                    text(
                        """
                        UPDATE sessions
                        SET payload = jsonb_set(
                            CASE
                                WHEN payload IS NULL THEN '{"trajectory": {}}'::jsonb
                                WHEN payload->'trajectory' IS NULL THEN jsonb_set(payload, '{trajectory}', '{}'::jsonb)
                                ELSE payload
                            END,
                            '{trajectory,reviews}',
                            COALESCE(payload->'trajectory'->'reviews', '[]'::jsonb) || CAST(:new_review_json AS jsonb)
                        ),
                        updated_at = NOW()
                        WHERE user_id = :user_id AND id = :session_id;
                        """
                    ),
                    {
                        "new_review_json": new_review_json,
                        "user_id": uid,
                        "session_id": review.session_id,
                    },
                )

            # 3. Write immutable event to session_events in the same transaction
            step_idx = self._get_next_step_index(review.session_id, db=db)
            event = SessionEventRecord(
                id=str(uuid4()),
                user_id=uid,
                session_id=review.session_id,
                step_index=step_idx,
                event_type="review_decision",
                payload=redacted_review_dump,
            )
            self.append_event(event, db=db)

            db.commit()

        # 4. Update in-memory session cache directly
        with self._lock:
            key = self.memory_key(review.session_id)
            cached = self._sessions.get(key)
            if cached is not None and not any(r.id == review.id for r in cached.trajectory.reviews):
                cached.trajectory.reviews.append(review)
            if key in self._reviews and not any(r.id == review.id for r in self._reviews[key]):
                self._reviews[key].append(review)

        # 5. Broadcast to local sync subscribers and Redis Streams
        self.broadcast_sync(review.session_id, "review_decision", redacted_review_dump)
        get_event_bus().xadd_sync("review_decision", redacted_review_dump, user_id=uid)

        # 6. Trigger materialization: synchronously if terminal, asynchronously with debounce otherwise
        terminal_statuses = {"completed", "failed", "aborted", "lockout", "error", "cancelled"}
        cached_sess = self._cached_session(review.session_id)
        if cached_sess and getattr(cached_sess, "status", "") in terminal_statuses:
            self.materialize_session(review.session_id)
        else:
            self.schedule_materialize(review.session_id)

        return AwaitableReviewRecord.model_validate(review.model_dump())

    def record_tool_result(self, session_id: str, result: ToolResult) -> None:
        """Record an execution tool result in session_events without rewriting sessions.payload."""
        from server.event_bus import get_event_bus

        user_id = current_user_id.get()
        if user_id is None:
            user_id = get_current_user_id()

        # Update in-memory session cache directly
        with self._lock:
            key = self.memory_key(session_id)
            cached = self._sessions.get(key)
            if cached is not None and not any(
                tr.tool_id == result.tool_id for tr in cached.trajectory.tool_results
            ):
                cached.trajectory.tool_results.append(result)

        # Append immutable event to session_events
        step_idx = self._get_next_step_index(session_id)
        event = SessionEventRecord(
            id=str(uuid4()),
            user_id=user_id,
            session_id=session_id,
            step_index=step_idx,
            event_type="tool_result",
            payload=result.model_dump(),
        )
        self.append_event(event)

        # Publish to subscribers and Redis Pub/Sub bus
        result_dump = result.model_dump()
        self.broadcast_sync(session_id, "tool_result", result_dump)
        get_event_bus().publish_sync("tool_result", result_dump, user_id=user_id)

        # Materialization trigger
        terminal_statuses = {"completed", "failed", "aborted", "lockout", "error", "cancelled"}
        cached_sess = self._cached_session(session_id)
        if cached_sess and getattr(cached_sess, "status", "") in terminal_statuses:
            self.materialize_session(session_id)
        else:
            self.schedule_materialize(session_id)

    def materialize_session(self, session_id: str, db: DbSession | None = None) -> Session:
        """Fold/reduce all append-only session_events into the base Session and upsert sessions.payload."""
        if db is not None:
            return self._execute_materialize(session_id, db)
        with SessionLocal() as sdb:
            return self._execute_materialize(session_id, sdb)

    def _execute_materialize(self, session_id: str, db: DbSession) -> Session:
        user_id = current_user_id.get()
        if user_id is None:
            user_id = get_current_user_id(db)

        # 1. Fetch base session row from DB or memory
        row = db.execute(
            text(
                """
                SELECT payload, status, agent_type, project_name, model, provider,
                       title, working_dir, current_activity, failure_reason,
                       passed, reward, total_tokens, total_duration_sec, estimated_cost_usd
                FROM sessions
                WHERE id = :id AND user_id = :uid
                """
            ),
            {"id": session_id, "uid": user_id},
        ).fetchone()

        base_session: Session | None = None
        if row and row[0]:
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            with contextlib.suppress(Exception):
                base_session = Session.model_validate(payload)

        if base_session is None:
            with self._lock:
                cached = self._cached_session(session_id)
                if cached is not None:
                    base_session = cached.model_copy(deep=True)

        if base_session is None:
            agent = "antigravity"
            if row and row[2]:
                agent = cast(AgentType, str(row[2]))
            base_session = Session(
                session_id=session_id,
                agent_type=agent,
                status=row[1] if row and row[1] else "working",
                project_name=row[3] if row and row[3] else "",
                title=row[6] if row and row[6] else f"Session ({session_id[:8]})",
                trajectory=Trajectory(session_id=session_id),
            )

        # 2. Query all events from session_events ordered by step_index ASC, created_at ASC
        event_rows = db.execute(
            text(
                """
                SELECT id, step_index, event_type, payload, created_at
                FROM session_events
                WHERE user_id = :uid AND session_id = :session_id
                ORDER BY step_index ASC, created_at ASC
                """
            ),
            {"uid": user_id, "session_id": session_id},
        ).fetchall()

        # 3. Fold/reduce events into base_session
        reviews_map: dict[str, ReviewRecord] = {r.id: r for r in base_session.trajectory.reviews}
        tool_calls_map: dict[str, ToolCall] = {
            tc.tool_id: tc for tc in base_session.trajectory.tool_calls
        }
        tool_results_map: dict[str, ToolResult] = {
            tr.tool_id: tr for tr in base_session.trajectory.tool_results
        }
        steps_list: list[Any] = list(getattr(base_session, "steps", []) or [])

        for erow in event_rows:
            _eid, step_idx, etype, epayload, _ecreated_at = erow
            if isinstance(epayload, str):
                epayload = json.loads(epayload)

            if etype == "review_decision":
                with contextlib.suppress(Exception):
                    rev = ReviewRecord.model_validate(epayload)
                    if rev.id not in reviews_map:
                        base_session.trajectory.reviews.append(rev)
                        reviews_map[rev.id] = rev
                    else:
                        idx = next(
                            (
                                i
                                for i, r in enumerate(base_session.trajectory.reviews)
                                if r.id == rev.id
                            ),
                            None,
                        )
                        if idx is not None:
                            base_session.trajectory.reviews[idx] = rev
                        reviews_map[rev.id] = rev
                    steps_list.append(
                        {"type": "review_decision", "step_index": step_idx, "data": epayload}
                    )

            elif etype == "tool_call":
                with contextlib.suppress(Exception):
                    tc = ToolCall.model_validate(epayload)
                    if tc.tool_id not in tool_calls_map:
                        base_session.trajectory.tool_calls.append(tc)
                        tool_calls_map[tc.tool_id] = tc
                    else:
                        idx = next(
                            (
                                i
                                for i, x in enumerate(base_session.trajectory.tool_calls)
                                if x.tool_id == tc.tool_id
                            ),
                            None,
                        )
                        if idx is not None:
                            base_session.trajectory.tool_calls[idx] = tc
                        tool_calls_map[tc.tool_id] = tc
                    base_session.current_activity = f"Call {tc.tool_name}"
                    steps_list.append(
                        {"type": "tool_call", "step_index": step_idx, "data": epayload}
                    )

            elif etype == "tool_result":
                with contextlib.suppress(Exception):
                    tr = ToolResult.model_validate(epayload)
                    if tr.tool_id not in tool_results_map:
                        base_session.trajectory.tool_results.append(tr)
                        tool_results_map[tr.tool_id] = tr
                    else:
                        idx = next(
                            (
                                i
                                for i, x in enumerate(base_session.trajectory.tool_results)
                                if x.tool_id == tr.tool_id
                            ),
                            None,
                        )
                        if idx is not None:
                            base_session.trajectory.tool_results[idx] = tr
                        tool_results_map[tr.tool_id] = tr
                    steps_list.append(
                        {"type": "tool_result", "step_index": step_idx, "data": epayload}
                    )

            elif etype == "operator_resolution":
                rev_id = epayload.get("id") or epayload.get("review_id")
                idx = next(
                    (i for i, r in enumerate(base_session.trajectory.reviews) if r.id == rev_id),
                    None,
                )
                if idx is not None:
                    target_rev = base_session.trajectory.reviews[idx].model_copy()
                    if "decision" in epayload:
                        target_rev.decision = epayload["decision"]
                    if "explanation" in epayload:
                        target_rev.explanation = epayload["explanation"]
                    if "human_override" in epayload:
                        target_rev.human_override = epayload["human_override"]
                    if "resolution_status" in epayload:
                        target_rev.resolution_status = epayload["resolution_status"]
                    base_session.trajectory.reviews[idx] = target_rev
                    if rev_id:
                        reviews_map[rev_id] = target_rev
                steps_list.append(
                    {"type": "operator_resolution", "step_index": step_idx, "data": epayload}
                )

            elif etype == "message":
                with contextlib.suppress(Exception):
                    msg = Message.model_validate(epayload)
                    if not any(
                        m.role == msg.role and m.content == msg.content
                        for m in base_session.trajectory.messages
                    ):
                        base_session.trajectory.messages.append(msg)
                    steps_list.append({"type": "message", "step_index": step_idx, "data": epayload})

        base_session.steps = steps_list
        base_session.total_steps = len(base_session.trajectory.tool_calls) + len(
            base_session.trajectory.messages
        )
        base_session.updated_at = datetime.now(UTC).isoformat()

        # 4. Upsert the materialized JSON into sessions.payload with updated_at = NOW()
        payload_json = json.dumps(_to_jsonable(base_session.model_dump()))
        res = db.execute(
            text(
                """
                UPDATE sessions
                SET payload = CAST(:payload AS jsonb),
                    current_activity = COALESCE(:current_activity, current_activity),
                    updated_at = NOW()
                WHERE id = :id AND user_id = :uid
                """
            ),
            {
                "id": session_id,
                "uid": user_id,
                "payload": payload_json,
                "current_activity": base_session.current_activity,
            },
        )
        if getattr(res, "rowcount", 0) == 0:
            db.execute(
                text(
                    """
                    INSERT INTO sessions (
                        id, user_id, agent_type, project_name, model, provider,
                        status, title, working_dir, current_activity, failure_reason,
                        passed, reward, total_tokens, total_duration_sec,
                        estimated_cost_usd, payload, created_at, updated_at
                    ) VALUES (
                        :id, :user_id, :agent_type, :project_name, :model, :provider,
                        :status, :title, :working_dir, :current_activity, :failure_reason,
                        :passed, :reward, :total_tokens, :total_duration_sec,
                        :estimated_cost_usd, CAST(:payload AS jsonb), NOW(), NOW()
                    )
                    ON CONFLICT (user_id, id) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                    WHERE sessions.user_id = EXCLUDED.user_id;
                    """
                ),
                {
                    "id": base_session.session_id,
                    "user_id": user_id,
                    "agent_type": base_session.agent_type,
                    "project_name": base_session.project_name or "",
                    "model": base_session.model or "",
                    "provider": base_session.provider or "",
                    "status": base_session.status,
                    "title": base_session.title or "",
                    "working_dir": base_session.working_dir or "",
                    "current_activity": base_session.current_activity or "",
                    "failure_reason": base_session.failure_reason or "",
                    "passed": base_session.passed,
                    "reward": base_session.reward,
                    "total_tokens": base_session.total_tokens,
                    "total_duration_sec": base_session.total_duration_sec,
                    "estimated_cost_usd": base_session.estimated_cost_usd,
                    "payload": payload_json,
                },
            )
        db.commit()

        # Update disk JSON cache
        with contextlib.suppress(Exception):
            target = self.storage_dir / f"{base_session.session_id}.json"
            tmp_target = self.storage_dir / f"{base_session.session_id}.tmp"
            tmp_target.write_text(base_session.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp_target, target)

        with self._lock:
            self._cache_session(base_session)
            key = self.memory_key(session_id)
            self._dirty_sessions.discard(key)

        return base_session

    def schedule_materialize(self, session_id: str, delay: float = 0.5) -> None:
        """Schedule materialization in a non-blocking background task with debounce/coalescing."""
        key = self.memory_key(session_id)
        with self._lock:
            self._dirty_sessions.add(key)

        user_id = current_user_id.get()
        req_id = request_id_ctx.get()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Synchronous context: materialize immediately
            self.materialize_session(session_id)
            return

        # Coalesce / debounce: cancel existing pending task if any
        existing_task = self._debounce_tasks.get(key)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        task = loop.create_task(self._debounced_materialize(session_id, user_id, delay, req_id))
        self._debounce_tasks[key] = task

    async def _debounced_materialize(
        self, session_id: str, user_id: int | None, delay: float, request_id: str | None = None
    ) -> None:
        try:
            await asyncio.sleep(delay)

            def _worker() -> None:
                if user_id is not None:
                    current_user_id.set(user_id)
                if request_id is not None:
                    request_id_ctx.set(request_id)
                self.materialize_session(session_id)

            await asyncio.to_thread(_worker)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Background materialization error for %s: %s", session_id, e)
        finally:
            key = self.memory_key(session_id)
            self._debounce_tasks.pop(key, None)

    def get_session_decisions(self, session_id: str) -> list[ReviewRecord]:
        """Get all review decisions for a given session."""
        with self._lock:
            key = self.memory_key(session_id)
            if (
                key in self._reviews
                and self._reviews[key]
                and self._cache_visible(
                    key, self._sessions.get(key) or Session(session_id=session_id)
                )
            ):
                return list(self._reviews[key])
            session = self._cached_session(session_id)
            if session and session.trajectory and session.trajectory.reviews:
                return list(session.trajectory.reviews)

        with SessionLocal() as db:
            user_id = get_current_user_id(db)
            rows = db.execute(
                text(
                    """
                    SELECT id, session_id, tool_name, tool_input, decision,
                           score, stage, rule_name, explanation, diff, latency_ms, created_at
                    FROM reviews
                    WHERE session_id = :session_id AND user_id = :uid
                    ORDER BY created_at ASC
                    """
                ),
                {"session_id": session_id, "uid": user_id},
            ).fetchall()

            decisions: list[ReviewRecord] = []
            for r in rows:
                decisions.append(
                    ReviewRecord(
                        id=str(r[0]),
                        session_id=str(r[1]),
                        tool_name=str(r[2]),
                        tool_input=str(r[3] or ""),
                        decision=cast(ReviewDecision, str(r[4])),
                        score=int(r[5] or 1),
                        stage=cast(ReviewStage, str(r[6] or "rule")),
                        rule_name=str(r[7] or "") if r[7] else None,
                        explanation=str(r[8] or ""),
                        diff=str(r[9] or "") if r[9] else None,
                        latency_ms=float(r[10] or 0.0),
                        timestamp=r[11].isoformat() if hasattr(r[11], "isoformat") else str(r[11]),
                    )
                )
            return decisions

    def resolve_decision(
        self,
        session_id: str,
        review_id: str | None,
        action: Literal["allow_once", "allow_session", "deny", "cancel"],
        notes: str | None = None,
    ) -> ReviewRecord | None:
        """Resolve an escalated or blocked decision via human oversight."""
        from server.event_bus import get_event_bus

        decisions = self.get_session_decisions(session_id)
        target_review: ReviewRecord | None = None
        if review_id:
            target_review = next((r for r in decisions if r.id == review_id), None)
        elif decisions:
            target_review = decisions[-1]

        if not target_review:
            return None

        if action in ("allow_once", "allow_session"):
            target_review.decision = "allow"
            target_review.explanation = (
                f"Human override ({action}): {notes or 'Approved by developer'}"
            )
        elif action == "deny":
            target_review.decision = "block"
            target_review.explanation = (
                f"Human confirmed denial: {notes or 'Rejected by developer'}"
            )
        elif action == "cancel":
            target_review.decision = "block"
            target_review.explanation = f"Operation cancelled by developer: {notes or ''}"

        with SessionLocal() as db:
            user_id = get_current_user_id(db)
            db.execute(
                text(
                    """
                    UPDATE reviews
                    SET decision = :decision, explanation = :explanation
                    WHERE id = :id AND user_id = :uid
                    """
                ),
                {
                    "decision": target_review.decision,
                    "explanation": target_review.explanation,
                    "id": target_review.id,
                    "uid": user_id,
                },
            )
            db.commit()

        # Update in-memory session model
        with self._lock:
            key = self.memory_key(session_id)
            cached = self._sessions.get(key)
            if cached is not None:
                for rev in cached.trajectory.reviews:
                    if rev.id == target_review.id:
                        rev.decision = target_review.decision
                        rev.explanation = target_review.explanation

        # Append immutable operator_resolution event
        step_idx = self._get_next_step_index(session_id)
        event = SessionEventRecord(
            id=str(uuid4()),
            user_id=user_id,
            session_id=session_id,
            step_index=step_idx,
            event_type="operator_resolution",
            payload=target_review.model_dump(),
        )
        self.append_event(event)

        # Broadcast and publish to Pub/Sub bus
        rev_dump = target_review.model_dump()
        self.broadcast_sync(session_id, "decision_resolved", rev_dump)
        get_event_bus().publish_sync("decision_resolved", rev_dump, user_id=user_id)

        # Schedule materialization
        self.schedule_materialize(session_id)

        return target_review

    # -------------------------------------------------------------------------
    # Trajectory Events
    # -------------------------------------------------------------------------

    def append_trajectory_event(
        self,
        session_id: str,
        event: Message | ToolCall | ToolResult,
    ) -> None:
        """Append a message, tool call, or tool result to a session trajectory without full payload rewrite."""
        from server.event_bus import get_event_bus

        session = self.get_session(session_id)
        if not session:
            with self._lock:
                session = self._cached_session(session_id)
        if not session:
            return

        redacted_event_dump = redact_payload(event.model_dump())
        kind = "message"
        if isinstance(event, Message):
            kind = "message"
            session.trajectory.messages.append(event)
            self.broadcast_sync(session_id, "message", redacted_event_dump)
        elif isinstance(event, ToolCall):
            kind = "tool_call"
            session.trajectory.tool_calls.append(event)
            session.current_activity = f"Call {event.tool_name}"
            self.broadcast_sync(session_id, "tool_call", redacted_event_dump)
        elif isinstance(event, ToolResult):
            kind = "tool_result"
            session.trajectory.tool_results.append(event)
            self.broadcast_sync(session_id, "tool_result", redacted_event_dump)

        # Single DB session for trajectory_events and session_events
        with SessionLocal() as db:
            user_id = get_current_user_id(db)
            db.execute(
                text(
                    """
                    INSERT INTO trajectory_events (user_id, session_id, kind, payload, created_at)
                    VALUES (:user_id, :session_id, :kind, CAST(:payload AS jsonb), NOW())
                    """
                ),
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "kind": kind,
                    "payload": json.dumps(_to_jsonable(redacted_event_dump)),
                },
            )
            step_idx = self._get_next_step_index(session_id, db=db)
            sevent = SessionEventRecord(
                id=str(uuid4()),
                user_id=user_id,
                session_id=session_id,
                step_index=step_idx,
                event_type=kind,
                payload=redacted_event_dump,
            )
            self.append_event(sevent, db=db)
            db.commit()

        # Publish to Redis Pub/Sub bus
        get_event_bus().publish_sync(kind, redacted_event_dump, user_id=user_id)

        # Schedule debounced materialization (replaces immediate full sessions.payload rewrite)
        self.schedule_materialize(session_id)

    def get_trajectory(self, session_id: str) -> Trajectory | None:
        """Get the full trajectory for a session."""
        session = self.get_session(session_id)
        return session.trajectory if session else None

    # -------------------------------------------------------------------------
    # Analytics & Analyzer
    # -------------------------------------------------------------------------

    def get_analytics_overview(self) -> dict[str, Any]:
        """Aggregate high-level overview metrics directly from PostgreSQL for current user."""
        with SessionLocal() as db:
            user_id = get_current_user_id(db)

            # Sessions counts
            total_sessions = (
                db.execute(
                    text("SELECT COUNT(*) FROM sessions WHERE user_id = :uid"), {"uid": user_id}
                ).scalar()
                or 0
            )

            deep_reviewed = (
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM sessions WHERE user_id = :uid AND (passed IS NOT NULL)"
                    ),
                    {"uid": user_id},
                ).scalar()
                or 0
            )

            # Reviews counts
            blocked_count = (
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND decision IN ('block', 'deny', 'ask')"
                    ),
                    {"uid": user_id},
                ).scalar()
                or 0
            )

            critical_reviews = (
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND (score >= 8 OR decision IN ('block', 'deny', 'ask'))"
                    ),
                    {"uid": user_id},
                ).scalar()
                or 0
            )

            total_reviews = (
                db.execute(
                    text("SELECT COUNT(*) FROM reviews WHERE user_id = :uid"), {"uid": user_id}
                ).scalar()
                or 0
            )

            auto_approved = (
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND decision = 'allow'"
                    ),
                    {"uid": user_id},
                ).scalar()
                or 0
            )

            escalated_count = (
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND decision = 'escalate'"
                    ),
                    {"uid": user_id},
                ).scalar()
                or 0
            )

            critical_rate = (
                round((critical_reviews / total_reviews) * 100, 1) if total_reviews > 0 else 0.0
            )
            auto_approved_pct = (
                round((auto_approved / total_reviews) * 100, 1) if total_reviews > 0 else 0.0
            )

            agent_counts_rows = db.execute(
                text(
                    "SELECT agent_type, COUNT(*) FROM sessions WHERE user_id = :uid GROUP BY agent_type"
                ),
                {"uid": user_id},
            ).fetchall()
            agent_counts = {str(r[0]): int(r[1]) for r in agent_counts_rows}

            return {
                "total_sessions": total_sessions,
                "deep_reviewed": deep_reviewed,
                "critical_warning_rate": f"{critical_rate}%",
                "blocked_incidents": blocked_count,
                "total_reviews": total_reviews,
                "auto_approved": auto_approved,
                "escalated_count": escalated_count,
                "auto_approved_pct": f"{auto_approved_pct}%",
                "agent_counts": agent_counts,
            }

    def get_analyzer_summary(self) -> dict[str, Any]:
        """Compute organization-wide Analyzer risk metrics and real SQL latency percentiles."""
        with SessionLocal() as db:
            user_id = get_current_user_id(db)

            # 1. Core counters
            total_reviews = (
                db.execute(
                    text("SELECT COUNT(*) FROM reviews WHERE user_id = :uid"), {"uid": user_id}
                ).scalar()
                or 0
            )

            total_blocked = (
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND decision IN ('block', 'deny', 'ask')"
                    ),
                    {"uid": user_id},
                ).scalar()
                or 0
            )

            total_allowed = (
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM reviews WHERE user_id = :uid AND decision = 'allow'"
                    ),
                    {"uid": user_id},
                ).scalar()
                or 0
            )

            total_sessions = (
                db.execute(
                    text("SELECT COUNT(*) FROM sessions WHERE user_id = :uid"), {"uid": user_id}
                ).scalar()
                or 0
            )

            block_rate_pct = (
                round((total_blocked / total_reviews) * 100.0, 1) if total_reviews > 0 else 0.0
            )

            # 2. Real SQL Latency percentiles across all recorded reviews for active user
            lat_row = db.execute(
                text(
                    """
                    SELECT
                        COALESCE(percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms), 0.0) AS p50,
                        COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0.0) AS p95,
                        COALESCE(AVG(latency_ms), 0.0) AS avg_lat
                    FROM reviews
                    WHERE user_id = :uid AND latency_ms > 0
                    """
                ),
                {"uid": user_id},
            ).fetchone()

            if lat_row and total_reviews > 0:
                p50_latency = round(float(lat_row[0]), 2)
                p95_latency = round(float(lat_row[1]), 2)
                avg_latency = round(float(lat_row[2]), 2)
            else:
                p50_latency = 0.0
                p95_latency = 0.0
                avg_latency = 0.0

            # 3. Top blocked threat categories
            threat_rows = db.execute(
                text(
                    """
                    SELECT
                        COALESCE(NULLIF(rule_name, ''), 'unclassified') as threat,
                        COUNT(*) as count,
                        ROUND(CAST(AVG(score) AS numeric), 1) as avg_severity
                    FROM reviews
                    WHERE user_id = :uid AND decision IN ('block', 'deny', 'ask')
                    GROUP BY threat
                    ORDER BY count DESC, avg_severity DESC
                    LIMIT 5
                    """
                ),
                {"uid": user_id},
            ).fetchall()
            top_threats = [
                {
                    "threat": str(row[0]),
                    "count": int(row[1]),
                    "avg_severity": float(row[2] or 0.0),
                }
                for row in threat_rows
            ]

            # 4. Lockout distribution by agent source
            agent_rows = db.execute(
                text(
                    """
                    SELECT
                        COALESCE(NULLIF(s.agent_type, ''), 'other') as agent,
                        COUNT(r.id) as total_events,
                        SUM(CASE WHEN r.decision IN ('block', 'deny', 'ask') THEN 1 ELSE 0 END) as lockouts
                    FROM reviews r
                    LEFT JOIN sessions s ON r.session_id = s.id
                    WHERE r.user_id = :uid
                    GROUP BY agent
                    ORDER BY lockouts DESC, total_events DESC
                    """
                ),
                {"uid": user_id},
            ).fetchall()
            agent_distribution = [
                {
                    "agent": str(row[0]),
                    "total_events": int(row[1]),
                    "lockouts": int(row[2] or 0),
                }
                for row in agent_rows
            ]

            # 5. Recent high-priority interventions
            recent_rows = db.execute(
                text(
                    """
                    SELECT
                        r.id,
                        r.session_id,
                        COALESCE(s.agent_type, 'unknown') as agent_type,
                        COALESCE(NULLIF(r.rule_name, ''), 'policy_violation') as rule_name,
                        r.tool_name,
                        r.decision,
                        r.score,
                        r.created_at,
                        r.explanation
                    FROM reviews r
                    LEFT JOIN sessions s ON r.session_id = s.id
                    WHERE r.user_id = :uid AND r.decision IN ('block', 'deny', 'ask')
                    ORDER BY r.created_at DESC
                    LIMIT 8
                    """
                ),
                {"uid": user_id},
            ).fetchall()
            recent_interventions = [
                {
                    "id": str(row[0]),
                    "session_id": str(row[1]),
                    "agent_type": str(row[2]),
                    "threat": str(row[3]),
                    "tool_name": str(row[4]),
                    "decision": str(row[5]),
                    "score": int(row[6] or 0),
                    "timestamp": row[7].isoformat()
                    if hasattr(row[7], "isoformat")
                    else str(row[7]),
                    "explanation": str(row[8] or ""),
                }
                for row in recent_rows
            ]

            return {
                "total_reviews": total_reviews,
                "total_blocked": total_blocked,
                "total_allowed": total_allowed,
                "block_rate_pct": block_rate_pct,
                "total_sessions": total_sessions,
                "avg_latency_ms": avg_latency,
                "p50_latency_ms": p50_latency,
                "p95_latency_ms": p95_latency,
                "top_threats": top_threats,
                "agent_distribution": agent_distribution,
                "recent_interventions": recent_interventions,
            }

    # -------------------------------------------------------------------------
    # Policy Management (Multi-Tenant Per-User)
    # -------------------------------------------------------------------------

    def _load_user_settings_from_disk(self) -> None:
        """Load persisted user policies and configs from disk if present."""
        pol_path = self.storage_dir / "user_policies.json"
        if pol_path.exists():
            try:
                data = json.loads(pol_path.read_text(encoding="utf-8"))
                for uid_str, p_dict in data.items():
                    self._user_policies[int(uid_str)] = Policy.model_validate(p_dict)
            except Exception as e:
                logger.warning("Failed to load user policies from disk: %s", e)

        cfg_path = self.storage_dir / "user_configs.json"
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                for uid_str, c_dict in data.items():
                    self._user_configs[int(uid_str)] = WatcherConfig.model_validate(c_dict)
            except Exception as e:
                logger.warning("Failed to load user configs from disk: %s", e)

        graders_path = self.storage_dir / "user_graders.json"
        if graders_path.exists():
            try:
                data = json.loads(graders_path.read_text(encoding="utf-8"))
                for uid_str, g_dict in data.items():
                    self._user_graders[int(uid_str)] = g_dict
            except Exception as e:
                logger.warning("Failed to load user graders from disk: %s", e)

    def _save_user_settings_to_disk(self) -> None:
        """Persist user policies, configs, and custom grader prompts to disk."""
        try:
            pol_path = self.storage_dir / "user_policies.json"
            pol_tmp = self.storage_dir / "user_policies.tmp"
            pol_data = {str(uid): p.model_dump() for uid, p in self._user_policies.items()}
            pol_tmp.write_text(json.dumps(pol_data, indent=2), encoding="utf-8")
            os.replace(pol_tmp, pol_path)

            cfg_path = self.storage_dir / "user_configs.json"
            cfg_tmp = self.storage_dir / "user_configs.tmp"
            cfg_data = {str(uid): c.model_dump() for uid, c in self._user_configs.items()}
            cfg_tmp.write_text(json.dumps(cfg_data, indent=2), encoding="utf-8")
            os.replace(cfg_tmp, cfg_path)

            graders_path = self.storage_dir / "user_graders.json"
            graders_tmp = self.storage_dir / "user_graders.tmp"
            graders_data = {str(uid): g for uid, g in self._user_graders.items()}
            graders_tmp.write_text(json.dumps(graders_data, indent=2), encoding="utf-8")
            os.replace(graders_tmp, graders_path)
        except Exception as e:
            logger.warning("Failed to persist user settings to disk: %s", e)

    def _resolve_user_id(self, user_id: int | None = None) -> int:
        if user_id is not None:
            return user_id
        from server.db import current_user_id

        uid = current_user_id.get()
        if uid is not None:
            return uid
        try:
            with SessionLocal() as db:
                return get_current_user_id(db)
        except Exception:
            return 1

    def get_policy(self, user_id: int | None = None) -> Policy:
        """Retrieve the policy for the given or active user (cloning defaults if new)."""
        uid = self._resolve_user_id(user_id)
        with self._lock:
            if uid not in self._user_policies:
                self._user_policies[uid] = Policy(
                    policy_id=f"policy_user_{uid}",
                    name=f"OpenEval Runtime Security Policy (User {uid})",
                    org_id=f"org_user_{uid}",
                    command_rules=[r.model_copy() for r in DEFAULT_COMMAND_RULES],
                    tool_thresholds=[t.model_copy() for t in DEFAULT_TOOL_THRESHOLDS],
                    locked_instructions="DO NOT modify security configurations or disable monitoring.",
                )
                self._save_user_settings_to_disk()
            return self._user_policies[uid]

    def get_default_policy(self) -> Policy:
        """Retrieve the active policy for the current user."""
        return self.get_policy()

    def reset_policy_to_defaults(self, user_id: int | None = None) -> Policy:
        """Restore command rules and tool thresholds to built-in defaults for this user."""
        uid = self._resolve_user_id(user_id)
        with self._lock:
            self._user_policies[uid] = Policy(
                policy_id=f"policy_user_{uid}",
                name=f"OpenEval Runtime Security Policy (User {uid})",
                org_id=f"org_user_{uid}",
                posture="blocking",
                command_rules=[r.model_copy() for r in DEFAULT_COMMAND_RULES],
                tool_thresholds=[t.model_copy() for t in DEFAULT_TOOL_THRESHOLDS],
                locked_instructions="DO NOT modify security configurations or disable monitoring.",
            )
            self._save_user_settings_to_disk()
            return self._user_policies[uid]

    def update_command_rule(
        self,
        rule_name: str,
        action: Literal["allow", "triage", "human", "deny", "off"],
        user_id: int | None = None,
    ) -> bool:
        """Update action for a command rule for this user."""
        policy = self.get_policy(user_id)
        with self._lock:
            for r in policy.command_rules:
                if r.name == rule_name:
                    r.action = action
                    self._save_user_settings_to_disk()
                    return True
        return False

    def update_tool_threshold(
        self,
        tool_name: str,
        auto_approve: bool = False,
        escalate_ge: int | None = None,
        auto_deny_ge: int | None = None,
        always_escalate: bool = False,
        auto_approve_le: int = 3,
        user_id: int | None = None,
    ) -> bool:
        """Update thresholds for a tool for this user."""
        policy = self.get_policy(user_id)
        with self._lock:
            for t in policy.tool_thresholds:
                if t.tool_name == tool_name:
                    t.auto_approve = auto_approve
                    t.escalate_ge = escalate_ge
                    t.auto_deny_ge = auto_deny_ge
                    t.always_escalate = always_escalate
                    t.auto_approve_le = auto_approve_le
                    self._save_user_settings_to_disk()
                    return True
            policy.tool_thresholds.append(
                ToolThreshold(
                    tool_name=tool_name,
                    auto_approve=auto_approve,
                    escalate_ge=escalate_ge,
                    auto_deny_ge=auto_deny_ge,
                    always_escalate=always_escalate,
                    auto_approve_le=auto_approve_le,
                )
            )
            self._save_user_settings_to_disk()
            return True

    def update_tool_thresholds(
        self, thresholds: list[ToolThreshold], user_id: int | None = None
    ) -> bool:
        """Bulk update tool thresholds for this user."""
        for incoming in thresholds:
            self.update_tool_threshold(
                tool_name=incoming.tool_name,
                auto_approve=incoming.auto_approve,
                escalate_ge=incoming.escalate_ge,
                auto_deny_ge=incoming.auto_deny_ge,
                always_escalate=incoming.always_escalate,
                auto_approve_le=incoming.auto_approve_le,
                user_id=user_id,
            )
        return True

    def get_user_config(self, user_id: int | None = None) -> WatcherConfig:
        """Retrieve the Watcher enforcement mode and configuration for this user."""
        uid = self._resolve_user_id(user_id)
        with self._lock:
            if uid not in self._user_configs:
                self._user_configs[uid] = WatcherConfig()
                self._save_user_settings_to_disk()
            return self._user_configs[uid]

    def update_user_config(
        self, new_config: WatcherConfig, user_id: int | None = None
    ) -> WatcherConfig:
        """Update the Watcher enforcement mode and configuration for this user."""
        uid = self._resolve_user_id(user_id)
        with self._lock:
            self._user_configs[uid] = new_config.model_copy()
            self._save_user_settings_to_disk()
            return self._user_configs[uid]

    def get_grader_definitions(self, user_id: int | None = None) -> list[Any]:
        """Retrieve the 5 Canonical Graders, applying user-specific prompt customizations if any."""
        from engine.graders_suite import list_canonical_graders

        uid = self._resolve_user_id(user_id)
        base_graders = list_canonical_graders()
        with self._lock:
            user_overrides = self._user_graders.get(uid, {})
            if not user_overrides:
                return base_graders

            customized = []
            for g in base_graders:
                if g.name in user_overrides:
                    customized.append(g.model_copy(update=user_overrides[g.name]))
                else:
                    customized.append(g)
            return customized

    def get_grader_definition(self, grader_name: str, user_id: int | None = None) -> Any | None:
        """Retrieve a specific Canonical Grader with user customizations applied."""
        definitions = self.get_grader_definitions(user_id)
        for g in definitions:
            if g.name == grader_name:
                return g
        norm = grader_name.lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "credentials_and_api_keys": "CredentialsGrader",
            "credentials": "CredentialsGrader",
            "credentials_grader": "CredentialsGrader",
            "credentialsgrader": "CredentialsGrader",
            "pii_and_sensitive_info": "PIIGrader",
            "pii_and_sensitive_information": "PIIGrader",
            "pii": "PIIGrader",
            "pii_grader": "PIIGrader",
            "piigrader": "PIIGrader",
            "instruction_and_policy_adherence": "InstructionGrader",
            "instruction": "InstructionGrader",
            "instruction_grader": "InstructionGrader",
            "instructiongrader": "InstructionGrader",
            "code_security": "SecurityGrader",
            "security": "SecurityGrader",
            "security_grader": "SecurityGrader",
            "securitygrader": "SecurityGrader",
            "infrastructure_safety": "DeletionGrader",
            "deletion": "DeletionGrader",
            "deletion_grader": "DeletionGrader",
            "deletiongrader": "DeletionGrader",
        }
        resolved = aliases.get(norm)
        for g in definitions:
            if resolved and g.name == resolved:
                return g
            if g.name.lower() == norm or g.name.lower().replace("grader", "") == norm:
                return g
        return None

    def update_grader_definition(
        self, grader_name: str, updates: dict[str, str], user_id: int | None = None
    ) -> Any | None:
        """Update prompt components for a specific grader for this user."""
        uid = self._resolve_user_id(user_id)
        from engine.graders_suite import CanonicalGrader, get_canonical_grader

        base = get_canonical_grader(grader_name)
        if not base:
            return None

        valid_fields = set(CanonicalGrader.model_fields.keys())
        filtered = {k: v for k, v in updates.items() if k in valid_fields and isinstance(v, str)}

        with self._lock:
            if uid not in self._user_graders:
                self._user_graders[uid] = {}
            if grader_name not in self._user_graders[uid]:
                self._user_graders[uid][grader_name] = {}
            self._user_graders[uid][grader_name].update(filtered)
            self._save_user_settings_to_disk()

        return self.get_grader_definition(grader_name, uid)

    def reset_grader_definition(self, grader_name: str, user_id: int | None = None) -> Any | None:
        """Reset a grader's prompt components back to defaults for this user."""
        uid = self._resolve_user_id(user_id)
        with self._lock:
            if uid in self._user_graders and grader_name in self._user_graders[uid]:
                del self._user_graders[uid][grader_name]
                self._save_user_settings_to_disk()

        return self.get_grader_definition(grader_name, uid)

    # -------------------------------------------------------------------------
    # Real-Time SSE Pub/Sub
    # -------------------------------------------------------------------------

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to real-time events for a session."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        if session_id not in self._subscribers:
            self._subscribers[session_id] = []
        self._subscribers[session_id].append(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        """Unsubscribe from real-time events."""
        if session_id in self._subscribers and q in self._subscribers[session_id]:
            self._subscribers[session_id].remove(q)

    def broadcast_sync(self, session_id: str, event_type: str, data: dict[str, Any]) -> None:
        """Safely push event to all active async SSE subscribers."""
        import contextlib

        redacted_data = redact_payload(data) if isinstance(data, dict) else data
        payload = {"event": event_type, "data": redacted_data, "timestamp": time.time()}
        subs = self._subscribers.get(session_id, [])
        for q in list(subs):
            with contextlib.suppress(Exception):
                q.put_nowait(payload)


# Global singleton store
_GLOBAL_WATCHER_STORE: WatcherStore | None = None


def get_watcher_store() -> WatcherStore:
    """Retrieve global singleton WatcherStore instance."""
    global _GLOBAL_WATCHER_STORE
    if _GLOBAL_WATCHER_STORE is None:
        _GLOBAL_WATCHER_STORE = WatcherStore()
    return _GLOBAL_WATCHER_STORE
