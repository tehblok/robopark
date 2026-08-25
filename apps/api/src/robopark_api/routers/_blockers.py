"""Shared mapping from a normalized Tracker issue to :class:`BlockerOut`.

Four routers (mechanic tasks/robots, operator blockers/robots) used to repeat
this conversion, which is how `priority` and `assignee` ended up missing from
some of them.
"""

from __future__ import annotations

from typing import Any

from robopark_api.schemas import BlockerOut, TrackerPersonOut
from robopark_api.services import tracker_client, tracker_filters


def person_out(raw: Any) -> TrackerPersonOut | None:
    if not isinstance(raw, dict):
        return None
    display = str(raw.get("display") or "").strip()
    login = str(raw.get("login") or "").strip()
    if not display and not login:
        return None
    return TrackerPersonOut(display=display or login, login=login)


def blocker_out(item: dict[str, Any]) -> BlockerOut:
    return BlockerOut(
        key=item["key"],
        summary=item["summary"],
        status=item["status"],
        status_key=str(item.get("status_key") or ""),
        robot=item.get("robot"),
        created_at=item.get("created"),
        hours_created=item.get("hours_created"),
        url=tracker_client.build_issue_url(item["key"]),
        bucket=tracker_filters.issue_status_bucket(item),
        priority=str(item.get("priority") or ""),
        assignee=person_out(item.get("assignee")),
    )
