"""Load and seed Emergency section config from JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from robopark_api.models import EmergencyField, EmergencySection, EmergencySectionRole

DEFAULT_JSON_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "emergency_sections.json"
)

_OPERATOR_SECTIONS = frozenset({"position_route", "metadata"})
_ALL_VIEWER_ROLES = ("mechanic", "operator", "admin", "royal")
_OPERATOR_ROLES = ("operator", "admin", "royal")
_ADMIN_ROLES = ("admin", "royal")


def roles_for_section(section_id: str) -> tuple[str, ...]:
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


def seed_emergency_config(db: Session, json_path: Path | str) -> None:
    raw = json.loads(Path(json_path).read_text(encoding="utf-8"))
    sections = raw.get("sections") if isinstance(raw, dict) else None
    if not isinstance(sections, dict):
        raise ValueError("emergency_sections.json must contain a sections mapping")

    for sort_order, (section_id, section) in enumerate(sections.items()):
        if not isinstance(section, dict):
            continue
        db.add(
            EmergencySection(
                id=section_id,
                title=str(section.get("title") or section_id),
                sort_order=sort_order,
                is_enabled=True,
                formatter=section.get("formatter"),
                meta_json=_section_meta(section),
            )
        )
        for field_order, field in enumerate(section.get("fields") or []):
            if not isinstance(field, dict):
                continue
            db.add(
                EmergencyField(
                    section_id=section_id,
                    path=str(field.get("path") or ""),
                    label=str(field.get("label") or field.get("path") or ""),
                    sort_order=field_order,
                )
            )
        for role in roles_for_section(section_id):
            db.add(EmergencySectionRole(section_id=section_id, role=role))
    db.commit()
