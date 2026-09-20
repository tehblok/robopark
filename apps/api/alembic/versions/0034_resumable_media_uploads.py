"""Add resumable media upload sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0034_resumable_media_uploads"
down_revision = "0033_offline_sync_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_upload_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.String(length=64), nullable=False),
        sa.Column("issue_key", sa.String(length=128), nullable=False),
        sa.Column("original_name", sa.String(length=256), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("received_offset", sa.Integer(), server_default="0", nullable=False),
        sa.Column("blob_name", sa.String(length=256), nullable=False),
        sa.Column("completed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("completed_at", sa.Float(), nullable=True),
        sa.CheckConstraint("size_bytes > 0 AND received_offset >= 0 AND received_offset <= size_bytes", name="ck_media_upload_offsets"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_user_id", "media_id", name="uq_media_upload_actor_media"),
    )
    op.create_index("ix_media_upload_expiry", "media_upload_sessions", ["completed", "expires_at", "id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_media_upload_expiry", table_name="media_upload_sessions")
    op.drop_table("media_upload_sessions")
