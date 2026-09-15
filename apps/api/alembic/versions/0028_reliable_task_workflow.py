"""Add durable local task workflow and generic reliable actions."""

import sqlalchemy as sa
from alembic import op

revision = "0028_reliable_task_workflow"
down_revision = "0027_emergency_readings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reliable_actions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'sending', 'succeeded', 'retry_wait', 'needs_attention')",
            name="ck_reliable_actions_state",
        ),
        sa.UniqueConstraint(
            "actor_user_id",
            "resource_type",
            "resource_id",
            "action",
            "idempotency_key",
            name="uq_reliable_actions_idempotency_scope",
        ),
    )
    op.create_index(
        "ix_reliable_actions_due", "reliable_actions", ["state", "next_attempt_at", "id"]
    )

    op.create_table(
        "task_messages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("issue_key", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("author_name", sa.String(128), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column(
            "action_id",
            sa.String(64),
            sa.ForeignKey("reliable_actions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sync_state", sa.String(32), nullable=False, server_default="saved"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.CheckConstraint("kind IN ('user', 'system', 'tracker')", name="ck_task_messages_kind"),
        sa.CheckConstraint(
            "sync_state IN ('saved', 'pending', 'synced', 'needs_attention')",
            name="ck_task_messages_sync_state",
        ),
        sa.UniqueConstraint(
            "issue_key", "kind", "external_id", name="uq_task_messages_external_id"
        ),
    )
    op.create_index(
        "ix_task_messages_issue_created", "task_messages", ["issue_key", "created_at", "id"]
    )

    op.create_table(
        "task_attachments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(64),
            sa.ForeignKey("task_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("blob_name", sa.String(256), nullable=False),
        sa.Column("original_name", sa.String(256), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("uploaded_at", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "length(blob_name) > 0 "
            "AND blob_name NOT LIKE '%/%' "
            "AND blob_name NOT LIKE '%!\\%' ESCAPE '!' "
            "AND blob_name NOT LIKE '%..%'",
            name="ck_task_attachments_safe_blob_name",
        ),
    )
    op.create_index("ix_task_attachments_message_id", "task_attachments", ["message_id", "id"])

    op.create_table(
        "task_reviews",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("issue_key", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewer_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("return_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("closed_at", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'returned', 'closed')", name="ck_task_reviews_state"
        ),
    )
    op.create_index("ix_task_reviews_issue_key", "task_reviews", ["issue_key"])
    op.create_index(
        "uq_task_reviews_open_issue",
        "task_reviews",
        ["issue_key"],
        unique=True,
        sqlite_where=sa.text("state != 'closed'"),
        postgresql_where=sa.text("state != 'closed'"),
    )

    op.create_table(
        "hidden_tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("issue_key", sa.String(128), nullable=False),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("restored_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("restored_at", sa.Float(), nullable=True),
    )
    op.create_index("ix_hidden_tasks_issue_key", "hidden_tasks", ["issue_key"])
    op.create_index("ix_hidden_tasks_park_id", "hidden_tasks", ["park_id"])
    op.create_index(
        "uq_hidden_tasks_active_issue",
        "hidden_tasks",
        ["issue_key"],
        unique=True,
        sqlite_where=sa.text("restored_at IS NULL"),
        postgresql_where=sa.text("restored_at IS NULL"),
    )

    op.execute(
        sa.text(
            "INSERT INTO reliable_actions "
            "(id, actor_user_id, resource_type, resource_id, action, idempotency_key, "
            "payload_hash, payload_json, state, result_json, error_code, attempts, "
            "next_attempt_at, lease_until, created_at, updated_at) "
            "SELECT 'legacy-' || CAST(id AS VARCHAR), actor_id, 'tracker_issue', issue_key, "
            "action, request_key, payload_hash, '{}', "
            "CASE WHEN state = 'uncertain' THEN 'needs_attention' ELSE state END, "
            "result_json, NULL, 0, "
            "created_at, NULL, created_at, created_at FROM tracker_submissions"
        )
    )
    op.drop_table("tracker_submissions")


def downgrade() -> None:
    op.create_table(
        "tracker_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("issue_key", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("actor_id", "issue_key", "action", "request_key"),
    )
    op.execute(
        sa.text(
            "INSERT INTO tracker_submissions "
            "(actor_id, issue_key, action, request_key, payload_hash, state, result_json, created_at) "
            "SELECT actor_user_id, resource_id, action, idempotency_key, payload_hash, state, "
            "result_json, created_at FROM reliable_actions WHERE resource_type = 'tracker_issue'"
        )
    )
    op.drop_index("uq_hidden_tasks_active_issue", table_name="hidden_tasks")
    op.drop_index("ix_hidden_tasks_park_id", table_name="hidden_tasks")
    op.drop_index("ix_hidden_tasks_issue_key", table_name="hidden_tasks")
    op.drop_table("hidden_tasks")
    op.drop_index("uq_task_reviews_open_issue", table_name="task_reviews")
    op.drop_index("ix_task_reviews_issue_key", table_name="task_reviews")
    op.drop_table("task_reviews")
    op.drop_index("ix_task_attachments_message_id", table_name="task_attachments")
    op.drop_table("task_attachments")
    op.drop_index("ix_task_messages_issue_created", table_name="task_messages")
    op.drop_table("task_messages")
    op.drop_index("ix_reliable_actions_due", table_name="reliable_actions")
    op.drop_table("reliable_actions")
