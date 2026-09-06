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
from dataclasses import dataclass
from typing import Any

import regex
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
REGEX_TIMEOUT_SECONDS = 0.01
type Location = tuple[str | int, ...]
type IdentityPath = tuple[str | int | None, ...]


@dataclass(frozen=True)
class _RawError:
    location: Location
    # None marks a collection-expanded index; explicitly configured indexes
    # remain integers. Dict keys cannot collide with either typed segment.
    identity_path: IdentityPath
    value: Any


def compile_diagnostic_regex(pattern: str) -> regex.Pattern:
    """Shared compiler for snapshot matching and rule validation/preview.

    Keep re-compatible VERSION0 semantics explicit rather than depending on the
    engine's configurable default. Do not expose persisted pattern text in errors.
    """
    if not 1 <= len(pattern) <= 512:
        raise ValueError("invalid_diagnostic_regex")
    try:
        return regex.compile(pattern, flags=regex.VERSION0)
    except (regex.error, OverflowError, RecursionError) as exc:
        raise ValueError("invalid_diagnostic_regex") from exc


def diagnostic_regex_matches(pattern: regex.Pattern, value: str) -> bool:
    """One bounded search; a timeout leaves the raw value unclassified.

    The engine interrupts the operation itself and releases the GIL while
    matching immutable strings. No abandoned thread continues running afterward.
    """
    try:
        return pattern.search(value, timeout=REGEX_TIMEOUT_SECONDS, concurrent=True) is not None
    except TimeoutError:
        return False


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
    value: Any,
    location: Location,
    *,
    list_item: bool = False,
    identity_path: IdentityPath | None = None,
) -> Iterator[_RawError]:
    if identity_path is None:
        identity_path = location
    if type(value) is list:
        for index, item in enumerate(value):
            yield from _raw_errors(
                item, (*location, index), list_item=True, identity_path=(*identity_path, None)
            )
    elif type(value) is dict and not list_item and not _ERROR_FIELDS.intersection(value):
        for key in sorted(key for key in value if type(key) is str):
            yield from _raw_errors(
                value[key], (*location, key), identity_path=(*identity_path, key)
            )
    elif type(value) in (str, int, float, bool, dict) and value != "":
        try:
            _JSON_VALUE.validate_python(value)
            _canonical(value)
        except (TypeError, ValueError, RecursionError, ValidationError):
            return
        yield _RawError(location, identity_path, value)


def _event_id(rule_id: int | None, identity_path: IdentityPath, raw_value: Any) -> str:
    # Hash typed path segments, not a lossy joined display string. Collection
    # positions are wildcards, so moving the same raw error keeps its selection.
    identity = _canonical([rule_id, identity_path, raw_value]).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _display_path(location: Location) -> str:
    return ".".join(
        part.replace("\\", "\\\\").replace(".", "\\.") if type(part) is str else str(part)
        for part in location
    )


def _is_within(location: Location, parent: Location) -> bool:
    return location[: len(parent)] == parent


def _unmatched_errors(error: _RawError, matched_locations: set[Location]) -> Iterator[_RawError]:
    location, raw = error.location, error.value
    if any(_is_within(location, matched) for matched in matched_locations):
        return
    descendants = [matched for matched in matched_locations if _is_within(matched, location)]
    if not descendants:
        yield error
        return
    # A matched code classifies its accompanying message/text/path, not an
    # unrelated navigation/power fault in the same enclosing object.
    if type(raw) is dict:
        classified_core = any(matched[len(location)] in _ERROR_FIELDS for matched in descendants)
        for key, value in raw.items():
            if classified_core and key in _ERROR_FIELDS:
                continue
            for child in _raw_errors(
                value, (*location, key), list_item=True, identity_path=(*error.identity_path, key)
            ):
                yield from _unmatched_errors(child, matched_locations)


def match_diagnostic_events(db: Session, payload: dict[str, Any]) -> list[DiagnosticEvent]:
    # Disabled rules still identify diagnostic sources; disabling their marker
    # must not remove a raw fault from a custom telemetry path.
    rules = list(db.scalars(select(DiagnosticRule)))
    sources = set(_ERROR_SOURCES) | {rule.source_path for rule in rules}
    values: dict[str, list[_RawError]] = {}
    source_locations: dict[str, Location] = {}
    for source in sorted(sources):
        parts = _source_parts(source)
        if parts is not None:
            location, value = _lookup(payload, parts)
            source_locations[source] = location
            values[source] = list(_raw_errors(value, location))

    events: dict[str, DiagnosticEvent] = {}
    event_paths: dict[str, IdentityPath] = {}
    matched_locations: set[Location] = set()
    for rule in rules:
        if not rule.is_enabled:
            continue
        pattern = None
        if rule.match_kind == "regex":
            try:
                pattern = compile_diagnostic_regex(rule.pattern)
            except ValueError:
                continue
        for error in values.get(rule.source_path, []):
            location, raw = error.location, error.value
            text = _raw_text(raw)
            matches = (
                diagnostic_regex_matches(pattern, text)
                if pattern is not None
                else rule.pattern == text
            )
            if not matches:
                continue
            matched_locations.add(location)
            source_location = source_locations[rule.source_path]
            event_id = _event_id(rule.id, source_location, raw)
            event_paths[event_id] = source_location
            events[event_id] = DiagnosticEvent(
                id=event_id,
                rule_id=rule.id,
                source_path=rule.source_path,
                source_segments=list(source_location),
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

    # Atomic metadata stays classified across overlapping configured paths too.
    for matched in tuple(matched_locations):
        if matched and matched[-1] in _ERROR_FIELDS:
            parent_location = matched[:-1]
            _, parent = _lookup(payload, tuple(str(part) for part in parent_location))
            if type(parent) is dict:
                matched_locations.update(
                    (*parent_location, key) for key in _ERROR_FIELDS.intersection(parent)
                )

    # Preserve unknown siblings when only part of a structured collection was
    # classified, then deduplicate overlapping configured source paths.
    unknown_locations: list[Location] = []
    candidates = [
        unmatched
        for items in values.values()
        for error in items
        for unmatched in _unmatched_errors(error, matched_locations)
    ]
    for error in sorted(
        candidates, key=lambda item: (len(item.location), _canonical(item.location))
    ):
        location, raw = error.location, error.value
        if any(_is_within(location, known) for known in unknown_locations):
            continue
        unknown_locations.append(location)
        source = _display_path(location)
        event_id = _event_id(None, error.identity_path, raw)
        event_paths[event_id] = error.identity_path
        events[event_id] = DiagnosticEvent(
            id=event_id,
            source_path=source,
            source_segments=list(location),
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
            _canonical(event_paths[item.id]),
            _canonical(item.raw_value),
            item.id,
        ),
    )
