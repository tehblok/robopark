"""Normalize diagnostic JSON locally, without changing the upstream payload/cache.

Paths contain dictionary keys and nonnegative list indexes separated by dots.
Lists and keyed collections expand to individual errors. Untouched structured
errors retain their raw fields; partially classified objects retain only their
residual diagnostic units and unknown siblings. Scalar code/message/text/path
fields form one unit, whereas structured diagnostic fields contain child units.
Exact matching retains raw compatibility and also uses canonical notification
bodies with normalized whitespace; nonstrings retain canonical JSON raw matching.
Regex uses search, once per rule/value, after compilation. Invalid persisted
patterns are skipped, leaving the corresponding raw errors visible.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import regex
from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import DiagnosticRule
from robopark_api.schemas import DiagnosticEvent

_ERROR_SOURCES = (
    "robotHudData.notifications.lastCritNotification",
    "robotHudData.notifications.lastErrorNotification",
    "robotHudData.notifications.lastWarnNotification",
    "lastCritNotification",
    "lastErrorNotification",
    "lastWarnNotification",
    "errors",
    "panics",
    "notifications",
)
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
_PATH_PART = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_-]*|[0-9]+)\Z")
_ERROR_FIELDS = {"code", "message", "text", "path"}
_JSON_VALUE = TypeAdapter(JsonValue)
REGEX_TIMEOUT_SECONDS = 0.01
MAX_REGEX_REPEAT = 1000
MAX_REGEX_EXPANSION = 10000
_COUNTED_REPEAT = re.compile(r"\{([0-9]*)(?:,([0-9]*))?\}")
_INLINE_FLAGS = re.compile(r"\(\?((?:[abefimprswxLu]|V0)*)(?:-([abefimprswxLu]+))?([:)])")
_POSIX_CLASS = re.compile(r"\[:\^?[A-Za-z0-9 &_.-]*(?:[:=][A-Za-z0-9 &_./-]+)?:\]")
_SUPPORTED_GROUP = re.compile(r"\(\?(?:[:=!>]|<[=!]|(?:P<|<)[A-Za-z_][A-Za-z0-9_]*>)")
_NOTIFICATION_PREFIX = re.compile(
    r"^(?:(CRIT|ERROR|WARN):\s*)?(?:\[\+\d+(?:\.\d+)?s\]\s*)?"
)
type Location = tuple[str | int, ...]
type IdentityPath = tuple[str | int | None, ...]


@dataclass(frozen=True)
class _RawError:
    location: Location
    # None marks a collection-expanded index; explicitly configured indexes
    # remain integers. Dict keys cannot collide with either typed segment.
    identity_path: IdentityPath
    value: Any


def _class_end(pattern: str, start: int) -> int:
    """Skip one VERSION0 class, including literal initial ] and POSIX classes."""
    index = start + 1
    if pattern[index : index + 1] == "^":
        index += 1
    if pattern[index : index + 1] == "]":
        index += 1
    while index < len(pattern):
        if pattern[index] == "\\":
            index += 2
        elif posix := _POSIX_CLASS.match(pattern, index):
            index = posix.end()
        elif pattern[index] == "]":
            return index + 1
        else:
            index += 1
    return index


def _repeat_at(pattern: str, start: int, verbose: bool) -> tuple[re.Match, int] | None:
    index = start + 1
    token = "{"
    while index < len(pattern):
        char = pattern[index]
        if verbose and char.isspace():
            index += 1
            continue
        if verbose and char == "#":
            end = pattern.find("\n", index)
            index = len(pattern) if end < 0 else end + 1
            continue
        if char not in "0123456789,}":
            return None
        token += char
        index += 1
        if char == "}":
            count = _COUNTED_REPEAT.fullmatch(token)
            return (count, index) if count and (count[1] or count[2]) else None
    return None


def _flags_at(pattern: str, start: int, verbose: bool) -> tuple[re.Match, int] | None:
    if not pattern.startswith("(?", start):
        return None
    index, token = start + 2, "(?"
    while index < len(pattern):
        char = pattern[index]
        if verbose and char.isspace():
            index += 1
            continue
        if verbose and char == "#":
            end = pattern.find("\n", index)
            index = len(pattern) if end < 0 else end + 1
            continue
        if not (char.isascii() and char.isalnum()) and char not in "-:)":
            return None
        token += char
        index += 1
        if char in ":)":
            flags = _INLINE_FLAGS.fullmatch(token)
            return (flags, index) if flags else None
    return None


def _check_regex_resources(pattern: str) -> None:
    """Bound compiled expansion before the native engine allocates instructions.

    Counted repetitions are limited to 1000. A conservative group-aware estimate
    caps their aggregate expansion at 10000 atoms: adjacent costs add, nested
    counts multiply. This lexical pass leaves syntax validation to the engine.
    Escapes, classes, comments and verbose/scoped flags are not repetition code.
    Only ordinary/named captures, noncapturing/atomic groups and lookarounds
    share the modeled group/flag stack. Other group extensions fail closed;
    notably, a conditional's condition is not an ordinary nested group.
    """
    index, total, last = 0, 0, 0
    verbose = False
    groups: list[tuple[int, int, bool]] = []
    while index < len(pattern):
        char = pattern[index]
        if verbose and char.isspace():
            index += 1
            continue
        if verbose and char == "#":
            end = pattern.find("\n", index)
            index = len(pattern) if end < 0 else end + 1
            continue
        if pattern.startswith("(?#", index):
            index += 3
            while index < len(pattern) and pattern[index] != ")":
                index += 2 if pattern[index] == "\\" else 1
            index += 1
            continue
        flag_token = _flags_at(pattern, index, verbose) if char == "(" else None
        if flag_token is not None:
            flags, end = flag_token
            outer_verbose = verbose
            if "x" in flags[1]:
                verbose = True
            if "x" in (flags[2] or ""):
                verbose = False
            if flags[3] == ":":
                groups.append((total, last, outer_verbose))
                total, last = 0, 0
            index = end
            continue
        if char == "\\":
            index += 2
            total, last = total + 1, 1
        elif char == "[":
            index = _class_end(pattern, index)
            total, last = total + 1, 1
        elif char == "(":
            if pattern.startswith(("(?", "(*"), index) and not _SUPPORTED_GROUP.match(
                pattern, index
            ):
                raise ValueError("unsupported_diagnostic_regex")
            groups.append((total, last, verbose))
            total, last = 0, 0
            index += 1
        elif char == ")" and groups:
            cost = max(1, total)
            outer_total, _, verbose = groups.pop()
            total, last = outer_total + cost, cost
            index += 1
        elif char == "{" and (repeat := _repeat_at(pattern, index, verbose)):
            count, end = repeat
            minimum = int(count[1] or 0)
            maximum = int(count[2]) if count[2] else minimum
            if max(minimum, maximum) > MAX_REGEX_REPEAT:
                raise ValueError("invalid_diagnostic_regex")
            multiplier = max(1, maximum + (count[2] == ""))
            total, last = total - last + last * multiplier, last * multiplier
            index = end
        elif char in "*+?^$|":
            if char == "|":
                last = 0
            index += 1
        else:
            total, last = total + 1, 1
            index += 1
        if total > MAX_REGEX_EXPANSION:
            raise ValueError("invalid_diagnostic_regex")


def compile_diagnostic_regex(pattern: str) -> regex.Pattern:
    """Shared compiler for snapshot matching and rule validation/preview.

    Keep re-compatible VERSION0 semantics explicit rather than depending on the
    engine's configurable default. Do not expose persisted pattern text in errors.
    A 512-character pattern has at most 1000 repetitions per count and a 10000
    atom conservative expansion budget. Validation happens before native compile.
    Supported group syntax: ordinary captures, ASCII-named (?P<name>)/(?<name>)
    captures, (?:), lookarounds, (?>), comments and VERSION0 inline flags.
    Conditional, recursive/subroutine, branch-reset, backreference-group and
    control-verb extensions raise unsupported_diagnostic_regex before compile.
    """
    if not 1 <= len(pattern) <= 512:
        raise ValueError("invalid_diagnostic_regex")
    _check_regex_resources(pattern)
    try:
        return regex.compile(pattern, flags=regex.VERSION0)
    except (regex.error, OverflowError, RecursionError, KeyError, TypeError) as exc:
        # Named Unicode sequences can reach the engine's single-character ord()
        # parser and raise TypeError. Normalize only errors from native compile.
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


def diagnostic_source_parts(path: str) -> tuple[str, ...] | None:
    """Validate the shared dotted key/index syntax without evaluating input."""
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


def canonical_notification_body(value: Any) -> str | None:
    """Project a stable rule body while leaving the upstream value untouched."""
    if isinstance(value, dict):
        name = str(value.get("name") or "").strip().rstrip(":")
        message = str(value.get("message") or "").strip()
        text = f"{name}: {message}" if name and message else name or message
    elif isinstance(value, str):
        text = _NOTIFICATION_PREFIX.sub("", value.strip(), count=1)
    else:
        return None
    return " ".join(text.split()) or None


def _exact_matches(pattern: str, value: Any) -> bool:
    raw_text = _raw_text(value)
    body = canonical_notification_body(value)
    normalized_pattern = " ".join(pattern.strip().split())
    return pattern == raw_text or (body is not None and normalized_pattern == body)


def _source_severity(source: str, value: Any) -> str | None:
    source_name = source.rsplit(".", maxsplit=1)[-1].lower()
    if source_name == "lastwarnnotification":
        return "warning"
    if source_name in {"lastcritnotification", "lasterrornotification"}:
        return "critical"
    if type(value) is str:
        prefix = _NOTIFICATION_PREFIX.match(value.strip())
        if prefix is not None and prefix.group(1):
            return "warning" if prefix.group(1) == "WARN" else "critical"
    return None


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


def _notification_identity_value(source: str, value: Any) -> Any:
    body = canonical_notification_body(value)
    if body is None:
        return value
    parts = source.split(".")
    structured_notification_source = "notifications" in parts or parts[0] in {
        "lastCritNotification",
        "lastErrorNotification",
        "lastWarnNotification",
        "notifications",
    }
    legacy_notification_source = structured_notification_source or parts[0] in {
        "errors",
        "panics",
    }
    if isinstance(value, dict):
        return body if structured_notification_source or "name" in value else value
    prefix = _NOTIFICATION_PREFIX.match(value.strip())
    return (
        body
        if legacy_notification_source or (prefix is not None and prefix.end() > 0)
        else value
    )


def _event_id(rule_id: int | None, identity_path: IdentityPath, identity_value: Any) -> str:
    # Hash typed path segments, not a lossy joined display string. Collection
    # positions are wildcards, so moving the same raw error keeps its selection.
    # Notification identity uses its stable body; raw values remain presentation
    # data. Array order inside every other value remains semantic.
    identity = _canonical([rule_id, identity_path, identity_value]).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _display_path(location: Location) -> str:
    return ".".join(
        part.replace("\\", "\\\\").replace(".", "\\.") if type(part) is str else str(part)
        for part in location
    )


def _is_within(location: Location, parent: Location) -> bool:
    return location[: len(parent)] == parent


@dataclass(frozen=True)
class _Residual:
    error: _RawError
    # Children retain original indexes, even when earlier items were classified.
    children: tuple[_Residual, ...] = ()
    changed: bool = False


def _residual_tree(error: _RawError, matched: set[Location]) -> _Residual | None:
    """Subtract classified units without discarding their enclosing containers.

    Scalar code/message/text/path fields describe one atomic diagnostic unit.
    Structured fields are collections: classifying one descendant does not
    classify its siblings. A remaining code collection keeps its scalar context;
    exhausting that collection also exhausts its accompanying scalar context.
    """
    location, raw = error.location, error.value
    if location in matched:
        return None
    if not any(_is_within(item, location) for item in matched):
        return _Residual(error)
    if type(raw) not in (dict, list):
        return _Residual(error)

    items = raw.items() if type(raw) is dict else enumerate(raw)
    children = {}
    for key, value in items:
        if type(raw) is list and (value is None or (type(value) is str and value == "")):
            # Empty collection slots are not faults and cannot keep an exhausted
            # code collection's scalar summary alive.
            continue
        identity_key = key if type(raw) is dict else None
        child = _residual_tree(
            _RawError((*location, key), (*error.identity_path, identity_key), value), matched
        )
        if child is not None:
            children[key] = child

    if type(raw) is dict:
        scalar_fields = {
            key for key in _ERROR_FIELDS.intersection(raw) if type(raw[key]) not in (dict, list)
        }
        classified_scalar = scalar_fields.difference(children)
        exhausted_codes = "code" in raw and "code" not in children
        if classified_scalar or exhausted_codes:
            for key in scalar_fields:
                children.pop(key, None)
        value = {key: child.error.value for key, child in children.items()}
    else:
        value = [child.error.value for child in children.values()]
    if not children:
        return None
    return _Residual(
        _RawError(location, error.identity_path, value), tuple(children.values()), changed=True
    )


def _residual_errors(node: _Residual, *, list_item: bool = False) -> Iterator[_RawError]:
    error = node.error
    if not node.changed:
        yield from _raw_errors(
            error.value, error.location, list_item=list_item, identity_path=error.identity_path
        )
        return
    grouped_fields: set[str] = set()
    if type(error.value) is dict:
        diagnostic_fields = _ERROR_FIELDS.intersection(error.value)
        if "code" in diagnostic_fields or any(
            type(error.value[key]) not in (dict, list) for key in diagnostic_fields
        ):
            # A coherent unclassified pair is a projection at its original
            # object path. Unrelated siblings below that path remain separate.
            grouped_fields = diagnostic_fields
            value = {key: error.value[key] for key in sorted(grouped_fields)}
            yield from _raw_errors(value, error.location, identity_path=error.identity_path)
    for child in node.children:
        if child.error.location[-1] not in grouped_fields:
            yield from _residual_errors(child, list_item=True)


def match_diagnostic_events(db: Session, payload: dict[str, Any]) -> list[DiagnosticEvent]:
    # Import locally: unknown capture already shares normalization helpers from
    # this module, while live projection alone needs persisted suppression.
    from robopark_api.services.diagnostic_unknowns import ignored_diagnostic_identities

    events = match_diagnostic_events_for_rules(list(db.scalars(select(DiagnosticRule))), payload)
    ignored = ignored_diagnostic_identities(db)
    return [event for event in events if event.rule_id is not None or event.id not in ignored]


def match_diagnostic_events_for_rules(
    rules: Sequence[DiagnosticRule], payload: dict[str, Any]
) -> list[DiagnosticEvent]:
    """The shared live/preview normalizer; supplied rules need no database attachment."""
    # Disabled rules still identify diagnostic sources; disabling their marker
    # must not remove a raw fault from a custom telemetry path.
    sources = set(_ERROR_SOURCES) | {rule.source_path for rule in rules}
    values: dict[str, list[_RawError]] = {}
    source_locations: dict[str, Location] = {}
    source_roots: dict[Location, _RawError] = {}
    for source in sorted(sources):
        parts = diagnostic_source_parts(source)
        if parts is not None:
            location, value = _lookup(payload, parts)
            source_locations[source] = location
            values[source] = list(_raw_errors(value, location))
            if location:
                source_roots[location] = _RawError(location, location, value)

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
                else _exact_matches(rule.pattern, raw)
            )
            if not matches:
                continue
            matched_locations.add(location)
            source_location = source_locations[rule.source_path]
            event_id = _event_id(
                rule.id,
                source_location,
                _notification_identity_value(rule.source_path, raw),
            )
            event_paths[event_id] = source_location
            events[event_id] = DiagnosticEvent(
                id=event_id,
                rule_id=rule.id,
                source_path=rule.source_path,
                source_segments=list(source_location),
                raw_value=raw,
                title=rule.title,
                description=rule.description,
                severity=_source_severity(rule.source_path, raw) or rule.severity,
                sort_order=rule.sort_order,
                part=rule.part,
                view=rule.preferred_view,
                x=rule.x,
                y=rule.y,
                indicator=rule.indicator,
            )

    # Select nonoverlapping source roots BEFORE pruning. A nested configured
    # source must not reintroduce metadata removed as part of an atomic unit.
    roots: list[Location] = []
    candidates: list[_RawError] = []
    for location in sorted(source_roots, key=lambda item: (len(item), _canonical(item))):
        if any(_is_within(location, root) for root in roots):
            continue
        roots.append(location)
        tree = _residual_tree(source_roots[location], matched_locations)
        if tree is not None:
            candidates.extend(_residual_errors(tree))
    for error in sorted(
        candidates,
        key=lambda item: (
            len(item.location),
            tuple((type(part) is int, part) for part in item.location),
        ),
    ):
        location, raw = error.location, error.value
        source = _display_path(location)
        event_id = _event_id(None, error.identity_path, _notification_identity_value(source, raw))
        # Identical raw occurrences on one wildcard-normalized source dedupe to
        # the first structural position; numeric index 2 precedes index 10.
        if event_id in events:
            continue
        event_paths[event_id] = error.identity_path
        events[event_id] = DiagnosticEvent(
            id=event_id,
            source_path=source,
            source_segments=list(location),
            raw_value=raw,
            title=_raw_text(raw),
            description="Неизвестная ошибка. Правило диагностики не найдено.",
            severity=_source_severity(source, raw) or "warning",
        )

    return sorted(
        events.values(),
        key=lambda item: (
            _SEVERITY_ORDER[item.severity],
            item.sort_order,
            item.rule_id is None,
            item.rule_id or 0,
            tuple(_canonical(part) for part in event_paths[item.id]),
            _canonical(item.raw_value),
            item.id,
        ),
    )


def diagnostic_rule_matches_sample(
    rule: DiagnosticRule,
    payload: dict[str, Any],
    sample_location: Location,
    *,
    consume: Callable[[], None] | None = None,
) -> bool:
    """Match only actual units at/below a recorded sample, excluding its envelope.

    Inbox reconstruction introduces ancestor containers and empty array padding.
    A rule must match the recorded unit (or its descendants), never a synthetic
    wrapper whose real upstream siblings were deliberately not retained.
    """
    parts = diagnostic_source_parts(rule.source_path)
    if not rule.is_enabled or parts is None:
        return False
    location, value = _lookup(payload, parts)
    pattern = compile_diagnostic_regex(rule.pattern) if rule.match_kind == "regex" else None
    if consume is not None:
        consume()
    for error in _raw_errors(value, location):
        if consume is not None:
            consume()
        if not _is_within(error.location, sample_location):
            continue
        text = _raw_text(error.value)
        if (
            diagnostic_regex_matches(pattern, text)
            if pattern is not None
            else _exact_matches(rule.pattern, error.value)
        ):
            return True
    return False
