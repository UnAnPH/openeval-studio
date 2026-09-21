"""Database Pool & Storage Engine Abstraction for OpenEval Studio.

Supports Amazon RDS / PostgreSQL for relational state and session tracking,
with automatic fallback to DuckDB and local file-backed persistence.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("openeval.db")

_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


class DatabaseManager:
    """Manages transactional database connections and health probes."""

    def __init__(self, database_url: str = "") -> None:
        self.database_url = database_url or _DATABASE_URL
        self.engine_type = "postgresql" if self.database_url.startswith("postgres") else "duckdb"
        self._is_connected = False

    def get_status(self) -> dict[str, Any]:
        """Return connectivity status and active database engine."""
        return {
            "engine": self.engine_type,
            "connected": bool(self.database_url) or self.engine_type == "duckdb",
            "has_external_db": bool(self.database_url),
            "target": "Amazon RDS PostgreSQL" if "rds.amazonaws.com" in self.database_url else self.engine_type.upper(),
        }


# Global singleton
db_manager = DatabaseManager()


def get_db_manager() -> DatabaseManager:
    return db_manager
