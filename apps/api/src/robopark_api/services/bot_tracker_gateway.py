"""Read-only Tracker facade for the optional transition Telegram bot.

The bot authenticates to Robopark with its own bridge key.  Tracker credentials
remain encrypted in Robopark and are never returned to, or accepted from, the
bot process.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import Park
from robopark_api.services import platform_settings, tracker_client

DEFAULT_BRIDGE_KEY_FILE = "/run/secrets/bot-bridge-key"
BRIDGE_KEY_FILE_ENV = "ROBOPARK_BOT_BRIDGE_KEY_FILE"
MIN_BRIDGE_KEY_BYTES = 32
MAX_BRIDGE_KEY_BYTES = 512
MAX_SEARCH_RESULTS = 2_000
MAX_COLLECTION_ITEMS = 2_000
MAX_CHANGELOG_ITEMS = 200
MAX_NESTING_DEPTH = 10
MAX_MAPPING_ITEMS = 256
MAX_STRING_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_QUEUE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_QUEUE_CLAUSE_PATTERN = re.compile(
    r'(?:^|[\s(])queue\s*:\s*(?:"([A-Z][A-Z0-9_]{1,63})"|([A-Z][A-Z0-9_]{1,63}))',
    flags=re.IGNORECASE,
)
_ISSUE_FIELDS = frozenset(
    {
        "key",
        "summary",
        "description",
        "status",
        "type",
        "tags",
        "resolution",
        "createdAt",
        "updatedAt",
        "resolvedAt",
        "statusStartTime",
        "priority",
        "homePort",
        "rover",
    }
)
_ISSUE_REFERENCE_FIELDS = frozenset({"status", "type", "resolution", "priority"})
_REFERENCE_FIELDS = frozenset({"id", "key", "display", "name"})


class BotBridgeUnavailable(RuntimeError):
    """The server-side bridge credential is absent or unsafe."""


class TrackerNotConfigured(RuntimeError):
    """Robopark has no usable platform Tracker token."""


class TrackerIssueNotFound(tracker_client.TrackerError):
    """Tracker does not contain the requested issue."""


class TrackerResponseTooLarge(tracker_client.TrackerError):
    """Tracker returned more data than the internal bridge permits."""


class TrackerQueueNotAuthorized(RuntimeError):
    """The requested data is outside queues configured for active parks."""


def _bridge_key_path() -> Path:
    raw = os.environ.get(BRIDGE_KEY_FILE_ENV, DEFAULT_BRIDGE_KEY_FILE).strip()
    path = Path(raw)
    if not raw or not path.is_absolute():
        raise BotBridgeUnavailable
    return path


def read_bridge_key() -> bytes:
    """Read a bounded, non-symlinked credential without following path races."""
    path = _bridge_key_path()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BotBridgeUnavailable from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise BotBridgeUnavailable
        if details.st_size < MIN_BRIDGE_KEY_BYTES or details.st_size > MAX_BRIDGE_KEY_BYTES + 1:
            raise BotBridgeUnavailable
        # A root-owned group-readable secret is supported; writable group or
        # any access for "other" users is not.
        if details.st_mode & 0o027:
            raise BotBridgeUnavailable
        raw = os.read(descriptor, MAX_BRIDGE_KEY_BYTES + 2)
    finally:
        os.close(descriptor)

    secret = raw.strip()
    if not (MIN_BRIDGE_KEY_BYTES <= len(secret) <= MAX_BRIDGE_KEY_BYTES):
        raise BotBridgeUnavailable
    if b"\x00" in secret or any(chr(value).isspace() for value in secret):
        raise BotBridgeUnavailable
    return secret


def bridge_key_matches(provided: str | None) -> bool:
    expected = read_bridge_key()
    candidate = b"" if provided is None else provided.encode("utf-8", errors="strict")
    if len(candidate) > MAX_BRIDGE_KEY_BYTES:
        candidate = candidate[: MAX_BRIDGE_KEY_BYTES + 1]
    return hmac.compare_digest(candidate, expected)


def tracker_token(db: Session) -> str:
    token = (platform_settings.get_tracker_token(db) or "").strip()
    if not token:
        raise TrackerNotConfigured
    return token


def active_tracker_queues(db: Session) -> tuple[str, ...]:
    """Return safe queue keys configured on active parks, or an empty tuple."""
    queues: set[str] = set()
    for raw in db.scalars(select(Park.tracker_queue).where(Park.is_active.is_(True))):
        queue = str(raw or "").strip().upper()
        if _QUEUE_PATTERN.fullmatch(queue):
            queues.add(queue)
    return tuple(sorted(queues))


def _require_queues(allowed_queues: Iterable[str]) -> tuple[str, ...]:
    queues = tuple(sorted({str(queue).strip().upper() for queue in allowed_queues}))
    queues = tuple(queue for queue in queues if _QUEUE_PATTERN.fullmatch(queue))
    if not queues:
        raise TrackerQueueNotAuthorized
    return queues


def _scoped_query(query: str, allowed_queues: Iterable[str]) -> str:
    queues = _require_queues(allowed_queues)
    requested = {(quoted or bare).upper() for quoted, bare in _QUEUE_CLAUSE_PATTERN.findall(query)}
    if requested.difference(queues):
        raise TrackerQueueNotAuthorized
    queue_clause = " OR ".join(f"Queue: {tracker_client.ql_token(queue)}" for queue in queues)
    return f"({query}) AND ({queue_clause})"


def _require_issue_queue(key: str, allowed_queues: Iterable[str]) -> None:
    queues = _require_queues(allowed_queues)
    queue = key.rsplit("-", 1)[0].upper()
    if queue not in queues:
        raise TrackerQueueNotAuthorized


def _loaded_value(value: Any) -> Any:
    raw = getattr(value, "__dict__", {}).get("_value")
    return raw if raw is not None else value


def _plain(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_NESTING_DEPTH:
        return None
    value = _loaded_value(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_STRING_BYTES:
            raise TrackerResponseTooLarge
        return value
    if isinstance(value, dict):
        if len(value) > MAX_MAPPING_ITEMS:
            raise TrackerResponseTooLarge
        return {
            str(key): _plain(item, depth=depth + 1)
            for key, item in value.items()
            if str(key) != "self"
        }
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise TrackerResponseTooLarge
        return [_plain(item, depth=depth + 1) for item in value]
    if hasattr(value, "as_dict"):
        try:
            return _plain(value.as_dict(), depth=depth + 1)
        except TrackerResponseTooLarge:
            raise
        except Exception:  # noqa: BLE001 - optional SDK serialization hook
            pass
    text = str(value)
    if len(text.encode("utf-8")) > MAX_STRING_BYTES:
        raise TrackerResponseTooLarge
    return text


def _bounded_payload(value: Any) -> Any:
    plain = _plain(value)
    encoded = json.dumps(plain, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise TrackerResponseTooLarge
    return plain


def _mapping(value: Any) -> dict[Any, Any]:
    loaded = _loaded_value(value)
    if isinstance(loaded, dict):
        return loaded
    if hasattr(loaded, "as_dict"):
        try:
            mapped = loaded.as_dict()
        except Exception:  # noqa: BLE001 - optional SDK serialization hook
            return {}
        return mapped if isinstance(mapped, dict) else {}
    return {}


def _reference_payload(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    return {str(key): raw[key] for key in _REFERENCE_FIELDS if key in raw}


def _issue_payload(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    filtered: dict[str, Any] = {}
    for field in _ISSUE_FIELDS:
        if field not in raw:
            continue
        item = raw[field]
        filtered[field] = _reference_payload(item) if field in _ISSUE_REFERENCE_FIELDS else item
    payload = _bounded_payload(filtered)
    return payload if isinstance(payload, dict) else {}


def _link_payload(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    filtered: dict[str, Any] = {}
    if "type" in raw:
        filtered["type"] = _reference_payload(raw["type"])
    if "direction" in raw:
        filtered["direction"] = raw["direction"]
    if "object" in raw:
        filtered["object"] = _reference_payload(raw["object"])
    payload = _bounded_payload(filtered)
    return payload if isinstance(payload, dict) else {}


def _changelog_payload(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    filtered: dict[str, Any] = {}
    if "updatedAt" in raw:
        filtered["updatedAt"] = raw["updatedAt"]
    fields: list[dict[str, Any]] = []
    for item in raw.get("fields") or []:
        field = _mapping(item)
        clean: dict[str, Any] = {}
        for name in ("field", "from", "to"):
            if name in field:
                clean[name] = _reference_payload(field[name])
        if clean:
            fields.append(clean)
    filtered["fields"] = fields
    payload = _bounded_payload(filtered)
    return payload if isinstance(payload, dict) else {}


def _issue_resource(token: str, key: str) -> Any:
    try:
        return tracker_client._client(token).issues[key]  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ in {"NotFound", "NotFoundError"}:
            raise TrackerIssueNotFound from exc
        raise


def search(
    *,
    token: str,
    query: str,
    order: list[str],
    per_page: int,
    max_pages: int,
    allowed_queues: Iterable[str],
) -> list[dict[str, Any]]:
    limit = min(per_page * max_pages, MAX_SEARCH_RESULTS)
    scoped_query = _scoped_query(query, allowed_queues)

    def load() -> list[Any]:
        resources: Iterable[Any] = tracker_client._client(token).issues.find(  # noqa: SLF001
            scoped_query,
            per_page=per_page,
            order=order,
        )
        result: list[Any] = []
        for resource in resources:
            result.append(resource)
            if len(result) >= limit:
                break
        return result

    loaded = tracker_client._run_tracked(  # noqa: SLF001
        load,
        max_attempts=1,
        call_timeout=tracker_client.SEARCH_CALL_TIMEOUT_SECONDS,
    )
    payload = _bounded_payload([_issue_payload(resource) for resource in loaded])
    return payload if isinstance(payload, list) else []


def issue(*, token: str, key: str, allowed_queues: Iterable[str]) -> dict[str, Any]:
    _require_issue_queue(key, allowed_queues)
    resource = tracker_client._run_tracked(  # noqa: SLF001
        lambda: _issue_resource(token, key),
        max_attempts=1,
        call_timeout=10.0,
    )
    payload = _issue_payload(resource)
    if not isinstance(payload, dict) or not payload:
        raise tracker_client.TrackerError("Tracker returned an invalid issue")
    return payload


def links(*, token: str, key: str, allowed_queues: Iterable[str]) -> list[dict[str, Any]]:
    _require_issue_queue(key, allowed_queues)

    def load() -> list[Any]:
        raw = _issue_resource(token, key).links.get_all()
        result: list[Any] = []
        for item in raw or []:
            if len(result) >= MAX_COLLECTION_ITEMS:
                raise TrackerResponseTooLarge
            result.append(item)
        return result

    loaded = tracker_client._run_tracked(  # noqa: SLF001
        load, max_attempts=1, call_timeout=10.0
    )
    payload = _bounded_payload([_link_payload(item) for item in loaded])
    return payload if isinstance(payload, list) else []


def status_changelog(
    *, token: str, key: str, allowed_queues: Iterable[str]
) -> list[dict[str, Any]]:
    _require_issue_queue(key, allowed_queues)

    def load() -> list[Any]:
        raw = _issue_resource(token, key).changelog.get_all(field="status")
        result: list[Any] = []
        for item in raw or []:
            result.append(item)
            if len(result) >= MAX_CHANGELOG_ITEMS:
                break
        return result

    loaded = tracker_client._run_tracked(  # noqa: SLF001
        load, max_attempts=1, call_timeout=10.0
    )
    payload = _bounded_payload([_changelog_payload(item) for item in loaded])
    return payload if isinstance(payload, list) else []
