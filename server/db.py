"""Database Connection & Multi-Tenant Context Engine for OpenEval Studio.

Manages SQLAlchemy 2 engine, SessionLocal factory, and request-scoped tenant
ContextVars (current_user_slug, current_user_id) for PostgreSQL 16.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger("openeval.db")

_DEFAULT_DB_URL = "postgresql+psycopg://openeval:openeval@127.0.0.1:5432/openeval"


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", _DEFAULT_DB_URL).strip()
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = get_database_url()

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=5,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Context variables populated by middleware per request
current_user_slug: ContextVar[str] = ContextVar("current_user_slug", default="owner")
current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)

_USER_ID_CACHE: dict[str, int] = {}


def get_user_id_by_slug(db: Session, slug: str) -> int:
    """Resolve user_id for a given slug, creating if missing."""
    if slug in _USER_ID_CACHE:
        return _USER_ID_CACHE[slug]

    row = db.execute(
        text("SELECT id FROM users WHERE slug = :slug"),
        {"slug": slug},
    ).fetchone()

    if row:
        uid = int(row[0])
        _USER_ID_CACHE[slug] = uid
        return uid

    # Upsert user if missing
    res = db.execute(
        text(
            "INSERT INTO users (slug) VALUES (:slug) "
            "ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug "
            "RETURNING id"
        ),
        {"slug": slug},
    )
    db.commit()
    inserted = res.fetchone()
    uid = int(inserted[0]) if inserted else 1
    _USER_ID_CACHE[slug] = uid
    return uid


def get_current_user_id(db: Session | None = None) -> int:
    """Get the active user_id from contextvar or query database by active slug."""
    uid = current_user_id.get()
    if uid is not None:
        return uid

    slug = current_user_slug.get() or "owner"
    if db is not None:
        resolved = get_user_id_by_slug(db, slug)
        current_user_id.set(resolved)
        return resolved

    with SessionLocal() as session:
        resolved = get_user_id_by_slug(session, slug)
        current_user_id.set(resolved)
        return resolved


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Yield a database session context."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
