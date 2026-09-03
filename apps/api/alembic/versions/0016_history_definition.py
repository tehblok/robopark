"""Separate legacy Updated-based history from resolution-based flow."""

import sqlalchemy as sa
from alembic import op

revision = "0016_history_definition"
down_revision = "0015_report_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "park_blocker_history",
        sa.Column("definition_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("park_blocker_history", "definition_version")
