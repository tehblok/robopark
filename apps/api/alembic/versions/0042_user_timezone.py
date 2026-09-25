"""Persist last verified user IANA timezone."""

import sqlalchemy as sa
from alembic import op

revision = "0042_user_timezone"
down_revision = "0041_system_metrics_presence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("timezone", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "timezone")
