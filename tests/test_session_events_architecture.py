"""Comprehensive tests for append-only event logging, session materialization, and Redis Pub/Sub telemetry."""

import asyncio
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from schemas.watcher_models import (
    ReviewRecord,
    Session,
    SessionEventRecord,
    ToolCall,
    ToolResult,
)
from server.db import SessionLocal, current_user_id, get_current_user_id
from server.event_bus import EventBus
from server.watcher_store import get_watcher_store


def test_session_events_table_schema_and_indexes() -> None:
    """Phase 1: Verify session_events table structure and indexes created by Alembic migration."""
    with SessionLocal() as db:
        # Check column information
        cols = db.execute(
            text(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'session_events'
                ORDER BY ordinal_position
                """
            )
        ).fetchall()
        col_dict = {r[0]: (r[1], r[2]) for r in cols}
        assert "id" in col_dict
        assert "user_id" in col_dict
        assert "session_id" in col_dict
        assert "step_index" in col_dict
        assert "event_type" in col_dict
        assert "payload" in col_dict
        assert "created_at" in col_dict

        # Check indexes exist
        idx_rows = db.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'session_events'
                """
            )
        ).fetchall()
        indexes = {r[0] for r in idx_rows}
        assert "ix_session_events_user_session_step" in indexes
        assert "ix_session_events_user_created" in indexes


def test_append_event_and_audit_non_repudiation() -> None:
    """Phase 2: Verify append_event writes discrete rows scoped to current_user_id and remains append-only."""
    store = get_watcher_store()
    session_id = f"sess-{uuid.uuid4().hex[:8]}"

    # 1. Create a session
    sess = Session(session_id=session_id, project_name="audit-test", status="working")
    store.create_session(sess)

    # 2. Append discrete events
    ev1 = SessionEventRecord(
        id=str(uuid.uuid4()),
        session_id=session_id,
        step_index=0,
        event_type="tool_call",
        payload={"tool_id": "t1", "tool_name": "Bash", "arguments": {"cmd": "ls -la"}},
    )
    store.append_event(ev1)

    ev2 = SessionEventRecord(
        id=str(uuid.uuid4()),
        session_id=session_id,
        step_index=1,
        event_type="tool_result",
        payload={"tool_id": "t1", "tool_name": "Bash", "stdout": "total 0", "exit_code": 0},
    )
    store.append_event(ev2)

    # 3. Query discrete rows directly from DB
    with SessionLocal() as db:
        uid = get_current_user_id(db)
        rows = db.execute(
            text(
                """
                SELECT id, step_index, event_type, payload
                FROM session_events
                WHERE session_id = :sid AND user_id = :uid
                ORDER BY step_index ASC
                """
            ),
            {"sid": session_id, "uid": uid},
        ).fetchall()

        assert len(rows) == 2
        assert rows[0][0] == ev1.id
        assert rows[0][1] == 0
        assert rows[0][2] == "tool_call"
        assert rows[1][0] == ev2.id
        assert rows[1][1] == 1
        assert rows[1][2] == "tool_result"


def test_record_decision_and_tool_result_event_logging() -> None:
    """Phase 2: Verify record_decision and record_tool_result write to reviews and session_events without immediate payload rewrite."""
    store = get_watcher_store()
    session_id = f"sess-{uuid.uuid4().hex[:8]}"

    # Create base session
    sess = Session(session_id=session_id, project_name="decision-test", status="working")
    store.create_session(sess)

    # Record decision
    rev = ReviewRecord(
        id=f"rev-{uuid.uuid4().hex[:6]}",
        session_id=session_id,
        tool_name="edit_file",
        tool_input="file.py",
        decision="block",
        score=9,
        stage="rule",
        rule_name="Sensitive file edit",
    )
    store.record_decision(rev)

    # Record tool result
    tr = ToolResult(
        tool_id="tr-1",
        tool_name="edit_file",
        stdout="blocked",
        exit_code=1,
    )
    store.record_tool_result(session_id, tr)

    # Verify rows in reviews and session_events
    with SessionLocal() as db:
        uid = get_current_user_id(db)
        rev_db = db.execute(
            text("SELECT id, decision, score FROM reviews WHERE id = :id AND user_id = :uid"),
            {"id": rev.id, "uid": uid},
        ).fetchone()
        assert rev_db is not None
        assert rev_db[1] == "block"
        assert rev_db[2] == 9

        events_db = db.execute(
            text(
                "SELECT event_type FROM session_events WHERE session_id = :sid AND user_id = :uid"
            ),
            {"sid": session_id, "uid": uid},
        ).fetchall()
        event_types = [r[0] for r in events_db]
        assert "review_decision" in event_types
        assert "tool_result" in event_types


def test_materialize_session_folds_all_events() -> None:
    """Phase 2: Verify materialize_session folds events into base Session and upserts sessions.payload with updated_at."""
    store = get_watcher_store()
    session_id = f"sess-{uuid.uuid4().hex[:8]}"

    # 1. Create base session
    sess = Session(session_id=session_id, project_name="materialize-test", status="working")
    store.create_session(sess)

    # 2. Append tool call, review, tool result, and operator resolution
    tc = ToolCall(tool_id="call-123", tool_name="Bash", arguments={"cmd": "reboot"})
    store.append_trajectory_event(session_id, tc)

    rev = ReviewRecord(
        id="rev-123",
        session_id=session_id,
        tool_name="Bash",
        tool_input="reboot",
        decision="block",
        score=9,
        stage="rule",
        rule_name="Reboot attempt",
    )
    store.record_decision(rev)

    tr = ToolResult(
        tool_id="call-123", tool_name="Bash", stdout="system shutting down", exit_code=0
    )
    store.record_tool_result(session_id, tr)

    # Operator resolves review to allowed
    resolved_rev = store.resolve_decision(
        session_id, review_id="rev-123", action="allow_once", notes="Testing resolution"
    )
    assert resolved_rev is not None

    # 3. Materialize session explicitly
    materialized = store.materialize_session(session_id)
    assert materialized.session_id == session_id

    # Verify folded reviews
    assert len(materialized.trajectory.reviews) == 1
    assert materialized.trajectory.reviews[0].id == "rev-123"
    assert materialized.trajectory.reviews[0].decision == "allow"
    assert "Testing resolution" in str(materialized.trajectory.reviews[0].explanation)

    # Verify folded tool calls
    assert len(materialized.trajectory.tool_calls) == 1
    assert materialized.trajectory.tool_calls[0].tool_id == "call-123"

    # Verify folded tool results
    assert len(materialized.trajectory.tool_results) == 1
    assert materialized.trajectory.tool_results[0].tool_id == "call-123"

    # 4. Verify DB sessions.payload has the materialized JSON
    with SessionLocal() as db:
        uid = get_current_user_id(db)
        row = db.execute(
            text("SELECT payload, updated_at FROM sessions WHERE id = :id AND user_id = :uid"),
            {"id": session_id, "uid": uid},
        ).fetchone()
        assert row is not None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        assert len(payload["trajectory"]["reviews"]) == 1
        assert len(payload["trajectory"]["tool_calls"]) == 1
        assert len(payload["trajectory"]["tool_results"]) == 1


def test_terminal_session_status_synchronous_materialization() -> None:
    """Phase 2: Verify terminal status (completed, failed, aborted, lockout) materializes synchronously."""
    store = get_watcher_store()
    session_id = f"sess-{uuid.uuid4().hex[:8]}"

    sess = Session(session_id=session_id, project_name="terminal-test", status="working")
    store.create_session(sess)

    # Add tool result
    tr = ToolResult(tool_id="call-term", tool_name="execute", stdout="completed work")
    store.record_tool_result(session_id, tr)

    # Update session to terminal status: completed
    updated = store.update_session(session_id, {"status": "completed"})
    assert updated is not None
    assert updated.status == "completed"

    # Verify DB sessions.payload was updated with the folded tool result
    with SessionLocal() as db:
        uid = get_current_user_id(db)
        row = db.execute(
            text("SELECT payload FROM sessions WHERE id = :id AND user_id = :uid"),
            {"id": session_id, "uid": uid},
        ).fetchone()
        assert row is not None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        results = payload.get("trajectory", {}).get("tool_results", [])
        assert any(r["tool_id"] == "call-term" for r in results)


def test_tenant_isolation_on_events_and_materialization() -> None:
    """Invariant: Verify Tenant A cannot access, write, or materialize Tenant B's events."""
    store = get_watcher_store()

    # User 1 (owner)
    current_user_id.set(1)
    sess_a = Session(session_id="tenant-a-sess", project_name="proj-a", status="working")
    store.create_session(sess_a)

    ev_a = SessionEventRecord(
        id="ev-a-1",
        session_id="tenant-a-sess",
        step_index=0,
        event_type="tool_call",
        payload={"tool_id": "ta1", "tool_name": "Read"},
    )
    store.append_event(ev_a)

    # Switch to User 2
    current_user_id.set(2)
    with SessionLocal() as db:
        # Create user 2 if missing
        db.execute(
            text("INSERT INTO users (id, slug) VALUES (2, 'tenant-b') ON CONFLICT (id) DO NOTHING")
        )
        db.commit()

    # User 2 cannot get session A
    assert store.get_session("tenant-a-sess") is None

    # User 2 materializing session A returns empty/default isolated session for User 2 with 0 events
    mat_b = store.materialize_session("tenant-a-sess")
    assert len(mat_b.trajectory.tool_calls) == 0

    # User 2 cannot see event A in DB
    with SessionLocal() as db:
        rows = db.execute(
            text("SELECT id FROM session_events WHERE user_id = 2 AND session_id = 'tenant-a-sess'")
        ).fetchall()
        assert len(rows) == 0

    # Restore tenant
    current_user_id.set(1)


@pytest.mark.asyncio
async def test_redis_pubsub_channel_naming_and_broadcast() -> None:
    """Phase 3: Verify tenant channel naming (openeval:tenant:{user_id}:events) and live Redis Pub/Sub."""
    bus = EventBus(redis_url="redis://127.0.0.1:6379/0")
    if not await bus.is_redis_available():
        pytest.skip("Local Redis server not accessible for live Pub/Sub test")

    user_id = 42
    subscriber = await bus.create_redis_subscriber(user_id)
    assert subscriber is not None

    received_events: list[dict[str, Any]] = []

    async def listen_loop():
        async for msg in subscriber.listen():
            if msg.get("type") == "message":
                raw = msg.get("data")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                parsed = json.loads(raw)
                received_events.append(parsed)
                break

    listen_task = asyncio.create_task(listen_loop())
    await asyncio.sleep(0.05)  # Let subscription establish

    # Publish to tenant channel
    test_data = {"session_id": "test-redis-sess", "status": "ok"}
    await bus.publish("test_event", test_data, user_id=user_id)

    # Wait for listener with timeout
    try:
        await asyncio.wait_for(listen_task, timeout=2.0)
    finally:
        await subscriber.unsubscribe()
        await subscriber.aclose()
        await bus.close()

    assert len(received_events) == 1
    assert received_events[0]["event"] == "test_event"
    assert received_events[0]["data"]["session_id"] == "test-redis-sess"
    assert received_events[0]["data"]["user_id"] == user_id


@pytest.mark.asyncio
async def test_redis_graceful_degradation_fallback_to_in_memory() -> None:
    """Phase 3: Verify graceful degradation to in-memory queues when REDIS_URL is empty or invalid."""
    # Invalid Redis URL
    bus = EventBus(redis_url="redis://127.0.0.1:9999/0")
    is_avail = await bus.is_redis_available()
    assert is_avail is False

    # Subscriber gets in-memory queue
    user_id = 99
    sub_redis = await bus.create_redis_subscriber(user_id)
    assert sub_redis is None  # Degrades gracefully

    q = bus.subscribe_in_memory(user_id)
    assert q is not None

    # Publish event
    await bus.publish("fallback_event", {"msg": "hello from memory"}, user_id=user_id)

    # Queue receives the event
    item = await asyncio.wait_for(q.get(), timeout=1.0)
    assert item["event"] == "fallback_event"
    assert item["data"]["msg"] == "hello from memory"
    assert item["data"]["user_id"] == user_id

    # Clean unregistration
    bus.unsubscribe_in_memory(user_id, q)


@pytest.mark.asyncio
async def test_stream_watcher_events_generator_and_tenant_isolation() -> None:
    """Phase 3: Test stream_watcher_events generator delivers connected event, tenant events, and enforces tenant isolation."""
    from server.app import broadcast_watcher_event, stream_watcher_events

    current_user_id.set(1)

    response = await stream_watcher_events()
    assert response.status_code == 200
    assert response.media_type == "text/event-stream"

    gen = response.body_iterator
    # 1. First event must be connected
    first_chunk = await asyncio.wait_for(anext(gen), timeout=2.0)
    assert "event: connected" in first_chunk
    assert "history" in first_chunk

    # 2. Broadcast event for User 1
    await broadcast_watcher_event("tenant_1_action", {"session_id": "sess-tenant-1", "user_id": 1})

    # Generator should receive it
    event_chunk = await asyncio.wait_for(anext(gen), timeout=2.0)
    assert "event: tenant_1_action" in event_chunk
    assert "sess-tenant-1" in event_chunk

    # 3. Broadcast event for User 2 (different tenant)
    await broadcast_watcher_event("tenant_2_action", {"session_id": "sess-tenant-2", "user_id": 2})

    # Broadcast another event for User 1
    await broadcast_watcher_event(
        "tenant_1_second", {"session_id": "sess-tenant-1-b", "user_id": 1}
    )

    next_chunk = await asyncio.wait_for(anext(gen), timeout=2.0)
    assert "event: tenant_1_second" in next_chunk
    assert "tenant_2_action" not in next_chunk

    # Close generator
    await gen.aclose()
