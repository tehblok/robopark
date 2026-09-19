"""Use dialect-safe boolean defaults for the PostgreSQL runtime."""

import sqlalchemy as sa
from alembic import op

revision = "0031_postgresql_runtime"
down_revision = "0030_user_activity"
branch_labels = None
depends_on = None


_TRUE_DEFAULTS = (
    ("parks", "feature_reports"),
    ("parks", "feature_blockers"),
    ("parks", "feature_sla_repair"),
    ("parks", "feature_backlog_alerts"),
    ("roles", "is_active"),
    ("user_permissions", "granted"),
    ("diagnostic_rules", "is_enabled"),
    ("campaigns", "is_active"),
    ("inventory_parts", "is_active"),
    ("inventory_catalog_components", "is_active"),
    ("inventory_catalog_parts", "is_active"),
    ("inventory_park_stocks", "is_active"),
    ("emergency_readings", "is_enabled"),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name, column_name in _TRUE_DEFAULTS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.Boolean(),
            server_default=sa.true(),
        )
    op.alter_column(
        "roles",
        "is_system",
        existing_type=sa.Boolean(),
        server_default=sa.false(),
    )


def downgrade() -> None:
    # SQLite's historical 1/0 defaults remain valid and untouched. PostgreSQL
    # cannot safely downgrade Boolean defaults to integer SQL expressions.
    pass
