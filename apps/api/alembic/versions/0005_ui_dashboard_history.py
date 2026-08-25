"""UI dashboard: park tracker filters and blocker history.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "parks",
        sa.Column("tracker_priority", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "parks",
        sa.Column("tracker_type", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "park_blocker_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("park_id", sa.Integer(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arrived_count", sa.Integer(), nullable=False),
        sa.Column("departed_count", sa.Integer(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["park_id"], ["parks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "park_id",
            "bucket_start",
            name="uq_park_blocker_history_park_bucket",
        ),
    )
    op.create_index(
        op.f("ix_park_blocker_history_park_id"),
        "park_blocker_history",
        ["park_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_park_blocker_history_park_id"), table_name="park_blocker_history")
    op.drop_table("park_blocker_history")
    op.drop_column("parks", "tracker_type")
    op.drop_column("parks", "tracker_priority")
