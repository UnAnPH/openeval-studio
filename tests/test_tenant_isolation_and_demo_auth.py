"""Tenant isolation for demo auth, run store, and watcher rate-limit keys."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from engine.approval_policy import WatcherRequest
from schemas.watcher_models import ReviewRecord, Session
from server.app import app
from server.db import (
    _USER_ID_CACHE,
    SessionLocal,
    current_user_id,
    current_user_slug,
    get_user_id_by_slug,
)
from server.rate_limiter import RateLimitResult
from server.store import global_run_store
from server.watcher_store import get_watcher_store


def _postgres_available() -> bool:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(), reason="PostgreSQL test database is not reachable"
)


@pytest.fixture(autouse=True)
def enforce_strict_auth(monkeypatch):
    monkeypatch.setenv("OPENEVAL_AUTH_DISABLED", "0")
    yield


@pytest.fixture
def client():
    _USER_ID_CACHE.setdefault("demo", 2)
    _USER_ID_CACHE.setdefault("owner", 1)
    return TestClient(app)


def test_demo_surface_mutating_eval_run_rejected(client):
    resp = client.post(
        "/api/eval/run",
        headers={"X-OpenEval-Surface": "demo"},
        json={"task_id": "demo_injection"},
    )
    assert resp.status_code == 401
    assert "read-only" in resp.json()["detail"].lower()


def test_demo_surface_mutating_watcher_policy_rejected(client):
    resp = client.post(
        "/api/v1/watcher/policy/reset",
        headers={"X-OpenEval-Surface": "demo"},
    )
    assert resp.status_code == 401


@requires_postgres
def test_demo_surface_search_still_allowed(client):
    resp = client.post(
        "/api/eval/search",
        headers={"X-OpenEval-Surface": "demo"},
        json={"query": "nonexistent-query-xyz"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@requires_postgres
def test_get_run_scoped_by_user_id():
    other_id = "eval-tenant-isolation-other-001"
    owner_id = "eval-tenant-isolation-owner-001"
    with SessionLocal() as db:
        owner_uid = get_user_id_by_slug(db, "owner")
        db.execute(
            text("INSERT INTO users (slug) VALUES ('iso_other_user') ON CONFLICT (slug) DO NOTHING")
        )
        db.commit()
        other_uid = get_user_id_by_slug(db, "iso_other_user")
        for sid, uid in ((other_id, other_uid), (owner_id, owner_uid)):
            payload = (
                f'{{"session_id": "{sid}", "run_id": "{sid}", "agent_type": "inspect_eval", '
                f'"task_id": "t", "model": "m", "status": "completed"}}'
            )
            db.execute(
                text(
                    """
                    INSERT INTO sessions (id, user_id, agent_type, status, payload, created_at, updated_at)
                    VALUES (
                        :id, :uid, 'inspect_eval', 'completed',
                        CAST(:payload AS jsonb), NOW(), NOW()
                    )
                    ON CONFLICT (user_id, id) DO NOTHING
                    """
                ),
                {"id": sid, "uid": uid, "payload": payload},
            )
        db.commit()

    current_user_slug.set("owner")
    current_user_id.set(owner_uid)
    assert global_run_store.get_run(owner_id) is not None
    assert global_run_store.get_run(other_id) is None


@requires_postgres
def test_list_runs_demo_user_does_not_include_other_tenant_eval():
    demo_eval_id = "eval-demo-scope-test-001"
    with SessionLocal() as db:
        owner_uid = get_user_id_by_slug(db, "owner")
        demo_uid = get_user_id_by_slug(db, "demo")
        payload = (
            f'{{"session_id": "{demo_eval_id}", "run_id": "{demo_eval_id}", '
            f'"agent_type": "inspect_eval", "task_id": "t", "model": "m", "status": "completed"}}'
        )
        db.execute(
            text(
                """
                INSERT INTO sessions (id, user_id, agent_type, status, payload, created_at, updated_at)
                VALUES (
                    :id, :uid, 'inspect_eval', 'completed',
                    CAST(:payload AS jsonb), NOW(), NOW()
                )
                ON CONFLICT (user_id, id) DO NOTHING
                """
            ),
            {"id": demo_eval_id, "uid": owner_uid, "payload": payload},
        )
        db.commit()

    current_user_slug.set("demo")
    current_user_id.set(demo_uid)
    run_ids = {s.session_id for s in global_run_store.list_runs()}
    assert demo_eval_id not in run_ids


def test_in_memory_cached_runs_isolated_across_tenants():
    """Peer tenant cache entries must not pass _cache_visible for the owner tenant."""
    cached_id = "eval-in-memory-cache-leak-test"
    peer_uid = 88001
    owner_uid = 88002

    peer_session = Session(
        session_id=cached_id,
        run_id=cached_id,
        agent_type="inspect_eval",
        task_id="peer-task",
        model="peer-model",
        status="completed",
    )
    store = get_watcher_store()
    current_user_slug.set("iso_cache_peer")
    current_user_id.set(peer_uid)
    with store._lock:
        store._cache_session(peer_session)

    current_user_slug.set("owner")
    current_user_id.set(owner_uid)

    visible_to_owner = [
        s.session_id for key, s in global_run_store._runs.items() if store._cache_visible(key, s)
    ]
    assert cached_id not in visible_to_owner

    # RunStore get_run/list_runs in-memory paths honor the same visibility gate.
    leaked_via_get_run_scan = False
    for key, s in global_run_store._runs.items():
        if not store._cache_visible(key, s):
            continue
        if s.session_id == cached_id or s.run_id == cached_id:
            leaked_via_get_run_scan = True
            break
    assert not leaked_via_get_run_scan


@pytest.mark.asyncio
async def test_rate_limit_uses_stable_key_without_session_id(monkeypatch):
    from server.routes import watcher_gate

    keys_seen: list[str] = []

    class _FakeLimiter:
        def check(self, key: str) -> RateLimitResult:
            keys_seen.append(key)
            return RateLimitResult(allowed=True, retry_after_ms=0, remaining_tokens=10)

    monkeypatch.setenv("OPENEVAL_DISABLE_RATE_LIMIT", "0")
    monkeypatch.setattr("server.rate_limiter.get_rate_limiter", lambda: _FakeLimiter())

    mock_gw = MagicMock()
    mock_gw.evaluate_tool_call.return_value = ReviewRecord(
        session_id="session-live-00000001",
        tool_name="bash",
        decision="allow",
        explanation="ok",
    )
    monkeypatch.setattr(watcher_gate.PolicyGateway, "get_shared", classmethod(lambda cls: mock_gw))
    import importlib

    app_module = importlib.import_module("server.app")
    monkeypatch.setattr(app_module, "broadcast_watcher_event", AsyncMock(return_value=None))

    current_user_slug.set("owner")
    current_user_id.set(1)

    req = WatcherRequest(tool_name="bash", tool_input="echo hi", agent_id="stable-agent-1")
    mock_request = MagicMock()
    mock_request.app.state.db_ready = True

    await watcher_gate.evaluate_action_watcher_gateway(req, mock_request)
    await watcher_gate.evaluate_action_watcher_gateway(req, mock_request)

    assert len(keys_seen) == 2
    assert keys_seen[0] == keys_seen[1]
    assert keys_seen[0].startswith("default-")
    assert "stable-agent-1" in keys_seen[0]
    assert not keys_seen[0].startswith("session-live-")


@requires_postgres
def test_owner_does_not_see_ephemeral_demo_uploads(client: TestClient) -> None:
    content = (
        json.dumps(
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "tenant isolation demo upload"}]},
            }
        )
        + "\n"
    )
    resp = client.post(
        "/api/v1/agent-logs/ingest",
        headers={"X-OpenEval-Surface": "demo"},
        json={
            "filename": "tenant-iso-demo-upload.jsonl",
            "file_content": content,
            "agent_type": "auto",
            "is_demo": True,
        },
    )
    assert resp.status_code == 200
    session_id = resp.json()["session"]["session_id"]

    with SessionLocal() as db:
        owner_uid = get_user_id_by_slug(db, "owner")
        db.rollback()

    current_user_slug.set("owner")
    current_user_id.set(owner_uid)
    store = get_watcher_store()
    assert store.get_session(session_id) is None
    owner_ids = {s.session_id for s in store.list_sessions(limit=500)}
    assert session_id not in owner_ids
