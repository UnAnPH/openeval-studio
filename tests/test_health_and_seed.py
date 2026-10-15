"""Tests for /api/health database probe and idempotent demo data seeding."""

from sqlalchemy import text
from starlette.testclient import TestClient

from schemas.watcher_models import Session
from server.app import app, get_db_opener, probe_database
from server.db import SessionLocal, current_user_id, current_user_slug
from server.demo_seed import seed_demo_data
from server.watcher_store import get_watcher_store


class _FailingSession:
    """Context manager whose enter raises, standing in for a down database."""

    def __enter__(self) -> None:
        raise Exception("Connection refused / database unreachable")

    def __exit__(self, *_args: object) -> bool:
        return False


def _failing_opener() -> _FailingSession:
    return _FailingSession()


def test_health_probe_returns_200_when_connected():
    """Verify GET /api/health returns 200 with status ok and db connected."""
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] == "connected"


def test_health_probe_returns_503_when_database_down():
    """Verify GET /api/health returns 503 with status degraded when PostgreSQL fails.

    Inverting the opener to a live database makes this assertion fail.
    """
    app.dependency_overrides[get_db_opener] = lambda: _failing_opener
    try:
        with TestClient(app) as client:
            resp = client.get("/api/health")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "degraded"
            assert data["db"] == "disconnected"
    finally:
        app.dependency_overrides.pop(get_db_opener, None)


def test_seed_demo_data_is_idempotent_and_preserves_owner_rows():
    """Verify seed_demo_data runs idempotently and does not mutate or wipe owner rows."""
    store = get_watcher_store()

    # 1. Create a real owner session
    current_user_slug.set("owner")
    current_user_id.set(None)
    owner_session = Session(
        project_name="private-owner-work",
        agent_type="antigravity",
        model="google/gemini-2.5-flash",
        status="active",
    )
    store.create_session(owner_session)

    # Verify owner row exists
    with SessionLocal() as db:
        owner_count_before = db.execute(
            text(
                "SELECT count(*) FROM sessions WHERE user_id = (SELECT id FROM users WHERE slug = 'owner')"
            )
        ).scalar()
        assert owner_count_before == 1

    # 2. Run seed_demo_data twice
    first_res = seed_demo_data()
    second_res = seed_demo_data()

    assert first_res["sessions"] >= 4
    assert second_res["sessions"] == first_res["sessions"]

    # 3. Verify owner rows were NEVER wiped or altered
    with SessionLocal() as db:
        owner_count_after = db.execute(
            text(
                "SELECT count(*) FROM sessions WHERE user_id = (SELECT id FROM users WHERE slug = 'owner')"
            )
        ).scalar()
        assert owner_count_after == 1

        demo_count = db.execute(
            text(
                "SELECT count(*) FROM sessions WHERE user_id = (SELECT id FROM users WHERE slug = 'demo')"
            )
        ).scalar()
        assert (demo_count or 0) >= 4

    # Verify the owner session is still retrievable as owner
    current_user_slug.set("owner")
    current_user_id.set(None)
    fetched_owner = store.get_session(owner_session.session_id)
    assert fetched_owner is not None
    assert fetched_owner.project_name == "private-owner-work"


def test_probe_database_is_false_when_opener_fails():
    """A down database is a failed probe. A succeeding opener would make this fail."""
    assert probe_database(_failing_opener) is False


def test_evaluate_fails_closed_when_database_not_ready():
    """Mutating evaluate returns 503 while db_ready is false.

    Leaving db_ready true would make this assertion fail.
    """
    orig_ready = getattr(app.state, "db_ready", True)
    try:
        with TestClient(app) as client:
            app.state.db_ready = False
            eval_resp = client.post(
                "/api/v1/watcher/evaluate",
                json={
                    "tool_name": "bash",
                    "tool_input": "echo 'failing-db'",
                    "agent_id": "test-agent",
                },
            )
            assert eval_resp.status_code == 503
            assert "Database" in eval_resp.json().get("detail", "")
    finally:
        app.state.db_ready = orig_ready
