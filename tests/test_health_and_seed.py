"""Tests for /api/health database probe and idempotent demo data seeding."""

from unittest.mock import MagicMock, patch

from sqlalchemy import text
from starlette.testclient import TestClient

from schemas.watcher_models import Session
from server.app import app
from server.db import SessionLocal, current_user_id, current_user_slug
from server.demo_seed import seed_demo_data
from server.watcher_store import get_watcher_store


def test_health_probe_returns_200_when_connected():
    """Verify GET /api/health returns 200 with status ok and db connected."""
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] == "connected"


def test_health_probe_returns_503_when_database_down():
    """Verify GET /api/health returns 503 with status degraded when PostgreSQL fails."""
    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("Connection refused / database unreachable")
    mock_session_maker = MagicMock()
    mock_session_maker.return_value.__enter__.return_value = mock_db

    with patch("server.db.SessionLocal", mock_session_maker), TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["db"] == "disconnected"


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
