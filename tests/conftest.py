import os
import shutil
import tempfile

import pytest
from alembic.config import Config

from alembic import command

_TEST_STORAGE = tempfile.mkdtemp(prefix="watcher_test_")
os.environ["WATCHER_STORAGE_DIR"] = _TEST_STORAGE
# Keep unit tests deterministic (no live LLM calls in PolicyGateway)
os.environ["OPENEVAL_WATCHER_USE_LLM"] = "0"
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://openeval:openeval@127.0.0.1:5432/openeval"
)


@pytest.fixture(autouse=True, scope="session")
def setup_test_database():
    """Ensure database schema is up-to-date for test suite."""
    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
    except Exception as e:
        print(f"Notice: Alembic auto-upgrade in test setup: {e}")
    yield


@pytest.fixture(autouse=True, scope="session")
def test_watcher_storage():
    yield _TEST_STORAGE
    shutil.rmtree(_TEST_STORAGE, ignore_errors=True)


@pytest.fixture(autouse=True)
def reset_tenant_context():
    """Ensure every test runs under the 'owner' tenant context with a clean state."""
    from sqlalchemy import text

    from server.db import SessionLocal, current_user_id, current_user_slug
    from server.watcher_store import get_watcher_store

    current_user_slug.set("owner")
    current_user_id.set(None)
    with SessionLocal() as db:
        db.execute(text("TRUNCATE TABLE sessions, reviews, trajectory_events CASCADE"))
        db.commit()
    store = get_watcher_store()
    with store._lock:
        store._sessions.clear()
        store._trajectories.clear()
        store._reviews.clear()

    yield

    with SessionLocal() as db:
        db.execute(text("TRUNCATE TABLE sessions, reviews, trajectory_events CASCADE"))
        db.commit()
    with store._lock:
        store._sessions.clear()
        store._trajectories.clear()
        store._reviews.clear()
    current_user_slug.set("owner")
    current_user_id.set(None)
