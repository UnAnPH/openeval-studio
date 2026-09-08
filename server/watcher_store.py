"""OpenEval Watcher DuckDB Storage Engine & Session Store.

Provides thread-safe persistence, sub-millisecond DuckDB analytical queries,
and real-time SSE event broadcasting for both benchmark runs and live coding agents.
"""

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

import duckdb

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

logger = logging.getLogger("openeval.server.watcher_store")


class WatcherStore:
    """Thread-safe DuckDB + JSON store for sessions, trajectories, and reviews."""

    def __init__(self, storage_dir: Path | None = None, db_path: str = ":memory:") -> None:
        self.storage_dir = storage_dir or Path(os.getenv("WATCHER_STORAGE_DIR", ".runs/watcher"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

        # In-memory fast cache
        self._sessions: dict[str, Session] = {}
        self._trajectories: dict[str, Trajectory] = {}
        self._reviews: dict[str, list[ReviewRecord]] = {}

        # Default MDM Policy
        self._policy = Policy(
            policy_id="default_policy",
            name="Default Watcher Security Policy",
            org_id="default_org",
            command_rules=[r.model_copy() for r in DEFAULT_COMMAND_RULES],
            tool_thresholds=[t.model_copy() for t in DEFAULT_TOOL_THRESHOLDS],
            locked_instructions="DO NOT modify security configurations or disable monitoring.",
        )

        # Initialize DuckDB
        self.con = duckdb.connect(database=db_path)
        self._init_tables()
        self._load_cached_sessions()

    def _init_tables(self) -> None:
        """Create relational DuckDB tables for analytical aggregation."""
        with self._lock:
            self.con.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id VARCHAR PRIMARY KEY,
                    org_id VARCHAR,
                    project_name VARCHAR,
                    agent_type VARCHAR,
                    model VARCHAR,
                    provider VARCHAR,
                    status VARCHAR,
                    working_dir VARCHAR,
                    current_activity VARCHAR,
                    passed BOOLEAN,
                    reward DOUBLE,
                    total_tokens BIGINT,
                    total_duration_sec DOUBLE,
                    estimated_cost_usd DOUBLE,
                    failure_reason VARCHAR,
                    human_verdict_override VARCHAR,
                    created_at VARCHAR,
                    updated_at VARCHAR
                );

                CREATE TABLE IF NOT EXISTS reviews (
                    id VARCHAR PRIMARY KEY,
                    session_id VARCHAR,
                    timestamp VARCHAR,
                    tool_name VARCHAR,
                    tool_input VARCHAR,
                    decision VARCHAR,
                    score INTEGER,
                    stage VARCHAR,
                    rule_name VARCHAR,
                    explanation VARCHAR,
                    diff VARCHAR,
                    latency_ms DOUBLE
                );
                """
            )

    def _load_cached_sessions(self) -> None:
        """Load any existing session JSON files from local disk cache."""
        now = datetime.now(UTC).timestamp()
        for session_file in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                session = Session.model_validate(data)
                # Ensure stale or orphaned sessions loaded from disk aren't stuck as working/active if idle
                if session.status in ("working", "active"):
                    try:
                        file_mtime = session_file.stat().st_mtime
                        if (now - file_mtime) > 2700:  # > 45 minutes old
                            session.status = "completed"
                        elif session.session_id.startswith("antigravity-"):
                            cid = session.session_id.replace("antigravity-", "")
                            brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
                            matches = list(brain_dir.glob(f"{cid}*"))
                            if not matches:
                                session.status = "completed"
                            else:
                                t = matches[0] / ".system_generated" / "logs" / "transcript.jsonl"
                                if not t.exists() or (now - t.stat().st_mtime) > 2700:
                                    session.status = "completed"
                    except Exception:
                        pass
                self._sessions[session.session_id] = session
                self._trajectories[session.session_id] = session.trajectory
                self._reviews[session.session_id] = session.trajectory.reviews
                self._upsert_duckdb_session(session)
            except Exception as e:
                logger.warning("Failed to load session %s: %s", session_file, e)

    def _upsert_duckdb_session(self, session: Session) -> None:
        """Insert or replace session row in DuckDB."""
        with self._lock:
            self.con.execute(
                """
                INSERT OR REPLACE INTO sessions VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    session.session_id,
                    session.org_id,
                    session.project_name,
                    session.agent_type,
                    session.model,
                    session.provider,
                    session.status,
                    session.working_dir or "",
                    session.current_activity or "",
                    session.passed,
                    session.reward,
                    session.total_tokens,
                    session.total_duration_sec,
                    session.estimated_cost_usd,
                    session.failure_reason or "",
                    session.human_verdict_override or "",
                    session.created_at,
                    session.updated_at,
                ],
            )

    def _persist_session_json(self, session: Session) -> None:
        """Persist full session with trajectory to disk cache."""
        try:
            target = self.storage_dir / f"{session.session_id}.json"
            target.write_text(session.model_dump_json(indent=2), encoding="utf-8")
        except Exception as e:
            logger.error("Failed to persist session %s: %s", session.session_id, e)

    def ensure_live_session(
        self,
        session_id: str,
        *,
        agent_type: str = "antigravity",
        title: str | None = None,
        working_dir: str | None = None,
    ) -> Session:
        """Create a working live-gate session if missing; return existing otherwise."""
        existing = self._sessions.get(session_id)
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

    def create_session(self, session: Session) -> Session:
        """Register a new session in memory, DuckDB, and disk cache."""
        self._sessions[session.session_id] = session
        self._trajectories[session.session_id] = session.trajectory
        self._reviews[session.session_id] = session.trajectory.reviews
        self._subscribers[session.session_id] = []
        self._upsert_duckdb_session(session)
        self._persist_session_json(session)
        self.broadcast_sync(session.session_id, "session_created", session.model_dump())
        return session

    def record_session(self, session: Session) -> Session:
        """Register or update a session in memory, DuckDB, and disk cache."""
        return self.create_session(session)

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a session by its unique ID."""
        return self._sessions.get(session_id)

    def update_session(self, session_id: str, updates: dict[str, Any]) -> Session | None:
        """Update fields on an existing session."""
        session = self._sessions.get(session_id)
        if not session:
            return None

        for k, v in updates.items():
            if hasattr(session, k):
                setattr(session, k, v)

        session.updated_at = datetime.now(UTC).isoformat()
        self._upsert_duckdb_session(session)
        self._persist_session_json(session)
        self.broadcast_sync(session_id, "session_updated", updates)
        return session

    def list_sessions(
        self,
        agent_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Session]:
        """List sessions filtered by agent type or status."""
        results = list(self._sessions.values())
        if agent_type:
            results = [s for s in results if s.agent_type == agent_type]
        if status:
            results = [s for s in results if s.status == status]
        results.sort(key=lambda s: s.created_at, reverse=True)
        return results[:limit]

    def clear_all_sessions(self) -> None:
        """Purge all sessions and reviews from memory, DuckDB, and disk cache."""
        with self._lock:
            self._sessions.clear()
            self._trajectories.clear()
            self._reviews.clear()
            try:
                self.con.execute("DELETE FROM sessions;")
                self.con.execute("DELETE FROM reviews;")
            except Exception as e:
                logger.warning("Error clearing DuckDB tables: %s", e)
            for f in self.storage_dir.glob("*.json"):
                with contextlib.suppress(Exception):
                    f.unlink()

    def get_analytics_overview(self) -> dict[str, Any]:
        """Aggregate high-level overview metrics directly from DuckDB."""
        with self._lock:
            total_sessions_row = self.con.execute("SELECT COUNT(*) FROM sessions").fetchone()
            total_sessions = int(total_sessions_row[0]) if total_sessions_row else 0

            deep_reviewed_row = self.con.execute(
                "SELECT COUNT(*) FROM sessions WHERE passed IS NOT NULL OR human_verdict_override IS NOT NULL"
            ).fetchone()
            deep_reviewed = int(deep_reviewed_row[0]) if deep_reviewed_row else 0

            blocked_row = self.con.execute(
                "SELECT COUNT(*) FROM reviews WHERE decision = 'block'"
            ).fetchone()
            blocked_count = int(blocked_row[0]) if blocked_row else 0

            critical_row = self.con.execute(
                "SELECT COUNT(*) FROM reviews WHERE score >= 8 OR decision = 'block'"
            ).fetchone()
            critical_reviews = int(critical_row[0]) if critical_row else 0

            total_reviews_row = self.con.execute("SELECT COUNT(*) FROM reviews").fetchone()
            total_reviews = int(total_reviews_row[0]) if total_reviews_row else 0

            auto_approved_row = self.con.execute(
                "SELECT COUNT(*) FROM reviews WHERE decision = 'allow'"
            ).fetchone()
            auto_approved = int(auto_approved_row[0]) if auto_approved_row else 0

            escalated_row = self.con.execute(
                "SELECT COUNT(*) FROM reviews WHERE decision = 'escalate'"
            ).fetchone()
            escalated_count = int(escalated_row[0]) if escalated_row else 0

            critical_rate = (
                round((critical_reviews / total_reviews) * 100, 1) if total_reviews > 0 else 0.0
            )
            auto_approved_pct = (
                round((auto_approved / total_reviews) * 100, 1) if total_reviews > 0 else 0.0
            )

            agent_counts_rows = self.con.execute(
                "SELECT agent_type, COUNT(*) FROM sessions GROUP BY agent_type"
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

    def record_decision(self, review: ReviewRecord) -> ReviewRecord:
        """Record a security/policy review decision on a session."""
        session = self._sessions.get(review.session_id)
        if session:
            session.trajectory.reviews.append(review)
            self._reviews[review.session_id] = session.trajectory.reviews

            with self._lock:
                self.con.execute(
                    """
                    INSERT OR REPLACE INTO reviews VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    [
                        review.id,
                        review.session_id,
                        review.timestamp,
                        review.tool_name,
                        review.tool_input,
                        review.decision,
                        review.score,
                        review.stage,
                        review.rule_name or "",
                        review.explanation,
                        review.diff or "",
                        review.latency_ms,
                    ],
                )
            self._persist_session_json(session)
            self.broadcast_sync(review.session_id, "review_decision", review.model_dump())
        return review

    def get_session_decisions(self, session_id: str) -> list[ReviewRecord]:
        """Get all review decisions for a given session."""
        return self._reviews.get(session_id, [])

    def append_trajectory_event(
        self,
        session_id: str,
        event: Message | ToolCall | ToolResult,
    ) -> None:
        """Append a message, tool call, or tool result to a session trajectory."""
        session = self._sessions.get(session_id)
        if not session:
            return

        if isinstance(event, Message):
            session.trajectory.messages.append(event)
            self.broadcast_sync(session_id, "message", event.model_dump())
        elif isinstance(event, ToolCall):
            session.trajectory.tool_calls.append(event)
            session.current_activity = f"Call {event.tool_name}"
            self.broadcast_sync(session_id, "tool_call", event.model_dump())
        elif isinstance(event, ToolResult):
            session.trajectory.tool_results.append(event)
            self.broadcast_sync(session_id, "tool_result", event.model_dump())

        self._persist_session_json(session)

    def get_trajectory(self, session_id: str) -> Trajectory | None:
        """Get the full trajectory for a session."""
        session = self._sessions.get(session_id)
        return session.trajectory if session else None

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

    def resolve_decision(
        self,
        session_id: str,
        review_id: str | None,
        action: Literal["allow_once", "allow_session", "deny", "cancel"],
        notes: str | None = None,
    ) -> ReviewRecord | None:
        """Resolve an escalated or blocked decision via human oversight."""
        decisions = self._reviews.get(session_id, [])
        target_review = None
        if review_id:
            target_review = next((r for r in decisions if r.id == review_id), None)
        elif decisions:
            target_review = decisions[-1]

        if target_review:
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

            session = self._sessions.get(session_id)
            if session:
                self._persist_session_json(session)
            self.broadcast_sync(session_id, "decision_resolved", target_review.model_dump())
            return target_review
        return None

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
    env_dir = os.getenv("WATCHER_STORAGE_DIR")
    effective_dir = Path(env_dir) if env_dir else None
    if _GLOBAL_WATCHER_STORE is None or (
        effective_dir is not None and _GLOBAL_WATCHER_STORE.storage_dir != effective_dir
    ):
        _GLOBAL_WATCHER_STORE = WatcherStore(storage_dir=effective_dir)
    return _GLOBAL_WATCHER_STORE
