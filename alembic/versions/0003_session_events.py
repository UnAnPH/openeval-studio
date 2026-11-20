"""Create session_events append-only event log table.

Revision ID: 0003_session_events
Revises: 0002_sessions_tenant_pk
Create Date: 2026-10-11 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_session_events"
down_revision: str | None = "0002_sessions_tenant_pk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("step_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_session_events_user_session_step",
        "session_events",
        ["user_id", "session_id", "step_index"],
    )
    op.create_index(
        "ix_session_events_user_created",
        "session_events",
        ["user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_session_events_user_created", table_name="session_events")
    op.drop_index("ix_session_events_user_session_step", table_name="session_events")
    op.drop_table("session_events")
