"""Pure Telegram HTML renderers for robot task views."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import escape, unescape
from zoneinfo import ZoneInfo


HTML_LIMIT = 3800
DESCRIPTION_LIMIT = 160
TITLE_LIMIT = 180
HISTORY_LIMIT = 10
TRACKER_URL = "https://st.yandex-team.ru/{key}"
MSK = ZoneInfo("Europe/Moscow")

PRIORITY_MARK = {"blocker": "🔴", "critical": "🟠", "normal": "🟡"}
PRIORITY_ORDER = {"blocker": 0, "critical": 1, "normal": 2}

ROBOT_PREFIX_RE = re.compile(r"^\[[aа]?\d{1,6}\]\s*", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\b[\w.-]+\.(?:yandex-team\.ru|yandex\.ru)\S*", re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
WIKI_LINK_RE = re.compile(r"\(\([^)]*\)\)")
CODE_RE = re.compile(r"`([^`]*)`")
ISSUE_KEY_IN_TEXT_RE = re.compile(r"\b[A-Z][A-Z0-9]{2,}-\d+\b")
SAFE_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}-[1-9]\d{0,18}$")
FORM_PLACEHOLDER_RE = re.compile(r"<\[[^\]]*\]\s*>|<\{[\s\S]*?\}\s*>", re.IGNORECASE)
FORM_CURLY_LABEL_RE = re.compile(r"\{[A-Za-zА-Яа-я][A-Za-zА-Яа-я0-9_ ]{0,40}\}")
METADATA_LINE_RE = re.compile(r"^\*\*[^*]+\*\*:?")
SKIP_LINE_RE = re.compile(
    r"(?i)^\*\*(links|release job|manual|ride|monitoring|branch|reporter|time|mode|"
    r"reported mode|rover name|port|partner|zone|classificator|suf|type of issue|"
    r"reason|release ticket|priority|приоритет)\*\*"
)
PRIORITY_LINE_RE = re.compile(r"(?i)^(?:\*\*)?приоритет(?:\*\*)?\s*[-–—]")
HTML_TAG_RE = re.compile(r"<[^>]+>")
WIKI_TABLE_BLOCK_RE = re.compile(r"#\|(.*?)\|#", re.DOTALL)
WIKI_TABLE_ROW_RE = re.compile(r"\|\|(.*?)\|\|", re.DOTALL)
ROBOMAINT_DATETIME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:\s*([+-]\d{4}))?\s*(?:MSK)?\s*$",
    re.IGNORECASE,
)
MOVEMENT_HISTORY_PREFIX_RE = re.compile(r"^Перевозка\s+[aа]\d+\s*", re.IGNORECASE)
MOVEMENT_CITY_SUFFIX_RE = re.compile(r",\s+[A-Za-z][A-Za-z ]+$")
PART_REQUIRED_LINE_RE = re.compile(
    r"(?i)^\s*(?:\*\*)?какая\s+запчасть\s+требуется\s*\??(?:\*\*)?\s*:?\s*(.*)$"
)
HEADER_LABEL_RE = re.compile(
    r"(?i)^\s*(?:\*\*)?(?:модель\s+объекта|имя\s+ровера|номер\s+тикета)"
)
FORM_FIELD_RE = re.compile(
    r"^(?:ROBOT_[A-Z0-9_]+|(?i:фотография|файлы|вложения))(?:\s*:)?\s*(.*)$"
)
EMPTY_FORM_ANSWERS = frozenset({"", "нет ответа", "-", "—", "–"})


def _clip(value: object, limit: int) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _robot_label(robot: object) -> str:
    return f"<b>{escape(_clip(robot, 64))}</b>"


def _task_title(summary: object) -> str:
    title = ROBOT_PREFIX_RE.sub("", str(summary or "").strip()).strip()
    return _clip(title or "—", TITLE_LIMIT)


def _description_lines(description: str) -> list[str]:
    return [line.strip() for line in description.replace("\\n", "\n").split("\n")]


def _strip_form_placeholders(text: str) -> str:
    text = FORM_PLACEHOLDER_RE.sub("", unescape(text))
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
    text = ISSUE_KEY_IN_TEXT_RE.sub("", text)
    text = re.sub(r"\s+(?:на|в|к|для)\s*$", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" .,-")


def _raw_description(issue: dict) -> str:
    value = issue.get("description")
    if isinstance(value, dict):
        value = value.get("text") or value.get("markdown") or ""
    return value if isinstance(value, str) else ""


def _description_snippet(description: str) -> str:
    if not description.strip():
        return "—"
    lines = _description_lines(_strip_form_placeholders(description))
    comment_patterns = (
        re.compile(r"(?i)^\*\*comment:\*\*\s*(.*)$"),
        re.compile(r"(?i)^\*\*comment\**:\s*(.*)$"),
        re.compile(r"(?i)^comment\s*:\s*(.*)$"),
    )
    text = ""
    for line in lines:
        for pattern in comment_patterns:
            match = pattern.match(line)
            if match and (text := _clean_description_text(match.group(1))):
                break
        if text:
            break
    if not text:
        parts: list[str] = []
        for line in lines:
            if (
                not line
                or METADATA_LINE_RE.match(line)
                or SKIP_LINE_RE.match(line)
                or PRIORITY_LINE_RE.match(line)
                or line.startswith("((http")
                or "[http" in line.lower()
                or "](http" in line.lower()
                or (URL_RE.search(line) and not URL_RE.sub("", line).strip())
            ):
                continue
            cleaned = _clean_description_text(line)
            if cleaned and cleaned not in parts:
                parts.append(cleaned)
        text = " ".join(parts) or _clean_description_text(description)
    return _clip(text or "—", DESCRIPTION_LIMIT)


def _priority(issue: dict) -> str:
    key = str((issue.get("priority") or {}).get("key") or "")
    return PRIORITY_MARK.get(key, "⚪")


def _date(issue: dict) -> str:
    raw = issue.get("resolvedAt") or issue.get("updatedAt")
    if not raw:
        return "—"
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).strftime(
            "%d.%m.%Y"
        )
    except ValueError:
        return "—"


def _bounded(header: str, blocks: list[str], total: int, truncated: bool) -> str:
    lines = [header]
    shown = 0
    note_needed = truncated
    note = "<i>список обрезан, полный — в Tracker</i>"
    for block in blocks:
        candidate = "\n\n".join(lines + [block])
        reserve = len("\n\n" + note) if shown + 1 < total or truncated else 0
        if len(candidate) + reserve > HTML_LIMIT:
            note_needed = True
            break
        lines.append(block)
        shown += 1
    if shown < total:
        note_needed = True
    if note_needed:
        while len("\n\n".join(lines + [note])) > HTML_LIMIT and len(lines) > 1:
            lines.pop()
        lines.append(note)
    return "\n\n".join(lines)[:HTML_LIMIT]


def _task_block(issue: dict, *, closed: bool = False) -> str:
    block = (
        f"{_priority(issue)} <b>{escape(_task_title(issue.get('summary')))}</b>\n"
        f"   {escape(_description_snippet(_raw_description(issue)))}"
    )
    if closed:
        block += f"\n   <i>закрыт {escape(_date(issue))}</i>"
    return block


def _open(robot: object, issues: list[dict], truncated: bool) -> str:
    ordered = sorted(
        issues,
        key=lambda issue: (
            PRIORITY_ORDER.get(str((issue.get("priority") or {}).get("key") or ""), 99),
            str(issue.get("statusStartTime") or issue.get("createdAt") or ""),
        ),
    )
    header = f"Робот {_robot_label(robot)}, открытые задачи: {len(issues)}"
    if not issues:
        return header
    return _bounded(
        header, [_task_block(issue) for issue in ordered], len(issues), truncated
    )


def _history(robot: object, issues: list[dict], truncated: bool) -> str:
    ordered = sorted(
        issues,
        key=lambda issue: str(issue.get("resolvedAt") or issue.get("updatedAt") or ""),
        reverse=True,
    )[:HISTORY_LIMIT]
    header = f"Робот {_robot_label(robot)}, история ремонтов: {len(ordered)}"
    if not ordered:
        return header + "\n\nЗакрытых ремонтов не найдено."
    return _bounded(
        header,
        [_task_block(issue, closed=True) for issue in ordered],
        len(ordered),
        truncated or len(issues) > HISTORY_LIMIT,
    )


def _clean_cell(text: str) -> str:
    text = re.sub(r"<br\s*/?>", " · ", unescape(text.strip()), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", HTML_TAG_RE.sub("", text)).strip(" ·")


def _wiki_rows(description: str) -> list[tuple[str, str]]:
    match = WIKI_TABLE_BLOCK_RE.search(description)
    if not match:
        return []
    rows = []
    for raw in WIKI_TABLE_ROW_RE.findall(match.group(1)):
        if "|" not in raw:
            continue
        key, value = (_clean_cell(cell) for cell in raw.strip().split("|", 1))
        if key:
            rows.append((key, value))
    return rows


def _movement_datetime(value: str) -> datetime | None:
    match = ROBOMAINT_DATETIME_RE.match(value.strip())
    if not match:
        return None
    date_s, time_s, tz_s = match.groups()
    try:
        parsed = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S")
        if not tz_s:
            return parsed.replace(tzinfo=MSK)
        sign = 1 if tz_s[0] == "+" else -1
        offset = timedelta(hours=sign * int(tz_s[1:3]), minutes=sign * int(tz_s[3:5]))
        return parsed.replace(tzinfo=timezone(offset))
    except ValueError:
        return None


def _table_value(key: str, value: str) -> str:
    parsed = _movement_datetime(value)
    if parsed is None and "дата" in key.casefold():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=MSK)
        except ValueError:
            pass
    return parsed.astimezone(MSK).strftime("%d.%m.%Y, %H:%M MSK") if parsed else value


def _park_ticket(issue: dict) -> str | None:
    value = issue.get("park_ticket")
    if isinstance(value, dict):
        value = value.get("key")
    key = str(value or "").strip()
    return key if SAFE_ISSUE_KEY_RE.fullmatch(key) else None


def _move_block(issue: dict) -> str:
    rows = _wiki_rows(_raw_description(issue))
    table = (
        "\n".join(
            f"<b>{escape('Желаемая дата и время' if key.casefold() == 'желаемая дата' else _clip(key, 100))}:</b> "
            f"{escape(_clip(_table_value(key, value), 300))}"
            for key, value in rows[:12]
        )
        or "—"
    )
    block = f"🚚 <b>{escape(_task_title(issue.get('summary')))}</b>\n\n{table}"
    park_ticket = _park_ticket(issue)
    if park_ticket:
        block += (
            f'\n\nТикет парка: <a href="{TRACKER_URL.format(key=park_ticket)}">'
            f"{escape(park_ticket)}</a>"
        )
    return block


def _moves(robot: object, issues: list[dict], truncated: bool) -> str:
    header = f"Робот {_robot_label(robot)}, активные задачи транспортировки"
    if not issues:
        return header + "\n\nАктивных перемещений нет."
    return _bounded(
        header, [_move_block(issue) for issue in issues], len(issues), truncated
    )


def _movement_title(issue: dict) -> str:
    title = MOVEMENT_HISTORY_PREFIX_RE.sub(
        "", _task_title(issue.get("summary"))
    ).strip()
    return _clip(MOVEMENT_CITY_SUFFIX_RE.sub("", title).strip() or "—", TITLE_LIMIT)


def _moves_history(robot: object, issues: list[dict], truncated: bool) -> str:
    ordered = sorted(
        issues,
        key=lambda issue: str(issue.get("resolvedAt") or issue.get("updatedAt") or ""),
        reverse=True,
    )[:HISTORY_LIMIT]
    if not ordered:
        return f"Робот {_robot_label(robot)}, история перемещений: 0\n\nЗакрытых перемещений не найдено."
    header = (
        f"Робот {_robot_label(robot)}, история последних перемещений: {len(ordered)}"
    )
    blocks = []
    for issue in ordered:
        delivered = (
            str((issue.get("resolution") or {}).get("key") or "").casefold()
            == "delivered"
        )
        blocks.append(
            f"🚚 <b>{escape(_movement_title(issue))}</b> {'🟢' if delivered else '🟡'}\n"
            f"   <i>закрыт {escape(_date(issue))}</i>"
        )
    return _bounded(
        header, blocks, len(ordered), truncated or len(issues) > HISTORY_LIMIT
    )


def _is_empty_answer(text: str) -> bool:
    return text.strip().casefold() in EMPTY_FORM_ANSWERS


def _is_field_key(text: str) -> bool:
    return bool(re.fullmatch(r"ROBOT_[A-Z0-9_]+", text.strip()))


def _prepared_part_lines(description: str) -> tuple[list[str], bool]:
    lines = _description_lines(description.strip()) if description.strip() else []
    out: list[str] = []
    filled = False
    index = 0
    while index < len(lines):
        match = FORM_FIELD_RE.match(lines[index])
        if not match:
            out.append(lines[index])
            index += 1
            continue
        inline = (match.group(1) or "").strip()
        index += 1
        block = [inline] if inline and not _is_field_key(inline) else []
        while (
            index < len(lines)
            and not FORM_FIELD_RE.match(lines[index])
            and not PART_REQUIRED_LINE_RE.match(lines[index])
        ):
            if not _is_field_key(lines[index]):
                block.append(lines[index])
            index += 1
        kept = [line for line in block if not _is_empty_answer(line)]
        if kept:
            filled = True
            out.extend(kept)
    return out, filled


def _skip_part_headers(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        if HEADER_LABEL_RE.match(lines[index]):
            index += 1
            while index < len(lines) and not lines[index].strip():
                index += 1
            if index < len(lines) and not HEADER_LABEL_RE.match(lines[index]):
                index += 1
        else:
            out.append(lines[index])
            index += 1
    return out


def _dedupe(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        key = line.rstrip(":").strip().casefold()
        if out and key == out[-1].rstrip(":").strip().casefold():
            if out[-1].endswith(":") and not line.endswith(":"):
                out[-1] = line
            continue
        out.append(line)
    return out


def _clean_part_lines(lines: list[str]) -> list[str]:
    return [
        cleaned
        for line in lines
        if not _is_empty_answer(line) and not _is_field_key(line)
        if (cleaned := _clean_description_text(line)) and not _is_empty_answer(cleaned)
    ]


def _part_text(description: str) -> list[str]:
    lines, form_filled = _prepared_part_lines(description)
    marker = next(
        (
            (i, m)
            for i, line in enumerate(lines)
            if (m := PART_REQUIRED_LINE_RE.match(line))
        ),
        None,
    )
    body = lines if marker is None else lines[: marker[0]]
    parts = _clean_part_lines(_skip_part_headers(body))
    if marker is not None:
        inline = (marker[1].group(1) or "").strip()
        if not (form_filled and _is_empty_answer(inline)):
            if inline:
                parts.extend(_clean_part_lines([inline]))
            parts.extend(_clean_part_lines(lines[marker[0] + 1 :]))
    return _dedupe(parts)


def _component(issue: dict) -> str:
    parts = _part_text(_raw_description(issue))
    return parts[0].rstrip(":").strip() if parts else ""


def _part_block(issue: dict) -> str:
    key = str(issue.get("key") or "").strip() or "—"
    parts = _part_text(_raw_description(issue)) or ["—"]
    body = escape("\n".join(_clip(part, 500) for part in parts)).replace("\n", "\n   ")
    title = escape(key)
    if SAFE_ISSUE_KEY_RE.fullmatch(key):
        title = f'<a href="{TRACKER_URL.format(key=key)}">{title}</a>'
    return f"📦 {title}\n   {body}"


def _parts(robot: object, issues: list[dict], truncated: bool) -> str:
    ordered = sorted(
        issues,
        key=lambda issue: (
            0 if _component(issue) else 1,
            _component(issue).casefold(),
            str(issue.get("key") or "").casefold(),
        ),
    )
    header = f"Робот {_robot_label(robot)} · ожидание ЗИП: {len(issues)}"
    if not ordered:
        return header + "\n\nОжидающих поставку задач нет."
    counts: dict[str, int] = {}
    for issue in ordered:
        component = _component(issue) or "—"
        counts[component] = counts.get(component, 0) + 1
    blocks: list[str] = []
    previous = None
    for issue in ordered:
        component = _component(issue) or "—"
        prefix = ""
        if component != previous:
            prefix = (
                f"▸ <b>{escape(_clip(component, 100))}</b> ({counts[component]})\n\n"
            )
            previous = component
        blocks.append(prefix + _part_block(issue))
    return _bounded(header, blocks, len(ordered), truncated)


def format_issues(
    robot: object,
    issues: list[dict],
    *,
    history: bool = False,
    view: str | None = None,
    truncated: bool = False,
) -> str:
    """Render one native bot task view as bounded, valid Telegram HTML."""
    selected = view or ("history" if history else "open")
    renderers = {
        "open": _open,
        "history": _history,
        "moves": _moves,
        "moves_history": _moves_history,
        "parts": _parts,
    }
    if selected not in renderers:
        raise ValueError("unsupported_task_view")
    safe_issues = [issue for issue in issues if isinstance(issue, dict)]
    return renderers[selected](robot, safe_issues, bool(truncated))
