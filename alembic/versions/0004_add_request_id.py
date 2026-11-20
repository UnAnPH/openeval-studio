"""Add request_id column and index to reviews table.

Revision ID: 0004_add_request_id
Revises: 0003_session_events
Create Date: 2026-10-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_add_request_id"
down_revision: str | None = "0003_session_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reviews", sa.Column("request_id", sa.String(length=64), nullable=True))
    op.create_index("ix_reviews_request_id", "reviews", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_reviews_request_id", table_name="reviews")
    op.drop_column("reviews", "request_id")
