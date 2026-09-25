"""Index schedule pattern series lookup."""

from alembic import op

revision = "0043_schedule_series_lookup"
down_revision = "0042_user_timezone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_schedule_owner_series_end",
        "schedule_entries",
        ["owner_user_id", "series_id", "end_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_owner_series_end", table_name="schedule_entries")
