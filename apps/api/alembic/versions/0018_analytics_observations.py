"""Persist successful two-hour snapshots and observed task statuses."""

import sqlalchemy as sa
from alembic import op

revision = "0018_analytics_observations"
down_revision = "0017_driver_work_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_snapshots",
        sa.Column("park_id", sa.Integer(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_hours", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["park_id"], ["parks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("park_id", "bucket_start"),
    )
    op.create_table(
        "analytics_observations",
        sa.Column("park_id", sa.Integer(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issue_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(128), nullable=False),
        sa.Column("status_bucket", sa.String(32), nullable=False),
        sa.Column("authorization_status", sa.String(32), nullable=True),
        sa.Column("age_hours", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["park_id", "bucket_start"],
            ["analytics_snapshots.park_id", "analytics_snapshots.bucket_start"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("park_id", "bucket_start", "issue_key", "status"),
    )


def downgrade() -> None:
    op.drop_table("analytics_observations")
    op.drop_table("analytics_snapshots")
