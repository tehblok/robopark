"""Internal Yandex Startrek client (st-api.yandex-team.ru).

Uses vendored ``startrek_client`` the same way as bot_otchet:
``per_page=API_PAGE_SIZE`` (50) — клиент сам ходит по Link next;
``count_only`` с разбором int/dict/_value и fallback на пагинацию;
слоты/retry через ``tracker_api.call_with_retry``.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from robopark_api.services.tracker_api import call_with_retry

logger = logging.getLogger(__name__)

API_BASE = os.environ.get("TRACKER_API_BASE", "https://st-api.yandex-team.ru")
WEB_BASE = os.environ.get("TRACKER_WEB_BASE", "https://st.yandex-team.ru")
USER_AGENT = os.environ.get("TRACKER_USER_AGENT", "robopark-api/0.1")
# Tracker API отдаёт не более 50 тикетов за один HTTP-запрос; find() сам
# дочитывает следующие страницы по Link header при итерации.
API_PAGE_SIZE = 50
DEFAULT_QUEUE = "SDCFLEETOPS"
DEFAULT_ISSUE_TYPES = ("repair", "service", "calibration")

_CLIENTS: dict[str, Any] = {}


class TrackerError(Exception):
    pass


def _ensure_startrek_on_path() -> None:
    env_path = (os.environ.get("TRACKER_STARTREK_PATH") or "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path))
    here = Path(__file__).resolve()
    for idx in (5, 7):
        if idx < len(here.parents):
            candidates.append(here.parents[idx] / "startrek_client-2.8")
    for root in candidates:
        if (root / "startrek_client").is_dir():
            path = str(root)
            if path not in sys.path:
                sys.path.insert(0, path)
            return
    raise TrackerError(
        "startrek_client-2.8 not found; place it at repo root or set TRACKER_STARTREK_PATH"
    )


def _import_startrek():
    _ensure_startrek_on_path()
    from startrek_client import Startrek  # type: ignore

    return Startrek


def clear_tracker_clients() -> None:
    """Сброс кэша Startrek после смены OAuth-токена."""
    _CLIENTS.clear()


def _client(token: str):
    cached = _CLIENTS.get(token)
    if cached is not None:
        return cached
    Startrek = _import_startrek()
    client = Startrek(useragent=USER_AGENT, base_url=API_BASE, token=token)
    _CLIENTS[token] = client
    return client


def build_issue_url(key: str) -> str:
    return f"{WEB_BASE.rstrip('/')}/{key}"


def _sanitize_ql_value(value: str) -> str:
    return str(value or "").strip().replace("\n", " ").replace("\r", "")


def _ql_quote(value: str) -> str:
    text = _sanitize_ql_value(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _ql_token(value: str) -> str:
    """Значение без кавычек, если безопасно для парсера Tracker."""
    text = _sanitize_ql_value(value)
    if not text:
        return ""
    if (
        text.isascii()
        and " " not in text
        and "(" not in text
        and ")" not in text
        and '"' not in text
    ):
        return text
    return _ql_quote(text)


def _join_query(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def join_query(*parts: str) -> str:
    return _join_query(*parts)


def ql_quote(value: str) -> str:
    return _ql_quote(value)


def ql_token(value: str) -> str:
    return _ql_token(value)


def assignee_clause(assignee: str | None) -> str | None:
    """Build Startrek QL for assignee filter. ``empty`` → unassigned issues."""
    if not assignee:
        return None
    normalized = assignee.strip().lower()
    if normalized in {"empty", "none", "__empty__"}:
        return "Assignee: empty()"
    login = assignee.strip()
    if not login:
        return None
    return f"Assignee: {_ql_token(login)}"


def open_issues_clause() -> str:
    """Открытые: без resolution + не closed/закрыт (Tracker иногда оставляет Resolution empty)."""
    return (
        "Resolution: empty() "
        '(Status: !closed AND Status: !"Закрыт" AND Status: !"Closed" '
        'AND Status: !resolved AND Status: !"Решен" AND Status: !"Решён")'
    )


def _open_issues_clause() -> str:
    return open_issues_clause()


def _queue_uses_issue_types(queue: str) -> bool:
    """Фильтр Type действует только для очереди SDCFLEETOPS (как в bot_otchet)."""
    raw = (queue or "").strip().strip('"') or DEFAULT_QUEUE
    return raw.upper() == DEFAULT_QUEUE.upper()


def type_clause(queue: str, issue_type: str | None = None) -> str:
    explicit = _sanitize_ql_value(issue_type or "")
    if explicit:
        return f"Type: {_ql_token(explicit)}"
    if _queue_uses_issue_types(queue):
        return f"Type: {', '.join(DEFAULT_ISSUE_TYPES)}"
    return ""


def _type_clause(queue: str, issue_type: str | None = None) -> str:
    return type_clause(queue, issue_type)


def _tag_clause(tag: str) -> str:
    token = _ql_token(tag)
    if not token:
        raise ValueError("тег парка пустой")
    return f"Tags: {token}"


def exclude_tag(tag: str) -> str:
    """Исключение тега: Tags: !\"donor\" (не -Tags:)."""
    text = _sanitize_ql_value(tag)
    if not text:
        return ""
    return f"Tags: !{_ql_quote(text)}"


def _priority_clause(priority: str = "blocker") -> str:
    return f"Priority: {_ql_token(priority) or 'blocker'}"


def _queue_clause(queue: str) -> str:
    return f"Queue: {_ql_token(queue) or DEFAULT_QUEUE}"


def build_open_blockers_query(
    queue: str,
    tag: str,
    *,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> str:
    parts = [
        _queue_clause(queue),
        type_clause(queue, issue_type),
        _priority_clause(priority),
        open_issues_clause(),
        _tag_clause(tag),
    ]
    return _join_query(*parts)


def build_untagged_blockers_query(
    queue: str,
    park_tags: list[str],
    *,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> str:
    """Открытые blocker без тегов известных парков (не Tags: empty())."""
    parts = [
        _queue_clause(queue),
        type_clause(queue, issue_type),
        _priority_clause(priority),
        open_issues_clause(),
    ]
    for raw in park_tags:
        clause = exclude_tag(raw)
        if clause:
            parts.append(clause)
    if not any(_sanitize_ql_value(t) for t in park_tags):
        parts.append("Tags: empty()")
    return _join_query(*parts)


def build_incident_blockers_query(
    queue: str,
    *,
    priority: str = "blocker",
) -> str:
    """Открытые blocker типа Incident (без фильтра тегов)."""
    return _join_query(
        _queue_clause(queue),
        "Type: incident",
        _priority_clause(priority),
        open_issues_clause(),
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
        dt = dt.replace(tzinfo=UTC)
    return (datetime.now(UTC) - dt.astimezone(UTC)).total_seconds() / 3600


def _fmt_hours(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.1f}"


def _is_relocation_status(status: str) -> bool:
    low = (status or "").strip().lower()
    if not low:
        return False
    if low in {"intransit", "moving", "перемещение"}:
        return True
    return "перемещ" in low or "relocation" in low


def parse_robot_from_summary(summary: str) -> str | None:
    match = re.search(r"\[([a-zA-Z]?\d+)\]", summary or "")
    return match.group(1) if match else None


def _status_display(status: Any) -> str:
    if status is None:
        return ""
    if isinstance(status, dict):
        return str(status.get("display") or status.get("key") or "")
    display = getattr(status, "display", None)
    if display:
        return str(display)
    key = getattr(status, "key", None)
    return str(key or status or "")


def _status_key(status: Any) -> str:
    if status is None:
        return ""
    if isinstance(status, dict):
        return str(status.get("key") or "")
    key = getattr(status, "key", None)
    if key:
        return str(key)
    return str(getattr(status, "display", "") or status or "")


def _queue_display(queue: Any) -> str:
    if queue is None:
        return ""
    if isinstance(queue, dict):
        return str(queue.get("key") or queue.get("display") or "")
    key = getattr(queue, "key", None)
    if key:
        return str(key)
    return str(getattr(queue, "display", None) or queue or "")


def _tags_from(raw: Any) -> list[str]:
    """Normalize Tracker tags (list of str / dicts / objects) to a list of strings."""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    try:
        items = list(raw)
    except TypeError:
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            value = item.get("name") or item.get("display") or item.get("key") or ""
        else:
            value = (
                getattr(item, "name", None)
                or getattr(item, "display", None)
                or getattr(item, "key", None)
                or item
            )
        text = str(value or "").strip()
        if text:
            out.append(text)
    return out


def _attachments_from(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("filename") or "").strip()
            attachment_id = str(item.get("id") or item.get("self") or "").strip()
            url = str(item.get("self") or item.get("url") or "").strip() or None
            size_raw = item.get("size")
            mimetype = str(item.get("mimetype") or item.get("contentType") or "").strip() or None
        else:
            name = str(getattr(item, "name", None) or getattr(item, "filename", None) or "").strip()
            attachment_id = str(
                getattr(item, "id", None) or getattr(item, "self", None) or ""
            ).strip()
            url = (
                str(getattr(item, "self", None) or getattr(item, "url", None) or "").strip() or None
            )
            size_raw = getattr(item, "size", None)
            mimetype = (
                str(
                    getattr(item, "mimetype", None) or getattr(item, "contentType", None) or ""
                ).strip()
                or None
            )
        if not name and not attachment_id:
            continue
        try:
            size = int(size_raw) if size_raw is not None else None
        except (TypeError, ValueError):
            size = None
        out.append(
            {
                "id": attachment_id or name,
                "name": name or attachment_id,
                "size": size,
                "url": url,
                "mimetype": mimetype,
            }
        )
    return out


def _person(raw: Any) -> dict[str, str] | None:
    """Normalize a Tracker user reference to ``{display, login}``."""
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        return {"display": text, "login": text} if text else None
    if isinstance(raw, dict):
        display = raw.get("display") or raw.get("login") or ""
        login = raw.get("login") or raw.get("id") or ""
    else:
        display = getattr(raw, "display", None) or getattr(raw, "login", None) or ""
        login = getattr(raw, "login", None) or getattr(raw, "id", None) or ""
    display = str(display or "").strip()
    login = str(login or "").strip()
    if not display and not login:
        return None
    return {"display": display or login, "login": login}


def _plain(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, dict):
        for key in ("display", "name", "key", "value"):
            if raw.get(key):
                return str(raw[key])
        return ""
    for attr in ("display", "name", "key"):
        value = getattr(raw, attr, None)
        if value:
            return str(value)
    return str(raw)


def _resolution_display(resolution: Any) -> str:
    if resolution is None:
        return ""
    if isinstance(resolution, dict):
        return str(resolution.get("key") or resolution.get("display") or "")
    for attr in ("key", "display", "name"):
        val = getattr(resolution, attr, None)
        if val:
            return str(val)
    return str(resolution)


def _field(issue: Any, name: str) -> Any:
    """Read a field from a REST dict or a Startrek object uniformly."""
    if isinstance(issue, dict):
        return issue.get(name)
    return getattr(issue, name, None)


def issue_to_dict(issue: Any) -> dict[str, Any]:
    """Normalize a Tracker issue (REST dict or Startrek object) to our DTO."""
    created = str(_field(issue, "createdAt") or "")
    status = _status_display(_field(issue, "status"))
    status_key = _status_key(_field(issue, "status"))
    summary = str(_field(issue, "summary") or "")
    resolution = _resolution_display(_field(issue, "resolution"))
    queue = _queue_display(_field(issue, "queue"))
    key = str(_field(issue, "key") or "")
    tags = _tags_from(_field(issue, "tags"))

    # Fields needed to render a Tracker-like issue card.
    description = str(_field(issue, "description") or "")
    updated = str(_field(issue, "updatedAt") or "")
    assignee = _person(_field(issue, "assignee"))
    reporter = _person(_field(issue, "createdBy"))
    priority = _plain(_field(issue, "priority"))
    issue_type = _plain(_field(issue, "type"))
    components = _tags_from(_field(issue, "components"))
    attachments = _attachments_from(_field(issue, "attachment") or _field(issue, "attachments"))

    hours_created = _hours_since(created)
    return {
        "key": key,
        "summary": summary,
        "status": status,
        "created": created,
        "updated": updated,
        "hours_created": _fmt_hours(hours_created),
        "in_relocation": "1" if _is_relocation_status(status) else "0",
        "robot": parse_robot_from_summary(summary),
        "status_key": status_key,
        "resolution": resolution,
        "description": description,
        "assignee": assignee,
        "reporter": reporter,
        "priority": priority,
        "type": issue_type,
        "components": components,
        "attachments": attachments,
        "queue": queue,
        "tags": tags,
    }


def _card_status_closed(status: str) -> bool:
    low = (status or "").strip().lower().replace("ё", "е")
    if not low:
        return False
    markers = (
        "closed",
        "закрыт",
        "resolved",
        "решен",
        "cancelled",
        "canceled",
        "отменен",
    )
    return any(marker in low for marker in markers)


def is_issue_open_item(item: dict[str, Any]) -> bool:
    """Клиентский фильтр: отсекает закрытые/отменённые по status/resolution."""
    if _card_status_closed(str(item.get("status") or "")):
        return False
    if _card_status_closed(str(item.get("status_key") or "")):
        return False
    resolution = str(item.get("resolution") or "").strip().lower()
    if resolution and resolution not in {"", "—", "none", "null", "empty"}:
        if any(
            marker in resolution
            for marker in (
                "fixed",
                "закрыт",
                "closed",
                "won't",
                "wont",
                "duplicate",
                "cancel",
                "отмен",
                "решен",
                "решён",
            )
        ):
            return False
        return False
    return True


def _map_exc(exc: BaseException) -> TrackerError | BaseException:
    name = type(exc).__name__
    if name in {"NotFound", "NotFoundError"}:
        return exc
    if isinstance(exc, TrackerError):
        return exc
    return TrackerError(str(exc))


def _run_tracked(fn, *, max_attempts: int = 2, call_timeout: float = 25.0):
    try:
        return call_with_retry(fn, max_attempts=max_attempts, call_timeout=call_timeout)
    except TrackerError:
        raise
    except Exception as exc:  # noqa: BLE001
        mapped = _map_exc(exc)
        if mapped is exc:
            raise
        raise mapped from exc


def _parse_count_result(result: Any) -> int | None:
    if isinstance(result, (int, float)):
        return int(result)
    if isinstance(result, dict):
        for key in ("count", "total", "value"):
            if key in result:
                return int(result[key])
    value = getattr(result, "_value", None)
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _search(
    token: str,
    query: str,
    *,
    filter_open: bool = True,
    order: list[str] | None = None,
) -> list[dict[str, Any]]:
    client = _client(token)
    kwargs: dict[str, Any] = {"per_page": API_PAGE_SIZE}
    if order:
        kwargs["order"] = order

    def _run() -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for issue in client.issues.find(query, **kwargs):
            item = issue_to_dict(issue)
            if filter_open and not is_issue_open_item(item):
                continue
            items.append(item)
        return items

    return _run_tracked(_run, call_timeout=30.0)


def count_issues(*, token: str, query: str) -> int:
    """Считает тикеты: сначала count_only, иначе пагинация по API_PAGE_SIZE.

    Без вложенного retry поверх call_with_retry (deadlock слотов).
    """
    client = _client(token)

    def _count_only() -> int | None:
        result = client.issues.find(query, count_only=True)
        return _parse_count_result(result)

    try:
        counted = _run_tracked(_count_only, max_attempts=2, call_timeout=20.0)
        if counted is not None:
            return counted
    except TrackerError as exc:
        logger.warning("count_only failed for %r: %s", query, exc)
        text = str(exc).lower()
        if "unprocessable" in text or "query" in text:
            raise

    def _paginate() -> int:
        total = 0
        for _ in client.issues.find(query, per_page=API_PAGE_SIZE):
            total += 1
        return total

    return _run_tracked(_paginate, max_attempts=1, call_timeout=30.0)


def health_check(*, token: str, queue: str = DEFAULT_QUEUE) -> bool:
    """Лёгкий ping: только count_only, без тяжёлой пагинации."""
    client = _client(token)
    query = f"Queue: {_ql_token(queue) or DEFAULT_QUEUE}"

    def _run() -> bool:
        result = client.issues.find(query, count_only=True)
        return _parse_count_result(result) is not None

    try:
        return bool(_run_tracked(_run, max_attempts=1, call_timeout=15.0))
    except Exception:  # noqa: BLE001
        return False


def fetch_park_blockers(
    *,
    token: str,
    queue: str,
    park_tag: str,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> list[dict[str, Any]]:
    query = build_open_blockers_query(
        queue,
        park_tag,
        priority=priority,
        issue_type=issue_type,
    )
    return _search(token, query, filter_open=True)


def search_issues(
    *,
    token: str,
    query: str,
    filter_open: bool = True,
    order: list[str] | None = None,
) -> list[dict[str, Any]]:
    return _search(token, query, filter_open=filter_open, order=order)


def get_issue(*, token: str, key: str) -> dict[str, Any] | None:
    client = _client(token)

    def _run() -> dict[str, Any] | None:
        try:
            issue = client.issues[key]
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ in {"NotFound", "NotFoundError"}:
                return None
            raise
        item = issue_to_dict(issue)
        return item

    try:
        return _run_tracked(_run)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ in {"NotFound", "NotFoundError"}:
            return None
        raise


def list_comments(*, token: str, key: str) -> list[dict[str, Any]]:
    client = _client(token)

    def _run() -> list[dict[str, Any]]:
        issue = client.issues[key]
        out: list[dict[str, Any]] = []
        for item in issue.comments.get_all():
            author = _person(getattr(item, "createdBy", None))
            out.append(
                {
                    "id": str(getattr(item, "id", "") or ""),
                    "text": str(getattr(item, "text", "") or ""),
                    "author": author["display"] if author else "",
                    "author_login": author["login"] if author else "",
                    "created_at": str(getattr(item, "createdAt", "") or ""),
                }
            )
        return out

    return _run_tracked(_run)


def add_comment(*, token: str, key: str, text: str) -> dict[str, Any]:
    client = _client(token)

    def _run() -> dict[str, Any]:
        comment = client.issues[key].comments.create(text=text)
        return {
            "id": str(getattr(comment, "id", "") or ""),
            "text": str(getattr(comment, "text", "") or text),
        }

    return _run_tracked(_run)


def assign_issue(*, token: str, key: str, assignee: str) -> None:
    client = _client(token)

    def _run() -> None:
        client.issues[key].update(assignee=assignee)

    _run_tracked(_run)


def unassign_issue(*, token: str, key: str) -> None:
    client = _client(token)

    def _run() -> None:
        client.issues[key].update(assignee=None)

    _run_tracked(_run)


def list_transitions(*, token: str, key: str) -> list[dict[str, Any]]:
    client = _client(token)

    def _run() -> list[dict[str, Any]]:
        issue = client.issues[key]
        transitions = issue.transitions
        if hasattr(transitions, "get_all"):
            raw_items = list(transitions.get_all())
        else:
            try:
                raw_items = list(transitions)
            except TypeError:
                raw_items = []
        out: list[dict[str, Any]] = []
        for item in raw_items:
            if isinstance(item, dict):
                out.append(
                    {
                        "id": str(item.get("id") or item.get("key") or ""),
                        "display": str(item.get("display") or item.get("name") or ""),
                    }
                )
                continue
            out.append(
                {
                    "id": str(getattr(item, "id", None) or getattr(item, "key", None) or ""),
                    "display": str(
                        getattr(item, "display", None) or getattr(item, "name", None) or ""
                    ),
                }
            )
        return out

    return _run_tracked(_run)


def transition_issue(
    *, token: str, key: str, transition: str, resolution: str | None = None
) -> None:
    client = _client(token)

    def _run() -> None:
        kwargs: dict[str, Any] = {}
        if resolution:
            kwargs["resolution"] = resolution
        client.issues[key].transitions[transition].execute(**kwargs)

    _run_tracked(_run)


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
    return bool(digits and digits in summary_lower)


def search_robot_tickets(*, token: str, queue: str, query: str) -> list[dict[str, Any]]:
    key = query.strip().upper()
    if re.fullmatch(r"[A-Z0-9-]+-\d+", key):
        issue = get_issue(token=token, key=key)
        if not issue:
            return []
        issue_queue = (issue.get("queue") or "").strip()
        if issue_queue and issue_queue != queue.strip():
            return []
        return [issue]

    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for variant in _robot_search_variants(query):
        search_query = _join_query(
            _queue_clause(queue),
            type_clause(queue),
            _priority_clause("blocker"),
            open_issues_clause(),
            f"Summary: {_ql_quote(variant)}",
        )
        for item in _search(token, search_query, filter_open=True):
            if not _summary_matches_robot(item["summary"], query):
                continue
            if item["key"] in seen:
                continue
            seen.add(item["key"])
            results.append(item)
        if results:
            break
    return results
