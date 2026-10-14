"""Pin corporate write authority and fence mutation retries."""

import sqlalchemy as sa
from alembic import op

revision = "0065_corporate_write_receipts"
down_revision = "0064_agent_device_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corporate_write_authorities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("requester_user_id", sa.Integer(), nullable=False),
        sa.Column("service", sa.String(16), nullable=False, server_default="tracker"),
        sa.Column("resource", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("device_id", sa.String(36), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("owner_tracker_login", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("mode IN ('mac', 'server')", name="ck_corporate_write_authority_mode"),
        sa.CheckConstraint(
            "(mode = 'server' AND device_id IS NULL AND owner_user_id IS NULL "
            "AND owner_tracker_login IS NULL) OR "
            "(mode = 'mac' AND device_id IS NOT NULL AND owner_user_id IS NOT NULL "
            "AND owner_tracker_login IS NOT NULL)",
            name="ck_corporate_write_authority_binding",
        ),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_corporate_write_authority_actor_created",
        "corporate_write_authorities",
        ["requester_user_id", "created_at"],
    )
    op.create_table(
        "corporate_write_receipts",
        sa.Column("command_id", sa.String(64), nullable=False),
        sa.Column("step_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="executing"),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "state IN ('executing', 'completed', 'unknown')",
            name="ck_corporate_write_receipt_state",
        ),
        sa.ForeignKeyConstraint(
            ["command_id"], ["corporate_write_authorities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("command_id", "step_id"),
    )

    # Before this release every durable Tracker action always executed using
    # the server/bot credential. Preserve that authority across retries after
    # enabling Mac-first routing; only commands created after this migration
    # are eligible for a new Mac pin.
    op.execute(
        sa.text(
            "INSERT INTO corporate_write_authorities "
            "(id, requester_user_id, service, resource, mode) "
            "SELECT id, actor_user_id, 'tracker', resource_id, 'server' "
            "FROM reliable_actions WHERE resource_type = 'tracker_issue'"
        )
    )


def downgrade() -> None:
    op.drop_table("corporate_write_receipts")
    op.drop_index(
        "ix_corporate_write_authority_actor_created",
        table_name="corporate_write_authorities",
    )
    op.drop_table("corporate_write_authorities")
