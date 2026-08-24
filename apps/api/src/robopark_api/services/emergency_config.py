"""Load and seed Emergency section config from JSON."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from robopark_api.models import EmergencyField, EmergencySection, EmergencySectionRole

DEFAULT_JSON_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "emergency_sections.json"
)

_OPERATOR_SECTIONS = frozenset({"position_route", "metadata"})
_ALL_VIEWER_ROLES = ("mechanic", "operator", "admin", "royal")
_OPERATOR_ROLES = ("operator", "admin", "royal")
_ADMIN_ROLES = ("admin", "royal")

_CONFIG_CACHE_TTL_SECONDS = 30.0
_sections_cache: list[dict[str, Any]] | None = None
_cache_loaded_at: float = 0.0


def roles_for_section(section_id: str) -> tuple[str, ...]:
    if section_id == "service_raw":
        return _ADMIN_ROLES
    if section_id in _OPERATOR_SECTIONS:
        return _OPERATOR_ROLES
    return _ALL_VIEWER_ROLES


def invalidate_config_cache() -> None:
    global _sections_cache, _cache_loaded_at
    _sections_cache = None
    _cache_loaded_at = 0.0


def _parse_meta(meta_json: str | None) -> dict[str, Any] | None:
    if not meta_json:
        return None
    parsed = json.loads(meta_json)
    if not isinstance(parsed, dict) or not parsed:
        return None
    return parsed


def _section_to_config(section: EmergencySection) -> dict[str, Any]:
    fields = sorted(section.fields, key=lambda field: field.sort_order)
    roles = sorted(role.role for role in section.roles)
    return {
        "id": section.id,
        "title": section.title,
        "sort_order": section.sort_order,
        "is_enabled": section.is_enabled,
        "formatter": section.formatter,
        "meta": _parse_meta(section.meta_json),
        "fields": [{"path": field.path, "label": field.label} for field in fields],
        "roles": roles,
    }


def _load_sections(db: Session) -> list[dict[str, Any]]:
    global _sections_cache, _cache_loaded_at
    now = time.monotonic()
    if _sections_cache is not None and (now - _cache_loaded_at) < _CONFIG_CACHE_TTL_SECONDS:
        return _sections_cache

    stmt = (
        select(EmergencySection)
        .options(
            selectinload(EmergencySection.fields),
            selectinload(EmergencySection.roles),
        )
        .order_by(EmergencySection.sort_order, EmergencySection.id)
    )
    sections = db.scalars(stmt).all()
    _sections_cache = [_section_to_config(section) for section in sections]
    _cache_loaded_at = now
    return _sections_cache


def list_sections_for_role(db: Session, role: str) -> list[tuple[str, str]]:
    return [
        (section["id"], section["title"])
        for section in _load_sections(db)
        if section["is_enabled"] and role in section["roles"]
    ]


def get_section_config(db: Session, section_id: str) -> dict[str, Any] | None:
    for section in _load_sections(db):
        if section["id"] == section_id:
            return {
                "id": section["id"],
                "title": section["title"],
                "formatter": section["formatter"],
                "meta": copy.deepcopy(section["meta"]),
                "fields": copy.deepcopy(section["fields"]),
                "roles": list(section["roles"]),
            }
    return None


def role_can_view_section(db: Session, role: str, section_id: str) -> bool:
    for section in _load_sections(db):
        if section["id"] == section_id:
            return section["is_enabled"] and role in section["roles"]
    return False


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
    invalidate_config_cache()
