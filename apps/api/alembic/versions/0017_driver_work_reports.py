"""Add driver task/read/report defaults without changing explicit user denies."""

import sqlalchemy as sa
from alembic import op

revision = "0017_driver_work_reports"
down_revision = "0016_history_definition"
branch_labels = None
depends_on = None

DRIVER_KEYS = ("nav.tasks", "tracker.read", "nav.reports", "reports.create")


def upgrade() -> None:
    bind = op.get_bind()
    # Fresh installs have only 0013's bootstrap roles and no permissions yet.
    # Do not create a partial catalog: startup initializes it with all defaults.
    for key in DRIVER_KEYS:
        bind.execute(
            sa.text("""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT roles.id, permissions.id FROM roles CROSS JOIN permissions
            WHERE roles.slug = 'driver' AND permissions.key = :key
              AND NOT EXISTS (
                SELECT 1 FROM role_permissions
                WHERE role_permissions.role_id = roles.id
                  AND role_permissions.permission_id = permissions.id
              )
        """),
            {"key": key},
        )


def downgrade() -> None:
    # Grants may predate this upgrade or be owner-managed now. Removing them
    # cannot be reversed safely without provenance; keep this additive data.
    pass
