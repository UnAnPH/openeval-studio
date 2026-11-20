"""Composite primary key on sessions (user_id, id).

Revision ID: 0002_sessions_tenant_pk
Revises: 0001_init
Create Date: 2026-10-11 12:00:00.000000

Session ids are only unique per tenant. Reviews and trajectory events follow
(user_id, session_id) so one tenant cannot overwrite or cascade-delete another.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_sessions_tenant_pk"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_foreign_keys_to_sessions(table: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys(table):
        if fk.get("referred_table") != "sessions":
            continue
        name = fk.get("name")
        if name:
            op.drop_constraint(name, table, type_="foreignkey")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    pk = inspector.get_pk_constraint("sessions")
    columns = list(pk.get("constrained_columns") or [])
    if columns == ["user_id", "id"]:
        return

    _drop_foreign_keys_to_sessions("reviews")
    _drop_foreign_keys_to_sessions("trajectory_events")

    pk_name = pk.get("name") or "sessions_pkey"
    op.drop_constraint(pk_name, "sessions", type_="primary")
    op.create_primary_key("sessions_pkey", "sessions", ["user_id", "id"])
    # Drop rows that cannot satisfy the new composite foreign keys.
    op.execute(
        sa.text(
            "DELETE FROM reviews r WHERE NOT EXISTS ("
            "SELECT 1 FROM sessions s WHERE s.user_id = r.user_id AND s.id = r.session_id)"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM trajectory_events e WHERE NOT EXISTS ("
            "SELECT 1 FROM sessions s WHERE s.user_id = e.user_id AND s.id = e.session_id)"
        )
    )
    op.create_foreign_key(
        "reviews_session_user_fkey",
        "reviews",
        "sessions",
        ["user_id", "session_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "trajectory_events_session_user_fkey",
        "trajectory_events",
        "sessions",
        ["user_id", "session_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    pk = inspector.get_pk_constraint("sessions")
    columns = list(pk.get("constrained_columns") or [])
    if columns == ["id"]:
        return

    _drop_foreign_keys_to_sessions("reviews")
    _drop_foreign_keys_to_sessions("trajectory_events")
    pk_name = pk.get("name") or "sessions_pkey"
    op.drop_constraint(pk_name, "sessions", type_="primary")
    op.create_primary_key("sessions_pkey", "sessions", ["id"])
    op.create_foreign_key(
        "reviews_session_id_fkey",
        "reviews",
        "sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "trajectory_events_session_id_fkey",
        "trajectory_events",
        "sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
