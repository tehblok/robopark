"""Extend parks with Tracker fields and add platform_settings.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("parks", sa.Column("tracker_queue", sa.String(length=128), nullable=True))
    op.add_column("parks", sa.Column("group_id", sa.Integer(), nullable=True))
    op.add_column("parks", sa.Column("chat_id", sa.Integer(), nullable=True))
    op.add_column(
        "parks",
        sa.Column("feature_reports", sa.Boolean(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "parks",
        sa.Column("feature_blockers", sa.Boolean(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "parks",
        sa.Column("feature_sla_repair", sa.Boolean(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "parks",
        sa.Column(
            "feature_backlog_alerts",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
    op.drop_column("parks", "feature_backlog_alerts")
    op.drop_column("parks", "feature_sla_repair")
    op.drop_column("parks", "feature_blockers")
    op.drop_column("parks", "feature_reports")
    op.drop_column("parks", "chat_id")
    op.drop_column("parks", "group_id")
    op.drop_column("parks", "tracker_queue")
