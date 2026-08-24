from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from robopark_api.services.tracker_client import (
    build_open_blockers_query,
    count_issues,
    join_query,
    ql_quote,
)

DEFAULT_STATUS_KEYS: dict[str, list[str]] = {
    "waiting_parts": ["delieveryWaiting", "Ожидание поставки"],
    "in_transit": ["moving", "Перемещение"],
    "queued": ["queued", "В очереди"],
    "waiting_team": ["waitingForAnotherTeam", "Ждём смежников"],
}

_METRICS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
METRICS_CACHE_TTL_SEC = 60


def clear_metrics_cache() -> None:
    _METRICS_CACHE.clear()


def get_cached_now_report(cache_key: str) -> dict[str, Any] | None:
    entry = _METRICS_CACHE.get(cache_key)
    if entry is None:
        return None
    expires_at, payload = entry
    if expires_at <= time.monotonic():
        _METRICS_CACHE.pop(cache_key, None)
        return None
    return payload


def set_cached_now_report(
    cache_key: str,
    payload: dict[str, Any],
    *,
    ttl_sec: int = METRICS_CACHE_TTL_SEC,
) -> None:
    _METRICS_CACHE[cache_key] = (time.monotonic() + ttl_sec, payload)


def _status_or_clause(statuses: list[str]) -> str:
    parts: list[str] = []
    for raw in statuses:
        text = raw.strip()
        if not text:
            continue
        if " " in text or not text.isascii():
            parts.append(f'Status: "{text}"')
        else:
            parts.append(f"Status: {text}")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "(" + " OR ".join(parts) + ")"


def build_backlog_query(queue: str, tag: str, donor_tag: str = "") -> str:
    parts = [
        f"Queue: {queue}",
        "Priority: blocker",
        "Resolution: empty()",
        f"Tags: {ql_quote(tag)}",
    ]
    donor = (donor_tag or "").strip()
    if donor:
        parts.append(f"Tags: !{ql_quote(donor)}")
    return join_query(*parts)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_ql_datetime(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def _park_scoped_parts(
    queue: str,
    tag: str,
    *,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> list[str]:
    parts = [
        f"Queue: {queue}",
        f"Priority: {priority}",
        f"Tags: {ql_quote(tag)}",
    ]
    if issue_type:
        parts.append(f"Type: {issue_type}")
    return parts


def build_arrived_today_query(queue: str, tag: str) -> str:
    return join_query(
        *_park_scoped_parts(queue, tag),
        "Resolution: empty(), fixed",
        "Created: today()",
    )


def build_done_today_query(queue: str, tag: str) -> str:
    return join_query(
        *_park_scoped_parts(queue, tag),
        "Resolution: fixed",
        "Updated: today()",
    )


def build_arrived_in_window_query(
    queue: str,
    tag: str,
    bucket_start: datetime,
    bucket_end: datetime,
    *,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> str:
    start = _format_ql_datetime(bucket_start)
    end = _format_ql_datetime(bucket_end)
    return join_query(
        *_park_scoped_parts(
            queue,
            tag,
            priority=priority,
            issue_type=issue_type,
        ),
        "Resolution: empty(), fixed",
        f'Created: >= "{start}"',
        f'Created: < "{end}"',
    )


def build_departed_in_window_query(
    queue: str,
    tag: str,
    bucket_start: datetime,
    bucket_end: datetime,
    *,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> str:
    start = _format_ql_datetime(bucket_start)
    end = _format_ql_datetime(bucket_end)
    return join_query(
        *_park_scoped_parts(
            queue,
            tag,
            priority=priority,
            issue_type=issue_type,
        ),
        "Resolution: fixed",
        f'Updated: >= "{start}"',
        f'Updated: < "{end}"',
    )


def build_status_query(queue: str, tag: str, statuses: list[str]) -> str | None:
    status_part = _status_or_clause(statuses)
    if not status_part:
        return None
    return join_query(
        f"Queue: {queue}",
        "Priority: blocker",
        "Resolution: empty()",
        f"Tags: {ql_quote(tag)}",
        status_part,
    )


def collect_park_metrics(*, token: str, queue: str, tag: str) -> dict[str, int]:
    keys = DEFAULT_STATUS_KEYS
    query_map: list[tuple[str, str | None]] = [
        ("open_blockers", build_open_blockers_query(queue, tag)),
        ("backlog", build_backlog_query(queue, tag, "")),
        ("in_transit", build_status_query(queue, tag, keys["in_transit"])),
        ("queued", build_status_query(queue, tag, keys["queued"])),
        ("waiting_team", build_status_query(queue, tag, keys["waiting_team"])),
        ("waiting_parts", build_status_query(queue, tag, keys["waiting_parts"])),
        ("arrived", build_arrived_today_query(queue, tag)),
        ("done", build_done_today_query(queue, tag)),
    ]
    metrics: dict[str, int] = {}
    for key, query in query_map:
        metrics[key] = count_issues(token=token, query=query) if query else 0
    return metrics
