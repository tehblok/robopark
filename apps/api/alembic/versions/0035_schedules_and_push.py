"""Add bounded schedules and notification inbox."""

import sqlalchemy as sa
from alembic import op

revision = "0035_schedules_and_push"
down_revision = "0034_resumable_media_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule_entries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="self"),
        sa.Column("series_id", sa.String(64)),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('shift','vacation','sick')", name="ck_schedule_kind"),
        sa.CheckConstraint("end_at > start_at", name="ck_schedule_range"),
    )
    op.create_index("ix_schedule_entries_owner_user_id", "schedule_entries", ["owner_user_id"])
    op.create_index("ix_schedule_entries_park_id", "schedule_entries", ["park_id"])
    op.create_index("ix_schedule_park_range", "schedule_entries", ["park_id", "start_at", "end_at"])

    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("endpoint_hash", sa.String(64), nullable=False),
        sa.Column("endpoint_encrypted", sa.Text(), nullable=False),
        sa.Column("p256dh_encrypted", sa.Text(), nullable=False),
        sa.Column("auth_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("endpoint_hash", name="uq_push_endpoint_hash"),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])
    op.create_table(
        "notification_preferences",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("categories_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("system_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "notification_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE")),
        sa.Column("protected_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_notifications_user_created", "notification_events", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_created", table_name="notification_events")
    op.drop_table("notification_events")
    op.drop_table("notification_preferences")
    op.drop_index("ix_push_subscriptions_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
    op.drop_index("ix_schedule_park_range", table_name="schedule_entries")
    op.drop_index("ix_schedule_entries_park_id", table_name="schedule_entries")
    op.drop_index("ix_schedule_entries_owner_user_id", table_name="schedule_entries")
    op.drop_table("schedule_entries")
