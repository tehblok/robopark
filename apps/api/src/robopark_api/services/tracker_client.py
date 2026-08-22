"""Yandex Tracker HTTP client."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

TRACKER_SEARCH_URL = "https://api.tracker.yandex.net/v2/issues/_search"
TRACKER_ISSUE_URL = "https://api.tracker.yandex.net/v2/issues"


class TrackerError(Exception):
    pass


def build_issue_url(key: str) -> str:
    return f"https://tracker.yandex.ru/{key}"


def _join_query(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def _ql_quote(value: str) -> str:
    text = value.replace('"', '\\"')
    return f'"{text}"'


def _open_issues_clause() -> str:
    return (
        'Resolution: empty() '
        '(Status: !closed AND Status: !"Закрыт" AND Status: !"Closed" '
        'AND Status: !resolved AND Status: !"Решен" AND Status: !"Решён")'
    )


def build_open_blockers_query(queue: str, tag: str) -> str:
    return _join_query(
        f"Queue: {queue}",
        "Priority: blocker",
        _open_issues_clause(),
        f"Tags: {_ql_quote(tag)}",
    )


def _hours_since(created: str) -> float | None:
    if not created:
        return None
    text = created.strip().replace("Z", "+00:00")
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600


def _fmt_hours(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.1f}"


def _issue_status_display(issue: dict[str, Any]) -> str:
    status = issue.get("status") or {}
    if isinstance(status, dict):
        return str(status.get("display") or status.get("key") or "")
    return str(status or "")


def _issue_status_key(issue: dict[str, Any]) -> str:
    status = issue.get("status") or {}
    if isinstance(status, dict):
        return str(status.get("key") or "")
    return str(status or "")


def _is_relocation_status(status: str) -> bool:
    low = status.lower()
    return "перемещ" in low or "moving" in low or "relocation" in low


def parse_robot_from_summary(summary: str) -> str | None:
    match = re.search(r"\[([a-zA-Z]?\d+)\]", summary or "")
    return match.group(1) if match else None


def issue_to_dict(issue: dict[str, Any]) -> dict[str, Any]:
    created = str(issue.get("createdAt") or "")
    status = _issue_status_display(issue)
    hours_created = _hours_since(created)
    summary = str(issue.get("summary") or "")
    return {
        "key": str(issue.get("key") or ""),
        "summary": summary,
        "status": status,
        "created": created,
        "hours_created": _fmt_hours(hours_created),
        "in_relocation": "1" if _is_relocation_status(status) else "0",
        "robot": parse_robot_from_summary(summary),
        "status_key": _issue_status_key(issue),
        "resolution": str((issue.get("resolution") or {}) if isinstance(issue.get("resolution"), dict) else issue.get("resolution") or ""),
    }


def is_issue_open_item(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").lower()
    if status in {"closed", "закрыт", "resolved", "решен", "решён"}:
        return False
    resolution = str(item.get("resolution") or "").strip().lower()
    return not resolution or resolution in {"—", "none", "null", "empty"}


def _search(token: str, query: str) -> list[dict[str, Any]]:
    headers = {"Authorization": f"OAuth {token}"}
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                TRACKER_SEARCH_URL,
                headers=headers,
                json={"query": query},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise TrackerError(str(exc)) from exc
    payload = response.json()
    if not isinstance(payload, list):
        raise TrackerError("unexpected tracker response")
    items = [issue_to_dict(item) for item in payload if isinstance(item, dict)]
    return [item for item in items if is_issue_open_item(item)]


def fetch_park_blockers(*, token: str, queue: str, park_tag: str) -> list[dict[str, Any]]:
    query = build_open_blockers_query(queue, park_tag)
    return _search(token, query)


def _robot_search_variants(robot_number: str) -> list[str]:
    text = robot_number.strip()
    if not text:
        return []
    variants = [text]
    if text.isdigit():
        variants.extend([f"a{text}", f"A{text}", f"[{text}]", f"[a{text}]"])
    else:
        match = re.match(r"^([a-zA-Z]+)(\d+)$", text)
        if match:
            digits = match.group(2)
            prefix = match.group(1)
            variants.extend([digits, f"{prefix.lower()}{digits}", f"[{digits}]"])
    seen: set[str] = set()
    ordered: list[str] = []
    for variant in variants:
        if variant not in seen:
            seen.add(variant)
            ordered.append(variant)
    return ordered


def _summary_matches_robot(summary: str, robot_number: str) -> bool:
    summary_lower = (summary or "").lower()
    query = robot_number.strip().lower()
    if not query:
        return False
    if query in summary_lower:
        return True
    digits = re.sub(r"^[a-z]+", "", query)
    if digits and digits in summary_lower:
        return True
    return False


def search_robot_tickets(*, token: str, queue: str, query: str) -> list[dict[str, Any]]:
    key = query.strip().upper()
    if re.fullmatch(r"[A-Z0-9-]+-\d+", key):
        headers = {"Authorization": f"OAuth {token}"}
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(f"{TRACKER_ISSUE_URL}/{key}", headers=headers)
                response.raise_for_status()
                issue = issue_to_dict(response.json())
                return [issue] if is_issue_open_item(issue) else []
        except httpx.HTTPError as exc:
            raise TrackerError(str(exc)) from exc

    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for variant in _robot_search_variants(query):
        search_query = _join_query(
            f"Queue: {queue}",
            "Priority: blocker",
            "Resolution: empty()",
            f"Summary: {_ql_quote(variant)}",
        )
        for item in _search(token, search_query):
            if not _summary_matches_robot(item["summary"], query):
                continue
            if item["key"] in seen:
                continue
            seen.add(item["key"])
            results.append(item)
        if results:
            break
    return results
