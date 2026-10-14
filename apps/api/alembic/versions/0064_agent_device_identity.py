"""Keep a Mac installation key bound to its first account."""

from alembic import op

revision = "0064_agent_device_identity"
down_revision = "0063_agent_devices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate_count = bind.exec_driver_sql(
        """
        SELECT COUNT(*) FROM (
            SELECT public_key_x963, environment, instance_id
            FROM agent_devices
            GROUP BY public_key_x963, environment, instance_id
            HAVING COUNT(*) > 1
        ) AS duplicate_installations
        """
    ).scalar_one()
    if duplicate_count:
        raise RuntimeError(
            "agent_device_identity_conflict: duplicate Mac installation keys must be "
            "resolved by an administrator before upgrade"
        )
    with op.batch_alter_table("agent_devices") as batch_op:
        batch_op.create_unique_constraint(
            "uq_agent_devices_installation_identity",
            ["public_key_x963", "environment", "instance_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_devices") as batch_op:
        batch_op.drop_constraint(
            "uq_agent_devices_installation_identity",
            type_="unique",
        )
