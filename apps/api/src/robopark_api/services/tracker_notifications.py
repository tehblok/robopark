"""Lease-owned polling for new Tracker task notifications."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.models import Park
from robopark_api.schedule_models import TrackerNotificationCursor
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_cache, tracker_client, tracker_filters, tracker_signatures

logger = logging.getLogger(__name__)

_SCOPE_KEY = "new-tasks"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _issue_position(issue: dict) -> tuple[datetime, str] | None:
    key = str(issue.get("key") or "").strip()
    raw_created = str(issue.get("created") or "").strip()
    if not key or not raw_created:
        return None
    try:
        created = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (_aware(created), key)


def _decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    try:
        created, key = json.loads(value)
        parsed = datetime.fromisoformat(str(created))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return (_aware(parsed), str(key))


def _encode_cursor(position: tuple[datetime, str]) -> str:
    return json.dumps([position[0].isoformat(), position[1]], separators=(",", ":"))


def _ensure_cursor_row(session_factory: Callable[[], Session]) -> None:
    with session_factory() as db:
        if db.get(TrackerNotificationCursor, _SCOPE_KEY) is not None:
            return
        db.add(TrackerNotificationCursor(scope_key=_SCOPE_KEY))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()


def _claim(
    session_factory: Callable[[], Session],
    *,
    owner_id: str,
    now: datetime,
    lease_seconds: float,
) -> str | None | bool:
    _ensure_cursor_row(session_factory)
    lease_until = now + timedelta(seconds=lease_seconds)
    with session_factory() as db:
        result = db.execute(
            update(TrackerNotificationCursor)
            .where(
                TrackerNotificationCursor.scope_key == _SCOPE_KEY,
                or_(
                    TrackerNotificationCursor.lease_owner == owner_id,
                    TrackerNotificationCursor.lease_until.is_(None),
                    TrackerNotificationCursor.lease_until <= now,
                ),
            )
            .values(lease_owner=owner_id, lease_until=lease_until)
        )
        if result.rowcount != 1:
            db.rollback()
            return False
        db.commit()
        row = db.get(TrackerNotificationCursor, _SCOPE_KEY)
        return row.cursor_value if row is not None else False


def _update_state(
    session_factory: Callable[[], Session],
    *,
    owner_id: str,
    cursor: tuple[datetime, str] | None = None,
    error: str | None = None,
    release: bool = False,
    lease_seconds: float,
) -> None:
    now = datetime.now(UTC)
    values: dict = {
        "last_error": error,
        "lease_until": None if release else now + timedelta(seconds=lease_seconds),
        "lease_owner": None if release else owner_id,
    }
    if cursor is not None:
        values["cursor_value"] = _encode_cursor(cursor)
        values["last_success_at"] = now
    with session_factory() as db:
        db.execute(
            update(TrackerNotificationCursor)
            .where(
                TrackerNotificationCursor.scope_key == _SCOPE_KEY,
                TrackerNotificationCursor.lease_owner == owner_id,
            )
            .values(**values)
        )
        db.commit()


def _query_context(session_factory: Callable[[], Session]) -> tuple[str | None, list[str]]:
    with session_factory() as db:
        token = settings_svc.get_tracker_token(db)
        queues = sorted(
            {
                str(queue or tracker_client.DEFAULT_QUEUE).strip()
                for queue in db.scalars(
                    select(Park.tracker_queue).where(Park.is_active.is_(True))
                )
                if str(queue or tracker_client.DEFAULT_QUEUE).strip()
            }
        )
    return token, queues


def _search_query(queues: list[str], cursor: tuple[datetime, str] | None) -> str:
    queue_clause = "(" + " OR ".join(
        f"Queue: {tracker_client.ql_token(queue)}" for queue in queues
    ) + ")"
    created_clause = (
        f'Created: >= "{cursor[0]:%Y-%m-%d %H:%M:%S}"' if cursor is not None else ""
    )
    return tracker_client.join_query(
        "Priority: blocker",
        tracker_client.open_issues_clause(),
        queue_clause,
        created_clause,
    )


def poll_tracker_notifications(
    session_factory: Callable[[], Session],
    emit: Callable[..., dict | None],
    *,
    page_size: int,
    owner_id: str,
    lease_seconds: float = 300.0,
) -> int:
    """Process one bounded Tracker page, returning emitted event count."""
    claimed = _claim(
        session_factory,
        owner_id=owner_id,
        now=datetime.now(UTC),
        lease_seconds=lease_seconds,
    )
    if claimed is False:
        return 0
    cursor = _decode_cursor(claimed)
    emitted = 0
    try:
        token, queues = _query_context(session_factory)
        if not token or not queues:
            _update_state(
                session_factory,
                owner_id=owner_id,
                error=None,
                release=True,
                lease_seconds=lease_seconds,
            )
            return 0
        issues = tracker_cache.search_issues(
            token=token,
            query=_search_query(queues, cursor),
            filter_open=True,
            order=["createdAt"],
        )
        positioned = sorted(
            (position, issue)
            for issue in issues
            if (position := _issue_position(issue)) is not None and (cursor is None or position > cursor)
        )[:page_size]
        for position, issue in positioned:
            if tracker_filters.issue_status_bucket(issue) in {"new", "queued"}:
                with session_factory() as db:
                    park = tracker_signatures.resolve_park(db, issue)
                    park_id = park.id if park is not None else None
                issue_key = position[1]
                if park_id is not None:
                    emit(
                        event_type="new_task",
                        park_id=park_id,
                        protected_text=f"Новая задача {issue_key}",
                        event_key=f"new-task:{issue_key}",
                    )
                    emitted += 1
            cursor = position
            _update_state(
                session_factory,
                owner_id=owner_id,
                cursor=cursor,
                error=None,
                lease_seconds=lease_seconds,
            )
        if not positioned:
            with session_factory() as db:
                row = db.get(TrackerNotificationCursor, _SCOPE_KEY)
                if row is not None and row.lease_owner == owner_id:
                    row.last_success_at = datetime.now(UTC)
                    row.last_error = None
                    db.commit()
        return emitted
    except tracker_client.TrackerError:
        logger.warning("Tracker notification poll failed", exc_info=True)
        _update_state(
            session_factory,
            owner_id=owner_id,
            error="tracker_unavailable",
            release=True,
            lease_seconds=lease_seconds,
        )
        return 0
    except Exception:
        logger.exception("Tracker notification poll failed")
        _update_state(
            session_factory,
            owner_id=owner_id,
            error="poll_failed",
            release=True,
            lease_seconds=lease_seconds,
        )
        return 0
    finally:
        _update_state(
            session_factory,
            owner_id=owner_id,
            error=None,
            release=True,
            lease_seconds=lease_seconds,
        )


async def run_tracker_notification_loop(
    session_factory: Callable[[], Session],
    stop_event: asyncio.Event,
    *,
    emit: Callable[..., dict | None],
    interval_seconds: float,
    page_size: int,
    lease_seconds: float,
) -> None:
    owner_id = str(uuid4())
    while not stop_event.is_set():
        await asyncio.to_thread(
            poll_tracker_notifications,
            session_factory,
            emit,
            page_size=page_size,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
