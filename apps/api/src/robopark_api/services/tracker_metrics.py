"""Park metrics queries for Startrek — aligned with bot_otchet patterns."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from robopark_api.services.tracker_cache import count_issues
from robopark_api.services.tracker_client import (
    DEFAULT_ISSUE_TYPES,
    DEFAULT_QUEUE,
    build_open_blockers_query,
    exclude_tag,
    join_query,
    ql_quote,
    ql_token,
)

# Fleet convention (bot_otchet): backlog excludes donor tickets.
DEFAULT_DONOR_TAG = "donor"

# typo in Tracker status key is intentional (fleet workflow)
DEFAULT_STATUS_KEYS: dict[str, list[str]] = {
    "waiting_parts": ["delieveryWaiting", "Ожидание поставки"],
    "in_transit": ["moving", "Перемещение"],
    "queued": ["queued", "В очереди"],
    "waiting_team": ["waitingForAnotherTeam", "Ждём смежников"],
}

_METRICS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
METRICS_CACHE_TTL_SEC = 90


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
        text = (raw or "").strip()
        if not text:
            continue
        token = ql_token(text) or ql_quote(text)
        parts.append(f"Status: {token}")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "(" + " OR ".join(parts) + ")"


def _queue_uses_issue_types(queue: str) -> bool:
    raw = (queue or "").strip().strip('"') or DEFAULT_QUEUE
    return raw.upper() == DEFAULT_QUEUE.upper()


def _type_part(queue: str, issue_type: str | None) -> str:
    explicit = (issue_type or "").strip()
    if explicit:
        return f"Type: {ql_token(explicit)}"
    if _queue_uses_issue_types(queue):
        return f"Type: {', '.join(DEFAULT_ISSUE_TYPES)}"
    return ""


def _park_scoped_parts(
    queue: str,
    tag: str,
    *,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> list[str]:
    q = ql_token(queue) or queue
    parts = [
        f"Queue: {q}",
        _type_part(queue, issue_type),
        f"Priority: {ql_token(priority) or 'blocker'}",
        f"Tags: {ql_token(tag)}",
    ]
    return [p for p in parts if p]


def build_backlog_query(
    queue: str,
    tag: str,
    donor_tag: str = "",
    *,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> str:
    parts = [
        *_park_scoped_parts(queue, tag, priority=priority, issue_type=issue_type),
        "Resolution: empty()",
    ]
    donor = exclude_tag(donor_tag)
    if donor:
        parts.append(donor)
    return join_query(*parts)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_ql_datetime(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def build_arrived_today_query(
    queue: str,
    tag: str,
    *,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> str:
    return join_query(
        *_park_scoped_parts(queue, tag, priority=priority, issue_type=issue_type),
        "Created: today()",
    )


def build_done_today_query(
    queue: str,
    tag: str,
    *,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> str:
    return join_query(
        *_park_scoped_parts(queue, tag, priority=priority, issue_type=issue_type),
        "Resolution: fixed",
        "Resolved: today()",
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
        f'Resolved: >= "{start}"',
        f'Resolved: < "{end}"',
    )


def build_status_query(
    queue: str,
    tag: str,
    statuses: list[str],
    *,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> str | None:
    status_part = _status_or_clause(statuses)
    if not status_part:
        return None
    return join_query(
        *_park_scoped_parts(queue, tag, priority=priority, issue_type=issue_type),
        "Resolution: empty()",
        status_part,
    )


def collect_park_metrics(
    *,
    token: str,
    queue: str,
    tag: str,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> dict[str, int]:
    keys = DEFAULT_STATUS_KEYS
    scoped = {"priority": priority, "issue_type": issue_type}
    query_map: list[tuple[str, str | None]] = [
        (
            "open_blockers",
            build_open_blockers_query(
                queue,
                tag,
                priority=priority,
                issue_type=issue_type,
            ),
        ),
        ("backlog", build_backlog_query(queue, tag, DEFAULT_DONOR_TAG, **scoped)),
        ("in_transit", build_status_query(queue, tag, keys["in_transit"], **scoped)),
        ("queued", build_status_query(queue, tag, keys["queued"], **scoped)),
        ("waiting_team", build_status_query(queue, tag, keys["waiting_team"], **scoped)),
        ("waiting_parts", build_status_query(queue, tag, keys["waiting_parts"], **scoped)),
        ("arrived", build_arrived_today_query(queue, tag, **scoped)),
        ("done", build_done_today_query(queue, tag, **scoped)),
    ]
    metrics: dict[str, int] = {}
    for key, query in query_map:
        metrics[key] = count_issues(token=token, query=query) if query else 0
    return metrics
