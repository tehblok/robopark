"""Bind privileged grants and audit rows to host capability revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0047_capability_revision"
down_revision = "0046_privileged_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "privileged_reauthorizations",
        sa.Column("capability_revision", sa.String(64), nullable=True),
    )
    op.add_column(
        "privileged_auth_audit",
        sa.Column("capability_revision", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("privileged_auth_audit", "capability_revision")
    op.drop_column("privileged_reauthorizations", "capability_revision")
