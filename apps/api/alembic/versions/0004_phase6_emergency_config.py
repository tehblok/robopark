"""Emergency config tables and seed from emergency_sections.json.

Revision ID: 0004
Revises: 0003
"""

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPERATOR_SECTIONS = frozenset({"position_route", "metadata"})
_ALL_VIEWER_ROLES = ("mechanic", "operator", "admin", "royal")
_OPERATOR_ROLES = ("operator", "admin", "royal")
_ADMIN_ROLES = ("admin", "royal")


def _roles_for_section(section_id: str) -> tuple[str, ...]:
    if section_id == "service_raw":
        return _ADMIN_ROLES
    if section_id in _OPERATOR_SECTIONS:
        return _OPERATOR_ROLES
    return _ALL_VIEWER_ROLES


def _section_meta(section: dict[str, Any]) -> str | None:
    extra = {
        key: value
        for key, value in section.items()
        if key not in {"title", "fields", "formatter"}
    }
    if not extra:
        return None
    return json.dumps(extra, ensure_ascii=False)


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

    emergency_sections = sa.table(
        "emergency_sections",
        sa.column("id", sa.String),
        sa.column("title", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("is_enabled", sa.Boolean),
        sa.column("formatter", sa.String),
        sa.column("meta_json", sa.Text),
    )
    emergency_fields = sa.table(
        "emergency_fields",
        sa.column("section_id", sa.String),
        sa.column("path", sa.String),
        sa.column("label", sa.String),
        sa.column("sort_order", sa.Integer),
    )
    emergency_section_roles = sa.table(
        "emergency_section_roles",
        sa.column("section_id", sa.String),
        sa.column("role", sa.String),
    )

    json_path = Path(__file__).resolve().parents[2] / "data" / "emergency_sections.json"
    if not json_path.is_file():
        logger.warning(
            "emergency seed file missing at %s; tables created empty",
            json_path,
        )
        return

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    sections = raw.get("sections") if isinstance(raw, dict) else None
    if not isinstance(sections, dict):
        raise ValueError("emergency_sections.json must contain a sections mapping")

    section_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    role_rows: list[dict[str, str]] = []

    for sort_order, (section_id, section) in enumerate(sections.items()):
        if not isinstance(section, dict):
            continue
        section_rows.append(
            {
                "id": section_id,
                "title": str(section.get("title") or section_id),
                "sort_order": sort_order,
                "is_enabled": True,
                "formatter": section.get("formatter"),
                "meta_json": _section_meta(section),
            }
        )
        for field_order, field in enumerate(section.get("fields") or []):
            if not isinstance(field, dict):
                continue
            field_rows.append(
                {
                    "section_id": section_id,
                    "path": str(field.get("path") or ""),
                    "label": str(field.get("label") or field.get("path") or ""),
                    "sort_order": field_order,
                }
            )
        for role in _roles_for_section(section_id):
            role_rows.append({"section_id": section_id, "role": role})

    if section_rows:
        op.bulk_insert(emergency_sections, section_rows)
    if field_rows:
        op.bulk_insert(emergency_fields, field_rows)
    if role_rows:
        op.bulk_insert(emergency_section_roles, role_rows)


def downgrade() -> None:
    op.drop_table("emergency_section_roles")
    op.drop_table("emergency_fields")
    op.drop_table("emergency_sections")
