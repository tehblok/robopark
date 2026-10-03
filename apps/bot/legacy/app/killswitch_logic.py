"""Killswitch campaign matching: Tracker tag query + park by tracker_tag."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def campaign_query(tag: str) -> str:
    t = (tag or "").strip()
    if not t:
        raise ValueError("пустой тег")
    if '"' in t or "\n" in t or "\r" in t:
        raise ValueError("тег не должен содержать кавычки")
    return f'Queue: SDCFLEETOPS AND Tags: "{t}"'


def identify_park_key(
    port: str | None,
    tags: list[str] | None,
    locations: list[dict[str, Any]],
) -> str | None:
    """Map a ticket to location.key via tracker_tag (never display_name)."""
    del port
    tag_set = {str(x) for x in (tags or []) if x}
    if not tag_set:
        return None
    for loc in locations:
        tracker_tag = str(loc.get("tracker_tag") or "")
        if tracker_tag and tracker_tag in tag_set:
            return str(loc["key"])
    return None


def aggregate_stats(
    issues: list[dict[str, Any]],
    location_keys: list[str],
    locations: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    allowed = {str(k) for k in location_keys}
    scoped = [loc for loc in locations if str(loc.get("key")) in allowed]
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"closed": 0, "open": 0})
    for issue in issues:
        park = identify_park_key(issue.get("homePort"), issue.get("tags"), scoped)
        if park is None or park not in allowed:
            continue
        closed = (issue.get("status") or {}).get("key") == "closed"
        stats[park]["closed" if closed else "open"] += 1
    return dict(stats)
