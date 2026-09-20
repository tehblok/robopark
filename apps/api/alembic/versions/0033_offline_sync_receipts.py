"""Add replay receipts for device synchronization."""

import sqlalchemy as sa
from alembic import op

revision = "0033_offline_sync_receipts"
down_revision = "0032_operator_inv_readonly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "offline_sync_receipts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("client_action_id", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_user_id",
            "device_id",
            "client_action_id",
            name="uq_offline_sync_receipt_actor_device_action",
        ),
    )
    op.create_index(
        "ix_offline_sync_receipts_created",
        "offline_sync_receipts",
        ["created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_offline_sync_receipts_created", table_name="offline_sync_receipts")
    op.drop_table("offline_sync_receipts")
