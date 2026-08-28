"""Per-user permission overrides on top of role defaults."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_user_permissions"
down_revision = "0013_rbac_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_permissions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "permission_id"),
    )


def downgrade() -> None:
    op.drop_table("user_permissions")
