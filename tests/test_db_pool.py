"""Unit tests for DatabaseManager and embedded DuckDB storage status."""

from server.db_pool import DatabaseManager, get_db_manager


def test_db_manager_status():
    manager = DatabaseManager()
    status = manager.get_status()

    assert status["engine"] == "duckdb"
    assert status["connected"] is True
    assert status["has_external_db"] is False
    assert "DuckDB" in status["target"]
    assert "storage_dir" in status
    assert len(status["storage_dir"]) > 0


def test_get_db_manager_singleton():
    m1 = get_db_manager()
    m2 = get_db_manager()
    assert m1 is m2
    assert m1.get_status()["engine"] == "duckdb"
