"""RBAC tables and migration away from users.role string column."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_rbac_roles"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False, server_default="nav"),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_permissions_key", "permissions", ["key"], unique=True)

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_roles_slug", "roles", ["slug"], unique=True)

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )

    # Seed catalog via raw SQL is fragile; app startup runs ensure_rbac_catalog after migrate.
    # Add nullable role_id, backfill from legacy role string, then drop legacy column.
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("role_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_users_role_id", "roles", ["role_id"], ["id"])
        batch_op.create_index("ix_users_role_id", ["role_id"])

    bind = op.get_bind()

    # Minimal bootstrap rows so backfill can run before app seed.
    bind.execute(
        sa.text(
            """
            INSERT INTO roles (slug, name, description, is_system, is_active)
            VALUES
              ('royal', 'Владелец', '', 1, 1),
              ('admin', 'Администратор', '', 1, 1),
              ('operator', 'Оператор', '', 1, 1),
              ('mechanic', 'Механик', '', 1, 1),
              ('driver', 'Водитель', '', 1, 1)
            """
        )
    )

    for slug in ("royal", "admin", "operator", "mechanic", "driver"):
        bind.execute(
            sa.text(
                """
                UPDATE users
                SET role_id = (SELECT id FROM roles WHERE slug = :slug)
                WHERE role = :slug
                """
            ),
            {"slug": slug},
        )

    # Unknown legacy roles fall back to operator.
    bind.execute(
        sa.text(
            """
            UPDATE users
            SET role_id = (SELECT id FROM roles WHERE slug = 'operator')
            WHERE role_id IS NULL
            """
        )
    )

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("role_id", nullable=False)
        batch_op.drop_column("role")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("role", sa.String(length=32), nullable=True))

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE users
            SET role = (SELECT slug FROM roles WHERE roles.id = users.role_id)
            """
        )
    )

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("role", nullable=False)
        batch_op.drop_constraint("fk_users_role_id", type_="foreignkey")
        batch_op.drop_column("role_id")

    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_table("permissions")
