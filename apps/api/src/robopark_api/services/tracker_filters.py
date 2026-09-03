"""Status buckets and park Tracker scope helpers."""

from __future__ import annotations

from typing import Any

STATUS_BUCKETS: dict[str, tuple[str, ...]] = {
    "new": ("new", "open", "новый", "новая", "открыт", "открыта"),
    "diagnostics": ("diagnostics", "diagnostic", "diagnosis", "диагностика", "на диагностике"),
    "queued": ("queued", "в очереди"),
    "moving": ("moving", "перемещение"),
    "waiting_team": ("waitingforanotherteam", "ждём смежников", "ждем смежников"),
    "waiting_parts": ("delieverywaiting", "deliverywaiting", "ожидание поставки"),
}

STATUS_FILTER_BUTTONS: tuple[tuple[str, str], ...] = (
    ("all", "All"),
    ("new", "New"),
    ("moving", "Moving"),
    ("queued", "Queued"),
    ("diagnostics", "Diagnostics"),
    ("waiting_team", "Waiting team"),
    ("waiting_parts", "Waiting parts"),
    ("other", "Other"),
)

VALID_STATUS_FILTERS = {name for name, _ in STATUS_FILTER_BUTTONS}


def park_priority_type(park: Any) -> tuple[str, str | None]:
    """Park-level Tracker priority/type overrides used by all blocker/report paths."""
    priority = (getattr(park, "tracker_priority", None) or "blocker").strip() or "blocker"
    issue_type = (getattr(park, "tracker_type", None) or "").strip() or None
    return priority, issue_type


def status_bucket(status_key: str, status_display: str = "") -> str | None:
    key = (status_key or "").strip().lower().replace("ё", "е")
    display = (status_display or "").strip().lower().replace("ё", "е")
    for bucket, aliases in STATUS_BUCKETS.items():
        for alias in aliases:
            normalized = alias.lower().replace("ё", "е")
            if key == normalized or display == normalized:
                return bucket
    return None


def issue_status_bucket(item: dict[str, Any]) -> str:
    if str(item.get("in_relocation") or "") == "1":
        return "moving"
    display = str(item.get("status") or "")
    bucket = status_bucket(str(item.get("status_key") or ""), display)
    if bucket:
        return bucket
    return "other"


def filter_issues_by_status(issues: list[dict[str, Any]], bucket: str) -> list[dict[str, Any]]:
    if bucket in ("", "all", None):
        return list(issues)
    if bucket == "other":
        known = set(STATUS_BUCKETS)
        return [item for item in issues if issue_status_bucket(item) not in known]
    return [item for item in issues if issue_status_bucket(item) == bucket]


def count_status_buckets(issues: list[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name, _ in STATUS_FILTER_BUTTONS}
    counts["all"] = len(issues)
    for item in issues:
        bucket = issue_status_bucket(item)
        if bucket not in counts:
            bucket = "other"
        if bucket != "all":
            counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def sort_issues_oldest_first(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _hours(item: dict[str, Any]) -> float:
        raw = item.get("hours_created")
        try:
            if raw is None or raw == "—":
                return -1.0
            return float(raw)
        except (TypeError, ValueError):
            return -1.0

    def _created(item: dict[str, Any]) -> str:
        return str(item.get("created") or "9999")

    return sorted(issues, key=lambda item: (-_hours(item), _created(item)))
