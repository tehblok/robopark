"""The single clock for a task's daily 09:00–21:00 working-hour SLA."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

OPEN = time(9)
CLOSE = time(21)
SLA_TARGET_HOURS = 5


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("sla_timestamp_requires_timezone")
    return value.astimezone(UTC)


def _window(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, OPEN, zone).astimezone(UTC),
        datetime.combine(day, CLOSE, zone).astimezone(UTC),
    )


def deadline(start: datetime, *, timezone: str, target_hours: int = SLA_TARGET_HOURS) -> datetime:
    """Add working hours on every local calendar day, including weekends."""
    if target_hours <= 0:
        raise ValueError("sla_target_invalid")
    zone = ZoneInfo(timezone)
    cursor = _utc(start)
    day = cursor.astimezone(zone).date()
    remaining = timedelta(hours=target_hours)
    while remaining:
        opens, closes = _window(day, zone)
        active = max(cursor, opens)
        if active < closes:
            spent = min(remaining, closes - active)
            cursor = active + spent
            remaining -= spent
        if remaining:
            day += timedelta(days=1)
    return cursor


def elapsed_working_hours(start: datetime, end: datetime, *, timezone: str) -> float:
    """Count UTC intersections with each local daily working window."""
    zone = ZoneInfo(timezone)
    start_utc, end_utc = _utc(start), _utc(end)
    if end_utc < start_utc:
        raise ValueError("sla_end_precedes_start")
    day = start_utc.astimezone(zone).date()
    last_day = end_utc.astimezone(zone).date()
    total_seconds = 0.0
    while day <= last_day:
        opens, closes = _window(day, zone)
        left, right = max(start_utc, opens), min(end_utc, closes)
        if right > left:
            total_seconds += (right - left).total_seconds()
        day += timedelta(days=1)
    return total_seconds / 3600
