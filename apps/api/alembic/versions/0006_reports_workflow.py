"""Reports workflow chain table.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("park_id", sa.Integer(), nullable=False),
        sa.Column("author_user_id", sa.Integer(), nullable=False),
        sa.Column("target_role", sa.String(length=32), nullable=False),
        sa.Column("tracker_key", sa.String(length=128), nullable=True),
        sa.Column("tracker_url", sa.String(length=512), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("parent_report_id", sa.Integer(), nullable=True),
        sa.Column("return_comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["parent_report_id"], ["reports.id"]),
        sa.ForeignKeyConstraint(["park_id"], ["parks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reports_author_user_id"), "reports", ["author_user_id"], unique=False)
    op.create_index(op.f("ix_reports_kind"), "reports", ["kind"], unique=False)
    op.create_index(op.f("ix_reports_park_id"), "reports", ["park_id"], unique=False)
    op.create_index(op.f("ix_reports_status"), "reports", ["status"], unique=False)
    op.create_index(op.f("ix_reports_target_role"), "reports", ["target_role"], unique=False)
    op.create_index(
        "ix_reports_author_user_id_created_at",
        "reports",
        ["author_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_reports_target_role_status_park_id",
        "reports",
        ["target_role", "status", "park_id"],
        unique=False,
    )
    op.create_index(
        "ix_reports_tracker_key",
        "reports",
        ["tracker_key"],
        unique=False,
        sqlite_where=sa.text("tracker_key IS NOT NULL"),
    )
    op.create_index(
        "uq_reports_open_close_review_park_tracker",
        "reports",
        ["park_id", "tracker_key"],
        unique=True,
        sqlite_where=sa.text("kind = 'ticket_close_review' AND status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_reports_open_close_review_park_tracker",
        table_name="reports",
        sqlite_where=sa.text("kind = 'ticket_close_review' AND status = 'open'"),
    )
    op.drop_index(
        "ix_reports_tracker_key",
        table_name="reports",
        sqlite_where=sa.text("tracker_key IS NOT NULL"),
    )
    op.drop_index("ix_reports_target_role_status_park_id", table_name="reports")
    op.drop_index("ix_reports_author_user_id_created_at", table_name="reports")
    op.drop_index(op.f("ix_reports_target_role"), table_name="reports")
    op.drop_index(op.f("ix_reports_status"), table_name="reports")
    op.drop_index(op.f("ix_reports_park_id"), table_name="reports")
    op.drop_index(op.f("ix_reports_kind"), table_name="reports")
    op.drop_index(op.f("ix_reports_author_user_id"), table_name="reports")
    op.drop_table("reports")
