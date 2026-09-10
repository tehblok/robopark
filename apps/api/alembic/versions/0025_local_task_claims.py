"""Local Robopark task ownership independent from Tracker assignees."""

import sqlalchemy as sa
from alembic import op

revision = "0025_local_task_claims"
down_revision = "0024_inventory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tracker_claims",
        sa.Column("issue_key", sa.String(128), primary_key=True),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_tracker_claims_park_id", "tracker_claims", ["park_id"])
    op.create_index("ix_tracker_claims_owner_user_id", "tracker_claims", ["owner_user_id"])


def downgrade():
    op.drop_table("tracker_claims")
