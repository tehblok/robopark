"""Retain original diagnostic units separately from pruned unknown projections."""

import sqlalchemy as sa
from alembic import op

revision = "0021_diagnostic_unknown_original"
down_revision = "0020_diagnostic_unknowns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy residual projections are not trustworthy originals. Leave NULL
    # until the next authorized snapshot captures the real bounded unit.
    op.add_column("diagnostic_unknowns", sa.Column("original_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("diagnostic_unknowns", "original_json")
