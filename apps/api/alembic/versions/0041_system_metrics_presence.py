"""Add bounded online presence and system metric history."""

import sqlalchemy as sa
from alembic import op

revision = "0041_system_metrics_presence"
down_revision = "0040_notification_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_presence",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_presence_last_seen_at", "user_presence", ["last_seen_at"])
    op.create_table(
        "presence_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("bucket_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "bucket_at", name="uq_presence_sample_user_bucket"),
    )
    op.create_index("ix_presence_samples_user_id", "presence_samples", ["user_id"])
    op.create_index("ix_presence_samples_bucket", "presence_samples", ["bucket_at", "user_id"])
    op.create_table(
        "system_metric_raw",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_system_metric_raw_sampled", "system_metric_raw", ["sampled_at", "id"])
    op.create_table(
        "system_metric_aggregates",
        sa.Column("bucket_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_metric_aggregates")
    op.drop_index("ix_system_metric_raw_sampled", table_name="system_metric_raw")
    op.drop_table("system_metric_raw")
    op.drop_index("ix_presence_samples_bucket", table_name="presence_samples")
    op.drop_index("ix_presence_samples_user_id", table_name="presence_samples")
    op.drop_table("presence_samples")
    op.drop_index("ix_user_presence_last_seen_at", table_name="user_presence")
    op.drop_table("user_presence")
