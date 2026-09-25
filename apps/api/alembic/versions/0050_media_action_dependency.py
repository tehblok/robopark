"""Retain completed media until its durable action reaches a terminal result."""

import sqlalchemy as sa
from alembic import op

revision = "0050_media_action_dependency"
down_revision = "0049_operation_request_digest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_upload_sessions") as batch:
        batch.add_column(sa.Column("dependent_device_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("dependent_action_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("dependency_bound_at", sa.Float(), nullable=True))
        batch.add_column(sa.Column("dependency_terminal_at", sa.Float(), nullable=True))
        batch.create_index(
            "ix_media_upload_dependency_cleanup",
            ["completed", "dependency_terminal_at", "completed_at", "id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("media_upload_sessions") as batch:
        batch.drop_index("ix_media_upload_dependency_cleanup")
        batch.drop_column("dependency_terminal_at")
        batch.drop_column("dependency_bound_at")
        batch.drop_column("dependent_action_id")
        batch.drop_column("dependent_device_id")
