"""Persist bounded campaign refresh state and selected Tracker tickets."""

import sqlalchemy as sa
from alembic import op

revision = "0029_campaign_snapshot"
down_revision = "0028_reliable_task_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("selection_mode", sa.String(16), nullable=False, server_default="tag"),
    )
    op.add_column(
        "campaigns",
        sa.Column("rule_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("campaigns", sa.Column("snapshot_at", sa.DateTime(timezone=True)))
    op.add_column(
        "campaigns",
        sa.Column("snapshot_state", sa.String(16), nullable=False, server_default="idle"),
    )
    op.add_column("campaigns", sa.Column("snapshot_error", sa.String(128)))
    op.add_column("campaigns", sa.Column("snapshot_retry_at", sa.DateTime(timezone=True)))
    op.add_column("campaigns", sa.Column("snapshot_lease_until", sa.DateTime(timezone=True)))
    op.add_column("campaigns", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("campaign_submissions", sa.Column("completion_key", sa.String(128)))
    op.add_column("campaign_submissions", sa.Column("completion_hash", sa.String(64)))

    op.create_table(
        "campaign_snapshot_tickets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("issue_key", sa.String(128), nullable=False),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column("status", sa.String(128), nullable=False),
        sa.Column("status_key", sa.String(128)),
        sa.Column("resolution", sa.String(128)),
        sa.Column("robot", sa.String(64)),
        sa.Column("tracker_updated_at", sa.DateTime(timezone=True)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rule_revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("campaign_id", "issue_key", name="uq_campaign_snapshot_issue"),
    )
    op.create_index(
        "ix_campaign_snapshot_campaign_revision_park",
        "campaign_snapshot_tickets",
        ["campaign_id", "rule_revision", "park_id"],
    )


def downgrade() -> None:
    with op.batch_alter_table("campaign_submissions") as batch_op:
        batch_op.drop_column("completion_hash")
        batch_op.drop_column("completion_key")
    op.drop_index("ix_campaign_snapshot_campaign_revision_park", table_name="campaign_snapshot_tickets")
    op.drop_table("campaign_snapshot_tickets")
    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.drop_column("archived_at")
        batch_op.drop_column("snapshot_lease_until")
        batch_op.drop_column("snapshot_retry_at")
        batch_op.drop_column("snapshot_error")
        batch_op.drop_column("snapshot_state")
        batch_op.drop_column("snapshot_at")
        batch_op.drop_column("rule_revision")
        batch_op.drop_column("selection_mode")
