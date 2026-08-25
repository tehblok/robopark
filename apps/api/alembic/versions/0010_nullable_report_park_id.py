"""Allow platform-wide reports without a park.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reports") as batch:
        batch.alter_column("park_id", existing_type=sa.Integer(), nullable=True)
    op.create_index(
        "uq_reports_open_emergency_cookie_stale",
        "reports",
        ["kind"],
        unique=True,
        sqlite_where=sa.text("kind = 'emergency_cookie_stale' AND status = 'open'"),
        postgresql_where=sa.text("kind = 'emergency_cookie_stale' AND status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("uq_reports_open_emergency_cookie_stale", table_name="reports")
    with op.batch_alter_table("reports") as batch:
        batch.alter_column("park_id", existing_type=sa.Integer(), nullable=False)
