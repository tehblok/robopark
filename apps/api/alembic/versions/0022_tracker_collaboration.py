"""Durable tracker submissions, advisory presence and handoffs."""

import sqlalchemy as sa
from alembic import op

revision = "0022_tracker_collaboration"
down_revision = "0021_diagnostic_unknown_original"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tracker_presence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_key", sa.String(128), nullable=False),
        sa.Column(
            "actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("issue_key", "actor_id"),
    )
    op.create_index("ix_tracker_presence_issue_key", "tracker_presence", ["issue_key"])
    op.create_index("ix_tracker_presence_expires_at", "tracker_presence", ["expires_at"])
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
    op.create_table(
        "tracker_handoffs",
        sa.Column("issue_key", sa.String(128), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("done", sa.Text(), nullable=False),
        sa.Column("remaining", sa.Text(), nullable=False),
        sa.Column("obstacles", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )


def downgrade():
    op.drop_table("tracker_handoffs")
    op.drop_table("tracker_submissions")
    op.drop_table("tracker_presence")
