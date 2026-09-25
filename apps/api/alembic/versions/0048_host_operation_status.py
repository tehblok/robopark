"""Add bounded durable host-operation status receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0048_host_operation_status"
down_revision = "0047_capability_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_operation_status",
        sa.Column("operation_id", sa.String(36), primary_key=True),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("receipt_state", sa.String(16), nullable=False, server_default="received"),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("phase", sa.String(64), nullable=False, server_default="request_received"),
        sa.Column("error", sa.String(64), nullable=True),
        sa.Column("host_result_json", sa.Text(), nullable=True),
        sa.Column("progress_percent", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "receipt_state IN ('received', 'accepted', 'terminal')",
            name="ck_host_operation_status_receipt_state",
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_host_operation_status_state",
        ),
        sa.CheckConstraint(
            "progress_percent IS NULL OR (progress_percent >= 0 AND progress_percent <= 100)",
            name="ck_host_operation_status_progress",
        ),
    )
    op.create_index(
        "ix_host_operation_status_actor_created",
        "host_operation_status",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_host_operation_status_updated", "host_operation_status", ["updated_at", "operation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_host_operation_status_updated", table_name="host_operation_status")
    op.drop_index("ix_host_operation_status_actor_created", table_name="host_operation_status")
    op.drop_table("host_operation_status")
