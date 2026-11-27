"""Database & Storage Engine Abstraction for OpenEval Studio.

Introspects the live PostgreSQL connection pool from server.db and manages local
storage directories for file-backed artifacts.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy.pool import QueuePool

from server.db import engine

logger = logging.getLogger("openeval.db")


class DatabaseManager:
    """Manages database connection pool telemetry and storage health probes."""

    def __init__(self) -> None:
        self.engine_type = engine.name

    def get_storage_dir(self) -> str:
        """Resolve the active storage directory on disk."""
        storage_path = Path(os.getenv("WATCHER_STORAGE_DIR", ".runs/watcher")).resolve()
        return str(storage_path)

    def get_status(self) -> dict[str, Any]:
        """Return connectivity status, pool telemetry, and active storage engine."""
        storage_dir = self.get_storage_dir()
        pool = engine.pool
        is_queue_pool = isinstance(pool, QueuePool)
        pool_size = pool.size() if is_queue_pool else 10
        checked_out = pool.checkedout() if is_queue_pool else 0
        overflow = pool.overflow() if is_queue_pool else 0
        checked_in = pool.checkedin() if is_queue_pool else 0

        connected = False
        try:
            from sqlalchemy import text

            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            connected = True
        except Exception:
            logger.warning("Database connectivity probe failed", exc_info=True)
            connected = False

        return {
            "engine": engine.name,
            "connected": connected,
            "has_external_db": True,
            "storage_dir": storage_dir,
            "target": f"{engine.name.capitalize()} (SQLAlchemy connection pool)",
            "pool_size": pool_size,
            "checked_out": checked_out,
            "overflow": overflow,
            "checked_in": checked_in,
        }


# Global singleton
db_manager = DatabaseManager()


def get_db_manager() -> DatabaseManager:
    return db_manager
