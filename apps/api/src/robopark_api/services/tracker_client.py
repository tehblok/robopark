"""Internal Yandex Startrek client (st-api.yandex-team.ru).

Uses the declared ``yandex-tracker-client`` package:
``per_page=API_PAGE_SIZE`` (50) — клиент сам ходит по Link next;
``count_only`` с разбором int/dict/_value и fallback на пагинацию;
слоты/retry через ``tracker_api.call_with_retry``.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import threading
import weakref
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import islice
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

import httpx

from robopark_api.services import sla_clock
from robopark_api.services.repair_fields import MAX_COMPONENTS
from robopark_api.services.response_cache import ResponseCache
from robopark_api.services.tracker_api import (
    NOTIFICATION_SEARCH_CALL_TIMEOUT_SEC,
    NOTIFICATION_SEARCH_MAX_ATTEMPTS,
    NOTIFICATION_SEARCH_OPERATION_TIMEOUT_SEC,
    call_with_retry,
)

logger = logging.getLogger(__name__)

API_BASE = os.environ.get("TRACKER_API_BASE", "https://st-api.yandex-team.ru")
WEB_BASE = os.environ.get("TRACKER_WEB_BASE", "https://st.yandex-team.ru")
USER_AGENT = os.environ.get("TRACKER_USER_AGENT", "robopark-api/0.1")
# Tracker API отдаёт не более 50 тикетов за один HTTP-запрос; find() сам
# дочитывает следующие страницы по Link header при итерации.
API_PAGE_SIZE = 50
SEARCH_CALL_TIMEOUT_SECONDS = NOTIFICATION_SEARCH_CALL_TIMEOUT_SEC
SEARCH_MAX_ATTEMPTS = NOTIFICATION_SEARCH_MAX_ATTEMPTS
SEARCH_OPERATION_TIMEOUT_SECONDS = NOTIFICATION_SEARCH_OPERATION_TIMEOUT_SEC
MAX_ROBOT_REFERENCE_LENGTH = 64
DEFAULT_QUEUE = "SDCFLEETOPS"
DEFAULT_ISSUE_TYPES = ("repair", "service", "calibration")
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
ALLOWED_ATTACHMENT_MIMES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    }
)
_ATTACHMENT_EXT_MIMES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "heic": "image/heic",
    "heif": "image/heif",
}

# Robopark uses one platform token. Retain only the last requested client;
# in-flight calls and SDK resources keep retired clients alive until they finish.
_CLIENTS: dict[str, Any] = {}
_CLIENTS_LOCK = threading.Lock()

_WORK_HISTORY_WORKERS = 2
_WORK_HISTORY_TTL_SECONDS = 60.0
_WORK_HISTORY_MAX_ENTRIES = 512
_work_history_executor = ThreadPoolExecutor(
    max_workers=_WORK_HISTORY_WORKERS,
    thread_name_prefix="tracker-work-history",
)
_work_history_slots = threading.BoundedSemaphore(_WORK_HISTORY_WORKERS)
_work_history_lock = threading.Lock()
_work_history_flights: dict[str, Future[list[Any]]] = {}
_issue_status_history_cache: ResponseCache[list[dict[str, Any]]] = ResponseCache(
    _WORK_HISTORY_TTL_SECONDS,
    name="tracker.issue_history",
    max_entries=_WORK_HISTORY_MAX_ENTRIES,
)


class TrackerError(Exception):
    pass


def _import_startrek():
    try:
        from yandex_tracker_client import TrackerClient
    except ImportError as exc:
        raise TrackerError("yandex-tracker-client is not installed") from exc

    return TrackerClient


def clear_tracker_clients() -> None:
    """Retire the cached client without closing sessions still used by SDK resources."""
    with _CLIENTS_LOCK:
        _CLIENTS.clear()


def _client(token: str):
    with _CLIENTS_LOCK:
        cached = _CLIENTS.get(token)
        if cached is not None:
            return cached
        TrackerClient = _import_startrek()
        client = TrackerClient(
            headers={"User-Agent": USER_AGENT},
            base_url=API_BASE,
            token=token,
            retries=0,
            timeout=10,
        )
        # SDK resources reference their connection and client. Close only when
        # that whole object graph is unreachable, never during a live request.
        # The session itself has no back-reference to the client.
        weakref.finalize(client, client._connection.session.close)
        _CLIENTS.clear()
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


def build_closed_blockers_query(
    queue: str,
    tag: str,
    *,
    priority: str,
    since: datetime,
    until: datetime,
    issue_type: str | None = None,
) -> str:
    """Bound one park's closed-ticket discovery by its immutable scan window."""
    if since.tzinfo is None or until.tzinfo is None or since >= until:
        raise ValueError("tracker_history_window_invalid")
    return _join_query(
        _queue_clause(queue),
        type_clause(queue, issue_type),
        _priority_clause(priority),
        _tag_clause(tag),
        '(Status: closed OR Status: resolved OR Status: "Закрыт" OR Status: "Решен" OR Status: "Решён")',
        # Date-only QL may use a different time zone than the UTC cursor.
        # Widen the search by a day and verify exact UTC timestamps locally.
        f"Updated: >= {(since.date() - timedelta(days=1)).isoformat()}",
        f"Updated: < {(until.date() + timedelta(days=2)).isoformat()}",
        '"Sort By": Updated DESC',
    )


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


def _tracker_datetime(raw: object) -> datetime | None:
    text = str(raw or "").strip().replace("Z", "+00:00")
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sla_deadline_text(queued_at: datetime, timezone: str | None) -> str | None:
    if not timezone:
        return None
    try:
        return _utc_text(sla_clock.deadline(queued_at, timezone=timezone))
    except (ValueError, ZoneInfoNotFoundError):
        return None


def _history_items(issue: Any) -> list[Any]:
    embedded = _field(issue, "status_history") or _field(issue, "statusHistory")
    if embedded is not None:
        try:
            return list(embedded)
        except TypeError:
            return []
    return []


def repair_sla_fields(
    issue: Any, *, status_history: list[Any] | None = None, timezone: str | None = None
) -> dict[str, str | None]:
    """Derive the five-working-hour repair SLA from an actual queued transition."""
    if status_history is None and (
        issue.get("sla_source") == "status_history" if isinstance(issue, dict) else False
    ):
        queued_at = _tracker_datetime(issue.get("queued_at"))
        return {
            "queued_at": _utc_text(queued_at) if queued_at else None,
            "sla_deadline": _sla_deadline_text(queued_at, timezone) if queued_at else None,
            "sla_source": issue.get("sla_source"),
        }

    queued: list[datetime] = []
    for event in status_history if status_history is not None else _history_items(issue):
        fields = _field(event, "fields") or []
        for change in fields:
            field = _field(change, "field")
            field_name = (
                str(
                    _field(field, "id")
                    or _field(field, "key")
                    or _field(change, "fieldId")
                    or field
                    or ""
                )
                .strip()
                .lower()
            )
            if field_name != "status":
                continue
            target = _field(change, "to") or _field(change, "newValue")
            target_text = (
                " ".join(
                    str(value or "")
                    for value in (_field(target, "key"), _field(target, "display"), target)
                )
                .lower()
                .replace("ё", "е")
            )
            if "queued" not in target_text and "в очереди" not in target_text:
                continue
            changed_at = _tracker_datetime(_field(event, "updatedAt") or _field(event, "createdAt"))
            if changed_at is not None:
                queued.append(changed_at)

    if not queued:
        return {"queued_at": None, "sla_deadline": None, "sla_source": None}
    queued_at = min(queued)
    return {
        "queued_at": _utc_text(queued_at),
        "sla_deadline": _sla_deadline_text(queued_at, timezone),
        "sla_source": "status_history",
    }


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


def _loaded_value(raw: Any) -> Any:
    """Read SDK response data without triggering lazy Reference/Resource HTTP."""
    value = getattr(raw, "__dict__", {}).get("_value")
    return value if isinstance(value, dict) else raw


def _status_display(status: Any) -> str:
    status = _loaded_value(status)
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
    status = _loaded_value(status)
    if status is None:
        return ""
    if isinstance(status, dict):
        return str(status.get("key") or "")
    key = getattr(status, "key", None)
    if key:
        return str(key)
    return str(getattr(status, "display", "") or status or "")


def _queue_display(queue: Any) -> str:
    queue = _loaded_value(queue)
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
        item = _loaded_value(item)
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


def _ids_from(raw: Any) -> list[str]:
    if raw is None:
        return []
    try:
        items = list(raw) if not isinstance(raw, str) else [raw]
    except TypeError:
        return []
    result: list[str] = []
    for item in items:
        item = _loaded_value(item)
        value = (
            item.get("id") or item.get("key")
            if isinstance(item, dict)
            else getattr(item, "id", None) or getattr(item, "key", None) or item
        )
        text = str(value or "").strip()
        if text:
            result.append(text)
    return result


def _attachments_from(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out: list[dict[str, Any]] = []
    for item in items:
        item = _loaded_value(item)
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("filename") or "").strip()
            attachment_id = str(item.get("id") or item.get("self") or "").strip()
            url = (
                str(item.get("content") or item.get("self") or item.get("url") or "").strip()
                or None
            )
            size_raw = item.get("size")
            mimetype = str(item.get("mimetype") or item.get("contentType") or "").strip() or None
        else:
            name = str(getattr(item, "name", None) or getattr(item, "filename", None) or "").strip()
            attachment_id = str(
                getattr(item, "id", None) or getattr(item, "self", None) or ""
            ).strip()
            url = (
                str(
                    getattr(item, "content", None)
                    or getattr(item, "self", None)
                    or getattr(item, "url", None)
                    or ""
                ).strip()
                or None
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


def _person(raw: Any, login_cache: dict[str, str] | None = None) -> dict[str, str] | None:
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
        display = _field(raw, "display") or _field(raw, "login") or ""
        identity = str(_field(raw, "id") or "")
        if login_cache is not None and identity in login_cache:
            login = login_cache[identity]
        else:
            # User references often omit login. Resolve once per search, retaining
            # the real login needed by workload and assignee filters.
            login = getattr(raw, "login", None) or identity
            if login_cache is not None and identity:
                login_cache[identity] = str(login or "").strip()
    display = str(display or "").strip()
    login = str(login or "").strip()
    if not display and not login:
        return None
    return {"display": display or login, "login": login}


def _plain(raw: Any) -> str:
    raw = _loaded_value(raw)
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
    resolution = _loaded_value(resolution)
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
    issue = _loaded_value(issue)
    if isinstance(issue, dict):
        return issue.get(name)
    return getattr(issue, name, None)


def issue_to_dict(issue: Any, *, login_cache: dict[str, str] | None = None) -> dict[str, Any]:
    """Normalize a Tracker issue (REST dict or Startrek object) to our DTO."""
    created = str(_field(issue, "createdAt") or "")
    status = _status_display(_field(issue, "status"))
    status_key = _status_key(_field(issue, "status"))
    summary = str(_field(issue, "summary") or "")
    raw_resolution = _field(issue, "resolution")
    resolution = _resolution_display(raw_resolution)
    resolution_key = _status_key(raw_resolution)
    queue = _queue_display(_field(issue, "queue"))
    key = str(_field(issue, "key") or "")
    tags = _tags_from(_field(issue, "tags"))

    # Fields needed to render a Tracker-like issue card.
    description = str(_field(issue, "description") or "")
    updated = str(_field(issue, "updatedAt") or "")
    assignee = _person(_field(issue, "assignee"), login_cache)
    reporter = _person(_field(issue, "createdBy"), login_cache)
    raw_priority = _field(issue, "priority")
    priority = _plain(raw_priority)
    priority_key = _status_key(raw_priority)
    issue_type = _plain(_field(issue, "type"))
    raw_components = _field(issue, "components")
    components = _tags_from(raw_components)
    component_ids = _ids_from(raw_components)
    defect_code = (
        _plain(
            _field(issue, "60df26695151a36df681d67b--theDefectCode")
            or _field(issue, "theDefectCode")
        )
        or None
    )
    solution_method = (
        _plain(_field(issue, "solutionMethod") or _field(issue, "solution_method")) or None
    )
    attachments = _attachments_from(_field(issue, "attachment") or _field(issue, "attachments"))

    hours_created = _hours_since(created)
    return {
        "key": key,
        "summary": summary,
        "status": status,
        "created": created,
        "updated": updated,
        "resolved": str(_field(issue, "resolvedAt") or ""),
        "hours_created": _fmt_hours(hours_created),
        "in_relocation": "1" if _is_relocation_status(status) else "0",
        "robot": parse_robot_from_summary(summary),
        "status_key": status_key,
        "resolution": resolution,
        "resolution_key": resolution_key,
        "rover": _plain(_field(issue, "rover")),
        "description": description,
        "assignee": assignee,
        "reporter": reporter,
        "priority": priority,
        "priority_key": priority_key,
        "type": issue_type,
        "type_key": str(_field(_field(issue, "type"), "key") or ""),
        "components": components,
        "component_ids": component_ids,
        "defect_code": defect_code,
        "solution_method": solution_method,
        "attachments": attachments,
        "queue": queue,
        "tags": tags,
        "status_start_time": str(
            _field(issue, "statusStartTime") or _field(issue, "status_start_time") or ""
        ),
        "home_port": _plain(_field(issue, "homePort") or _field(issue, "home_port")),
        **repair_sla_fields(issue),
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


def _run_mutation(fn):
    """One synchronous write: no retry and no orphan future after timeout.

    The SDK has finite HTTP timeouts and retries=0. Holding the caller here
    also keeps its per-task lease until the actual write attempt terminates.
    """
    from robopark_api.services.tracker_api import tracker_slot

    try:
        with tracker_slot():
            return fn()
    except Exception as exc:
        raise TrackerError(str(exc)) from exc


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
    limit: int | None = None,
) -> list[dict[str, Any]]:
    client = _client(token)
    kwargs: dict[str, Any] = {"per_page": API_PAGE_SIZE}
    if order:
        kwargs["order"] = order

    def _run() -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        login_cache: dict[str, str] = {}
        for seen, issue in enumerate(client.issues.find(query, **kwargs), start=1):
            item = issue_to_dict(issue, login_cache=login_cache)
            if filter_open and not is_issue_open_item(item):
                if limit is not None and seen >= limit:
                    break
                continue
            item["_tracker_resource"] = issue
            items.append(item)
            if limit is not None and seen >= limit:
                break
        return items

    return _run_tracked(
        _run,
        max_attempts=SEARCH_MAX_ATTEMPTS,
        call_timeout=SEARCH_CALL_TIMEOUT_SECONDS,
    )


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


def search_issue_page(
    *,
    token: str,
    query: str,
    limit: int,
    filter_open: bool = True,
    order: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Read at most one bounded page without exhausting the SDK iterator."""
    if not 1 <= limit <= API_PAGE_SIZE:
        raise ValueError("tracker_page_limit_invalid")
    return _search(token, query, filter_open=filter_open, order=order, limit=limit)


def search_closed_history_page(*, token: str, query: str, page: int) -> list[dict[str, Any]]:
    """Fetch one small page for background history import without full issue cards."""
    if not 1 <= page <= 200:
        raise ValueError("tracker_page_number_invalid")
    client = _client(token)

    def _run() -> list[dict[str, Any]]:
        result = []
        for issue in client.issues.find(
            query,
            per_page=API_PAGE_SIZE,
            page=page,
            fields="key,queue,tags,status,updatedAt",
        ):
            result.append(
                {
                    "key": str(_field(issue, "key") or ""),
                    "queue": _queue_display(_field(issue, "queue")),
                    "tags": _tags_from(_field(issue, "tags")),
                    "status_key": _status_key(_field(issue, "status")),
                    "status": _status_display(_field(issue, "status")),
                    "updated": str(_field(issue, "updatedAt") or ""),
                    "_tracker_resource": issue,
                }
            )
            if len(result) >= API_PAGE_SIZE:
                break
        return result

    return _run_tracked(_run, max_attempts=1, call_timeout=15.0)


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
        item["_tracker_resource"] = issue
        return item

    try:
        return _run_tracked(_run)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ in {"NotFound", "NotFoundError"}:
            return None
        raise


def _project_issue_status_history(history: list[Any]) -> list[dict[str, Any]]:
    """Keep only bounded status and tag changes needed for historical park/SLA attribution."""

    def tags_value(raw: Any) -> list[str] | None:
        if not isinstance(raw, (list, tuple)) or len(raw) > 256:
            return None
        tags = []
        for item in raw:
            value = (
                item
                if isinstance(item, str)
                else (
                    _field(item, "name")
                    or _field(item, "display")
                    or _field(item, "key")
                    or _field(item, "id")
                )
            )
            if not isinstance(value, str) or not value.strip() or len(value) > 480:
                return None
            tags.append(value.strip())
        return tags

    projected: list[dict[str, Any]] = []
    for event in history:
        changed_at = _tracker_datetime(_field(event, "updatedAt") or _field(event, "createdAt"))
        if changed_at is None:
            continue
        fields: list[dict[str, Any]] = []
        for change in _field(event, "fields") or []:
            field = _field(change, "field")
            field_id = (
                _field(field, "id")
                or _field(field, "key")
                or _field(change, "fieldId")
                or field
                or ""
            )
            field_id = str(field_id).strip().lower()
            if field_id == "tags":
                before = _field(change, "from")
                after = _field(change, "to")
                if before is None:
                    before = _field(change, "oldValue")
                if after is None:
                    after = _field(change, "newValue")
                fields.append(
                    {
                        "field": {"id": "tags"},
                        "from": tags_value(before),
                        "to": tags_value(after),
                    }
                )
                continue
            if field_id != "status":
                continue
            target = _field(change, "to") or _field(change, "newValue")
            source = _field(change, "from") or _field(change, "oldValue")
            target_key = _field(target, "key") or ""
            target_display = _field(target, "display") or ""
            if not target_key and not target_display and target is not None:
                target_display = str(target)
            fields.append(
                {
                    "field": {"id": str(field_id)},
                    "to": {
                        "key": str(target_key),
                        "display": str(target_display),
                    },
                    "from": {
                        "key": str(_field(source, "key") or ""),
                        "display": str(_field(source, "display") or ""),
                    },
                }
            )
        if fields:
            projected.append(
                {
                    "id": str(_field(event, "id") or ""),
                    "updatedAt": _utc_text(changed_at),
                    "fields": fields,
                }
            )
    return projected


def _fetch_issue_status_history(
    *, token: str, key: str, issue: dict[str, Any], tracked: bool
) -> list[dict[str, Any]]:
    resource = issue.get("_tracker_resource")

    def _run() -> list[Any]:
        nonlocal resource
        if resource is None:
            resource = _client(token).issues[key]
        return list(resource.changelog.get_all())

    history = _run_tracked(_run, max_attempts=1, call_timeout=10.0) if tracked else _run()
    return _project_issue_status_history(history)


def _issue_history_cache_key(key: str, issue: dict[str, Any]) -> str:
    # A cached pre-queue changelog must not cover a newer issue version. The
    # bounded cache retains old versions only until its normal TTL/entry limit.
    updated = str(_field(issue, "updated") or _field(issue, "updatedAt") or "")
    return f"{key}:{hashlib.sha256(updated.encode()).hexdigest()}" if updated else key


def get_issue_status_history(*, token: str, key: str, issue: dict[str, Any]) -> list[Any]:
    try:
        return _issue_status_history_cache.get_or_load(
            _issue_history_cache_key(key, issue),
            lambda: _fetch_issue_status_history(
                token=token,
                key=key,
                issue=issue,
                tracked=True,
            ),
        )
    except TrackerError:
        raise
    except Exception as exc:  # noqa: BLE001
        mapped = _map_exc(exc)
        if mapped is exc:
            raise
        raise mapped from exc


def _load_work_status_history(*, token: str, key: str, issue: dict[str, Any]) -> list[Any]:
    """Load one Work changelog in its dedicated, already-bounded worker.

    This path deliberately does not use the general Tracker slot/executor.  A
    stuck read therefore occupies one of the two Work-history workers only;
    it cannot poison the general Tracker semaphore or grow its retry queue.
    """
    try:
        return _issue_status_history_cache.get_or_load(
            _issue_history_cache_key(key, issue),
            lambda: _fetch_issue_status_history(
                token=token,
                key=key,
                issue=issue,
                tracked=False,
            ),
        )
    except TrackerError:
        raise
    except Exception as exc:  # noqa: BLE001
        mapped = _map_exc(exc)
        if mapped is exc:
            raise
        raise mapped from exc


def schedule_issue_status_history(
    *, token: str, key: str, issue: dict[str, Any], allow_start: bool = True
) -> tuple[Future[list[Any]] | None, bool]:
    """Return cached/shared Work history without growing a request-owned queue.

    At most two list hydrations exist process-wide. Callers may stop admitting
    new work after their own small request quota while still consuming a cached
    value or an already-running single flight.
    """
    history_key = _issue_history_cache_key(key, issue)
    found, cached = _issue_status_history_cache.get_if_fresh(history_key)
    if found:
        ready: Future[list[Any]] = Future()
        ready.set_result(cached or [])
        return ready, False

    cache_key = f"{hashlib.sha256(token.encode()).hexdigest()}:{history_key}"
    with _work_history_lock:
        flight = _work_history_flights.get(cache_key)
        if flight is not None:
            return flight, False
        if not allow_start or not _work_history_slots.acquire(blocking=False):
            return None, False

        def load() -> list[Any]:
            try:
                return _load_work_status_history(token=token, key=key, issue=issue)
            except TrackerError:
                return []

        try:
            flight = _work_history_executor.submit(load)
        except BaseException:
            _work_history_slots.release()
            raise
        _work_history_flights[cache_key] = flight

    def finish(done: Future[list[Any]]) -> None:
        with contextlib.suppress(BaseException):
            done.result()
        with _work_history_lock:
            if _work_history_flights.get(cache_key) is done:
                _work_history_flights.pop(cache_key, None)
        _work_history_slots.release()

    flight.add_done_callback(finish)
    return flight, True


def clear_issue_status_history_cache() -> None:
    _issue_status_history_cache.clear()


def invalidate_issue_status_history(key: str) -> None:
    _issue_status_history_cache.invalidate(key)
    _issue_status_history_cache.invalidate_prefix(f"{key}:")


def list_comments(*, token: str, key: str) -> list[dict[str, Any]]:
    client = _client(token)

    def _run() -> list[dict[str, Any]]:
        issue = client.issues[key]
        out: list[dict[str, Any]] = []
        for item in issue.comments.get_all():
            author = _person(getattr(item, "createdBy", None))
            attachments = _attachments_from(
                getattr(item, "attachments", None) or getattr(item, "attachment", None)
            )
            out.append(
                {
                    "id": str(getattr(item, "id", "") or ""),
                    "text": str(getattr(item, "text", "") or ""),
                    "author": author["display"] if author else "",
                    "author_login": author["login"] if author else "",
                    "created_at": str(getattr(item, "createdAt", "") or ""),
                    "attachments": attachments,
                }
            )
        return out

    return _run_tracked(_run)


def add_comment(
    *,
    token: str,
    key: str,
    text: str,
    attachment_ids: list[str] | None = None,
) -> dict[str, Any]:
    if attachment_ids:
        url = f"{API_BASE.rstrip('/')}/v2/issues/{key}/comments"
        headers = {
            "Authorization": f"OAuth {token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        }
        body = {"text": text, "attachmentIds": attachment_ids}

        def _run() -> dict[str, Any]:
            try:
                with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                    response = client.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                raise TrackerError(str(exc)) from exc
            if response.status_code >= 400:
                raise TrackerError(f"comment create failed: {response.status_code}")
            data = response.json()
            if not isinstance(data, dict):
                raise TrackerError("unexpected comment response")
            return {
                "id": str(data.get("id") or data.get("longId") or ""),
                "text": str(data.get("text") or text),
            }

        return _run_mutation(_run)

    client = _client(token)

    def _run() -> dict[str, Any]:
        comment = client.issues[key].comments.create(text=text)
        return {
            "id": str(getattr(comment, "id", "") or ""),
            "text": str(getattr(comment, "text", "") or text),
        }

    return _run_mutation(_run)


def upload_temp_attachment(
    *,
    token: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> str:
    """Upload a file to Tracker temp storage; returns id for attachmentIds."""
    url = f"{API_BASE.rstrip('/')}/v2/attachments/"
    headers = {"Authorization": f"OAuth {token}", "User-Agent": USER_AGENT}
    files = {"file": (filename, content, content_type)}

    def _run() -> str:
        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                response = client.post(url, headers=headers, files=files)
        except httpx.HTTPError as exc:
            raise TrackerError(str(exc)) from exc
        if response.status_code >= 400:
            raise TrackerError(f"temp attachment upload failed: {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise TrackerError("unexpected temp attachment response")
        attachment_id = str(data.get("id") or "").strip()
        if not attachment_id:
            raise TrackerError("temp attachment upload missing id")
        return attachment_id

    return _run_mutation(_run)


def guess_image_content_type(filename: str, content: bytes) -> str | None:
    """Mobile cameras often send ``application/octet-stream`` or an empty type."""
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in _ATTACHMENT_EXT_MIMES:
        return _ATTACHMENT_EXT_MIMES[ext]
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def normalize_attachment_content_type(
    *,
    filename: str,
    content: bytes,
    content_type: str | None,
) -> str | None:
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if declared in ALLOWED_ATTACHMENT_MIMES:
        return declared
    if declared in {"", "application/octet-stream", "binary/octet-stream"}:
        return guess_image_content_type(filename, content)
    return None


def add_attachment(
    *,
    token: str,
    key: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> dict[str, Any]:
    url = f"{API_BASE.rstrip('/')}/v2/issues/{key}/attachments"
    headers = {"Authorization": f"OAuth {token}", "User-Agent": USER_AGENT}
    files = {"file": (filename, content, content_type)}

    def _run() -> dict[str, Any]:
        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                response = client.post(url, headers=headers, files=files)
        except httpx.HTTPError as exc:
            raise TrackerError(str(exc)) from exc
        if response.status_code >= 400:
            raise TrackerError(f"attachment upload failed: {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise TrackerError("unexpected attachment response")
        parsed = _attachments_from([data])
        if parsed:
            return parsed[0]
        return {
            "id": str(data.get("id") or data.get("self") or filename),
            "name": str(data.get("name") or filename),
            "size": len(content),
            "url": str(data.get("self") or data.get("content") or "") or None,
            "mimetype": content_type,
        }

    return _run_mutation(_run)


def assign_issue(*, token: str, key: str, assignee: str) -> None:
    client = _client(token)

    def _run() -> None:
        client.issues[key].update(assignee=assignee)

    _run_mutation(_run)


def set_issue_tags(*, token: str, key: str, tags: list[str]) -> None:
    client = _client(token)

    def _run() -> None:
        client.issues[key].update(tags=tags)

    _run_mutation(_run)


def set_issue_components(
    *, token: str, key: str, components: list[str], issue_resource: Any | None = None
) -> None:
    client = _client(token)

    def _run() -> None:
        issue = issue_resource or client.issues[key]
        issue.update(components=components)

    _run_mutation(_run)


def list_queue_components(*, token: str, queue: str) -> list[dict[str, str]]:
    client = _client(token)

    def _run() -> list[dict[str, str]]:
        resource = client.queues[queue]
        result: list[dict[str, str]] = []
        for raw in islice(resource.components, MAX_COMPONENTS):
            value = _loaded_value(raw)
            component_id = str(_field(value, "id") or _field(value, "key") or "").strip()
            label = str(_field(value, "display") or _field(value, "name") or component_id).strip()
            archived = bool(
                _field(value, "archived")
                or _field(value, "isArchived")
                or _field(value, "is_archived")
            )
            if component_id and label and not archived:
                result.append({"id": component_id, "label": label})
        return result

    return _run_tracked(_run, max_attempts=1, call_timeout=15.0)


def set_repair_fields(
    *,
    token: str,
    key: str,
    component_ids: list[str],
    defect_code: str,
    solution_method: str,
    issue_resource: Any | None = None,
) -> None:
    client = _client(token)

    def _run() -> None:
        issue = issue_resource or client.issues[key]
        issue.update(
            **{
                "components": component_ids,
                "60df26695151a36df681d67b--theDefectCode": defect_code,
                "solutionMethod": solution_method,
            }
        )

    _run_mutation(_run)


def unassign_issue(*, token: str, key: str) -> None:
    client = _client(token)

    def _run() -> None:
        client.issues[key].update(assignee=None)

    _run_mutation(_run)


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

    _run_mutation(_run)


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


def robot_summary_clause(number: str) -> str:
    """Narrow by indexed robot spellings; callers still compare exact identities.

    Tracker matches whole words: searching for 447 does not find a447.
    Include padding through the API's reference length limit, not a shorter
    arbitrary limit that could drop a previously accepted exact identity.
    """
    variants = []
    for width in range(len(number), MAX_ROBOT_REFERENCE_LENGTH + 1):
        padded = number.zfill(width)
        variants.extend((padded, f"a{padded}", f"YASADR{padded}"))
    return "(" + " OR ".join(f"Summary: {_ql_quote(v)}" for v in dict.fromkeys(variants)) + ")"


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
