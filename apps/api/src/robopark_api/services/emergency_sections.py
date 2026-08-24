"""Render Emergency API payload sections from database config."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from robopark_api.services.emergency_config import (
    get_section_config,
    list_sections_for_role,
)

PARKTRONICS_NO_DATA = 2147483647
_MISSING = object()


def list_sections(db: Session, role: str) -> list[tuple[str, str]]:
    return list_sections_for_role(db, role)


def _dig(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _scalar(value: Any) -> str:
    if value is None or value == "":
        return "нет данных"
    if value == PARKTRONICS_NO_DATA or str(value) == str(PARKTRONICS_NO_DATA):
        return "нет данных"
    if value is True:
        return "да"
    if value is False:
        return "нет"
    return str(value)


def _value_lines(value: Any, *, indent: int = 0) -> list[str]:
    prefix = "  " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}нет данных"]
        lines: list[str] = []
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_value_lines(nested, indent=indent + 1))
            else:
                lines.append(f"{prefix}{key}: {_scalar(nested)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}нет данных"]
        if all(not isinstance(item, (dict, list)) for item in value):
            return [f"{prefix}{', '.join(_scalar(item) for item in value)}"]
        lines = []
        for index, item in enumerate(value, start=1):
            lines.append(f"{prefix}{index}:")
            lines.extend(_value_lines(item, indent=indent + 1))
        return lines
    return [f"{prefix}{_scalar(value)}"]


def _render_fields(payload: dict[str, Any], fields: list[dict[str, str]]) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    for field in fields:
        path = str(field.get("path") or "")
        label = str(field.get("label") or path)
        value = _dig(payload, path)
        if value is _MISSING:
            lines = ["нет данных"]
        else:
            lines = _value_lines(value)
        rendered.append({"label": label, "lines": lines})
    return rendered


def _render_errors(payload: dict[str, Any], section: dict[str, Any]) -> list[dict[str, Any]]:
    meta = section.get("meta") or {}
    covered = meta.get("covered_top_level") or ["panics", "errors"]
    rendered: list[dict[str, Any]] = []
    for key in covered:
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        rendered.append({"label": str(key), "lines": _value_lines(value)})
    if not rendered:
        rendered.append({"label": "Ошибки", "lines": ["нет данных"]})
    return rendered


def _render_fields_and_top_level_leftovers(
    payload: dict[str, Any], fields: list[dict[str, str]]
) -> list[dict[str, Any]]:
    rendered = _render_fields(payload, fields)
    covered_top_level = {
        str(field.get("path") or "").split(".", maxsplit=1)[0] for field in fields
    }
    rendered.extend(
        {"label": str(key), "lines": _value_lines(value)}
        for key, value in payload.items()
        if key not in covered_top_level
    )
    return rendered


def render_section(
    db: Session, payload: dict[str, Any], section_id: str
) -> dict[str, Any]:
    section = get_section_config(db, section_id)
    if section is None:
        raise KeyError(f"unknown or disabled emergency section: {section_id}")
    title = str(section.get("title") or section_id)
    formatter = section.get("formatter")
    if formatter == "errors_classify":
        fields = _render_errors(payload, section)
    elif formatter == "fields_and_top_level_leftovers":
        fields = _render_fields_and_top_level_leftovers(
            payload, list(section.get("fields") or [])
        )
    else:
        fields = _render_fields(payload, list(section.get("fields") or []))
    return {"id": section_id, "title": title, "fields": fields}
