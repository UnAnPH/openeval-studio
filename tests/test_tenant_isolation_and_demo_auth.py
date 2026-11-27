"""Tenant isolation for demo auth, run store, and watcher rate-limit keys."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from engine.approval_policy import WatcherRequest
from schemas.watcher_models import ReviewRecord, Session
from server.app import IngestAgentLogPayload, app, ingest_agent_log
from server.auth import SESSION_COOKIE_NAME, create_session_token
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


def _prime_user_id_cache() -> None:
    """Prime demo/owner user ids from PostgreSQL; fall back to fixture ids only when offline."""
    try:
        with SessionLocal() as db:
            _USER_ID_CACHE["demo"] = get_user_id_by_slug(db, "demo")
            _USER_ID_CACHE["owner"] = get_user_id_by_slug(db, "owner")
    except Exception:
        _USER_ID_CACHE.setdefault("demo", 2)
        _USER_ID_CACHE.setdefault("owner", 1)


def _owner_user_id() -> int:
    """Resolve the owner user id from PostgreSQL, falling back to 1 when offline."""
    _prime_user_id_cache()
    return _USER_ID_CACHE.get("owner", 1)


@pytest.fixture
def client():
    _prime_user_id_cache()
    return TestClient(app)


def test_demo_surface_eval_run_permitted_under_demo_tenant(client, monkeypatch):
    """POST /api/eval/run is allowed on /demo and scoped to the demo tenant."""
    import server.routes.eval as eval_routes

    async def _fake_worker(**kwargs):
        return None

    monkeypatch.setattr(eval_routes, "_run_evaluation_worker", _fake_worker)
    monkeypatch.setattr(eval_routes, "active_run_tasks", {})

    run_id = "eval-cancel-async-tasks-demo-live-001"
    resp = client.post(
        "/api/eval/run",
        headers={"X-OpenEval-Surface": "demo", "X-Forwarded-For": "203.0.113.211"},
        json={"task_id": "cancel-async-tasks", "run_id": run_id},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["status"] == "running"
    assert run_id in eval_routes.active_run_tasks

    detail = client.get(
        f"/api/eval/runs/{run_id}",
        headers={"X-OpenEval-Surface": "demo", "X-Forwarded-For": "203.0.113.211"},
    )
    assert detail.status_code == 200
    assert detail.json()["run_id"] == run_id

    global_run_store.delete_run(run_id)


def test_demo_surface_delete_run_still_rejected(client):
    """Non-launch mutations (delete/keys/policy) remain blocked on /demo."""
    resp = client.delete(
        "/api/eval/runs/eval-cancel-async-tasks-demo-delete-001",
        headers={"X-OpenEval-Surface": "demo"},
    )
    assert resp.status_code == 401
    assert "read-only" in resp.json()["detail"].lower()


def test_list_runs_includes_live_demo_tenant_run():
    """Live demo runs that are not in the static fixture allowlist stay visible."""
    _prime_user_id_cache()
    current_user_slug.set("demo")
    current_user_id.set(_USER_ID_CACHE.get("demo", 2))

    run_id = "eval-cancel-async-tasks-live-demo-list-001"
    record = global_run_store.create_run(
        task_id="cancel-async-tasks",
        model="gemini-3.8-flash",
        run_id=run_id,
    )
    try:
        listed_ids = {s.session_id for s in global_run_store.list_runs()}
        assert record.session_id in listed_ids
    finally:
        global_run_store.delete_run(run_id)


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
    mock_gw.evaluate_tool_call_async = AsyncMock(
        return_value=ReviewRecord(
            session_id="session-live-00000001",
            tool_name="bash",
            decision="allow",
            explanation="ok",
        )
    )
    monkeypatch.setattr(watcher_gate.PolicyGateway, "get_shared", classmethod(lambda cls: mock_gw))
    import importlib

    app_module = importlib.import_module("server.app")
    monkeypatch.setattr(app_module, "broadcast_watcher_event", AsyncMock(return_value=None))

    current_user_slug.set("owner")
    current_user_id.set(_owner_user_id())

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


@requires_postgres
def test_demo_status_reports_seeded_session_count(client: TestClient, monkeypatch) -> None:
    """The public status endpoint must read the demo tenant, not the anonymous caller."""
    monkeypatch.setenv("OPENEVAL_DEMO_SEED", "1")

    resp = client.get("/api/demo/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["demo_seed"] is True
    assert body["demo_mode"] is True
    assert body["seeded_sessions"] > 0
    assert body["seeded_eval_runs"] > 0


@requires_postgres
@pytest.mark.asyncio
async def test_demo_status_restores_tenant_context(monkeypatch) -> None:
    """Seeding under the demo tenant must not leak into the ambient request context."""
    from server.app import demo_status

    monkeypatch.setenv("OPENEVAL_DEMO_SEED", "1")
    owner_uid = _owner_user_id()
    current_user_slug.set("owner")
    current_user_id.set(owner_uid)

    body = await demo_status()

    assert body["demo_seed"] is True
    assert current_user_slug.get() == "owner"
    assert current_user_id.get() == owner_uid


def test_demo_ingest_rejects_server_side_file_path(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/agent-logs/ingest",
        headers={"X-OpenEval-Surface": "demo", "X-Forwarded-For": "203.0.113.11"},
        json={"file_path": "/tmp/tenant-iso-demo-blocked.jsonl", "agent_type": "auto"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == (
        "Server-side file_path ingestion is disabled in demo mode; upload file_content instead."
    )


def test_demo_post_rate_limit_returns_429_after_burst(client: TestClient, monkeypatch) -> None:
    import importlib

    from server.rate_limiter import TokenBucketRateLimiter

    app_module = importlib.import_module("server.app")
    limiter = TokenBucketRateLimiter(capacity=10, refill_rate_per_sec=0.2, redis_url="")
    monkeypatch.setattr(app_module, "demo_post_rate_limiter", limiter)

    headers = {"X-OpenEval-Surface": "demo", "X-Forwarded-For": "203.0.113.42"}
    payload = {"file_path": "/tmp/tenant-iso-demo-rate-limit.jsonl", "agent_type": "auto"}

    burst = [
        client.post("/api/v1/agent-logs/ingest", headers=headers, json=payload) for _ in range(10)
    ]
    assert [r.status_code for r in burst] == [403] * 10

    blocked = client.post("/api/v1/agent-logs/ingest", headers=headers, json=payload)
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == (
        "Demo rate limit exceeded. Please sign in for unrestricted access."
    )

    # GET stays unrestricted on the demo surface even after the POST bucket drains.
    read_resp = client.get(
        "/api/v1/analyzer/summary",
        headers={"X-OpenEval-Surface": "demo", "X-Forwarded-For": "203.0.113.42"},
    )
    assert read_resp.status_code == 200


def test_demo_post_rate_limit_keyed_on_last_forwarded_hop(client: TestClient, monkeypatch) -> None:
    """Rotating the spoofable first X-Forwarded-For hop must not reset the bucket.

    The reverse proxy appends the real peer as the last hop, so only that hop keys
    the demo bucket. A caller that forges a new left-most address on every request
    still has to pay for the burst it already spent.
    """
    import importlib

    from server.rate_limiter import TokenBucketRateLimiter

    app_module = importlib.import_module("server.app")
    limiter = TokenBucketRateLimiter(capacity=10, refill_rate_per_sec=0.2, redis_url="")
    monkeypatch.setattr(app_module, "demo_post_rate_limiter", limiter)

    real_peer = "198.51.100.7"
    payload = {"file_path": "/tmp/tenant-iso-demo-antisp00f.jsonl", "agent_type": "auto"}

    for hop in range(10):
        resp = client.post(
            "/api/v1/agent-logs/ingest",
            headers={
                "X-OpenEval-Surface": "demo",
                "X-Forwarded-For": f"203.0.113.{hop + 1}, {real_peer}",
            },
            json=payload,
        )
        assert resp.status_code == 403, resp.text

    # A fresh spoofed first hop does not buy a fresh bucket: the last hop is unchanged.
    blocked = client.post(
        "/api/v1/agent-logs/ingest",
        headers={
            "X-OpenEval-Surface": "demo",
            "X-Forwarded-For": f"203.0.113.250, {real_peer}",
        },
        json=payload,
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == (
        "Demo rate limit exceeded. Please sign in for unrestricted access."
    )

    # Sanity check: a genuinely different proxy-appended peer is a distinct bucket,
    # proving the key comes from the last hop rather than a constant/global bucket.
    other_peer = client.post(
        "/api/v1/agent-logs/ingest",
        headers={
            "X-OpenEval-Surface": "demo",
            "X-Forwarded-For": "203.0.113.250, 198.51.100.8",
        },
        json=payload,
    )
    assert other_peer.status_code == 403


def test_anonymous_ingest_with_file_path_returns_401(client: TestClient, monkeypatch) -> None:
    """Anonymous callers must not read arbitrary server files via file_path."""
    monkeypatch.setenv("OPENEVAL_AUTH_DISABLED", "0")
    monkeypatch.delenv("OPENEVAL_ENV", raising=False)

    resp = client.post(
        "/api/v1/agent-logs/ingest",
        json={"file_path": "/etc/openeval.env", "agent_type": "auto"},
    )

    assert resp.status_code == 401
    assert "Authentication required" in resp.json()["detail"]


def test_production_authenticated_ingest_with_file_path_returns_403(
    client: TestClient, monkeypatch
) -> None:
    """Even an authenticated tenant cannot read server files on a hosted deployment."""
    monkeypatch.setenv("OPENEVAL_AUTH_DISABLED", "0")
    monkeypatch.setenv("OPENEVAL_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "test-production-session-secret-for-hmac")
    client.cookies.set(
        SESSION_COOKIE_NAME,
        create_session_token(_owner_user_id(), "owner"),
    )

    resp = client.post(
        "/api/v1/agent-logs/ingest",
        json={"file_path": "/etc/passwd", "agent_type": "auto"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == (
        "Server-side file_path ingestion is disabled in production; upload file_content instead."
    )


@pytest.mark.asyncio
async def test_local_authenticated_file_path_ingest_still_allowed(tmp_path, monkeypatch) -> None:
    """Non-production authenticated file_path ingestion keeps working for local dev."""
    from server.agent_log_loader import UniversalAgentLogLoader

    monkeypatch.delenv("OPENEVAL_ENV", raising=False)

    log_path = tmp_path / "local-agent-session.jsonl"
    log_path.write_text(
        json.dumps({"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}})
        + "\n",
        encoding="utf-8",
    )
    stubbed_session = Session(
        session_id="local-file-path-ok",
        run_id="local-file-path-ok",
        agent_type="cursor",
        task_id="local-dev",
        model="m",
        status="completed",
    )
    monkeypatch.setattr(
        UniversalAgentLogLoader,
        "ingest_agent_log_file",
        staticmethod(lambda *args, **kwargs: stubbed_session),
    )

    current_user_slug.set("owner")
    current_user_id.set(_owner_user_id())
    request = MagicMock(headers={})

    body = await ingest_agent_log(IngestAgentLogPayload(file_path=str(log_path)), request)

    assert body["status"] == "ingested"
    assert body["session"]["session_id"] == "local-file-path-ok"
