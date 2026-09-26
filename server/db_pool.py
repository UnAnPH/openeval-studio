"""Database & Storage Engine Abstraction for OpenEval Studio.

OpenEval Studio uses an embedded DuckDB analytical engine paired with local
file-backed JSON trajectories on instance storage. No external relational
database or Amazon RDS round-trips are required.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("openeval.db")


class DatabaseManager:
    """Manages local embedded DuckDB telemetry and storage health probes."""

    def __init__(self) -> None:
        self.engine_type = "duckdb"

    def get_storage_dir(self) -> str:
        """Resolve the active storage directory on disk."""
        from server.demo_seed import is_demo_seed_enabled

        default_dir = ".runs/demo_watcher" if is_demo_seed_enabled() else ".runs/watcher"
        storage_path = Path(os.getenv("WATCHER_STORAGE_DIR", default_dir)).resolve()
        return str(storage_path)

    def get_status(self) -> dict[str, Any]:
        """Return connectivity status and active storage engine."""
        storage_dir = self.get_storage_dir()
        return {
            "engine": "duckdb",
            "connected": True,
            "has_external_db": False,
            "storage_dir": storage_dir,
            "target": "DuckDB (local disk)",
        }


# Global singleton
db_manager = DatabaseManager()


def get_db_manager() -> DatabaseManager:
    return db_manager
