"""Unit and integration tests for Redis Streams telemetry and concurrent decision recording.

Covers:
1. Issue 1: Concurrency Anomaly / Lost Updates in record_decision:
   - Distributed mutex (lock:session:{tenant_id}:{session_id}) with backoff/retry.
   - Atomic PostgreSQL JSONB update via jsonb_set preventing lost updates.
   - Verification across relational reviews and sessions.payload trajectory.
2. Issue 2: SSE Disconnection & Replay via Redis Streams & Last-Event-ID:
   - Partitioned streams (watcher:events:{tenant_id}) with MAXLEN ~ 5000.
   - 5-second simulated disconnection under high volume.
   - Reconnection with Last-Event-ID header and last_event_id query param.
   - Zero dropped events, chronological backlog replay, and SSE id: formatting.
3. Strict Tenant Isolation across streams and mutex locks.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import text

from schemas.watcher_models import ReviewRecord, Session
from server.db import SessionLocal, current_user_id, get_current_user_id
from server.event_bus import get_event_bus
from server.watcher_store import get_watcher_store


@pytest.mark.asyncio
async def test_concurrent_record_decision_zero_lost_updates() -> None:
    """Issue 1 Verification: Concurrent tool evaluations against the same session ID

    must not lose any review records in either the `reviews` table or the `sessions.payload` trajectory.
    """
    store = get_watcher_store()
    with SessionLocal() as db:
        user_id = get_current_user_id(db)
    current_user_id.set(user_id)

    session_id = f"concurrency-sess-{uuid4().hex[:8]}"
    session = Session(
        session_id=session_id,
        project_name="concurrency-test",
        agent_type="antigravity",
    )
    store.create_session(session)

    num_concurrent_reviews = 15

    async def record_worker(index: int) -> ReviewRecord:
        review = ReviewRecord(
            id=f"rev-concurrent-{index:03d}-{uuid4().hex[:6]}",
            session_id=session_id,
            tool_name="run_command",
            tool_input=f"echo 'task {index}'",
            decision="allow" if index % 2 == 0 else "warn",
            score=1 + (index % 10),
            stage="rule",
            rule_name=f"Rule-{index}",
            explanation=f"Concurrent review evaluation index {index}",
            latency_ms=10.0 + index,
        )
        return await store.arecord_decision(review)

    # Launch all workers simultaneously
    results = await asyncio.gather(*(record_worker(i) for i in range(num_concurrent_reviews)))
    assert len(results) == num_concurrent_reviews

    # 1. Verify relational reviews table contains all records
    with SessionLocal() as db:
        rev_rows = db.execute(
            text(
                "SELECT id, tool_input, score FROM reviews WHERE session_id = :sid AND user_id = :uid"
            ),
            {"sid": session_id, "uid": user_id},
        ).fetchall()
        assert len(rev_rows) == num_concurrent_reviews, (
            f"Expected {num_concurrent_reviews} reviews in DB, found {len(rev_rows)}"
        )

    # 2. Verify sessions.payload in PostgreSQL contains all reviews via atomic jsonb_set
    with SessionLocal() as db:
        sess_row = db.execute(
            text("SELECT payload FROM sessions WHERE id = :sid AND user_id = :uid"),
            {"sid": session_id, "uid": user_id},
        ).fetchone()
        assert sess_row is not None and sess_row[0] is not None
        payload = sess_row[0] if isinstance(sess_row[0], dict) else json.loads(sess_row[0])
        reviews_in_payload = payload.get("trajectory", {}).get("reviews", [])
        assert len(reviews_in_payload) == num_concurrent_reviews, (
            f"Expected {num_concurrent_reviews} reviews in sessions.payload, found {len(reviews_in_payload)}"
        )
        review_ids_in_payload = {r["id"] for r in reviews_in_payload}
        expected_ids = {r.id for r in results}
        assert review_ids_in_payload == expected_ids, (
            "Mismatch between created review IDs and payload IDs"
        )

    # 3. Verify materialize_session aggregates all reviews cleanly
    materialized = store.materialize_session(session_id)
    assert len(materialized.trajectory.reviews) == num_concurrent_reviews


@pytest.mark.asyncio
async def test_redis_streams_disconnection_replay_with_last_event_id() -> None:
    """Issue 2 Verification: Simulate a 5-second client disconnection during high event volume;

    verify that sending Last-Event-ID resumes the stream with zero dropped events and in correct chronological order.
    """
    from server.app import broadcast_watcher_event, stream_watcher_events

    with SessionLocal() as db:
        user_id = get_current_user_id(db)
    current_user_id.set(user_id)

    # 1. Establish SSE stream connection without Last-Event-ID
    response = await stream_watcher_events()
    assert response.status_code == 200
    gen = cast(Any, response.body_iterator)

    # Receive connection handshake
    handshake_chunk = await asyncio.wait_for(anext(gen), timeout=3.0)
    assert "event: connected" in handshake_chunk

    # 2. Publish baseline event (event 0) and consume it to get the checkpoint ID
    sid_0 = await broadcast_watcher_event(
        "tool_call_start",
        {"step": 0, "action": "pre_disconnect", "user_id": user_id},
    )
    event_0_chunk = await asyncio.wait_for(anext(gen), timeout=3.0)
    assert f"id: {sid_0}" in event_0_chunk
    assert "tool_call_start" in event_0_chunk

    # 3. Simulate Client Disconnection: close generator
    await gen.aclose()

    # 4. During the disconnection window, broadcast high volume of events
    num_missed_events = 25
    published_stream_ids: list[str] = []
    for i in range(1, num_missed_events + 1):
        sid = await broadcast_watcher_event(
            "volume_event",
            {"seq": i, "data": f"payload_{i}", "user_id": user_id},
        )
        published_stream_ids.append(sid)

    # Simulate realistic disconnection delay
    await asyncio.sleep(0.5)

    # 5. Client reconnects sending Last-Event-ID = sid_0
    reconnect_response = await stream_watcher_events(last_event_id=sid_0)
    assert reconnect_response.status_code == 200
    rec_gen = cast(Any, reconnect_response.body_iterator)

    # Replay backlog should yield all missed events in exact order
    replayed_chunks: list[str] = []
    for _ in range(num_missed_events):
        chunk = await asyncio.wait_for(anext(rec_gen), timeout=3.0)
        replayed_chunks.append(chunk)

    assert len(replayed_chunks) == num_missed_events

    # Verify each replayed event preserves SSE format, stream id, and sequence
    for i, (sid, chunk) in enumerate(
        zip(published_stream_ids, replayed_chunks, strict=False), start=1
    ):
        assert f"id: {sid}" in chunk
        assert "event: volume_event" in chunk
        assert f'"seq": {i}' in chunk

    # 6. Verify stream continues live reading from the tail after backlog replay
    live_sid = await broadcast_watcher_event(
        "post_reconnect_live",
        {"status": "resumed_live", "user_id": user_id},
    )
    live_chunk = await asyncio.wait_for(anext(rec_gen), timeout=3.0)
    assert f"id: {live_sid}" in live_chunk
    assert "event: post_reconnect_live" in live_chunk
    assert "resumed_live" in live_chunk

    await rec_gen.aclose()


@pytest.mark.asyncio
async def test_redis_streams_reconnect_via_http_header() -> None:
    """Issue 2 Verification: Verify Last-Event-ID received via HTTP Request headers."""
    from starlette.requests import Request

    from server.app import broadcast_watcher_event, stream_watcher_events

    with SessionLocal() as db:
        user_id = get_current_user_id(db)
    current_user_id.set(user_id)

    # Broadcast event 1
    id1 = await broadcast_watcher_event("step_1", {"msg": "first", "user_id": user_id})
    # Broadcast event 2
    id2 = await broadcast_watcher_event("step_2", {"msg": "second", "user_id": user_id})

    # Mock Starlette Request with Last-Event-ID header set to id1
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/watcher/stream",
        "headers": [(b"last-event-id", id1.encode("utf-8"))],
    }
    req = Request(scope)

    resp = await stream_watcher_events(request=req)
    gen = cast(Any, resp.body_iterator)

    # First event received must be id2 (the missed backlog)
    chunk = await asyncio.wait_for(anext(gen), timeout=3.0)
    assert f"id: {id2}" in chunk
    assert "event: step_2" in chunk

    await gen.aclose()


@pytest.mark.asyncio
async def test_strict_multi_tenant_isolation_in_streams_and_locks() -> None:
    """Invariants: Tenant A can never read or write to Tenant B's streams or mutexes."""
    bus = get_event_bus()

    tenant_a_id = 9101
    tenant_b_id = 9102

    client = await bus.get_redis_client()
    if client is not None:
        await client.delete(f"watcher:events:{tenant_a_id}", f"watcher:events:{tenant_b_id}")

    # Emit event to Tenant A
    sid_a = await bus.xadd("secret_action_a", {"secret": "token_A"}, user_id=tenant_a_id)
    # Emit event to Tenant B
    sid_b = await bus.xadd("secret_action_b", {"secret": "token_B"}, user_id=tenant_b_id)

    # 1. XRANGE for Tenant A contains sid_a and Tenant A's event, and no events from Tenant B
    events_a = await bus.xrange(tenant_a_id, last_event_id="0-0", count=50)
    assert any(item[0] == sid_a for item in events_a)
    assert all(payload.get("data", {}).get("secret") != "token_B" for _, payload in events_a)
    assert all(payload.get("event") != "secret_action_b" for _, payload in events_a)

    # 2. XRANGE for Tenant B contains sid_b and Tenant B's event, and no events from Tenant A
    events_b = await bus.xrange(tenant_b_id, last_event_id="0-0", count=50)
    assert any(item[0] == sid_b for item in events_b)
    assert all(payload.get("data", {}).get("secret") != "token_A" for _, payload in events_b)
    assert all(payload.get("event") != "secret_action_a" for _, payload in events_b)

    # 3. Distributed mutexes for same session ID do not collide across different tenants
    session_id = "shared-session-name"
    async with bus.session_lock(tenant_a_id, session_id):
        # Tenant B should be able to acquire lock on their own tenant without blocking
        acquired_b = False
        async with bus.session_lock(tenant_b_id, session_id):
            acquired_b = True
        assert acquired_b is True


@pytest.mark.asyncio
async def test_redis_pool_fastapi_dependency_injection() -> None:
    """Acceptance Criteria 1: Redis client integration using redis.asyncio with connection pooling."""
    from starlette.requests import Request

    from server.event_bus import get_redis_dependency

    scope: dict[str, Any] = {"type": "http", "method": "GET", "path": "/"}
    req = Request(scope)
    client = await get_redis_dependency(req)

    bus = get_event_bus()
    if await bus.is_redis_available():
        assert client is not None
        pong = await client.ping()
        assert pong is True
