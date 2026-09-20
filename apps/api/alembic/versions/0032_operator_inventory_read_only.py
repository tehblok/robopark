"""Make the operator inventory role read-only."""

import sqlalchemy as sa
from alembic import op

revision = "0032_operator_inventory_read_only"
down_revision = "0031_postgresql_runtime"
branch_labels = None
depends_on = None


_WRITE_PERMISSIONS = (
    "inventory.stock.manage",
    "inventory.documents.post",
    "inventory.export",
)


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("""
            DELETE FROM role_permissions
            WHERE role_id IN (SELECT id FROM roles WHERE slug = 'operator')
              AND permission_id IN (
                SELECT id FROM permissions WHERE key IN (
                  'inventory.stock.manage',
                  'inventory.documents.post',
                  'inventory.export'
                )
              )
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    for permission_key in _WRITE_PERMISSIONS:
        bind.execute(
            sa.text("""
                INSERT INTO role_permissions (role_id, permission_id)
                SELECT roles.id, permissions.id
                FROM roles CROSS JOIN permissions
                WHERE roles.slug = 'operator'
                  AND permissions.key = :permission_key
                  AND NOT EXISTS (
                    SELECT 1 FROM role_permissions
                    WHERE role_permissions.role_id = roles.id
                      AND role_permissions.permission_id = permissions.id
                  )
            """),
            {"permission_key": permission_key},
        )
