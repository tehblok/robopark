"""Mac Emergency bridge device enrollment registry."""

import sqlalchemy as sa
from alembic import op

revision = "0063_agent_devices"
down_revision = "0062_notification_retention_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_enrollments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("label", sa.String(96), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("instance_id", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_agent_enrollments_owner_created",
        "agent_enrollments",
        ["owner_user_id", "created_at"],
    )
    op.create_table(
        "agent_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(96), nullable=False),
        sa.Column("public_key_x963", sa.LargeBinary(65), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("instance_id", sa.String(36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_agent_devices_owner_active", "agent_devices", ["owner_user_id", "revoked_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_devices_owner_active", table_name="agent_devices")
    op.drop_table("agent_devices")
    op.drop_index("ix_agent_enrollments_owner_created", table_name="agent_enrollments")
    op.drop_table("agent_enrollments")
