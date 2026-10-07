"""Native Telegram configuration, identities, schedules, and delivery journal."""

import sqlalchemy as sa
from alembic import op

revision = "0060_native_telegram"
down_revision = "0059_ai_action_receipts"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("park_requests") as batch:
        batch.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
        )
    with op.batch_alter_table("parks") as batch:
        batch.alter_column("chat_id", existing_type=sa.Integer(), type_=sa.BigInteger())
        batch.add_column(sa.Column("thread_id", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("bot_revision", sa.Integer(), nullable=False, server_default="1")
        )

    op.create_table(
        "telegram_accounts",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_telegram_accounts_telegram_user_id",
        "telegram_accounts",
        ["telegram_user_id"],
        unique=True,
    )
    op.create_table(
        "telegram_link_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_telegram_link_codes_user_id", "telegram_link_codes", ["user_id"], unique=True
    )
    op.create_index(
        "ix_telegram_link_codes_code_hash", "telegram_link_codes", ["code_hash"], unique=True
    )
    op.create_index("ix_telegram_link_codes_expires_at", "telegram_link_codes", ["expires_at"])
    op.create_table(
        "telegram_link_attempts",
        sa.Column("telegram_user_id", sa.BigInteger(), primary_key=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "native_bot_usage_totals",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("greeted_on", sa.Date()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "native_bot_usage_daily",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("native_bot_usage_totals.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_native_bot_usage_daily_day", "native_bot_usage_daily", ["day"])
    op.create_table(
        "native_bot_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("queries_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deliveries_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_native_bot_control_singleton"),
    )
    op.bulk_insert(
        sa.table(
            "native_bot_control",
            sa.column("id", sa.Integer()),
            sa.column("queries_paused", sa.Boolean()),
            sa.column("deliveries_paused", sa.Boolean()),
            sa.column("revision", sa.Integer()),
        ),
        [{"id": 1, "queries_paused": False, "deliveries_paused": False, "revision": 1}],
    )
    op.create_table(
        "native_bot_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_ref", sa.String(255)),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("schedule", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64)),
        sa.Column("time", sa.String(5)),
        sa.Column("run_at", sa.DateTime(timezone=True)),
        sa.Column("weekdays", sa.String(32), nullable=False, server_default=""),
        sa.Column("start_hour", sa.Integer()),
        sa.Column("end_hour", sa.Integer()),
        sa.Column("text", sa.Text()),
        sa.Column("url", sa.String(2048)),
        sa.Column("tracker_tag", sa.String(128)),
        sa.Column("alternate", sa.String(8), nullable=False, server_default="all"),
        sa.Column("anchor_date", sa.Date()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('report', 'text', 'zoom', 'campaign')", name="ck_native_bot_job_kind"
        ),
        sa.CheckConstraint(
            "schedule IN ('daily', 'hourly', 'once')", name="ck_native_bot_job_schedule"
        ),
        sa.CheckConstraint(
            "alternate IN ('all', 'odd', 'even')", name="ck_native_bot_job_alternate"
        ),
    )
    op.create_index("ix_native_bot_jobs_park_id", "native_bot_jobs", ["park_id"])
    op.create_index(
        "ix_native_bot_jobs_source_ref", "native_bot_jobs", ["source_ref"], unique=True
    )
    op.create_table(
        "native_bot_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.String(36),
            nullable=False,
        ),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manual", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("request_id", sa.String(36)),
        sa.Column("job_revision", sa.Integer()),
        sa.Column("park_revision", sa.Integer()),
        sa.Column("destination_chat_id", sa.BigInteger()),
        sa.Column("destination_thread_id", sa.BigInteger()),
        sa.Column("park_tag", sa.String(64)),
        sa.Column("tracker_queue", sa.String(128)),
        sa.Column("lease_token_hash", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "state IN ('preparing', 'sending', 'sent', 'failed', 'unknown')",
            name="ck_native_bot_delivery_state",
        ),
        sa.UniqueConstraint("job_id", "scheduled_at", name="uq_native_bot_delivery_slot"),
        sa.UniqueConstraint("request_id", name="uq_native_bot_delivery_request_id"),
    )
    op.create_index(
        "ix_native_bot_delivery_claim",
        "native_bot_deliveries",
        ["state", "lease_until", "scheduled_at"],
    )
    op.create_table(
        "native_bot_migrations",
        sa.Column("fingerprint", sa.String(64), primary_key=True),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_native_bot_migrations_source_fingerprint",
        "native_bot_migrations",
        ["source_fingerprint"],
        unique=True,
    )
    op.create_table(
        "native_bot_migration_sources",
        sa.Column("source_ref", sa.String(255), primary_key=True),
        sa.Column(
            "fingerprint",
            sa.String(64),
            sa.ForeignKey("native_bot_migrations.fingerprint", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("native_job_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_native_bot_migration_sources_fingerprint",
        "native_bot_migration_sources",
        ["fingerprint"],
    )


def downgrade():
    op.drop_table("native_bot_migration_sources")
    op.drop_table("native_bot_migrations")
    op.drop_table("native_bot_deliveries")
    op.drop_table("native_bot_jobs")
    op.drop_table("native_bot_control")
    op.drop_table("native_bot_usage_daily")
    op.drop_table("native_bot_usage_totals")
    op.drop_table("telegram_link_attempts")
    op.drop_table("telegram_link_codes")
    op.drop_table("telegram_accounts")
    with op.batch_alter_table("parks") as batch:
        batch.drop_column("bot_revision")
        batch.drop_column("thread_id")
        batch.alter_column("chat_id", existing_type=sa.BigInteger(), type_=sa.Integer())
    with op.batch_alter_table("park_requests") as batch:
        batch.drop_column("revision")
