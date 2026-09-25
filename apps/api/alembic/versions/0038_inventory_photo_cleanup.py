"""Persist retryable cleanup for managed inventory photos."""

import sqlalchemy as sa
from alembic import op

revision = "0038_inventory_photo_cleanup"
down_revision = "0037_claim_workflow_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_photo_cleanup",
        sa.Column("storage_key", sa.String(128), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("inventory_photo_cleanup")
