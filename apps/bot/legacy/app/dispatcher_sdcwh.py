"""SDCWH: ожидание поставки ЗИП по роботу для диспетчер-бота."""

from __future__ import annotations

import re
from html import escape

from dispatcher_tracker import (
    _clean_description_text,
    _description_lines,
    _search_tracker,
    robot_label,
)

SDCWH_QUEUE = "SDCWH"
DELIVERY_WAITING_STATUS = "delieveryWaiting"
TRACKER_ISSUE_URL = "https://st.yandex-team.ru/{key}"
ZIP_MSG_MAX_LEN = 3800

PART_REQUIRED_LINE_RE = re.compile(
    r"(?i)^\s*(?:\*\*)?какая\s+запчасть\s+требуется\s*\??(?:\*\*)?\s*:?\s*(.*)$"
)
HEADER_LABEL_RE = re.compile(
    r"(?i)^\s*(?:\*\*)?(?:модель\s+объекта|имя\s+ровера|номер\s+тикета)"
)
# ROBOT_* только в верхнем регистре: иначе robot_r3 из шапки съедает анкету.
FORM_FIELD_RE = re.compile(
    r"^(?:ROBOT_[A-Z0-9_]+|(?i:фотография|файлы|вложения))(?:\s*:)?\s*(.*)$"
)
EMPTY_FORM_ANSWERS = frozenset({"", "нет ответа", "-", "—", "–"})


def _is_empty_form_answer(text: str) -> bool:
    return text.strip().casefold() in EMPTY_FORM_ANSWERS


def _is_redundant_field_key(text: str) -> bool:
    """Повтор имени поля внутри блока: ROBOT_BOARDS_MOTORCONTROL."""
    return bool(re.fullmatch(r"ROBOT_[A-Z0-9_]+", text.strip()))


def _strip_empty_form_fields(lines: list[str]) -> tuple[list[str], bool]:
    """Убрать пустые поля анкеты. Ключ поля не показывать. Второй результат — было ли заполненное поле."""
    out: list[str] = []
    filled = False
    i = 0
    while i < len(lines):
        match = FORM_FIELD_RE.match(lines[i])
        if not match:
            out.append(lines[i])
            i += 1
            continue
        inline = (match.group(1) or "").strip()
        i += 1
        block: list[str] = []
        if inline and not _is_redundant_field_key(inline):
            block.append(inline)
        while i < len(lines):
            nxt = lines[i]
            if FORM_FIELD_RE.match(nxt) or PART_REQUIRED_LINE_RE.match(nxt):
                break
            if not _is_redundant_field_key(nxt):
                block.append(nxt)
            i += 1
        kept = [ln for ln in block if not _is_empty_form_answer(ln)]
        if kept:
            filled = True
            out.extend(kept)
    return out, filled


def _prepared_lines(description: str | None) -> tuple[list[str], bool]:
    raw = (description or "").strip()
    if not raw:
        return [], False
    return _strip_empty_form_fields(_description_lines(raw))


def _part_required_index(lines: list[str]) -> tuple[int | None, str]:
    for i, line in enumerate(lines):
        match = PART_REQUIRED_LINE_RE.match(line)
        if match:
            return i, (match.group(1) or "").strip()
    return None, ""


def _skip_header_blocks(lines: list[str]) -> list[str]:
    """Убрать шапку формы (модель, робот, номер fleetops-тикета)."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if HEADER_LABEL_RE.match(line):
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines) and not HEADER_LABEL_RE.match(lines[i]):
                i += 1
            continue
        out.append(line)
        i += 1
    return out


def _line_dedupe_key(line: str) -> str:
    return line.rstrip(":").strip().casefold()


def _dedupe_consecutive_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        key = _line_dedupe_key(line)
        if out and key == _line_dedupe_key(out[-1]):
            if out[-1].rstrip().endswith(":") and not line.rstrip().endswith(":"):
                out[-1] = line
            continue
        out.append(line)
    return out


def _collect_clean_lines(raw_lines: list[str]) -> list[str]:
    parts: list[str] = []
    for line in raw_lines:
        if _is_empty_form_answer(line) or _is_redundant_field_key(line):
            continue
        cleaned = _clean_description_text(line)
        if cleaned and not _is_empty_form_answer(cleaned):
            parts.append(cleaned)
    return parts


def _body_line_parts(description: str | None) -> list[str]:
    lines, _filled = _prepared_lines(description)
    if not lines:
        return []

    marker_idx, _ = _part_required_index(lines)
    body_lines = lines if marker_idx is None else lines[:marker_idx]
    return _dedupe_consecutive_lines(
        _collect_clean_lines(_skip_header_blocks(body_lines))
    )


def _tail_line_parts(description: str | None) -> list[str]:
    lines, form_filled = _prepared_lines(description)
    if not lines:
        return []

    start_idx, inline_tail = _part_required_index(lines)
    if start_idx is None:
        return []

    # Новая анкета: запчасть уже в ROBOT_*, хвост после пустого вопроса — складской мусор.
    if form_filled and _is_empty_form_answer(inline_tail):
        return []

    parts: list[str] = []
    if inline_tail:
        cleaned = _clean_description_text(inline_tail)
        if cleaned:
            parts.append(cleaned)

    parts.extend(_collect_clean_lines(lines[start_idx + 1 :]))
    return _dedupe_consecutive_lines(parts)


def extract_component_name(description: str | None) -> str:
    """Имя компонента для сортировки — первая строка тела запроса."""
    parts = _body_line_parts(description)
    if parts:
        return parts[0].rstrip(":").strip()
    return ""


def zip_issue_sort_key(issue: dict) -> tuple[int, str, str]:
    component = extract_component_name(issue.get("description")).casefold()
    key = ((issue.get("key") or "")).casefold()
    return (0 if component else 1, component, key)


def sort_zip_issues(issues: list[dict]) -> list[dict]:
    return sorted(issues, key=zip_issue_sort_key)


def extract_request_body(description: str | None) -> str:
    parts = _body_line_parts(description)
    if not parts:
        return "—"
    return "\n".join(parts)


def extract_part_required_text(description: str | None) -> str:
    parts = _tail_line_parts(description)
    if not parts:
        return "—"
    return "\n".join(parts)


def extract_zip_display_text(
    description: str | None,
    *,
    skip_component: str | None = None,
) -> str:
    """Тело запроса + текст ниже «Какая запчасть требуется?»."""
    parts = _dedupe_consecutive_lines(
        _body_line_parts(description) + _tail_line_parts(description)
    )
    if skip_component and parts and _line_dedupe_key(parts[0]) == _line_dedupe_key(skip_component):
        parts = parts[1:]
    if not parts:
        return "—"
    return "\n".join(parts)


def format_zip_issue_block(issue: dict, *, skip_component: str | None = None) -> str:
    key = (issue.get("key") or "").strip() or "—"
    part_text = extract_zip_display_text(
        issue.get("description"),
        skip_component=skip_component,
    )
    url = TRACKER_ISSUE_URL.format(key=key) if key != "—" else ""
    if url:
        title = f'<a href="{escape(url)}">{escape(key)}</a>'
    else:
        title = escape(key)
    body = escape(part_text).replace("\n", "\n   ")
    return f"📦 {title}\n   {body}"


def format_zip_response(rover: str, issues: list[dict]) -> str:
    n = len(issues)
    header = f"Робот {robot_label(rover)} · ожидание ЗИП: {n}"
    if n == 0:
        return f"{header}\n\nОжидающих поставку задач нет."

    lines = [header, ""]
    comp_counts: dict[str, int] = {}
    for issue in issues:
        comp = extract_component_name(issue.get("description")) or "—"
        comp_counts[comp] = comp_counts.get(comp, 0) + 1

    prev_component: str | None = None
    for i, issue in enumerate(issues):
        comp = extract_component_name(issue.get("description")) or "—"
        if comp != prev_component:
            if prev_component is not None:
                lines.append("")
            lines.append(f"▸ <b>{escape(comp)}</b> ({comp_counts[comp]})")
            lines.append("")
            prev_component = comp
        lines.append(format_zip_issue_block(issue))
        if i < n - 1:
            lines.append("")

    text = "\n".join(lines)
    while text.endswith("\n\n"):
        text = text[:-1]
    if len(text) > ZIP_MSG_MAX_LEN:
        text = text[: ZIP_MSG_MAX_LEN - 40].rstrip() + "\n\n… список обрезан, полный — в Tracker"
    return text


def search_delivery_waiting_by_rover(rover: str) -> tuple[list[dict], str | None]:
    query = (
        f'Queue: {SDCWH_QUEUE} AND rover: "{rover}" '
        f"AND Status: {DELIVERY_WAITING_STATUS} "
        f"AND Resolution: empty()"
    )
    issues, err = _search_tracker(query, order="+created")
    if err:
        return [], err
    return sort_zip_issues(issues), None
