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
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DbSession

from engine.approval_policy import WatcherConfig
from engine.redaction import redact_payload, redact_text
from schemas.watcher_models import (
    DEFAULT_COMMAND_RULES,
    DEFAULT_TOOL_THRESHOLDS,
    AgentType,
    CalibratedDecisionTelemetry,
    Message,
    Organization,
    Policy,
    ReviewRecord,
    ReviewStage,
    Session,
    SessionEventRecord,
    ToolCall,
    ToolResult,
    ToolThreshold,
    Trajectory,
    review_from_row,
)
from server.db import SessionLocal, current_user_id, current_user_slug, get_current_user_id
from server.store_mixins.analytics import (
    MACRO_TO_SIG,
    SIGNATURE_SPECS,
    WatcherAnalyticsMixin,
    _build_capture_sources,
    _build_threat_groups,
    _canonical_threat_category,
    _capture_source_bucket,
    _classify_signature,
    _classify_threat_row,
    _infer_agent_type,
)
from server.store_mixins.materializer import WatcherMaterializerMixin

logger = logging.getLogger("openeval.server.watcher_store")

__all__ = [
    "MACRO_TO_SIG",
    "SIGNATURE_SPECS",
    "AwaitableReviewRecord",
    "CompatDBConn",
    "CompatResult",
    "WatcherStore",
    "WatcherAnalyticsMixin",
    "WatcherMaterializerMixin",
    "_build_capture_sources",
    "_build_threat_groups",
    "_canonical_threat_category",
    "_capture_source_bucket",
    "_classify_signature",
    "_classify_threat_row",
    "_infer_agent_type",
    "_to_jsonable",
    "get_watcher_store",
    "is_session_blocked",
    "normalize_session_status",
    "review_from_row",
]


def _to_jsonable(value: Any) -> Any:
    """Recursively convert Pydantic models / nested containers for JSON/SSE."""
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _to_jsonable(value.model_dump())
    if isinstance(value, str):
        return value.replace("\x00", "")
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


def is_session_blocked(session: Session) -> bool:
    """Check if session is blocked by status, policy review block/deny, or blocked_count."""
    reviews = getattr(getattr(session, "trajectory", None), "reviews", []) or []
    has_unresolved_block = False
    for r in reviews:
        decision = getattr(r, "decision", "") or ""
        score = getattr(r, "score", 0) or 0
        human_override = getattr(r, "human_override", None)
        resolution_status = getattr(r, "resolution_status", None)
        if (
            (decision in ("block", "deny", "reject", "escalate") or score >= 8)
            and human_override != "allow"
            and resolution_status != "human_approved"
        ):
            has_unresolved_block = True
            break

    if has_unresolved_block:
        return True

    if getattr(session, "blocked_count", 0) > 0 and not reviews:
        return True

    return session.status == "blocked" and not any(
        getattr(r, "human_override", None) == "allow"
        or getattr(r, "resolution_status", None) == "human_approved"
        for r in reviews
    )


def normalize_session_status(session: Session, stale_timeout_sec: float = 120.0) -> Session:
    """Ensure sessions with blocked reviews are marked 'blocked' and inactive working sessions are 'closed'."""
    # 1. Any session with unapproved policy blocks or high risk is 'blocked'
    if is_session_blocked(session):
        session.status = "blocked"
        return session

    # 2. In demo mode, sessions should never be live ('working'/'active'/'running')
    is_demo = current_user_slug.get() == "demo" or getattr(session, "org_id", "") == "demo"
    if is_demo and session.status in ("working", "active", "running"):
        session.status = "closed"
        return session

    # 3. Check staleness for active sessions
    if session.status in ("working", "active", "running"):
        last_dt: datetime | None = None
        for ts_str in (session.updated_at, session.created_at):
            if ts_str:
                with contextlib.suppress(Exception):
                    dt = datetime.fromisoformat(str(ts_str))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    if last_dt is None or dt > last_dt:
                        last_dt = dt

        trajectory = getattr(session, "trajectory", None)
        if trajectory:
            for item in (
                trajectory.tool_results[-1:]
                + trajectory.tool_calls[-1:]
                + trajectory.messages[-1:]
                + trajectory.reviews[-1:]
            ):
                t_val = getattr(item, "timestamp", None)
                if isinstance(t_val, (int, float)):
                    if t_val > 1e11:  # ms
                        t_val = t_val / 1000.0
                    with contextlib.suppress(Exception):
                        dt = datetime.fromtimestamp(t_val, UTC)
                        if last_dt is None or dt > last_dt:
                            last_dt = dt
                elif isinstance(t_val, str):
                    with contextlib.suppress(Exception):
                        dt = datetime.fromisoformat(t_val)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=UTC)
                        if last_dt is None or dt > last_dt:
                            last_dt = dt

        now_dt = datetime.now(UTC)
        if last_dt is not None:
            if (now_dt - last_dt).total_seconds() > stale_timeout_sec:
                session.status = "closed"
        else:
            session.status = "closed"

    return session


class WatcherStore(WatcherAnalyticsMixin, WatcherMaterializerMixin):
    """PostgreSQL 16 multi-tenant store for sessions, trajectories, and reviews."""

    def __init__(self, storage_dir: Path | None = None, db_path: str | None = None) -> None:
        """Create the store.

        ``db_path`` is a legacy DuckDB-era argument. Persistence now lives in
        PostgreSQL (``server/db.py``), so the value is ignored; it stays in the
        signature because existing tests/eval callers still pass it.
        """
        del db_path  # deprecated no-op
        self._lock = threading.RLock()
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self.storage_dir = storage_dir or Path(os.getenv("WATCHER_STORAGE_DIR", ".runs/watcher"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # Degraded-mode telemetry: latched to True on the first primary PostgreSQL
        # read/write failure that forced an offline fallback. It stays latched for the
        # process lifetime so analytics/health consumers can never mistake fallback
        # zeros for healthy measurements. Callers may clear it after a recovery probe.
        self.degraded: bool = False
        self.last_db_error: str | None = None

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

        # Ephemeral demo uploads (never written to PostgreSQL)
        self._in_memory_demo_sessions: dict[str, Session] = {}

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
            locked_instructions="",
        )

    @property
    def con(self) -> CompatDBConn:
        """Database connection object providing .execute(sql, params) for backward compatibility."""
        return CompatDBConn()

    def _record_db_failure(self, context: str, exc: BaseException) -> None:
        """Mark the store degraded and surface a primary-DB failure instead of hiding it.

        Expected infrastructure failures (SQLAlchemy/OS errors) are logged at WARNING;
        anything else is a programming error and is logged with a traceback so it is
        not silently swallowed by an offline fallback path.
        """
        self.degraded = True
        self.last_db_error = f"{context}: {exc}"
        if isinstance(exc, (SQLAlchemyError, OSError)):
            logger.warning(
                "WatcherStore degraded - %s failed (offline/fallback mode): %s", context, exc
            )
        else:
            logger.exception("WatcherStore degraded - unexpected error in %s", context)

    def reload_from_disk(self) -> int:
        """Re-read sessions from store / disk (no-op for Postgres)."""
        return 0

    def reset_runtime_state(self) -> None:
        """Clear in-memory caches and the degraded-mode latch.

        Intended for test isolation and explicit session resets: persistent
        PostgreSQL rows and on-disk user settings are untouched.
        """
        with self._lock:
            self._sessions.clear()
            self._trajectories.clear()
            self._reviews.clear()
            self._session_owners.clear()
            self._in_memory_demo_sessions.clear()
            self._dirty_sessions.clear()
            self._debounce_tasks.clear()
            self._step_indices.clear()
            self._subscribers.clear()
        self.degraded = False
        self.last_db_error = None

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
        is_demo_session = getattr(session, "org_id", "") == "demo" or (
            bool(session.session_id) and "demo" in session.session_id
        )
        if current_user_slug.get() == "demo":
            return is_demo_session or owner == uid
        if is_demo_session:
            return False
        if owner is None:
            return True
        if uid is None:
            return False
        return owner == uid

    def _cache_session(self, session: Session) -> str:
        key = self.memory_key(session.session_id)
        if key != session.session_id:
            self._sessions.pop(session.session_id, None)
            self._trajectories.pop(session.session_id, None)
            self._reviews.pop(session.session_id, None)
            self._session_owners.pop(session.session_id, None)
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
        if current_user_id.get() is None:
            if db is not None:
                with contextlib.suppress(Exception):
                    get_current_user_id(db)
            else:
                with contextlib.suppress(Exception), SessionLocal() as check_db:
                    get_current_user_id(check_db)

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

        try:
            if db is not None:
                _execute_write(db)
            else:
                with SessionLocal() as active_db, active_db.begin():
                    _execute_write(active_db)
        except Exception as e:
            self._record_db_failure("save_session DB write", e)

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
        normalize_session_status(session)
        self._write_session_to_db(session, db=db)
        self.broadcast_sync(session.session_id, "session_created", session.model_dump())
        return session

    def record_session(self, session: Session, *, is_seeded: bool = False) -> Session:
        """Register or update a session in PostgreSQL for the active user."""
        normalize_session_status(session)
        is_demo_context = current_user_slug.get() == "demo"
        if is_demo_context and not is_seeded and not getattr(session, "is_seeded", False):
            session.org_id = "demo"
            with self._lock:
                self._in_memory_demo_sessions[session.session_id] = session
                self._cache_session(session)
            return session
        with self._lock:
            self._write_session_to_db(session)
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a session by ID scoped to the active tenant."""
        if current_user_slug.get() == "demo":
            with self._lock:
                ephemeral = self._in_memory_demo_sessions.get(session_id)
                if ephemeral is not None:
                    return normalize_session_status(ephemeral)

        key = self.memory_key(session_id)
        with self._lock:
            is_dirty = key in self._dirty_sessions
            cached = self._cached_session(session_id)
            if cached is not None and not is_dirty:
                return normalize_session_status(cached)
            if cached is not None and current_user_slug.get() == "demo":
                return normalize_session_status(cached)

        if is_dirty:
            return self.materialize_session(session_id)

        row = None
        db_failed = False
        try:
            with SessionLocal() as db:
                try:
                    user_id = get_current_user_id(db)
                    row = db.execute(
                        text("SELECT payload FROM sessions WHERE id = :id AND user_id = :uid"),
                        {"id": session_id, "uid": user_id},
                    ).fetchone()

                    if (
                        (not row or not row[0])
                        and session_id.startswith("eval-")
                        and current_user_slug.get() == "demo"
                    ):
                        # Demo eval fallback: same tenant only (demo user's eval rows)
                        row = db.execute(
                            text(
                                "SELECT payload FROM sessions WHERE id = :id "
                                "AND user_id = :uid AND id LIKE 'eval-%'"
                            ),
                            {"id": session_id, "uid": user_id},
                        ).fetchone()
                finally:
                    db.rollback()
        except Exception as e:
            self._record_db_failure("get_session", e)
            row = None
            db_failed = True

        if not row or not row[0]:
            if db_failed:
                # Check disk file fallback
                disk_file = self.storage_dir / f"{session_id}.json"
                if disk_file.exists():
                    try:
                        payload = json.loads(disk_file.read_text(encoding="utf-8"))
                        sess = Session.model_validate(payload)
                        normalize_session_status(sess)
                        with self._lock:
                            self._cache_session(sess)
                        return sess
                    except Exception as e:
                        logger.warning("Error reading session from disk %s: %s", disk_file, e)
            return None

        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        try:
            sess = Session.model_validate(payload)
            normalize_session_status(sess)
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

        # Guard: if session is blocked or being updated with status in ("working", "active", "running") when blocked
        if updates.get("status") in ("working", "active", "running") and is_session_blocked(
            session
        ):
            updates["status"] = "blocked"

        for k, v in updates.items():
            if hasattr(session, k):
                setattr(session, k, v)

        normalize_session_status(session)
        session.updated_at = datetime.now(UTC).isoformat()
        self._write_session_to_db(session)

        terminal_statuses = {
            "completed",
            "failed",
            "aborted",
            "lockout",
            "error",
            "cancelled",
            "blocked",
        }
        if getattr(session, "status", "") in terminal_statuses:
            self.materialize_session(session_id)

        self.broadcast_sync(session_id, "session_updated", _to_jsonable(updates))
        return session

    def list_sessions(
        self,
        agent_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        summary: bool = False,
        min_messages: int = 0,
    ) -> list[Session]:
        """List sessions for the current user, optionally filtered."""
        if current_user_slug.get() == "demo":
            from server.demo_seed import ensure_demo_timeline_fresh

            ensure_demo_timeline_fresh(self)

        rows = []
        try:
            with SessionLocal() as db:
                try:
                    user_id = get_current_user_id(db)
                    query = "SELECT payload FROM sessions WHERE user_id = :uid"
                    params: dict[str, Any] = {"uid": user_id}

                    if agent_type:
                        query += " AND agent_type = :agent_type"
                        params["agent_type"] = agent_type

                    query += " ORDER BY created_at DESC"
                    sql_limit = limit if not status else max(limit * 3, 200)
                    query += " LIMIT :limit"
                    params["limit"] = sql_limit

                    rows = db.execute(text(query), params).fetchall()
                finally:
                    db.rollback()
        except Exception as e:
            self._record_db_failure("list_sessions", e)
            rows = []

        results: list[Session] = []
        for r in rows:
            if r[0]:
                # Shallow copy so summary stripping never mutates the fetched row.
                payload = dict(r[0]) if isinstance(r[0], dict) else json.loads(r[0])
                raw_traj = payload.get("trajectory")
                traj = raw_traj if isinstance(raw_traj, dict) else {}
                if min_messages > 0 and not (
                    len(traj.get("messages") or []) >= min_messages
                    or len(traj.get("tool_calls") or []) >= min_messages
                    or len(traj.get("reviews") or []) >= min_messages
                ):
                    # Filter on raw counts before paying Pydantic validation cost.
                    continue
                if summary:
                    # Drop heavy blobs before validation; otherwise every message,
                    # tool call/result and step is instantiated in memory.
                    payload["total_steps"] = (
                        payload.get("total_steps")
                        or len(payload.get("steps") or [])
                        or len(payload.get("turns") or [])
                        or len(traj.get("tool_calls") or [])
                        or len(traj.get("reviews") or [])
                    )
                    payload["steps"] = []
                    payload["turns"] = []
                    payload["verifier_output"] = None
                    # ``decisions`` duplicates ``trajectory.reviews``; dashboards read the
                    # trajectory copy, so dropping it halves the payload.
                    payload["decisions"] = []
                    payload["trajectory"] = {
                        "session_id": traj.get("session_id") or payload.get("session_id") or "",
                        "messages": [],
                        "tool_calls": [],
                        "tool_results": [],
                        "reviews": [
                            rev
                            for rev in (traj.get("reviews") or [])
                            if isinstance(rev, dict)
                            and (
                                rev.get("decision")
                                in ("block", "deny", "reject", "ask", "escalate", "force_ask")
                                or (rev.get("score") or 0) >= 8
                                or rev.get("human_override") is not None
                            )
                        ],
                    }
                with contextlib.suppress(Exception):
                    s = Session.model_validate(payload)
                    normalize_session_status(s)
                    results.append(s)

        db_ids = {s.session_id for s in results}
        if not rows and self.storage_dir.exists():
            is_demo_tenant = current_user_slug.get() == "demo"

            def _disk_session_visible(sess: Session) -> bool:
                """Apply the same tenant/demo visibility gate to unscoped disk sessions."""
                is_demo_sess = getattr(sess, "org_id", "") == "demo" or "-demo-" in (
                    sess.session_id or ""
                )
                if is_demo_sess:
                    # Demo fixtures are readable only from the demo tenant.
                    return is_demo_tenant
                if is_demo_tenant:
                    return False
                with self._lock:
                    for key, cached in self._sessions.items():
                        if cached.session_id == sess.session_id:
                            return self._cache_visible(key, cached)
                return True

            for f in self.storage_dir.glob("*.json"):
                if f.stem in db_ids or f.name.startswith("user_"):
                    continue
                try:
                    p = json.loads(f.read_text(encoding="utf-8"))
                    s = Session.model_validate(p)
                    normalize_session_status(s)
                    if not _disk_session_visible(s):
                        continue
                    if s.session_id not in db_ids:
                        results.append(s)
                        db_ids.add(s.session_id)
                except Exception:
                    pass
            with self._lock:
                for key, s in self._sessions.items():
                    if not self._cache_visible(key, s):
                        continue
                    if s.session_id not in db_ids:
                        normalize_session_status(s)
                        results.append(s)
                if current_user_slug.get() == "demo":
                    for s in self._in_memory_demo_sessions.values():
                        if s.session_id in db_ids:
                            continue
                        normalize_session_status(s)
                        results.append(s)

        if agent_type:
            results = [s for s in results if s.agent_type == agent_type]
        if status:
            results = [s for s in results if s.status == status]

        results.sort(key=lambda x: str(x.created_at or ""), reverse=True)
        return results[:limit]

    def clear_all_sessions(self) -> None:
        """Purge all sessions and reviews for the active tenant."""
        if current_user_slug.get() != "demo":
            try:
                with SessionLocal() as db:
                    user_id = get_current_user_id(db)
                    db.execute(
                        text("DELETE FROM session_events WHERE user_id = :uid"), {"uid": user_id}
                    )
                    db.execute(text("DELETE FROM sessions WHERE user_id = :uid"), {"uid": user_id})
                    db.commit()
            except Exception as e:
                self._record_db_failure("clear_all_sessions", e)
        with self._lock:
            if current_user_slug.get() == "demo":
                self._in_memory_demo_sessions.clear()
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
            if session_id in self._in_memory_demo_sessions:
                self._in_memory_demo_sessions.pop(session_id, None)
                mem_deleted = True

        target = self.storage_dir / f"{session_id}.json"
        if target.exists():
            with contextlib.suppress(Exception):
                target.unlink()

        if current_user_slug.get() == "demo":
            return mem_deleted

        deleted = False
        try:
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
        except Exception as e:
            self._record_db_failure("delete_session", e)
        return deleted or mem_deleted

    def purge_empty_sessions(self, min_messages: int = 1) -> list[str]:
        """Remove sessions that have fewer than min_messages and no tool calls or reviews."""
        purged: list[str] = []
        for s in self.list_sessions(limit=500):
            msgs = s.trajectory.messages if s.trajectory else []
            tools = s.trajectory.tool_calls if s.trajectory else []
            reviews = s.trajectory.reviews if s.trajectory else []
            if (
                len(msgs) < min_messages
                and len(tools) == 0
                and len(reviews) == 0
                and self.delete_session(s.session_id)
            ):
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
                    text("SELECT id FROM sessions WHERE user_id = :uid AND updated_at < :cutoff"),
                    {"uid": user_id, "cutoff": cutoff},
                ).fetchall()
                session_ids_to_prune = [str(r[0]) for r in rows]

                if session_ids_to_prune:
                    db.execute(
                        text(
                            "DELETE FROM session_events WHERE user_id = :uid AND session_id = ANY(:ids)"
                        ),
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
            self._record_db_failure("session pruning", e)

        with self._lock:
            for _key, session in list(self._sessions.items()):
                created_str = getattr(session, "created_at", None) or getattr(
                    session, "updated_at", None
                )
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
        max_idx = -1
        try:
            if db is not None:
                user_id = get_current_user_id(db)
                row = db.execute(text(sql), {"session_id": session_id, "uid": user_id}).fetchone()
            else:
                with SessionLocal() as sdb:
                    user_id = get_current_user_id(sdb)
                    row = sdb.execute(
                        text(sql), {"session_id": session_id, "uid": user_id}
                    ).fetchone()
            max_idx = int(row[0]) if row and row[0] is not None else -1
        except Exception:
            max_idx = self._step_indices.get(key, -1)

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
            payload_data = (
                redact_payload(payload_data) if isinstance(payload_data, dict) else payload_data
            )
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

        try:
            if db is not None:
                _execute_append(db)
            else:
                with SessionLocal() as active_db:
                    _execute_append(active_db)
                    active_db.commit()
        except Exception as e:
            self._record_db_failure("append_event", e)

        key = self.memory_key(event.session_id)
        with self._lock:
            self._dirty_sessions.add(key)

    async def arecord_decision(self, review: ReviewRecord) -> ReviewRecord:
        """Asynchronously record a security/policy review decision with Redis distributed mutex and atomic JSONB append."""
        from server.event_bus import get_event_bus

        user_id = current_user_id.get()
        if user_id is None:
            with contextlib.suppress(Exception):
                user_id = get_current_user_id()
            if user_id is None:
                user_id = 1

        async with get_event_bus().session_lock(user_id, review.session_id, timeout=5.0):
            return self._apply_record_decision(review, user_id, publish_async=True)

    def record_decision_sync(self, review: ReviewRecord) -> ReviewRecord:
        """Synchronously record a security/policy review decision with Redis distributed mutex and atomic JSONB append."""
        from server.event_bus import get_event_bus

        user_id = current_user_id.get()
        if user_id is None:
            with contextlib.suppress(Exception):
                user_id = get_current_user_id()
            if user_id is None:
                user_id = 1

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

        # Calibrated prefill telemetry is serialized on the record AND mirrored into
        # metadata so it survives every persistence path (relational row + JSONB payload).
        if review.calibrated_telemetry is not None:
            review.metadata = review.metadata or {}
            review.metadata.setdefault(
                "calibrated_telemetry", review.calibrated_telemetry.model_dump()
            )
            review.metadata.setdefault(
                "deep_review_status", review.calibrated_telemetry.deep_review_status
            )

        redacted_review_dump = redact_payload(review.model_dump())
        tool_input_redacted = redact_text(review.tool_input or "")
        explanation_redacted = redact_text(review.explanation or "")
        diff_redacted = redact_text(review.diff or "")
        rule_name = str(review.rule_name or getattr(review, "threat_category", "") or "")
        req_id = getattr(review, "request_id", None)
        uid = user_id if user_id is not None else 1

        is_blocked = (
            review.decision in ("block", "deny", "reject", "escalate")
            or (review.score is not None and review.score >= 8)
        ) and getattr(review, "human_override", None) != "allow"

        try:
            with SessionLocal() as db, db.begin():
                if user_id is not None:
                    uid = user_id
                else:
                    with contextlib.suppress(Exception):
                        uid = get_current_user_id(db)

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
                        SET payload = CASE
                            WHEN :is_blocked THEN jsonb_set(
                                jsonb_set(
                                    CASE
                                        WHEN payload IS NULL THEN '{"trajectory": {}}'::jsonb
                                        WHEN payload->'trajectory' IS NULL THEN jsonb_set(payload, '{trajectory}', '{}'::jsonb)
                                        ELSE payload
                                    END,
                                    '{trajectory,reviews}',
                                    COALESCE(payload->'trajectory'->'reviews', '[]'::jsonb) || CAST(:new_review_json AS jsonb)
                                ),
                                '{status}',
                                '"blocked"'::jsonb
                            )
                            ELSE jsonb_set(
                                CASE
                                    WHEN payload IS NULL THEN '{"trajectory": {}}'::jsonb
                                    WHEN payload->'trajectory' IS NULL THEN jsonb_set(payload, '{trajectory}', '{}'::jsonb)
                                    ELSE payload
                                END,
                                '{trajectory,reviews}',
                                COALESCE(payload->'trajectory'->'reviews', '[]'::jsonb) || CAST(:new_review_json AS jsonb)
                            )
                        END,
                        status = CASE WHEN :is_blocked THEN 'blocked' ELSE status END,
                        updated_at = NOW()
                        WHERE user_id = :user_id AND id = :session_id;
                        """
                    ),
                    {
                        "new_review_json": new_review_json,
                        "is_blocked": is_blocked,
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
                            SET payload = CASE
                                WHEN :is_blocked THEN jsonb_set(
                                    jsonb_set(
                                        CASE
                                            WHEN payload IS NULL THEN '{"trajectory": {}}'::jsonb
                                            WHEN payload->'trajectory' IS NULL THEN jsonb_set(payload, '{trajectory}', '{}'::jsonb)
                                            ELSE payload
                                        END,
                                        '{trajectory,reviews}',
                                        COALESCE(payload->'trajectory'->'reviews', '[]'::jsonb) || CAST(:new_review_json AS jsonb)
                                    ),
                                    '{status}',
                                    '"blocked"'::jsonb
                                )
                                ELSE jsonb_set(
                                    CASE
                                        WHEN payload IS NULL THEN '{"trajectory": {}}'::jsonb
                                        WHEN payload->'trajectory' IS NULL THEN jsonb_set(payload, '{trajectory}', '{}'::jsonb)
                                        ELSE payload
                                    END,
                                    '{trajectory,reviews}',
                                    COALESCE(payload->'trajectory'->'reviews', '[]'::jsonb) || CAST(:new_review_json AS jsonb)
                                )
                            END,
                            status = CASE WHEN :is_blocked THEN 'blocked' ELSE status END,
                            updated_at = NOW()
                            WHERE user_id = :user_id AND id = :session_id;
                            """
                        ),
                        {
                            "new_review_json": new_review_json,
                            "is_blocked": is_blocked,
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
        except Exception as e:
            self._record_db_failure("_apply_record_decision DB write", e)

        # 4. Update in-memory session cache directly
        with self._lock:
            key = self.memory_key(review.session_id)
            cached = self._sessions.get(key)
            if cached is None:
                cached = self.ensure_live_session(review.session_id)
            if not any(r.id == review.id for r in cached.trajectory.reviews):
                cached.trajectory.reviews.append(review)
            if is_blocked:
                cached.status = "blocked"
            if key in self._reviews:
                if not any(r.id == review.id for r in self._reviews[key]):
                    self._reviews[key].append(review)
            else:
                self._reviews[key] = [review]

            # Also persist to disk json cache
            try:
                target = self.storage_dir / f"{cached.session_id}.json"
                tmp_target = self.storage_dir / f"{cached.session_id}.tmp"
                tmp_target.write_text(cached.model_dump_json(indent=2), encoding="utf-8")
                os.replace(tmp_target, target)
            except Exception:
                pass

        # 5. Broadcast to local sync subscribers and Redis Streams
        self.broadcast_sync(review.session_id, "review_decision", redacted_review_dump)
        get_event_bus().xadd_sync("review_decision", redacted_review_dump, user_id=uid)

        # 6. Trigger materialization: synchronously if terminal or blocked, asynchronously with debounce otherwise
        terminal_statuses = {
            "completed",
            "failed",
            "aborted",
            "lockout",
            "error",
            "cancelled",
            "blocked",
        }
        cached_sess = self._cached_session(review.session_id)
        if is_blocked or (cached_sess and getattr(cached_sess, "status", "") in terminal_statuses):
            self.materialize_session(review.session_id)
        else:
            self.schedule_materialize(review.session_id)

        return AwaitableReviewRecord.model_validate(review.model_dump())

    def _memory_keys_for_session(self, session_id: str) -> list[str]:
        """Cache keys for a session, including tenant-scoped keys in unbound contexts.

        ``record_decision`` may resolve ``get_current_user_id()`` (e.g. owner -> 1)
        while a background task runs with an unbound ContextVar. In that case the
        in-memory key is ``"1:<session>"``; this helper keeps reads working.
        """
        key = self.memory_key(session_id)
        keys = [key]
        if current_user_id.get() is None:
            suffix = f":{session_id}"
            for candidate in (*self._reviews.keys(), *self._sessions.keys()):
                if candidate.endswith(suffix) and candidate not in keys:
                    keys.append(candidate)
        return keys

    def _find_review(
        self, review_id: str, session_id: str | None = None
    ) -> tuple[ReviewRecord | None, str | None]:
        """Locate a review in memory (hot path) or fall back to the relational row."""
        with self._lock:
            keys = (
                self._memory_keys_for_session(session_id)
                if session_id
                else list(self._reviews.keys())
            )
            for key in keys:
                for rev in self._reviews.get(key, []):
                    if rev.id == review_id:
                        return rev, key
            session_keys = (
                self._memory_keys_for_session(session_id)
                if session_id
                else list(self._sessions.keys())
            )
            for key in session_keys:
                cached = self._sessions.get(key)
                if cached is None:
                    continue
                for rev in cached.trajectory.reviews:
                    if rev.id == review_id:
                        return rev, key

        try:
            with SessionLocal() as db:
                uid = get_current_user_id(db)
                row = db.execute(
                    text(
                        """
                        SELECT id, session_id, tool_name, tool_input, decision, score,
                               stage, rule_name, explanation, diff, latency_ms, created_at,
                               request_id
                        FROM reviews
                        WHERE id = :rid AND user_id = :uid
                        """
                    ),
                    {"rid": review_id, "uid": uid},
                ).fetchone()
                if row is None:
                    return None, None
                rev = review_from_row(row)
        except Exception as exc:
            self._record_db_failure("_find_review", exc)
            return None, None

        key = self.memory_key(rev.session_id)
        with self._lock:
            if not any(r.id == rev.id for r in self._reviews.get(key, [])):
                self._reviews.setdefault(key, []).append(rev)
        return rev, key

    def update_review_deep_audit(
        self,
        review_id: str,
        explanation: str,
        score: int,
        stage: str,
        deep_review_status: str,
        metadata_patch: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        calibrated_telemetry: CalibratedDecisionTelemetry | dict[str, Any] | None = None,
    ) -> ReviewRecord | None:
        """Enrich a ReviewRecord after asynchronous Tier 3 deep review completes.

        Updates the relational row, the JSONB review snapshot, the in-memory cache,
        the on-disk session mirror, and emits an immutable ``deep_review_audit`` event.
        Returns the updated record, or ``None`` when the review is unknown.
        """
        from server.event_bus import get_event_bus

        target, key = self._find_review(review_id, session_id)
        if target is None:
            logger.debug("update_review_deep_audit: review %s not found", review_id)
            return None

        allowed_statuses = {"not_needed", "pending_async", "completed", "failed"}
        status = deep_review_status if deep_review_status in allowed_statuses else "completed"
        target.explanation = explanation or target.explanation
        target.score = max(1, min(10, int(score)))
        target.stage = cast(
            ReviewStage,
            stage if stage in ("rule", "threshold", "triage", "deep_review") else "deep_review",
        )
        target.metadata = dict(target.metadata or {})
        if metadata_patch:
            target.metadata.update(metadata_patch)
        target.metadata["deep_review_status"] = status

        telemetry: CalibratedDecisionTelemetry | None = target.calibrated_telemetry
        if isinstance(calibrated_telemetry, dict):
            with contextlib.suppress(Exception):
                telemetry = CalibratedDecisionTelemetry.model_validate(calibrated_telemetry)
        elif isinstance(calibrated_telemetry, CalibratedDecisionTelemetry):
            telemetry = calibrated_telemetry
        if telemetry is not None:
            telemetry.deep_review_status = cast(Any, status)
            target.calibrated_telemetry = telemetry
            target.metadata["calibrated_telemetry"] = telemetry.model_dump()

        patched_json = json.dumps(_to_jsonable(target.model_dump()))
        try:
            with SessionLocal() as db:
                uid = get_current_user_id(db)
                db.execute(
                    text(
                        """
                        UPDATE reviews
                        SET score = :score, stage = :stage, explanation = :explanation
                        WHERE id = :id AND user_id = :uid
                        """
                    ),
                    {
                        "score": target.score,
                        "stage": target.stage,
                        "explanation": target.explanation,
                        "id": target.id,
                        "uid": uid,
                    },
                )
                db.execute(
                    text(
                        """
                        UPDATE sessions
                        SET payload = jsonb_set(
                                payload,
                                '{trajectory,reviews}',
                                COALESCE(
                                    (
                                        SELECT jsonb_agg(
                                            CASE
                                                WHEN elem->>'id' = :rid
                                                THEN CAST(:patched AS jsonb)
                                                ELSE elem
                                            END
                                        )
                                        FROM jsonb_array_elements(
                                            payload->'trajectory'->'reviews'
                                        ) AS elem
                                    ),
                                    payload->'trajectory'->'reviews'
                                ),
                                false
                            ),
                            updated_at = NOW()
                        WHERE user_id = :uid
                          AND id = :sid
                          AND payload->'trajectory'->'reviews' IS NOT NULL
                        """
                    ),
                    {
                        "rid": target.id,
                        "patched": patched_json,
                        "uid": uid,
                        "sid": target.session_id,
                    },
                )
                db.commit()
        except Exception as exc:
            self._record_db_failure("update_review_deep_audit", exc)

        with self._lock:
            cache_key = key or self.memory_key(target.session_id)
            cached = self._sessions.get(cache_key)
            if cached is not None:
                replaced = False
                for idx, rev in enumerate(cached.trajectory.reviews):
                    if rev.id == target.id:
                        cached.trajectory.reviews[idx] = target
                        replaced = True
                        break
                if not replaced:
                    cached.trajectory.reviews.append(target)
                try:
                    target_path = self.storage_dir / f"{cached.session_id}.json"
                    tmp_target = self.storage_dir / f"{cached.session_id}.tmp"
                    tmp_target.write_text(cached.model_dump_json(indent=2), encoding="utf-8")
                    os.replace(tmp_target, target_path)
                except Exception:
                    pass
            if key is not None:
                for idx, rev in enumerate(self._reviews.get(key, [])):
                    if rev.id == target.id:
                        self._reviews[key][idx] = target
                        break

        payload = target.model_dump()
        try:
            step_idx = self._get_next_step_index(target.session_id)
            event = SessionEventRecord(
                id=str(uuid4()),
                user_id=current_user_id.get() or 1,
                session_id=target.session_id,
                step_index=step_idx,
                event_type="deep_review_audit",
                payload=payload,
            )
            self.append_event(event)
        except Exception as exc:
            self._record_db_failure("append deep_review_audit event", exc)

        self.broadcast_sync(target.session_id, "review_deep_audit", payload)
        with contextlib.suppress(Exception):
            get_event_bus().xadd_sync("review_deep_audit", payload, user_id=current_user_id.get())

        return target

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

    def get_session_decisions(self, session_id: str) -> list[ReviewRecord]:
        """Get all review decisions for a given session."""
        with self._lock:
            for key in self._memory_keys_for_session(session_id):
                if self._reviews.get(key) and self._cache_visible(
                    key, self._sessions.get(key) or Session(session_id=session_id)
                ):
                    return list(self._reviews[key])
            for key in self._memory_keys_for_session(session_id):
                session = self._sessions.get(key)
                if (
                    session
                    and self._cache_visible(key, session)
                    and session.trajectory
                    and session.trajectory.reviews
                ):
                    return list(session.trajectory.reviews)

        try:
            with SessionLocal() as db:
                user_id = get_current_user_id(db)
                rows = db.execute(
                    text(
                        """
                        SELECT r.id, r.session_id, r.tool_name, r.tool_input, r.decision,
                               r.score, r.stage, r.rule_name, r.explanation, r.diff,
                               r.latency_ms, r.created_at, s.payload
                        FROM reviews r
                        LEFT JOIN sessions s
                            ON r.user_id = s.user_id AND r.session_id = s.id
                        WHERE r.session_id = :session_id AND r.user_id = :uid
                        ORDER BY r.created_at ASC
                        """
                    ),
                    {"session_id": session_id, "uid": user_id},
                ).fetchall()

                # The JSONB payload keeps the fully-typed record (calibrated telemetry,
                # provenance, consent disclosure). ``review_from_row`` overlays it onto
                # the relational columns, including a JSON payload in the last slot.
                return [review_from_row(r) for r in rows]
        except Exception as e:
            self._record_db_failure("get_session_decisions", e)
            sess = self.get_session(session_id)
            if sess and sess.trajectory and sess.trajectory.reviews:
                return list(sess.trajectory.reviews)
            return []

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

        user_id = current_user_id.get() or 1
        try:
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
        except Exception as e:
            self._record_db_failure("resolve_decision", e)

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
        user_id = current_user_id.get() or 1
        try:
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
        except Exception as e:
            self._record_db_failure("append_trajectory_event", e)

        # Publish to Redis Pub/Sub bus
        get_event_bus().publish_sync(kind, redacted_event_dump, user_id=user_id)

        # Schedule debounced materialization (replaces immediate full sessions.payload rewrite)
        self.schedule_materialize(session_id)

    def get_trajectory(self, session_id: str) -> Trajectory | None:
        """Get the full trajectory for a session."""
        session = self.get_session(session_id)
        return session.trajectory if session else None

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
        """Persist user policies, configs, and custom grader prompts to disk.

        Each file is written to a unique temp path (pid + random suffix) before the
        atomic ``os.replace``. A shared ``.tmp`` name would let concurrent writers
        clobber each other mid-write and publish a truncated JSON file.
        """

        def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, path)

        try:
            pol_path = self.storage_dir / "user_policies.json"
            pol_data = {str(uid): p.model_dump() for uid, p in self._user_policies.items()}
            _atomic_write(pol_path, pol_data)

            cfg_path = self.storage_dir / "user_configs.json"
            cfg_data = {str(uid): c.model_dump() for uid, c in self._user_configs.items()}
            _atomic_write(cfg_path, cfg_data)

            graders_path = self.storage_dir / "user_graders.json"
            graders_data = {str(uid): g for uid, g in self._user_graders.items()}
            _atomic_write(graders_path, graders_data)
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
                    locked_instructions="",
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
                locked_instructions="",
            )
            self._save_user_settings_to_disk()
            return self._user_policies[uid]

    def get_organization(
        self, org_id: str = "org_default", user_id: int | None = None
    ) -> Organization:
        """Retrieve Organization entity with its active policy and locked custom instructions."""
        policy = self.get_policy(user_id)
        return Organization(
            org_id=org_id,
            name=org_id,
            display_name="Engineering Security",
            active_policy_id=policy.policy_id,
            locked_instructions=policy.locked_instructions or "",
        )

    def get_custom_instructions(
        self, org_id: str = "org_default", user_id: int | None = None
    ) -> str:
        """Retrieve custom natural-language organization policy instructions."""
        policy = self.get_policy(user_id)
        return policy.locked_instructions or ""

    def set_custom_instructions(
        self,
        instructions: str,
        org_id: str = "org_default",
        user_id: int | None = None,
    ) -> dict[str, Any]:
        """Update custom natural-language organization policy instructions."""
        policy = self.get_policy(user_id)
        with self._lock:
            policy.locked_instructions = str(instructions or "")
            self._save_user_settings_to_disk()

        from server.policy_gateway import PolicyGateway

        PolicyGateway.reset_shared()
        now_iso = datetime.now(UTC).isoformat()
        return {"instructions": policy.locked_instructions, "updated_at": now_iso}

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
        """Retrieve strictly the 11 active Canonical Graders, applying user-specific prompt customizations if any."""
        from engine.graders_suite import list_canonical_graders

        uid = self._resolve_user_id(user_id)
        base_graders = list_canonical_graders(from_factory=True)
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
            # Legacy Threat Monitors & Task Scorers -> Closest Active Judges
            # CredentialsGrader -> PolicyGatewayDeepGrader
            "credentialsgrader": "PolicyGatewayDeepGrader",
            "credentials_grader": "PolicyGatewayDeepGrader",
            "credentials_and_api_keys": "PolicyGatewayDeepGrader",
            "credentials": "PolicyGatewayDeepGrader",
            "data_exfiltration": "PolicyGatewayDeepGrader",
            # PIIGrader -> PolicyGatewayDeepGrader
            "piigrader": "PolicyGatewayDeepGrader",
            "pii_grader": "PolicyGatewayDeepGrader",
            "pii_and_sensitive_info": "PolicyGatewayDeepGrader",
            "pii_and_sensitive_information": "PolicyGatewayDeepGrader",
            "pii": "PolicyGatewayDeepGrader",
            "privacy_and_pii": "PolicyGatewayDeepGrader",
            "privacy": "PolicyGatewayDeepGrader",
            # InstructionGrader -> PlanAdherenceGrader
            "instructiongrader": "PlanAdherenceGrader",
            "instruction_grader": "PlanAdherenceGrader",
            "instruction_and_policy_adherence": "PlanAdherenceGrader",
            "instruction": "PlanAdherenceGrader",
            "instruction_following": "PlanAdherenceGrader",
            # SecurityGrader -> PolicyGatewayDeepGrader
            "securitygrader": "PolicyGatewayDeepGrader",
            "security_grader": "PolicyGatewayDeepGrader",
            "code_security": "PolicyGatewayDeepGrader",
            "security": "PolicyGatewayDeepGrader",
            # DeletionGrader -> PolicyGatewayDeepGrader
            "deletiongrader": "PolicyGatewayDeepGrader",
            "deletion_grader": "PolicyGatewayDeepGrader",
            "infrastructure_safety": "PolicyGatewayDeepGrader",
            "deletion": "PolicyGatewayDeepGrader",
            "destructive_deletions": "PolicyGatewayDeepGrader",
            # AlignmentFakingGrader -> SituationalAwarenessGrader
            "alignmentfakinggrader": "SituationalAwarenessGrader",
            "alignment_faking_grader": "SituationalAwarenessGrader",
            "alignment_faking": "SituationalAwarenessGrader",
            "alignmentfaking": "SituationalAwarenessGrader",
            "strategic_compliance": "SituationalAwarenessGrader",
            # CitationGroundingGrader -> HallucinationGrader
            "citationgroundinggrader": "HallucinationGrader",
            "citation_grounding_grader": "HallucinationGrader",
            "citation_grounding": "HallucinationGrader",
            "citationgrounding": "HallucinationGrader",
            "grounding_and_citation": "HallucinationGrader",
            "citation_grounding_scorer": "HallucinationGrader",
            # Active 11 Platform Judges
            # 1. SituationalAwarenessGrader
            "situational_awareness": "SituationalAwarenessGrader",
            "situationalawareness": "SituationalAwarenessGrader",
            "situational_awareness_grader": "SituationalAwarenessGrader",
            "situationalawarenessgrader": "SituationalAwarenessGrader",
            "sandbagging": "SituationalAwarenessGrader",
            "eval_awareness": "SituationalAwarenessGrader",
            # 2. GoalGuardingSchemingGrader
            "goal_guarding_scheming": "GoalGuardingSchemingGrader",
            "goal_guarding": "GoalGuardingSchemingGrader",
            "goalguardingscheming": "GoalGuardingSchemingGrader",
            "goal_guarding_scheming_grader": "GoalGuardingSchemingGrader",
            "scheming": "GoalGuardingSchemingGrader",
            "goalguarding": "GoalGuardingSchemingGrader",
            # 3. UnfaithfulCoTGrader
            "unfaithful_cot": "UnfaithfulCoTGrader",
            "unfaithfulcot": "UnfaithfulCoTGrader",
            "unfaithful_cot_grader": "UnfaithfulCoTGrader",
            "cot_faithfulness": "UnfaithfulCoTGrader",
            "unfaithful_chain_of_thought": "UnfaithfulCoTGrader",
            # 4. PlanAdherenceGrader
            "plan_adherence": "PlanAdherenceGrader",
            "planadherence": "PlanAdherenceGrader",
            "plan_adherence_grader": "PlanAdherenceGrader",
            "planadherencegrader": "PlanAdherenceGrader",
            "plan": "PlanAdherenceGrader",
            # 5. HallucinationGrader
            "hallucination": "HallucinationGrader",
            "hallucinations": "HallucinationGrader",
            "hallucinationgrader": "HallucinationGrader",
            "hallucination_grader": "HallucinationGrader",
            "hallucination_detection": "HallucinationGrader",
            "grounding": "HallucinationGrader",
            "hallucination_and_grounding": "HallucinationGrader",
            # 6. RewardTamperingGrader
            "reward_tampering": "RewardTamperingGrader",
            "rewardtampering": "RewardTamperingGrader",
            "reward_tampering_grader": "RewardTamperingGrader",
            "rewardtamperinggrader": "RewardTamperingGrader",
            "tampering": "RewardTamperingGrader",
            "test_tampering": "RewardTamperingGrader",
            "sabotage": "RewardTamperingGrader",
            # 7. RewardSeekingGrader
            "reward_seeking": "RewardSeekingGrader",
            "rewardseeking": "RewardSeekingGrader",
            "reward_seeking_grader": "RewardSeekingGrader",
            "specification_gaming": "RewardSeekingGrader",
            # PolicyGatewayFastTriageGrader
            "policy_gateway_fast_triage": "PolicyGatewayFastTriageGrader",
            "fast_triage": "PolicyGatewayFastTriageGrader",
            "policy_gateway_fast_triage_grader": "PolicyGatewayFastTriageGrader",
            "fasttriage": "PolicyGatewayFastTriageGrader",
            "triage_grader": "PolicyGatewayFastTriageGrader",
            # PolicyGatewayDeepGrader
            "policy_gateway": "PolicyGatewayDeepGrader",
            "policygateway": "PolicyGatewayDeepGrader",
            "policy_gateway_deep": "PolicyGatewayDeepGrader",
            "policy_gateway_deep_grader": "PolicyGatewayDeepGrader",
            "policygatewaydeepgrader": "PolicyGatewayDeepGrader",
            "policy_gateway_grader": "PolicyGatewayDeepGrader",
            "gateway": "PolicyGatewayDeepGrader",
            "deep_review": "PolicyGatewayDeepGrader",
            # SemanticRunComparatorGrader
            "semantic_run_comparator": "SemanticRunComparatorGrader",
            "semantic_compare": "SemanticRunComparatorGrader",
            "semantic_run_comparator_grader": "SemanticRunComparatorGrader",
            "run_comparator": "SemanticRunComparatorGrader",
            "semantic_trajectory_compare": "SemanticRunComparatorGrader",
            # IncidentRemediationGrader
            "incident_remediation": "IncidentRemediationGrader",
            "incident_remediation_grader": "IncidentRemediationGrader",
            "remediation_grader": "IncidentRemediationGrader",
            "incident_remediator": "IncidentRemediationGrader",
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
        """Update prompt components for a specific grader for this user and live engine."""
        uid = self._resolve_user_id(user_id)
        from engine.graders_suite import (
            CanonicalGrader,
            get_canonical_grader,
            update_live_canonical_grader,
        )

        base = get_canonical_grader(grader_name)
        if not base:
            return None

        valid_fields = set(CanonicalGrader.model_fields.keys())
        filtered = {k: v for k, v in updates.items() if k in valid_fields and isinstance(v, str)}

        # Update live canonical grader in the engine registry
        update_live_canonical_grader(grader_name, filtered)

        with self._lock:
            if uid not in self._user_graders:
                self._user_graders[uid] = {}
            if grader_name not in self._user_graders[uid]:
                self._user_graders[uid][grader_name] = {}
            self._user_graders[uid][grader_name].update(filtered)
            self._save_user_settings_to_disk()

        return self.get_grader_definition(grader_name, uid)

    def reset_grader_definition(self, grader_name: str, user_id: int | None = None) -> Any | None:
        """Reset a grader's prompt components back to defaults for this user and live engine."""
        uid = self._resolve_user_id(user_id)
        from engine.graders_suite import reset_live_canonical_grader

        # Reset live canonical grader in the engine registry
        reset_live_canonical_grader(grader_name)

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
