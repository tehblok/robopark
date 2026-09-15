"""Project allow-listed scalar Emergency values through the global readings catalog."""

from __future__ import annotations

import json
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import (
    EmergencyReading,
    EmergencySection,
    EmergencySectionRole,
)
from robopark_api.schemas import EmergencyReadingValue
from robopark_api.services.diagnostic_rules import diagnostic_source_parts
from robopark_api.services.emergency_reading_paths import safe_reading_key

_MISSING = object()
_UNAVAILABLE_DISPLAY = "Нет показания"
_DEFAULT_UNITS = {"percent": "%", "distance": "m", "current": "A"}
_DISPLAY_KINDS = {"text", "number", "percent", "distance", "current", "state"}


def _lookup(payload: Any, path: str) -> Any:
    parts = diagnostic_source_parts(path)
    if parts is None:
        return _MISSING
    current = payload
    for part in parts:
        if type(current) is dict and part in current:
            current = current[part]
        elif type(current) is list and part.isascii() and part.isdecimal():
            index = int(part)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _same_json_scalar(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _no_data_values(reading: EmergencyReading) -> list[Any] | None:
    try:
        values = json.loads(reading.no_data_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(values, list) or any(isinstance(value, (dict, list)) for value in values):
        return None
    return values


def _number(value: Any) -> float | None:
    if type(value) not in {int, float}:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _format(reading: EmergencyReading, value: Any) -> str | None:
    kind = reading.display_kind
    if kind not in _DISPLAY_KINDS:
        return None
    if kind == "text":
        if value is None or isinstance(value, (dict, list)):
            return None
        if value is True:
            return "Да"
        if value is False:
            return "Нет"
        return str(value)
    if kind == "state":
        if value is True:
            return "Да"
        if value is False:
            return "Нет"
        if value is None or isinstance(value, (dict, list)):
            return None
        return str(value)

    number = _number(value)
    if number is None or not 0 <= reading.precision <= 4:
        return None
    display = f"{number:.{reading.precision}f}"
    unit = reading.unit if reading.unit is not None else _DEFAULT_UNITS.get(kind)
    return f"{display} {unit}" if unit else display


def _state(reading: EmergencyReading, value: Any) -> str | None:
    number = _number(value)
    thresholds = (
        reading.warning_below,
        reading.warning_above,
        reading.critical_below,
        reading.critical_above,
    )
    if any(threshold is not None and not math.isfinite(threshold) for threshold in thresholds):
        return None
    if number is None:
        return "normal"
    if (reading.critical_below is not None and number < reading.critical_below) or (
        reading.critical_above is not None and number > reading.critical_above
    ):
        return "critical"
    if (reading.warning_below is not None and number < reading.warning_below) or (
        reading.warning_above is not None and number > reading.warning_above
    ):
        return "warning"
    return "normal"


def _unavailable(
    reading: EmergencyReading, display: str = _UNAVAILABLE_DISPLAY
) -> EmergencyReadingValue:
    return EmergencyReadingValue(
        id=reading.id,
        section_id=reading.section_id,
        label=reading.label,
        display=display,
        state="unavailable",
        view=reading.view,
        x=reading.x,
        y=reading.y,
        label_direction=reading.label_direction,
    )


def _render_one(reading: EmergencyReading, payload: dict[str, Any]) -> EmergencyReadingValue:
    values = _no_data_values(reading)
    value = _lookup(payload, reading.path)
    enabled = True if reading.enabled_path is None else _lookup(payload, reading.enabled_path)
    if enabled is False:
        return _unavailable(reading, "Отключён")
    if (
        values is None
        or value is _MISSING
        or enabled is not True
        or any(_same_json_scalar(value, sentinel) for sentinel in values)
    ):
        return _unavailable(reading)
    display = _format(reading, value)
    state = _state(reading, value)
    if display is None or state is None:
        return _unavailable(reading)
    return EmergencyReadingValue(
        id=reading.id,
        section_id=reading.section_id,
        label=reading.label,
        display=display,
        state=state,
        view=reading.view,
        x=reading.x,
        y=reading.y,
        label_direction=reading.label_direction,
    )


def render_readings(db: Session, payload: dict[str, Any], role: str) -> list[EmergencyReadingValue]:
    """Render active readings from sections visible to ``role``.

    Persisted rows are treated as untrusted configuration. A malformed row is
    projected as unavailable and cannot prevent the remaining rows rendering.
    """
    readings = db.scalars(
        select(EmergencyReading)
        .join(EmergencySection, EmergencySection.id == EmergencyReading.section_id)
        .join(
            EmergencySectionRole,
            (EmergencySectionRole.section_id == EmergencyReading.section_id)
            & (EmergencySectionRole.role == role),
        )
        .where(EmergencyReading.is_enabled.is_(True), EmergencySection.is_enabled.is_(True))
        .order_by(EmergencyReading.sort_order, EmergencyReading.id)
    ).all()
    rendered: list[EmergencyReadingValue] = []
    for reading in readings:
        if any(
            not safe_reading_key(part)
            for path in (reading.path, reading.enabled_path)
            if path is not None
            for part in path.split(".")
        ):
            continue
        try:
            rendered.append(_render_one(reading, payload))
        except (TypeError, ValueError, OverflowError):
            rendered.append(_unavailable(reading))
    return rendered
