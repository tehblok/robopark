"""ROBOMAINT: перемещения робота для диспетчер-бота."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import escape, unescape
from zoneinfo import ZoneInfo

from dispatcher_tracker import (
    _search_tracker,
    format_no_access_response,
    resolved_at_display,
    robot_label,
    search_tasks_by_rover,
    task_title,
)
from dispatcher_auth import has_global_robot_search, has_robot_access
from tracker_api import get_issue_links

MOVEMENT_DELIVERED_MARK = "🟢"
MOVEMENT_NOT_DELIVERED_MARK = "🟡"
ROBOMAINT_QUEUE = "ROBOMAINT"
OPEN_MOVEMENT_STATUSES = "inProgress, new, needEstimate, needInfo"
MOVEMENT_HISTORY_LIMIT = 10
TRACKER_ISSUE_URL = "https://st.yandex-team.ru/{key}"
EXCLUDED_LINK_QUEUES = frozenset({"ROBOMAINT", "SDCFLEETOPS"})
MSK = ZoneInfo("Europe/Moscow")

WIKI_TABLE_BLOCK_RE = re.compile(r"#\|(.*?)\|#", re.DOTALL)
WIKI_TABLE_ROW_RE = re.compile(r"\|\|(.*?)\|\|", re.DOTALL)
HTML_TAG_RE = re.compile(r"<[^>]+>")
ROBOMAINT_DATETIME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:\s*([+-]\d{4}))?\s*(?:MSK)?\s*$",
    re.I,
)
MOVEMENT_HISTORY_PREFIX_RE = re.compile(
    r"^Перевозка\s+a\d+\s*",
    re.I,
)
MOVEMENT_CITY_SUFFIX_RE = re.compile(r",\s+[A-Za-z][A-Za-z ]+$")


def parse_wiki_table(description: str) -> list[tuple[str, str]]:
    if not description:
        return []
    match = WIKI_TABLE_BLOCK_RE.search(description)
    if not match:
        return []

    rows: list[tuple[str, str]] = []
    for raw in WIKI_TABLE_ROW_RE.findall(match.group(1)):
        raw = raw.strip()
        if not raw or "|" not in raw:
            continue
        key, val = raw.split("|", 1)
        key = _clean_cell(key)
        val = _clean_cell(val)
        if key:
            rows.append((key, val))
    return rows


def _clean_cell(text: str) -> str:
    text = unescape(text.strip())
    text = re.sub(r"<br\s*/?>", " · ", text, flags=re.I)
    text = HTML_TAG_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" ·")
    return text


def _parse_robomaint_datetime(value: str) -> datetime | None:
    match = ROBOMAINT_DATETIME_RE.match(value.strip())
    if not match:
        return None
    date_s, time_s, tz_s = match.groups()
    dt = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S")
    if tz_s:
        sign = 1 if tz_s[0] == "+" else -1
        hours = int(tz_s[1:3])
        mins = int(tz_s[3:5])
        tz = timezone(timedelta(hours=sign * hours, minutes=sign * mins))
        return dt.replace(tzinfo=tz)
    return dt.replace(tzinfo=MSK)


def format_table_label(key: str) -> str:
    if key.casefold() == "желаемая дата":
        return "Желаемая дата и время"
    return key


def format_table_value(key: str, value: str) -> str:
    parsed = _parse_robomaint_datetime(value)
    if parsed is not None:
        return parsed.astimezone(MSK).strftime("%d.%m.%Y, %H:%M MSK")
    if "дата" in key.casefold():
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MSK)
            return dt.astimezone(MSK).strftime("%d.%m.%Y, %H:%M MSK")
        except ValueError:
            pass
    return value


def _issue_queue(issue_key: str) -> str:
    return issue_key.split("-", 1)[0]


def fetch_issue_links(issue_key: str) -> tuple[list[dict], str | None]:
    return get_issue_links(issue_key)


def pick_park_linked_issue(links: list[dict]) -> dict | None:
    candidates: list[tuple[int, str, str]] = []
    for link in links:
        obj = link.get("object") or {}
        key = obj.get("key") or ""
        if not key:
            continue
        if _issue_queue(key) in EXCLUDED_LINK_QUEUES:
            continue

        link_type = (link.get("type") or {}).get("id", "")
        direction = link.get("direction", "")
        if link_type == "subtask" and direction == "outward":
            score = 2
        elif link_type == "subtask":
            score = 1
        else:
            score = 0
        candidates.append((score, key, obj.get("display") or key))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    _, key, display = candidates[0]
    return {"key": key, "display": display}


def format_park_link_line(issue_key: str) -> str | None:
    if not issue_key:
        return None
    links, _err = fetch_issue_links(issue_key)
    picked = pick_park_linked_issue(links)
    if not picked:
        return None
    key = picked["key"]
    return (
        f'Тикет парка: <a href="{TRACKER_ISSUE_URL.format(key=key)}">'
        f"{escape(key)}</a>"
    )


def search_open_movements_by_rover(rover: str) -> tuple[list[dict], str | None]:
    query = (
        f'Queue: {ROBOMAINT_QUEUE} AND rover: "{rover}" '
        f"AND Status: {OPEN_MOVEMENT_STATUSES}"
    )
    return _search_tracker(query, order="-updated")


def search_closed_movements_by_rover(
    rover: str,
    limit: int = MOVEMENT_HISTORY_LIMIT,
) -> tuple[list[dict], str | None]:
    query = f'Queue: {ROBOMAINT_QUEUE} AND rover: "{rover}" AND Resolution: !empty()'
    issues, err = _search_tracker(query, order="-updated", max_pages=1)
    if err:
        return [], err
    issues.sort(
        key=lambda issue: issue.get("resolvedAt") or issue.get("updatedAt") or "",
        reverse=True,
    )
    return issues[:limit], None


def has_movement_access(user_id: int, profile: dict, rover: str, tasks: list[dict]) -> bool:
    if has_robot_access(user_id, profile, tasks):
        return True
    if has_global_robot_search(user_id, profile):
        return True
    open_tasks, err = search_tasks_by_rover(rover)
    if err:
        return False
    return has_robot_access(user_id, profile, open_tasks)


def format_movement_table_block(issue: dict) -> str:
    rows = parse_wiki_table(issue.get("description") or "")
    if not rows:
        return "—"
    return "\n".join(
        f"<b>{escape(format_table_label(key))}:</b> {escape(format_table_value(key, val))}"
        for key, val in rows
    )


def format_active_movement_block(issue: dict, rover: str) -> str:
    title = escape((issue.get("summary") or "").strip() or "—")
    lines = [f"🚚 <b>{title}</b>", "", format_movement_table_block(issue)]
    park_line = format_park_link_line(issue.get("key") or "")
    if park_line:
        lines.extend(["", park_line])
    return "\n".join(lines)


def format_active_movements_response(rover: str, issues: list[dict]) -> str:
    header = f"Робот {robot_label(rover)}, активные задачи транспортировки"
    if not issues:
        return f"{header}\n\nАктивных перемещений нет."

    lines = [header, ""]
    for i, issue in enumerate(issues):
        lines.append(format_active_movement_block(issue, rover))
        if i < len(issues) - 1:
            lines.append("")
    return "\n".join(lines)


def movement_history_title(summary: str, rover: str) -> str:
    text = task_title(summary, rover)
    text = MOVEMENT_HISTORY_PREFIX_RE.sub("", text).strip()
    text = MOVEMENT_CITY_SUFFIX_RE.sub("", text).strip()
    return text or "—"


def movement_delivery_mark(issue: dict) -> str:
    resolution = ((issue.get("resolution") or {}).get("key") or "").casefold()
    if resolution == "delivered":
        return MOVEMENT_DELIVERED_MARK
    return MOVEMENT_NOT_DELIVERED_MARK


def format_movement_history_title_block(issue: dict, rover: str) -> str:
    title = escape(movement_history_title(issue.get("summary") or "", rover))
    closed = resolved_at_display(issue)
    mark = movement_delivery_mark(issue)
    return f"🚚 <b>{title}</b> {mark}\n   <i>закрыт {escape(closed)}</i>"


def format_movement_history_response(rover: str, issues: list[dict]) -> str:
    n = len(issues)
    if n == 0:
        return (
            f"Робот {robot_label(rover)}, история перемещений: 0\n\n"
            "Закрытых перемещений не найдено."
        )

    lines = [f"Робот {robot_label(rover)}, история последних перемещений: {n}", ""]
    for i, issue in enumerate(issues):
        lines.append(format_movement_history_title_block(issue, rover))
        if i < n - 1:
            lines.append("")
    return "\n".join(lines)


def robot_actions_keyboard(
    rover: str,
    *,
    show_zip: bool = False,
    show_move: bool = True,
    show_hist: bool = True,
    show_qr: bool = True,
) -> dict:
    rows: list[list[dict]] = []
    if show_move:
        rows.append([{"text": "🚚 Перемещение", "callback_data": f"move:{rover}"}])
    if show_hist:
        rows.append([{"text": "📜 История ремонтов", "callback_data": f"hist:{rover}"}])
    if show_qr:
        rows.append([{"text": "📱 QR-код", "callback_data": f"qr:{rover}"}])
    if show_zip:
        rows.append([{"text": "📦 ЗИП", "callback_data": f"zip:{rover}"}])
    return {"inline_keyboard": rows}


def robot_view_keyboard(
    rover: str,
    *,
    show_zip: bool = False,
    show_move: bool = True,
    show_hist: bool = True,
    show_qr: bool = True,
    show_back: bool = False,
    extra_rows: list[list[dict]] | None = None,
) -> dict:
    rows: list[list[dict]] = []
    if show_back:
        rows.append([{"text": "← К задачам", "callback_data": f"tasks:{rover}"}])
    if extra_rows:
        rows.extend(extra_rows)
    rows.extend(
        robot_actions_keyboard(
            rover,
            show_zip=show_zip,
            show_move=show_move,
            show_hist=show_hist,
            show_qr=show_qr,
        )["inline_keyboard"]
    )
    return {"inline_keyboard": rows}


def movement_history_keyboard(rover: str) -> dict:
    return {
        "inline_keyboard": [[{
            "text": "📜 История перемещений",
            "callback_data": f"mhist:{rover}",
        }]],
    }
