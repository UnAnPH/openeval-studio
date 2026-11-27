"""Event materialization and folding mixin for the Watcher store.

Extracted from :mod:`server.watcher_store`. ``SessionLocal`` and
``get_current_user_id`` are resolved through the ``server.watcher_store`` module at
call time so ``monkeypatch.setattr("server.watcher_store.SessionLocal", ...)`` in
tests still intercepts these methods.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from schemas.watcher_models import (
    AgentType,
    Message,
    ReviewRecord,
    Session,
    ToolCall,
    ToolResult,
    Trajectory,
)
from server.db import current_user_id
from server.request_context import request_id_ctx

logger = logging.getLogger("openeval.server.watcher_store")


def _watcher_module() -> Any:  # noqa: ANN401 - lazy ref keeps module monkeypatches live
    """Resolve ``server.watcher_store`` lazily so module monkeypatches still apply."""
    from server import watcher_store

    return watcher_store


class WatcherMaterializerMixin:
    """Fold append-only ``session_events`` into materialized ``sessions.payload`` rows."""

    if TYPE_CHECKING:
        _lock: threading.RLock
        storage_dir: Path
        degraded: bool
        _sessions: dict[str, Session]
        _dirty_sessions: set[str]
        _debounce_tasks: dict[str, asyncio.Task[Any]]

        def memory_key(self, session_id: str) -> str: ...
        def _cached_session(self, session_id: str) -> Session | None: ...
        def _cache_session(self, session: Session) -> str: ...
        def _record_db_failure(self, context: str, exc: BaseException) -> None: ...

    def materialize_session(self, session_id: str, db: DbSession | None = None) -> Session:
        """Fold/reduce all append-only session_events into the base Session and upsert sessions.payload."""
        if db is not None:
            return self._execute_materialize(session_id, db)
        try:
            with _watcher_module().SessionLocal() as sdb:
                return self._execute_materialize(session_id, sdb)
        except Exception as e:  # noqa: BLE001 - degraded-mode fallback must catch all
            self._record_db_failure("materialize_session", e)
            with self._lock:
                cached = self._cached_session(session_id)
                if cached:
                    return cached
            disk_file = self.storage_dir / f"{session_id}.json"
            if disk_file.exists():
                try:
                    payload = json.loads(disk_file.read_text(encoding="utf-8"))
                    sess = Session.model_validate(payload)
                    _watcher_module().normalize_session_status(sess)
                    with self._lock:
                        self._cache_session(sess)
                    return sess
                except Exception:  # noqa: BLE001 - corrupt disk cache is non-fatal
                    pass
            return Session(session_id=session_id)

    def _execute_materialize(self, session_id: str, db: DbSession) -> Session:
        user_id = current_user_id.get()
        if user_id is None:
            user_id = _watcher_module().get_current_user_id(db)

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
        _watcher_module().normalize_session_status(base_session)

        # 4. Upsert the materialized JSON into sessions.payload with updated_at = NOW()
        payload_json = json.dumps(_watcher_module()._to_jsonable(base_session.model_dump()))
        res = db.execute(
            text(
                """
                UPDATE sessions
                SET payload = CAST(:payload AS jsonb),
                    status = :status,
                    current_activity = COALESCE(:current_activity, current_activity),
                    updated_at = NOW()
                WHERE id = :id AND user_id = :uid
                """
            ),
            {
                "id": session_id,
                "uid": user_id,
                "payload": payload_json,
                "status": base_session.status,
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
        except Exception as e:  # noqa: BLE001 - degraded-mode fallback must catch all
            logger.warning("Background materialization error for %s: %s", session_id, e)
        finally:
            key = self.memory_key(session_id)
            self._debounce_tasks.pop(key, None)
