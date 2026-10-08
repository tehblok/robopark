"""Index notification inbox retention scans."""

from alembic import op

revision = "0062_notification_retention_idx"
down_revision = "0061_telegram_account_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_notification_events_created_id",
        "notification_events",
        ["created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_events_created_id", table_name="notification_events")
