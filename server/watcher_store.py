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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import text

from schemas.watcher_models import (
    DEFAULT_COMMAND_RULES,
    DEFAULT_TOOL_THRESHOLDS,
    AgentType,
    Message,
    Policy,
    ReviewRecord,
    Session,
    ToolCall,
    ToolResult,
    ToolThreshold,
    Trajectory,
)
from server.db import SessionLocal, get_current_user_id

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
            try:
                rows = result.fetchall()
            except Exception:
                rows = []
            return CompatResult(rows)


class WatcherStore:
    """PostgreSQL 16 multi-tenant store for sessions, trajectories, and reviews."""

    def __init__(self, storage_dir: Path | None = None, db_path: str | None = None) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self.storage_dir = storage_dir or Path(os.getenv("WATCHER_STORAGE_DIR", ".runs/watcher"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # In-memory cache dictionaries for fast access and backward compatibility
        self._sessions: dict[str, Session] = {}
        self._trajectories: dict[str, Trajectory] = {}
        self._reviews: dict[str, list[ReviewRecord]] = {}

        # Default MDM Policy (in-memory runtime)
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

    def ensure_live_session(
        self,
        session_id: str,
        *,
        agent_type: str = "antigravity",
        title: str | None = None,
        working_dir: str | None = None,
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
        return self.create_session(session)

    def _write_session_to_db(self, session: Session) -> None:
        """Write session and trajectory reviews to PostgreSQL and disk cache."""
        payload = session.model_dump()
        payload_json = json.dumps(_to_jsonable(payload))

        with SessionLocal() as db:
            user_id = get_current_user_id(db)
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
                    ON CONFLICT (id) DO UPDATE SET
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
                        updated_at = NOW();
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
                    "title": session.title or "",
                    "working_dir": session.working_dir or "",
                    "current_activity": session.current_activity or "",
                    "failure_reason": session.failure_reason or "",
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
                db.execute(
                    text(
                        """
                        INSERT INTO reviews (
                            id, user_id, session_id, tool_name, tool_input,
                            decision, score, stage, rule_name, explanation,
                            diff, latency_ms, created_at
                        ) VALUES (
                            :id, :user_id, :session_id, :tool_name, :tool_input,
                            :decision, :score, :stage, :rule_name, :explanation,
                            :diff, :latency_ms, NOW()
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            decision = EXCLUDED.decision,
                            score = EXCLUDED.score,
                            explanation = EXCLUDED.explanation;
                        """
                    ),
                    {
                        "id": rev.id,
                        "user_id": user_id,
                        "session_id": session.session_id,
                        "tool_name": rev.tool_name,
                        "tool_input": rev.tool_input or "",
                        "decision": rev.decision,
                        "score": rev.score,
                        "stage": rev.stage,
                        "rule_name": rule_name,
                        "explanation": rev.explanation or "",
                        "diff": rev.diff or "",
                        "latency_ms": rev.latency_ms,
                    },
                )

            db.commit()

        # Update disk JSON cache for file-based tools / inspection
        try:
            target = self.storage_dir / f"{session.session_id}.json"
            target.write_text(session.model_dump_json(indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to write session file %s: %s", session.session_id, e)

        with self._lock:
            self._sessions[session.session_id] = session
            self._trajectories[session.session_id] = session.trajectory
            self._reviews[session.session_id] = session.trajectory.reviews

    def create_session(self, session: Session) -> Session:
        """Register a new session in PostgreSQL for the active user."""
        self._write_session_to_db(session)
        self.broadcast_sync(session.session_id, "session_created", session.model_dump())
        return session

    def record_session(self, session: Session) -> Session:
        """Register or update a session in PostgreSQL for the active user."""
        self._write_session_to_db(session)
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a session by ID scoped to the active tenant."""
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
                return Session.model_validate(payload)
            except Exception as e:
                logger.warning("Error validating session model %s: %s", session_id, e)
                return None

    def update_session(self, session_id: str, updates: dict[str, Any]) -> Session | None:
        """Update fields on an existing session."""
        session = self.get_session(session_id)
        if not session:
            return None

        for k, v in updates.items():
            if hasattr(session, k):
                setattr(session, k, v)

        session.updated_at = datetime.now(UTC).isoformat()
        self._write_session_to_db(session)
        self.broadcast_sync(session_id, "session_updated", _to_jsonable(updates))
        return session

    def list_sessions(
        self,
        agent_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Session]:
        """List sessions for the current user, optionally filtered."""
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
            return results

    def clear_all_sessions(self) -> None:
        """Purge all sessions and reviews for the active tenant."""
        with SessionLocal() as db:
            user_id = get_current_user_id(db)
            db.execute(text("DELETE FROM sessions WHERE user_id = :uid"), {"uid": user_id})
            db.commit()
        with self._lock:
            self._sessions.clear()
            self._trajectories.clear()
            self._reviews.clear()

    def delete_session(self, session_id: str) -> bool:
        """Delete a single session for the active tenant."""
        with SessionLocal() as db:
            user_id = get_current_user_id(db)
            res = db.execute(
                text("DELETE FROM sessions WHERE id = :id AND user_id = :uid"),
                {"id": session_id, "uid": user_id},
            )
            db.commit()
            deleted = (res.rowcount or 0) > 0
        with self._lock:
            self._sessions.pop(session_id, None)
            self._trajectories.pop(session_id, None)
            self._reviews.pop(session_id, None)
        return deleted

    def purge_empty_sessions(self, min_messages: int = 1) -> list[str]:
        """Remove sessions that have fewer than min_messages."""
        purged: list[str] = []
        for s in self.list_sessions(limit=500):
            msgs = s.trajectory.messages if s.trajectory else []
            if len(msgs) < min_messages and self.delete_session(s.session_id):
                purged.append(s.session_id)
        return purged

    # -------------------------------------------------------------------------
    # Reviews & Decisions
    # -------------------------------------------------------------------------

    def record_decision(self, review: ReviewRecord) -> ReviewRecord:
        """Record a security/policy review decision on a session."""
        with SessionLocal() as db:
            user_id = get_current_user_id(db)
            rule_name = str(review.rule_name or getattr(review, "threat_category", "") or "")
            db.execute(
                text(
                    """
                    INSERT INTO reviews (
                        id, user_id, session_id, tool_name, tool_input,
                        decision, score, stage, rule_name, explanation,
                        diff, latency_ms, created_at
                    ) VALUES (
                        :id, :user_id, :session_id, :tool_name, :tool_input,
                        :decision, :score, :stage, :rule_name, :explanation,
                        :diff, :latency_ms, NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        decision = EXCLUDED.decision,
                        score = EXCLUDED.score,
                        explanation = EXCLUDED.explanation;
                    """
                ),
                {
                    "id": review.id,
                    "user_id": user_id,
                    "session_id": review.session_id,
                    "tool_name": review.tool_name,
                    "tool_input": review.tool_input or "",
                    "decision": review.decision,
                    "score": review.score,
                    "stage": review.stage,
                    "rule_name": rule_name,
                    "explanation": review.explanation or "",
                    "diff": review.diff or "",
                    "latency_ms": review.latency_ms,
                },
            )
            db.commit()

        # Update session payload in background/inline
        session = self.get_session(review.session_id)
        if session:
            session.trajectory.reviews.append(review)
            self.record_session(session)

        self.broadcast_sync(review.session_id, "review_decision", review.model_dump())
        return review

    def get_session_decisions(self, session_id: str) -> list[ReviewRecord]:
        """Get all review decisions for a given session."""
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
                        decision=str(r[4]),
                        score=int(r[5] or 1),
                        stage=str(r[6] or "gate"),
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

        # Update session model
        session = self.get_session(session_id)
        if session:
            for rev in session.trajectory.reviews:
                if rev.id == target_review.id:
                    rev.decision = target_review.decision
                    rev.explanation = target_review.explanation
            self.record_session(session)

        self.broadcast_sync(session_id, "decision_resolved", target_review.model_dump())
        return target_review

    # -------------------------------------------------------------------------
    # Trajectory Events
    # -------------------------------------------------------------------------

    def append_trajectory_event(
        self,
        session_id: str,
        event: Message | ToolCall | ToolResult,
    ) -> None:
        """Append a message, tool call, or tool result to a session trajectory."""
        session = self.get_session(session_id)
        if not session:
            return

        kind = "message"
        if isinstance(event, Message):
            kind = "message"
            session.trajectory.messages.append(event)
            self.broadcast_sync(session_id, "message", event.model_dump())
        elif isinstance(event, ToolCall):
            kind = "tool_call"
            session.trajectory.tool_calls.append(event)
            session.current_activity = f"Call {event.tool_name}"
            self.broadcast_sync(session_id, "tool_call", event.model_dump())
        elif isinstance(event, ToolResult):
            kind = "tool_result"
            session.trajectory.tool_results.append(event)
            self.broadcast_sync(session_id, "tool_result", event.model_dump())

        # Save to trajectory_events table and update session payload
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
                    "payload": json.dumps(_to_jsonable(event.model_dump())),
                },
            )
            db.commit()

        self.record_session(session)

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
    # Policy Management
    # -------------------------------------------------------------------------

    def get_default_policy(self) -> Policy:
        """Retrieve the active default policy."""
        return self._policy

    def reset_policy_to_defaults(self) -> Policy:
        """Restore command rules and tool thresholds to built-in defaults."""
        self._policy = Policy(
            policy_id=self._policy.policy_id,
            name=self._policy.name,
            org_id=self._policy.org_id,
            posture=self._policy.posture,
            command_rules=[r.model_copy() for r in DEFAULT_COMMAND_RULES],
            tool_thresholds=[t.model_copy() for t in DEFAULT_TOOL_THRESHOLDS],
        )
        return self._policy

    def update_command_rule(
        self, rule_name: str, action: Literal["allow", "triage", "human", "deny", "off"]
    ) -> bool:
        """Update action for a command rule."""
        for r in self._policy.command_rules:
            if r.name == rule_name:
                r.action = action
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
    ) -> bool:
        """Update thresholds for a tool."""
        for t in self._policy.tool_thresholds:
            if t.tool_name == tool_name:
                t.auto_approve = auto_approve
                t.escalate_ge = escalate_ge
                t.auto_deny_ge = auto_deny_ge
                t.always_escalate = always_escalate
                t.auto_approve_le = auto_approve_le
                return True
        self._policy.tool_thresholds.append(
            ToolThreshold(
                tool_name=tool_name,
                auto_approve=auto_approve,
                escalate_ge=escalate_ge,
                auto_deny_ge=auto_deny_ge,
                always_escalate=always_escalate,
                auto_approve_le=auto_approve_le,
            )
        )
        return True

    def update_tool_thresholds(self, thresholds: list[ToolThreshold]) -> bool:
        """Bulk update tool thresholds."""
        for incoming in thresholds:
            self.update_tool_threshold(
                tool_name=incoming.tool_name,
                auto_approve=incoming.auto_approve,
                escalate_ge=incoming.escalate_ge,
                auto_deny_ge=incoming.auto_deny_ge,
                always_escalate=incoming.always_escalate,
                auto_approve_le=incoming.auto_approve_le,
            )
        return True

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

        payload = {"event": event_type, "data": data, "timestamp": time.time()}
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
