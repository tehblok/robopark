"""Persist the global catalog of typed Emergency readings."""

import sqlalchemy as sa
from alembic import op

revision = "0027_emergency_readings"
down_revision = "0026_global_inventory_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "emergency_readings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "section_id",
            sa.String(length=64),
            sa.ForeignKey("emergency_sections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=256), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("display_kind", sa.String(length=16), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("precision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled_path", sa.String(length=256), nullable=True),
        sa.Column("no_data_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("warning_below", sa.Float(), nullable=True),
        sa.Column("warning_above", sa.Float(), nullable=True),
        sa.Column("critical_below", sa.Float(), nullable=True),
        sa.Column("critical_above", sa.Float(), nullable=True),
        sa.Column("view", sa.String(length=16), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("label_direction", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "display_kind IN ('text', 'number', 'percent', 'distance', 'current', 'state')",
            name="ck_emergency_readings_display_kind",
        ),
        sa.CheckConstraint(
            "view IN ('top', 'front', 'rear', 'left', 'right', 'isometric')",
            name="ck_emergency_readings_view",
        ),
        sa.CheckConstraint(
            "label_direction IN ('auto', 'left', 'right', 'top', 'bottom')",
            name="ck_emergency_readings_label_direction",
        ),
        sa.CheckConstraint(
            "x = x AND x >= 0.0 AND x <= 1.0",
            name="ck_emergency_readings_x",
        ),
        sa.CheckConstraint(
            "y = y AND y >= 0.0 AND y <= 1.0",
            name="ck_emergency_readings_y",
        ),
        sa.CheckConstraint(
            "precision >= 0 AND precision <= 4",
            name="ck_emergency_readings_precision",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("section_id", "path", name="uq_emergency_readings_section_path"),
    )
    op.create_index(
        "ix_emergency_readings_sort_order_id",
        "emergency_readings",
        ["sort_order", "id"],
        unique=False,
    )
    op.create_index(
        "ix_emergency_readings_section_id",
        "emergency_readings",
        ["section_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_emergency_readings_section_id", table_name="emergency_readings")
    op.drop_index("ix_emergency_readings_sort_order_id", table_name="emergency_readings")
    op.drop_table("emergency_readings")
