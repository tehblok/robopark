"""Durable role-bound local AI invocations and explicit human confirmation."""
import sqlalchemy as sa
from alembic import op

revision = "0059_ai_action_receipts"
down_revision = "0058_ai_bundle_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("ai_jobs.id", ondelete="SET NULL")),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(128), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("expected", sa.JSON(), nullable=False),
        sa.Column("preview", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.String(120)),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("job_id", "ordinal", name="uq_ai_action_ordinal"),
    )
    op.create_index("ix_ai_actions_job_id", "ai_actions", ["job_id"])
    op.create_index("ix_ai_actions_state_updated", "ai_actions", ["state", "updated_at"])


def downgrade():
    op.drop_table("ai_actions")
