"""Add signed device authentication for installed Mac applications."""

import sqlalchemy as sa
from alembic import op

revision = "0066_device_auth"
down_revision = "0065_corporate_write_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_devices") as batch_op:
        batch_op.add_column(sa.Column("device_auth_enabled_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("device_auth_disabled_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("device_auth_rate_window_at", sa.DateTime(timezone=True)))
        batch_op.add_column(
            sa.Column("device_auth_rate_count", sa.Integer(), nullable=False, server_default="0")
        )

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "auth_method",
                sa.String(16),
                nullable=False,
                server_default="password",
            )
        )
        batch_op.add_column(sa.Column("device_id", sa.String(36), nullable=True))
        batch_op.create_check_constraint(
            "ck_sessions_auth_method", "auth_method IN ('password', 'device')"
        )
        batch_op.create_foreign_key(
            "fk_sessions_device_id_agent_devices",
            "agent_devices",
            ["device_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "device_auth_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), nullable=False),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("length(nonce_hash) = 64", name="ck_device_auth_nonce_hash"),
        sa.ForeignKeyConstraint(["device_id"], ["agent_devices.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_device_auth_challenges_device_id",
        "device_auth_challenges",
        ["device_id"],
    )
    op.create_index(
        "ix_device_auth_challenges_device_created",
        "device_auth_challenges",
        ["device_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_device_auth_challenges_device_created",
        table_name="device_auth_challenges",
    )
    op.drop_index(
        "ix_device_auth_challenges_device_id",
        table_name="device_auth_challenges",
    )
    op.drop_table("device_auth_challenges")
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint("fk_sessions_device_id_agent_devices", type_="foreignkey")
        batch_op.drop_constraint("ck_sessions_auth_method", type_="check")
        batch_op.drop_column("device_id")
        batch_op.drop_column("auth_method")
    with op.batch_alter_table("agent_devices") as batch_op:
        batch_op.drop_column("device_auth_rate_count")
        batch_op.drop_column("device_auth_rate_window_at")
        batch_op.drop_column("device_auth_disabled_at")
        batch_op.drop_column("device_auth_enabled_at")
