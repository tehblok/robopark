"""Store the local timezone used for each park's operational clock."""

import sqlalchemy as sa
from alembic import op

revision = "0051_park_timezone"
down_revision = "0050_media_action_dependency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("parks") as batch:
        batch.add_column(
            sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Moscow")
        )


def downgrade() -> None:
    with op.batch_alter_table("parks") as batch:
        batch.drop_column("timezone")
