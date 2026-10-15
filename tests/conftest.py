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
os.environ["OPENEVAL_CIRCUIT_STATE_FILE"] = os.path.join(_TEST_STORAGE, "circuit_breaker.json")
os.environ["OPENEVAL_SUBAGENT_REGISTRY"] = os.path.join(_TEST_STORAGE, "subagent_registry.json")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://openeval:openeval@127.0.0.1:5432/openeval_test"
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")


def _test_db_truncate_allowed() -> bool:
    """Refuse to wipe a developer database that is not an explicit test database."""
    if os.environ.get("ALLOW_TEST_DB_TRUNCATE", "").strip() == "1":
        return True
    return "test" in os.environ.get("DATABASE_URL", "").lower()


def _truncate_watcher_tables() -> None:
    from sqlalchemy import text

    from server.db import SessionLocal

    if not _test_db_truncate_allowed():
        raise RuntimeError(
            "Refusing to TRUNCATE sessions, reviews, and trajectory_events. "
            "DATABASE_URL does not contain 'test'. Set ALLOW_TEST_DB_TRUNCATE=1 "
            "only when this database is disposable."
        )
    try:
        with SessionLocal() as db:
            db.execute(
                text("TRUNCATE TABLE sessions, reviews, trajectory_events, session_events CASCADE")
            )
            db.commit()
    except Exception:
        # DB unreachable during offline unit tests
        pass


os.environ.setdefault("OPENEVAL_AUTH_DISABLED", "1")


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
    from server.app import app
    from server.db import current_user_id, current_user_slug
    from server.watcher_store import get_watcher_store

    app.state.db_ready = True
    current_user_slug.set("owner")
    current_user_id.set(None)
    _truncate_watcher_tables()
    store = get_watcher_store()
    with store._lock:
        store._sessions.clear()
        store._trajectories.clear()
        store._reviews.clear()
        store._session_owners.clear()

    yield

    _truncate_watcher_tables()
    with store._lock:
        store._sessions.clear()
        store._trajectories.clear()
        store._reviews.clear()
        store._session_owners.clear()
    current_user_slug.set("owner")
    current_user_id.set(None)
