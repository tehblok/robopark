"""Persist bounded historical closed-ticket discovery progress."""

import sqlalchemy as sa
from alembic import op

revision = "0053_tracker_history_backfill"
down_revision = "0052_tracker_issue_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracker_history_backfill_cursors",
        sa.Column("park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scan_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scan_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_page", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("search_failed", sa.Boolean(), nullable=False),
        sa.Column("page_cap_reached", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tracker_history_backfill_cursors")
