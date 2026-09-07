"""Persist global Emergency diagnostic rules."""

import sqlalchemy as sa
from alembic import op

revision = "0019_diagnostic_rules"
down_revision = "0018_analytics_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_path", sa.String(length=256), nullable=False),
        sa.Column("match_kind", sa.String(length=16), nullable=False),
        sa.Column("pattern", sa.String(length=512), nullable=False),
        sa.Column("example", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("part", sa.String(length=128), nullable=False),
        sa.Column("preferred_view", sa.String(length=16), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("indicator", sa.String(length=16), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "match_kind IN ('exact', 'regex')",
            name="ck_diagnostic_rules_match_kind",
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_diagnostic_rules_severity",
        ),
        sa.CheckConstraint(
            "preferred_view IN ('top', 'front', 'rear', 'left', 'right', 'isometric')",
            name="ck_diagnostic_rules_preferred_view",
        ),
        sa.CheckConstraint(
            "indicator IN ('point', 'outline', 'zone')",
            name="ck_diagnostic_rules_indicator",
        ),
        sa.CheckConstraint(
            "x = x AND x >= 0.0 AND x <= 1.0",
            name="ck_diagnostic_rules_x",
        ),
        sa.CheckConstraint(
            "y = y AND y >= 0.0 AND y <= 1.0",
            name="ck_diagnostic_rules_y",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_path",
            "match_kind",
            "pattern",
            name="uq_diagnostic_rules_source_match_pattern",
        ),
    )
    op.create_index(
        "ix_diagnostic_rules_sort_order_id",
        "diagnostic_rules",
        ["sort_order", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_diagnostic_rules_sort_order_id", table_name="diagnostic_rules")
    op.drop_table("diagnostic_rules")
