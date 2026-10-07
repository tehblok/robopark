"""Persist Telegram display metadata for access review."""

import sqlalchemy as sa
from alembic import op

revision = "0061_telegram_account_metadata"
down_revision = "0060_native_telegram"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("telegram_accounts") as batch:
        batch.add_column(sa.Column("display_name", sa.String(128), nullable=True))
        batch.add_column(sa.Column("telegram_username", sa.String(64), nullable=True))


def downgrade():
    with op.batch_alter_table("telegram_accounts") as batch:
        batch.drop_column("telegram_username")
        batch.drop_column("display_name")
