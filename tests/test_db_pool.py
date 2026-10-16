"""Unit tests for DatabaseManager and PostgreSQL pool telemetry."""

from server.db_pool import DatabaseManager, get_db_manager


def test_db_manager_status():
    manager = DatabaseManager()
    status = manager.get_status()

    assert status["engine"] in ("postgresql", "sqlite")
    assert status["connected"] is True
    assert status["has_external_db"] is True
    assert "pool_size" in status
    assert status["pool_size"] == 10
    assert "checked_out" in status
    assert "overflow" in status
    assert "storage_dir" in status
    assert len(status["storage_dir"]) > 0


def test_get_db_manager_singleton():
    m1 = get_db_manager()
    m2 = get_db_manager()
    assert m1 is m2
    assert m1.get_status()["engine"] in ("postgresql", "sqlite")
    assert m1.get_status()["pool_size"] == 10


def test_db_manager_probe_failure(monkeypatch):
    """Verify connected is False when live DB probe fails."""
    from unittest.mock import MagicMock

    import server.db_pool

    mock_engine = MagicMock()
    mock_engine.connect.side_effect = OSError("DB down")
    mock_engine.name = "postgresql"
    mock_engine.pool = MagicMock()
    mock_engine.pool.size.return_value = 10
    mock_engine.pool.checkedout.return_value = 0
    mock_engine.pool.overflow.return_value = 0
    mock_engine.pool.checkedin.return_value = 0

    monkeypatch.setattr(server.db_pool, "engine", mock_engine)
    mgr = DatabaseManager()
    status = mgr.get_status()
    assert status["connected"] is False
