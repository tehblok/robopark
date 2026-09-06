"""Normalize diagnostic JSON locally, without changing the upstream payload/cache.

Paths contain dictionary keys and nonnegative list indexes separated by dots.
Lists and keyed collections expand to individual errors. An object with a code,
message, text or path is one structured error and keeps all its original fields.
Exact matching is case/whitespace sensitive; nonstrings use canonical JSON.
Regex uses search, once per rule/value, after compilation. Invalid persisted
patterns are skipped, leaving the corresponding raw errors visible.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from typing import Any

from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import DiagnosticRule
from robopark_api.schemas import DiagnosticEvent

_ERROR_SOURCES = (
    "lastCritNotification",
    "lastErrorNotification",
    "errors",
    "panics",
    "notifications",
)
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
_PATH_PART = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_-]*|[0-9]+)\Z")
_ERROR_FIELDS = {"code", "message", "text", "path"}
_JSON_VALUE = TypeAdapter(JsonValue)
type Location = tuple[str | int, ...]


def _source_parts(path: str) -> tuple[str, ...] | None:
    parts = tuple(path.split("."))
    if len(path) > 256 or any(
        not _PATH_PART.fullmatch(part) or part.startswith("__") for part in parts
    ):
        return None
    return parts


def _lookup(payload: Any, parts: tuple[str, ...]) -> tuple[Location, Any]:
    value = payload
    location: Location = ()
    for part in parts:
        if type(value) is dict and part in value:
            value = value[part]
            location += (part,)
        elif type(value) is list and part.isascii() and part.isdecimal():
            index = int(part)
            if index >= len(value):
                return (), None
            value = value[index]
            location += (index,)
        else:
            return (), None
    return location, value


def _canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _raw_text(value: Any) -> str:
    return value if type(value) is str else _canonical(value)


def _raw_errors(
    value: Any, location: Location, *, list_item: bool = False
) -> Iterator[tuple[Location, Any]]:
    if type(value) is list:
        for index, item in enumerate(value):
            yield from _raw_errors(item, (*location, index), list_item=True)
    elif type(value) is dict and not list_item and not _ERROR_FIELDS.intersection(value):
        for key in sorted(key for key in value if type(key) is str):
            yield from _raw_errors(value[key], (*location, key))
    elif type(value) in (str, int, float, bool, dict) and value != "":
        try:
            _JSON_VALUE.validate_python(value)
            _canonical(value)
        except (TypeError, ValueError, RecursionError, ValidationError):
            return
        yield location, value


def _event_id(rule_id: int | None, source_path: str, raw_value: Any) -> str:
    # Excludes list positions and mutable presentation fields, so selection stays
    # stable when upstream reorders a collection or an admin edits the title.
    identity = _canonical([rule_id, source_path, raw_value]).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _is_within(location: Location, parent: Location) -> bool:
    return location[: len(parent)] == parent


def match_diagnostic_events(db: Session, payload: dict[str, Any]) -> list[DiagnosticEvent]:
    # Disabled rules still identify diagnostic sources; disabling their marker
    # must not remove a raw fault from a custom telemetry path.
    rules = list(db.scalars(select(DiagnosticRule)))
    sources = set(_ERROR_SOURCES) | {rule.source_path for rule in rules}
    values: dict[str, list[tuple[Location, Any]]] = {}
    for source in sorted(sources):
        parts = _source_parts(source)
        if parts is not None:
            location, value = _lookup(payload, parts)
            values[source] = list(_raw_errors(value, location))

    events: dict[str, DiagnosticEvent] = {}
    matched_locations: set[Location] = set()
    for rule in rules:
        if not rule.is_enabled:
            continue
        pattern = None
        if rule.match_kind == "regex":
            try:
                pattern = re.compile(rule.pattern)
            except (re.error, OverflowError, RecursionError):
                continue
        for location, raw in values.get(rule.source_path, []):
            text = _raw_text(raw)
            matches = (
                pattern.search(text) is not None if pattern is not None else rule.pattern == text
            )
            if not matches:
                continue
            matched_locations.add(location)
            event_id = _event_id(rule.id, rule.source_path, raw)
            events[event_id] = DiagnosticEvent(
                id=event_id,
                rule_id=rule.id,
                source_path=rule.source_path,
                raw_value=raw,
                title=rule.title,
                description=rule.description,
                severity=rule.severity,
                sort_order=rule.sort_order,
                part=rule.part,
                view=rule.preferred_view,
                x=rule.x,
                y=rule.y,
                indicator=rule.indicator,
            )

    # A rule targeting errors.0.code classifies that whole structured error.
    # Overlapping configured paths must neither duplicate nor hide other errors.
    unknown_locations: list[Location] = []
    candidates = [item for items in values.values() for item in items]
    for location, raw in sorted(candidates, key=lambda item: (len(item[0]), _canonical(item[0]))):
        if any(
            _is_within(location, matched) or _is_within(matched, location)
            for matched in matched_locations
        ) or any(_is_within(location, known) for known in unknown_locations):
            continue
        unknown_locations.append(location)
        source = ".".join(part for part in location if type(part) is str)
        event_id = _event_id(None, source, raw)
        events[event_id] = DiagnosticEvent(
            id=event_id,
            source_path=source,
            raw_value=raw,
            title=_raw_text(raw),
            description="Неизвестная ошибка. Правило диагностики не найдено.",
            severity="warning",
        )

    return sorted(
        events.values(),
        key=lambda item: (
            _SEVERITY_ORDER[item.severity],
            item.sort_order,
            item.rule_id is None,
            item.rule_id or 0,
            item.source_path,
            _canonical(item.raw_value),
            item.id,
        ),
    )
