"""Tests for multi-tenant authentication, registration, session cookies, and API key management."""

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from server.app import app
from server.db import SessionLocal


@pytest.fixture(autouse=True)
def enforce_strict_auth(monkeypatch):
    """Enforce strict authentication middleware for all auth tests."""
    monkeypatch.setenv("OPENEVAL_AUTH_DISABLED", "0")
    # Clean up users and api_keys created during auth tests
    yield
    with SessionLocal() as db:
        db.execute(
            text(
                "DELETE FROM users WHERE slug NOT IN ('demo', 'owner');"
                "DELETE FROM api_keys WHERE user_id NOT IN ("
                "  SELECT id FROM users WHERE slug IN ('demo', 'owner')"
                ");"
            )
        )
        db.commit()


@pytest.fixture
def client():
    return TestClient(app)


def test_register_success_and_headers(client):
    """Registering creates user, bcrypt password, hashed API key, and session cookie."""
    resp = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "alice"
    assert "user_id" in data
    assert data["api_key"].startswith("oe_live_")
    # Assert password_hash is never exposed
    assert "password_hash" not in data
    assert "password" not in data

    # Check session cookie is set
    assert "oe_session" in resp.cookies

    # Verify database persistence
    with SessionLocal() as db:
        user_row = db.execute(
            text("SELECT id, slug, password_hash FROM users WHERE slug = 'alice'")
        ).fetchone()
        assert user_row is not None
        assert user_row[1] == "alice"
        assert str(user_row[2]).startswith("$2b$")  # bcrypt hash format

        expected_hash = hashlib.sha256(data["api_key"].encode("utf-8")).hexdigest()
        key_row = db.execute(
            text("SELECT token_hash FROM api_keys WHERE user_id = :uid"),
            {"uid": user_row[0]},
        ).fetchone()
        assert key_row is not None
        assert key_row[0] == expected_hash


def test_register_second_user_generates_distinct_api_key(client):
    """Registering distinct users yields separate user IDs and API keys."""
    resp1 = client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password123"},
    )
    assert resp1.status_code == 200
    key1 = resp1.json()["api_key"]
    uid1 = resp1.json()["user_id"]

    resp2 = client.post(
        "/api/auth/register",
        json={"username": "charlie", "password": "password456"},
    )
    assert resp2.status_code == 200
    key2 = resp2.json()["api_key"]
    uid2 = resp2.json()["user_id"]

    assert uid1 != uid2
    assert key1 != key2


def test_register_demo_username_rejected(client):
    """Username 'demo' is strictly rejected with 400 Bad Request."""
    resp = client.post(
        "/api/auth/register",
        json={"username": "demo", "password": "password123"},
    )
    assert resp.status_code == 400
    assert "demo" in resp.json()["detail"].lower()


def test_register_duplicate_username_rejected(client):
    """Duplicate username registration fails with 400 Bad Request."""
    client.post(
        "/api/auth/register",
        json={"username": "david", "password": "password123"},
    )
    resp = client.post(
        "/api/auth/register",
        json={"username": "david", "password": "different_password"},
    )
    assert resp.status_code == 400
    assert "already registered" in resp.json()["detail"].lower()


def test_login_and_logout(client):
    """Login validates password and sets cookie; logout clears cookie."""
    client.post(
        "/api/auth/register",
        json={"username": "eve", "password": "correct_password"},
    )

    # Failed login with wrong password
    bad_login = client.post(
        "/api/auth/login",
        json={"username": "eve", "password": "wrong_password"},
    )
    assert bad_login.status_code == 401

    # Successful login
    good_login = client.post(
        "/api/auth/login",
        json={"username": "eve", "password": "correct_password"},
    )
    assert good_login.status_code == 200
    assert good_login.json()["username"] == "eve"
    assert "oe_session" in good_login.cookies

    # Authenticated /me endpoint using session cookie
    me_resp = client.get("/api/auth/me", cookies=good_login.cookies)
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "eve"

    # Logout
    logout_resp = client.post("/api/auth/logout")
    assert logout_resp.status_code == 200

    # Unauthenticated /me fails with 401
    me_after_logout = client.get("/api/auth/me")
    assert me_after_logout.status_code == 401


def test_api_key_authentication(client):
    """API key can authenticate via Authorization Bearer or X-OpenEval-Key."""
    reg = client.post(
        "/api/auth/register",
        json={"username": "frank", "password": "password123"},
    )
    api_key = reg.json()["api_key"]

    # Bearer header
    resp1 = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["username"] == "frank"

    # X-OpenEval-Key header
    resp2 = client.get(
        "/api/auth/me",
        headers={"X-OpenEval-Key": api_key},
    )
    assert resp2.status_code == 200
    assert resp2.json()["username"] == "frank"

    # Invalid key returns 401
    resp_invalid = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer oe_live_invalidkey12345"},
    )
    assert resp_invalid.status_code == 401


def test_api_key_rotation(client):
    """Rotating API key invalidates the previous key and issues a new one."""
    reg = client.post(
        "/api/auth/register",
        json={"username": "grace", "password": "password123"},
    )
    old_key = reg.json()["api_key"]
    cookies = reg.cookies

    # Rotate key using authenticated session
    rot = client.post("/api/auth/rotate-key", cookies=cookies)
    assert rot.status_code == 200
    new_key = rot.json()["api_key"]
    assert new_key != old_key
    assert new_key.startswith("oe_live_")

    # Old key returns 401
    resp_old = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {old_key}"},
    )
    assert resp_old.status_code == 401

    # New key returns 200
    resp_new = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {new_key}"},
    )
    assert resp_new.status_code == 200
    assert resp_new.json()["username"] == "grace"


def test_unauthenticated_protected_route_fails(client):
    """Unauthenticated requests to protected endpoints return 401."""
    resp = client.get("/api/watcher/sessions")
    assert resp.status_code == 401


def test_demo_surface_read_allowed(client):
    """Read requests with X-OpenEval-Surface: demo succeed without credentials."""
    resp = client.get(
        "/api/watcher/config",
        headers={"X-OpenEval-Surface": "demo"},
    )
    assert resp.status_code == 200


def test_demo_surface_write_rejected_without_auth(client):
    """Mutating evaluate calls with X-OpenEval-Surface: demo reject unauthenticated writes with 401."""
    resp = client.post(
        "/api/watcher/evaluate",
        headers={"X-OpenEval-Surface": "demo"},
        json={
            "tool_name": "bash",
            "tool_input": "echo 'eval test'",
            "agent_id": "test-agent",
        },
    )
    assert resp.status_code == 401
    assert "authentication required" in resp.json()["detail"].lower()


def test_demo_surface_write_with_auth_writes_as_user(client):
    """Mutating calls with demo header and valid API key write under the authenticated user."""
    reg = client.post(
        "/api/auth/register",
        json={"username": "heidi", "password": "password123"},
    )
    api_key = reg.json()["api_key"]

    resp = client.post(
        "/api/watcher/evaluate",
        headers={
            "X-OpenEval-Surface": "demo",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "tool_name": "bash",
            "tool_input": "echo 'authenticated eval'",
            "agent_id": "test-agent",
        },
    )
    assert resp.status_code == 200
    # The session belongs to 'heidi'
    with SessionLocal() as db:
        heidi_uid = db.execute(text("SELECT id FROM users WHERE slug = 'heidi'")).scalar()
        demo_uid = db.execute(text("SELECT id FROM users WHERE slug = 'demo'")).scalar()

        heidi_sessions = db.execute(
            text("SELECT count(*) FROM sessions WHERE user_id = :uid"),
            {"uid": heidi_uid},
        ).scalar()
        assert (heidi_sessions or 0) > 0

        # Verify demo user was not written to
        demo_sessions = db.execute(
            text("SELECT count(*) FROM sessions WHERE user_id = :uid AND project_name = 'heidi'"),
            {"uid": demo_uid},
        ).scalar()
        assert demo_sessions == 0


def test_public_routes_bypass_auth(client):
    """Public endpoints (/api/health, /demo) do not require authentication."""
    health_resp = client.get("/api/health")
    assert health_resp.status_code == 200

    demo_resp = client.get("/demo")
    assert demo_resp.status_code == 200


def test_openeval_api_key_env_writes_as_owner(client, monkeypatch):
    """OPENEVAL_API_KEY environment variable writes and authenticates as owner."""
    monkeypatch.setenv("OPENEVAL_API_KEY", "system-override-secret-key")
    resp = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer system-override-secret-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "owner"


def test_per_user_session_isolation_and_demo_fixtures(client):
    """Smoke test: Register user1 and user2, submit evaluate calls with their keys,
    and verify user1 sees only user1's sessions, user2 sees only user2's sessions,
    and demo surface sees only demo fixtures.
    """
    from server.demo_seed import seed_demo_data

    seed_demo_data()

    # 1. Register user1 & submit evaluate call
    u1 = client.post(
        "/api/auth/register", json={"username": "user1_iso", "password": "passuser1"}
    ).json()
    client.post(
        "/api/watcher/evaluate",
        headers={"Authorization": f"Bearer {u1['api_key']}"},
        json={
            "tool_name": "bash",
            "tool_input": "echo user1_unique_token",
            "agent_id": "agent-user1",
        },
    )

    # 2. Register user2 & submit evaluate call
    u2 = client.post(
        "/api/auth/register", json={"username": "user2_iso", "password": "passuser2"}
    ).json()
    client.post(
        "/api/watcher/evaluate",
        headers={"Authorization": f"Bearer {u2['api_key']}"},
        json={
            "tool_name": "bash",
            "tool_input": "echo user2_unique_token",
            "agent_id": "agent-user2",
        },
    )

    # 3. User1 queries sessions
    s1_resp = client.get(
        "/api/v1/watcher/sessions", headers={"Authorization": f"Bearer {u1['api_key']}"}
    )
    assert s1_resp.status_code == 200
    s1_sessions = s1_resp.json()
    assert len(s1_sessions) == 1
    assert "agent-user1" in str(s1_sessions[0])
    assert "user2_unique_token" not in str(s1_sessions[0])

    # 4. User2 queries sessions
    s2_resp = client.get(
        "/api/v1/watcher/sessions", headers={"Authorization": f"Bearer {u2['api_key']}"}
    )
    assert s2_resp.status_code == 200
    s2_sessions = s2_resp.json()
    assert len(s2_sessions) == 1
    assert "agent-user2" in str(s2_sessions[0])
    assert "user1_unique_token" not in str(s2_sessions[0])

    # 5. Demo surface queries sessions (unauthenticated read)
    demo_resp = client.get("/api/v1/watcher/sessions", headers={"X-OpenEval-Surface": "demo"})
    assert demo_resp.status_code == 200
    demo_sessions = demo_resp.json()
    assert len(demo_sessions) > 0
    assert not any("agent-user1" in str(s) for s in demo_sessions)
    assert not any("agent-user2" in str(s) for s in demo_sessions)


def test_evaluate_persists_while_demo_seed_enabled(client, monkeypatch):
    """OPENEVAL_DEMO_SEED=1 only loads fixtures; authenticated evaluate still writes the caller's session."""
    monkeypatch.setenv("OPENEVAL_DEMO_SEED", "1")
    monkeypatch.setenv("OPENEVAL_AUTH_DISABLED", "0")

    from server.demo_seed import seed_demo_data

    seed_demo_data()

    reg = client.post(
        "/api/auth/register",
        json={"username": "hook_user_seed", "password": "password123"},
    )
    assert reg.status_code == 200
    api_key = reg.json()["api_key"]

    eval_resp = client.post(
        "/api/watcher/evaluate",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "tool_name": "bash",
            "tool_input": "echo hook_user_seed_token",
            "agent_id": "agent-hook-seed",
            "session_id": "hook-seed-session-1",
        },
    )
    assert eval_resp.status_code == 200

    sess_resp = client.get(
        "/api/v1/watcher/sessions",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert sess_resp.status_code == 200
    sessions = sess_resp.json()
    assert len(sessions) >= 1
    assert any(
        s.get("session_id") == "hook-seed-session-1" or "agent-hook-seed" in str(s)
        for s in sessions
    )

    # Demo surface still only sees fixture rows
    demo_resp = client.get(
        "/api/v1/watcher/sessions",
        headers={"X-OpenEval-Surface": "demo"},
    )
    assert demo_resp.status_code == 200
    demo_sessions = demo_resp.json()
    assert not any(s.get("session_id") == "hook-seed-session-1" for s in demo_sessions)


def test_api_key_lookup_with_short_env_key_succeeds(client, monkeypatch):
    """When OPENEVAL_API_KEY is set to a short string, a registered user with oe_live_... key returns 200."""
    monkeypatch.setenv("OPENEVAL_API_KEY", "short")
    reg = client.post(
        "/api/auth/register",
        json={"username": "short_env_user", "password": "password123"},
    )
    assert reg.status_code == 200
    api_key = reg.json()["api_key"]

    resp = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "short_env_user"


def test_production_env_refuses_default_session_secret(client, monkeypatch):
    """When OPENEVAL_ENV=production and SESSION_SECRET is default or unset, registration refuses cookies with 500."""
    monkeypatch.setenv("OPENEVAL_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "openeval-dev-secret-key-32-chars-long")

    resp = client.post(
        "/api/auth/register",
        json={"username": "prod_fail_user", "password": "password123"},
    )
    assert resp.status_code == 500
    assert "Insecure or missing SESSION_SECRET" in resp.json().get("detail", "")
