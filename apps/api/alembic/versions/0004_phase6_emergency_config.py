"""Emergency config tables and seed from emergency_sections.json.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from robopark_api.services.emergency_config import DEFAULT_JSON_PATH, seed_emergency_config


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "emergency_sections",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("formatter", sa.String(length=64), nullable=True),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "emergency_fields",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("section_id", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=256), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["section_id"], ["emergency_sections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "emergency_section_roles",
        sa.Column("section_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["section_id"], ["emergency_sections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("section_id", "role"),
    )

    json_path = Path(__file__).resolve().parents[2] / "data" / "emergency_sections.json"
    if not json_path.is_file():
        json_path = DEFAULT_JSON_PATH
    bind = op.get_bind()
    with Session(bind=bind) as session:
        seed_emergency_config(session, json_path)


def downgrade() -> None:
    op.drop_table("emergency_section_roles")
    op.drop_table("emergency_fields")
    op.drop_table("emergency_sections")
