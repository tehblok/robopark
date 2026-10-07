"""Legacy-compatible in-memory PNG reports and watchdog text."""

from __future__ import annotations

import html

STATUS_LABELS = {
    "closed": "Закрыт",
    "delieveryWaiting": "Ожидание поставки",
    "deliveryWaiting": "Ожидание поставки",
    "diagnostics": "В очереди",
    "inProgress": "В работе",
    "moving": "Перемещение",
    "new": "Перемещение",
    "pause": "Пауза",
    "queued": "В очереди",
    "waitingForAnotherTeam": "Ждем смежников",
    "waitingForInspection": "В очереди",
    "treated": "В очереди",
    "check": "В очереди",
    "open": "В очереди",
}
STATUS_ORDER = [
    "Ожидание поставки",
    "Ждем смежников",
    "Перемещение",
    "В работе",
    "Пауза",
    "В очереди",
]
STATUS_RANK = {name: index for index, name in enumerate(STATUS_ORDER)}

MAX_RENDER_ROWS = 40
MAX_WATCHDOG_PARTS = 12
EMPTY_WATCHDOG_TEXT = "Задачи с превышением времени в очереди — отсутствуют"


def ref_key(issue, field):
    value = issue.get(field)
    return str(value.get("key") or "") if isinstance(value, dict) else str(value or "")


def status_label(issue):
    key = ref_key(issue, "status")
    value = issue.get("status")
    return STATUS_LABELS.get(
        key, str(value.get("display") or key) if isinstance(value, dict) else key
    )


def _number(value):
    return float(value) if isinstance(value, (int, float)) else None


def _repair_category(hours):
    if hours is None:
        return "unknown"
    if hours < 3:
        return "green"
    if hours <= 5:
        return "yellow"
    return "red"


def _downtime_category(hours):
    if hours is None:
        return "unknown"
    if hours < 24:
        return "green"
    if hours < 48:
        return "yellow"
    if hours < 72:
        return "orange"
    return "red"


def _task_text(issue):
    text = (
        str(issue.get("summary") or issue.get("key") or "—").replace("\n", " ").strip()
    )
    return text if len(text) <= 42 else text[:41] + "…"


def report_rows(issues):
    rows = []
    for issue in issues:
        timing = issue.get("bot_report") or {}
        repair = _number(timing.get("repair_hours", timing.get("sla_hours")))
        downtime = _number(timing.get("downtime_hours"))
        log_dump = bool(timing.get("log_dump"))
        label = status_label(issue) or "—"
        rows.append(
            {
                "issue": issue,
                "task": _task_text(issue),
                "status": label,
                "repair_hours": repair,
                "repair_text": "Слив логов"
                if log_dump
                else f"{int(repair)} ч"
                if repair is not None
                else "—",
                "repair_category": "log_dump" if log_dump else _repair_category(repair),
                "downtime_hours": downtime,
                "downtime_text": f"{round(downtime)} ч"
                if downtime is not None
                else "—",
                "downtime_category": _downtime_category(downtime),
                "log_dump": log_dump,
            }
        )
    rows.sort(
        key=lambda row: (
            STATUS_RANK.get(row["status"], len(STATUS_ORDER)),
            not row["log_dump"],
            -(row["downtime_hours"] if row["downtime_hours"] is not None else -1),
        )
    )
    return rows


def report_page_count(issues, *, page_size=MAX_RENDER_ROWS):
    if not 1 <= page_size <= MAX_RENDER_ROWS:
        raise ValueError("report_page_size_invalid")
    return max(1, (len(issues) + page_size - 1) // page_size)


def render_report(
    job,
    park,
    issues,
    *,
    truncated=False,
    report_summary=None,
    page=0,
    page_size=MAX_RENDER_ROWS,
):
    del job, report_summary
    import pandas as pd

    from native.report_table import DISPLAY_COLS, render_location_png

    ordered = report_rows(issues)
    pages = report_page_count(ordered, page_size=page_size)
    if not 0 <= page < pages:
        raise ValueError("report_page_invalid")
    rows = ordered[page * page_size : (page + 1) * page_size]
    table = pd.DataFrame(
        [
            [row["task"], row["status"], row["repair_text"], row["downtime_text"]]
            for row in rows
        ],
        columns=DISPLAY_COLS,
    )
    if not rows:
        table = pd.DataFrame(
            [["Активные задачи отсутствуют", "—", "—", "—"]], columns=DISPLAY_COLS
        )
    note = f"Страница {page + 1} из {pages}" if pages > 1 else ""
    if truncated:
        note += (
            " · " if note else ""
        ) + "Выборка ограничена; полный список — в Robopark"
    return render_location_png(
        str(park["name"]),
        table,
        [row["repair_hours"] for row in rows] or [None],
        total_count=len(issues),
        note=note,
    )


def _watchdog_line(icon, row):
    title = html.escape(str(row["task"])[:55], quote=False)
    hours = row["repair_hours"]
    return f"{icon} {title}" if hours is None else f"{icon} {title} · {int(hours)}ч"


def _section_pages(title, rows, icon, footer):
    pages = []
    lines = []
    for row in rows:
        line = _watchdog_line(icon, row)
        heading = (
            f"<b>{title}: {len(rows)}</b>"
            if not pages
            else f"<b>{title} (продолжение)</b>"
        )
        candidate = f"{heading}\n\n" + "\n".join([*lines, line]) + f"\n\n{footer}"
        if lines and len(candidate) > 3900:
            pages.append(f"{heading}\n\n" + "\n".join(lines) + f"\n\n{footer}")
            lines = [line]
        else:
            lines.append(line)
    if lines:
        heading = (
            f"<b>{title}: {len(rows)}</b>"
            if not pages
            else f"<b>{title} (продолжение)</b>"
        )
        pages.append(f"{heading}\n\n" + "\n".join(lines) + f"\n\n{footer}")
    return pages


def watchdog_parts(issues):
    """Return bounded legacy watchdog sections; sending remains caller-owned."""
    if not issues:
        return []
    queued = [row for row in report_rows(issues[:500]) if row["status"] == "В очереди"]
    log_dump = [row for row in queued if row["log_dump"]]
    measured = [
        row for row in queued if not row["log_dump"] and row["repair_hours"] is not None
    ]
    unknown = [
        row for row in queued if not row["log_dump"] and row["repair_hours"] is None
    ]
    red = sorted(
        [row for row in measured if row["repair_hours"] > 5],
        key=lambda row: row["repair_hours"],
        reverse=True,
    )
    yellow = sorted(
        [row for row in measured if 3 <= row["repair_hours"] <= 5],
        key=lambda row: row["repair_hours"],
        reverse=True,
    )
    green = sorted(
        [row for row in measured if row["repair_hours"] < 3],
        key=lambda row: row["repair_hours"],
        reverse=True,
    )
    if not queued:
        return [EMPTY_WATCHDOG_TEXT]
    parts = []
    definitions = [
        (
            "Задача по сливу логов",
            log_dump,
            "🔌",
            "Необходимо поставить робота на шнурок и проконтролировать, когда слив логов закончится",
        ),
        (
            "Превышено время в очереди",
            red,
            "🔴",
            "Необходимо взять в работу или сообщить причину задержки",
        ),
        (
            "Время отведенное на ремонт истекает",
            yellow,
            "⚠️",
            "Приоритетная задача, необходимо взять в работу или сообщить о результатах диагностики",
        ),
        (
            "Новые задачи",
            green,
            "🟢",
            "Необходимо взять в работу или сообщить о результатах диагностики",
        ),
    ]
    for title, rows, icon, footer in definitions:
        if not rows:
            continue
        parts.extend(_section_pages(title, rows, icon, footer))
    if unknown:
        parts.append(
            f"Время в очереди не рассчитано: {len(unknown)}. "
            "Нет подтверждённой истории статусов."
        )
    if len(parts) > MAX_WATCHDOG_PARTS:
        parts = [
            *parts[: MAX_WATCHDOG_PARTS - 1],
            (
                "<b>Список ограничен</b>\n\n"
                "Часть задач не показана из-за лимита сообщений. "
                "Полный список сохранён в PNG и Robopark."
            ),
        ]
    return parts
