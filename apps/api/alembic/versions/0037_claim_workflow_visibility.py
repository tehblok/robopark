"""Persist ordered claim state and timeline visibility."""

import sqlalchemy as sa
from alembic import op

revision = "0037_claim_workflow_visibility"
down_revision = "0036_audit_remediation_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tracker_claims") as batch_op:
        batch_op.add_column(
            sa.Column("state", sa.String(16), nullable=False, server_default="active")
        )
        batch_op.add_column(
            sa.Column(
                "start_action_id",
                sa.String(64),
                sa.ForeignKey(
                    "reliable_actions.id",
                    name="fk_tracker_claims_start_action",
                    ondelete="SET NULL",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "operator_user_id",
                sa.Integer(),
                sa.ForeignKey(
                    "users.id", name="fk_tracker_claims_operator_user", ondelete="SET NULL"
                ),
                nullable=True,
            )
        )
        batch_op.create_check_constraint(
            "ck_tracker_claims_state", "state IN ('pending', 'active')"
        )

    with op.batch_alter_table("tracker_claims") as batch_op:
        batch_op.alter_column(
            "state",
            existing_type=sa.String(16),
            existing_nullable=False,
            server_default="pending",
        )

    with op.batch_alter_table("task_messages") as batch_op:
        batch_op.add_column(
            sa.Column(
                "visibility", sa.String(16), nullable=False, server_default="participants"
            )
        )
        batch_op.create_check_constraint(
            "ck_task_messages_visibility", "visibility IN ('participants', 'staff')"
        )


def downgrade() -> None:
    with op.batch_alter_table("task_messages") as batch_op:
        batch_op.drop_constraint("ck_task_messages_visibility", type_="check")
        batch_op.drop_column("visibility")

    with op.batch_alter_table("tracker_claims") as batch_op:
        batch_op.drop_constraint("ck_tracker_claims_state", type_="check")
        batch_op.drop_column("operator_user_id")
        batch_op.drop_column("start_action_id")
        batch_op.drop_column("state")
