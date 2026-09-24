"""Index unconfirmed accepted reviews for bounded Tracker closure scans."""

from alembic import op

revision = "0039_sync_closure_scan"
down_revision = "0038_inventory_photo_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_task_reviews_closure_scan",
        "task_reviews",
        ["state", "closed_at", "issue_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_reviews_closure_scan", table_name="task_reviews")
