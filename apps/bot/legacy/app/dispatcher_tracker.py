"""Поиск задач по роботу в Tracker и форматирование ответа."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html import escape, unescape

from config import QUEUE
from dispatcher_overrides import description_override, should_show_task, title_override
from tracker_api import search_issues

STATUS_RU = {
    "closed": "Закрыт",
    "delieveryWaiting": "Ожидание поставки",
    "diagnostics": "В очереди",
    "inProgress": "В очереди",
    "moving": "Перемещение",
    "new": "Перемещение",
    "pause": "В очереди",
    "queued": "В очереди",
    "waitingForAnotherTeam": "Ждем смежников",
    "waitingForInspection": "В очереди",
    "treated": "В очереди",
}

def _service_tags() -> set[str]:
    try:
        from store.locations import service_tags

        return service_tags()
    except Exception:
        return {
            "Next",
            "РОКАЛАБ",
            "Юг",
            "АРГАТЕХНН",
            "АРГАТЕХКАЗ",
            "МскСевер",
            "КалиевАстана",
            "КалиевАлматы",
            "Сигма",
            "Континент",
            "АрмаМСК",
        }


SERVICE_TAGS = _service_tags()

ROBOT_RE = re.compile(r"^(?:/robot\s+)?(?:a)?(\d{1,6})$", re.IGNORECASE)

PRIORITY_MARK = {
    "blocker": "🔴",
    "critical": "🟠",
    "normal": "🟡",
}

DESCRIPTION_MAX_LEN = 160

PRIORITY_ORDER = {
    "blocker": 0,
    "critical": 1,
    "normal": 2,
}

REPAIR_TYPE_KEYS = frozenset({"repair", "service", "calibration"})
REPAIR_HISTORY_RESOLUTION = "fixed"
HISTORY_LIMIT = 10

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\b[\w.-]+\.(?:yandex-team\.ru|yandex\.ru)\S*", re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
WIKI_LINK_RE = re.compile(r"\(\([^)]*\)\)")
CODE_RE = re.compile(r"`([^`]*)`")
ISSUE_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]{2,}-\d+\b")
# Секции формы Tracker (могут быть многострочными): <[…]>, <{Файлы\n\n}>
FORM_PLACEHOLDER_RE = re.compile(
    r"<\[[^\]]*\]\s*>|<\{[\s\S]*?\}\s*>",
    re.IGNORECASE,
)
FORM_CURLY_LABEL_RE = re.compile(
    r"\{[А-Яа-яA-Za-z][А-Яа-яA-Za-z0-9_ ]{0,40}\}",
)
METADATA_LINE_RE = re.compile(r"^\*\*[^*]+\*\*:?", re.IGNORECASE)
SKIP_LINE_RE = re.compile(
    r"(?i)^\*\*(links|release job|manual|ride|monitoring|branch|reporter|time|mode|"
    r"reported mode|rover name|port|partner|zone|classificator|suf|type of issue|"
    r"reason|release ticket|priority|приоритет)\*\*"
)
PRIORITY_LINE_RE = re.compile(r"(?i)^(?:\*\*)?приоритет(?:\*\*)?\s*[-–—]")


def parse_robot_number(text: str) -> str | None:
    t = text.strip()
    if not t or t.startswith("/"):
        return None
    m = ROBOT_RE.match(t)
    if not m:
        return None
    return f"a{m.group(1).lower()}"


def _search_tracker(
    query: str,
    *,
    order: str = "+created",
    max_pages: int | None = None,
) -> tuple[list[dict], str | None]:
    return search_issues(query, order=order, max_pages=max_pages)


def search_tasks_by_rover(rover: str) -> tuple[list[dict], str | None]:
    """Open repair-family tickets only (same Type filter as PNG / история ремонтов)."""
    query = (
        f'Queue: {QUEUE} AND rover: "{rover}" '
        f"AND Resolution: empty() "
        f"AND Type: repair, service, calibration"
    )
    issues, err = _search_tracker(query, order="+created", max_pages=2)
    if err:
        return [], err
    # Client-side belt: API can return non-repair types / closed-without-resolution.
    open_repairs = [
        issue
        for issue in issues
        if is_repair_type(issue)
        and (issue.get("status") or {}).get("key") != "closed"
    ]
    return open_repairs, None


def issue_type_key(issue: dict) -> str:
    return (issue.get("type") or {}).get("key", "")


def is_repair_type(issue: dict) -> bool:
    return issue_type_key(issue) in REPAIR_TYPE_KEYS


def is_repair_history_resolution(issue: dict) -> bool:
    resolution = (issue.get("resolution") or {}).get("key") or ""
    return resolution == REPAIR_HISTORY_RESOLUTION


def search_closed_repairs_by_rover(
    rover: str,
    limit: int = HISTORY_LIMIT,
) -> tuple[list[dict], str | None]:
    query = (
        f'Queue: {QUEUE} AND rover: "{rover}" '
        f"AND Resolution: {REPAIR_HISTORY_RESOLUTION} "
        f"AND Type: repair, service, calibration"
    )
    issues, err = _search_tracker(query, order="-updated", max_pages=1)
    if err:
        return [], err

    repairs = [
        issue for issue in issues
        if is_repair_type(issue) and is_repair_history_resolution(issue)
    ]
    repairs.sort(
        key=lambda issue: issue.get("resolvedAt") or issue.get("updatedAt") or "",
        reverse=True,
    )
    return repairs[:limit], None


def status_display(issue: dict) -> str:
    status = issue.get("status") or {}
    key = status.get("key", "")
    return STATUS_RU.get(key, status.get("display") or key or "—")


def location_tags(issue: dict) -> str:
    tags = issue.get("tags") or []
    found = [t for t in tags if t in _service_tags()]
    if found:
        return ", ".join(found)
    other = [t for t in tags if t not in {"SUF", "order_no", "waiting_complete", "Донор"}]
    return ", ".join(other[:3]) if other else "—"


def duration_in_status(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        started = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return "—"
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - started
    total_min = int(delta.total_seconds() // 60)
    if total_min < 0:
        return "—"
    hours, minutes = divmod(total_min, 60)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


def priority_display(issue: dict) -> str:
    priority = issue.get("priority") or {}
    key = priority.get("key", "")
    return PRIORITY_MARK.get(key, "⚪")


def task_title(summary: str, rover: str) -> str:
    text = (summary or "").strip()
    prefix = f"[{rover}]"
    if text.lower().startswith(prefix.lower()):
        text = text[len(prefix) :].strip()
    return text or "—"


def _strip_form_placeholders(text: str) -> str:
    text = unescape(text)
    text = FORM_PLACEHOLDER_RE.sub("", text)
    return FORM_CURLY_LABEL_RE.sub("", text)


def _clean_description_text(text: str) -> str:
    text = _strip_form_placeholders(text)
    text = WIKI_LINK_RE.sub("", text)
    text = MARKDOWN_LINK_RE.sub("", text)
    text = URL_RE.sub("", text)
    text = DOMAIN_RE.sub("", text)
    text = CODE_RE.sub(r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"%%([^%]+)%%", r"\1", text)
    text = ISSUE_KEY_RE.sub("", text)
    text = re.sub(r"\s+(?:на|в|к|для)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .,-")
    return text


def _is_link_or_metadata_line(line: str) -> bool:
    if not line.strip():
        return True
    if METADATA_LINE_RE.match(line) or SKIP_LINE_RE.match(line):
        return True
    if PRIORITY_LINE_RE.match(line):
        return True
    if line.startswith("((http"):
        return True
    if "[http" in line.lower() or "](http" in line.lower():
        return True
    if URL_RE.search(line) and not re.sub(URL_RE, "", line).strip():
        return True
    return False


def _description_lines(description: str) -> list[str]:
    return [line.strip() for line in description.replace("\\n", "\n").split("\n")]


def _extract_comment(lines: list[str]) -> str | None:
    patterns = (
        re.compile(r"(?i)^\*\*comment:\*\*\s*(.*)$"),
        re.compile(r"(?i)^\*\*comment\*\*:\s*(.*)$"),
        re.compile(r"(?i)^comment\s*:\s*(.*)$"),
    )
    for line in lines:
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                comment = _clean_description_text(match.group(1))
                if comment:
                    return comment
    return None


def _extract_prose(lines: list[str]) -> str:
    parts: list[str] = []
    for line in lines:
        if _is_link_or_metadata_line(line):
            continue
        cleaned = _clean_description_text(line)
        if not cleaned:
            continue
        if cleaned in parts:
            continue
        parts.append(cleaned)
    return " ".join(parts)


def description_snippet(description: str | None) -> str:
    text = (description or "").strip()
    if not text:
        return "—"

    text = _strip_form_placeholders(text)
    lines = _description_lines(text)
    text = _extract_comment(lines) or _extract_prose(lines)
    if not text:
        text = _clean_description_text(description)

    if not text:
        return "—"
    if len(text) > DESCRIPTION_MAX_LEN:
        return text[: DESCRIPTION_MAX_LEN - 1] + "…"
    return text


def short_summary(summary: str, rover: str) -> str:
    return task_title(summary, rover)


def filter_tasks_for_display(tasks: list[dict], rover: str) -> list[dict]:
    visible: list[dict] = []
    for task in tasks:
        title = task_title(task.get("summary") or "", rover)
        if should_show_task(task, title):
            visible.append(task)
    return visible


def format_task_block(issue: dict, rover: str) -> str:
    title = task_title(issue.get("summary") or "", rover)
    display_title = title_override(title) or title
    override = description_override(title)
    if override:
        snippet = _clean_description_text(override) or "—"
    else:
        raw_desc = issue.get("description")
        if isinstance(raw_desc, dict):
            raw_desc = raw_desc.get("text") or raw_desc.get("markdown") or ""
        snippet = description_snippet(raw_desc if isinstance(raw_desc, str) else None)
    priority = priority_display(issue)
    return (
        f"{priority} <b>{escape(display_title)}</b>\n"
        f"   {escape(snippet)}"
    )


def resolved_at_display(issue: dict) -> str:
    raw = issue.get("resolvedAt") or issue.get("updatedAt")
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return "—"
    return dt.strftime("%d.%m.%Y")


def format_closed_task_block(issue: dict, rover: str) -> str:
    closed = resolved_at_display(issue)
    return f"{format_task_block(issue, rover)}\n   <i>закрыт {escape(closed)}</i>"


def repair_history_keyboard(rover: str) -> dict:
    return {
        "inline_keyboard": [[{
            "text": "📜 История ремонтов",
            "callback_data": f"hist:{rover}",
        }]],
    }


def robot_label(rover: str) -> str:
    return f"<b>{escape(rover)}</b>"


def robot_header(rover: str, count: int) -> str:
    return f"Робот {robot_label(rover)}, открытые задачи: {count}"


def task_sort_key(issue: dict) -> tuple[int, str]:
    priority_key = (issue.get("priority") or {}).get("key", "")
    rank = PRIORITY_ORDER.get(priority_key, 99)
    started = issue.get("statusStartTime") or issue.get("createdAt") or ""
    return rank, started


TELEGRAM_HTML_MAX = 3800


def format_robot_response(rover: str, tasks: list[dict]) -> str:
    n = len(tasks)
    if n == 0:
        return robot_header(rover, 0)

    lines = [robot_header(rover, n), ""]
    sorted_tasks = sorted(tasks, key=task_sort_key)
    shown = 0
    for task in sorted_tasks:
        block = format_task_block(task, rover)
        extra = ("\n\n" if shown else "") + block
        if shown and len("\n".join(lines)) + len(extra) > TELEGRAM_HTML_MAX - 80:
            lines.append("")
            lines.append(f"<i>ещё {n - shown} в Tracker — список обрезан</i>")
            break
        if shown:
            lines.append("")
        lines.append(block)
        shown += 1
    return "\n".join(lines)


def format_no_access_response(rover: str, allowed_label: str) -> str:
    del allowed_label
    return (
        f"Робот {robot_label(rover)}\n"
        "По вашему участку на этом роботе нет открытых задач.\n\n"
        "За дополнительной информацией обратитесь в сервисный чат."
    )


def format_repair_history_response(rover: str, tasks: list[dict]) -> str:
    n = len(tasks)
    if n == 0:
        return (
            f"Робот {robot_label(rover)}, история ремонтов: 0\n\n"
            "Закрытых ремонтов не найдено."
        )

    lines = [f"Робот {robot_label(rover)}, история ремонтов: {n}", ""]
    for i, task in enumerate(tasks):
        lines.append(format_closed_task_block(task, rover))
        if i < n - 1:
            lines.append("")
    return "\n".join(lines)
