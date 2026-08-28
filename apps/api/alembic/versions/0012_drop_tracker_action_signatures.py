"""Drop unused tracker_action_signatures table.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_tracker_action_sigs_issue_created", table_name="tracker_action_signatures")
    op.drop_table("tracker_action_signatures")


def downgrade() -> None:
    import sqlalchemy as sa

    op.create_table(
        "tracker_action_signatures",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("issue_key", sa.String(length=128), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("operator_login", sa.String(length=64), nullable=True),
        sa.Column("park_id", sa.Integer(), nullable=True),
        sa.Column("park_name", sa.String(length=128), nullable=False),
        sa.Column("mechanic_login", sa.String(length=128), nullable=True),
        sa.Column("mechanic_name", sa.String(length=256), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("display_line", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("tracker_comment_id", sa.String(length=64), nullable=True),
        sa.Column("external_ref", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["park_id"], ["parks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tracker_action_sigs_issue_created",
        "tracker_action_signatures",
        ["issue_key", "created_at"],
    )
