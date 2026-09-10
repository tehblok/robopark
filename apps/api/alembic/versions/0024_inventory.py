"""Park inventory catalog and stock movements."""

import sqlalchemy as sa
from alembic import op

revision = "0024_inventory"
down_revision = "0023_campaigns"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text("""
            INSERT INTO permissions (key, category, label, sort_order)
            SELECT 'nav.inventory', 'nav', 'Склад', 75
            WHERE EXISTS (SELECT 1 FROM permissions)
              AND NOT EXISTS (
                SELECT 1 FROM permissions WHERE key = 'nav.inventory'
              )
        """)
    )
    bind.execute(
        sa.text("""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT roles.id, permissions.id FROM roles CROSS JOIN permissions
            WHERE roles.slug IN ('mechanic', 'operator', 'admin', 'royal')
              AND permissions.key = 'nav.inventory'
              AND NOT EXISTS (
                SELECT 1 FROM role_permissions
                WHERE role_permissions.role_id = roles.id
                  AND role_permissions.permission_id = permissions.id
              )
        """)
    )
    op.create_table(
        "inventory_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("photo_storage_key", sa.String(128)),
        sa.Column("photo_filename", sa.String(240)),
        sa.Column("photo_content_type", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("park_id", "name", name="uq_inventory_component_park_name"),
    )
    op.create_index("ix_inventory_components_park_id", "inventory_components", ["park_id"])
    op.create_table(
        "inventory_parts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "component_id",
            sa.Integer(),
            sa.ForeignKey("inventory_components.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("article", sa.String(128), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("minimum_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("location", sa.String(256), nullable=False),
        sa.Column("photo_storage_key", sa.String(128)),
        sa.Column("photo_filename", sa.String(240)),
        sa.Column("photo_content_type", sa.String(64)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_inventory_part_quantity"),
        sa.CheckConstraint("minimum_quantity >= 0", name="ck_inventory_part_minimum"),
        sa.UniqueConstraint("park_id", "article", name="uq_inventory_part_park_article"),
    )
    op.create_index("ix_inventory_parts_park_id", "inventory_parts", ["park_id"])
    op.create_index("ix_inventory_parts_component_id", "inventory_parts", ["component_id"])
    op.create_index(
        "ix_inventory_parts_park_component", "inventory_parts", ["park_id", "component_id", "name"]
    )
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "part_id",
            sa.Integer(),
            sa.ForeignKey("inventory_parts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("issue_key", sa.String(128)),
        sa.Column("note", sa.String(500)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_inventory_movements_part_id", "inventory_movements", ["part_id"])
    op.create_index("ix_inventory_movements_park_id", "inventory_movements", ["park_id"])
    op.create_index(
        "ix_inventory_movements_actor_user_id", "inventory_movements", ["actor_user_id"]
    )
    op.create_index("ix_inventory_movements_issue_key", "inventory_movements", ["issue_key"])
    op.create_index(
        "ix_inventory_movements_part_created", "inventory_movements", ["part_id", "created_at"]
    )


def downgrade():
    op.drop_table("inventory_movements")
    op.drop_table("inventory_parts")
    op.drop_table("inventory_components")
