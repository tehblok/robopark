"""Lease-owned polling for new Tracker task notifications."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from time import monotonic
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.collaboration_models import TrackerClaim
from robopark_api.models import Park, User
from robopark_api.schedule_models import TrackerNotificationCursor
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_cache, tracker_client, tracker_filters
from robopark_api.services.task_lifecycle import (
    reconcile_external_closure,
    tracker_issue_is_closed,
)
from robopark_api.services.tracker_policy import issue_tags
from robopark_api.task_workflow_models import TaskReview

logger = logging.getLogger(__name__)

_SCOPE_KEY = "new-tasks"
_CLOSURE_SCOPE_KEY = "closures"


def poll_delay(active: bool, failures: int) -> timedelta:
    base = 15 if active else 60
    return timedelta(seconds=min(300, base * (2 ** min(max(failures, 0), 5))))


def _active(session_factory: Callable[[], Session]) -> bool:
    with session_factory() as db:
        if db.scalar(select(TrackerClaim.issue_key).limit(1)) is not None:
            return True
        if db.scalar(
            select(TaskReview.id).where(
                TaskReview.state == "closed", TaskReview.closed_at.is_(None)
            ).limit(1)
        ) is not None:
            return True
        recent = datetime.now(UTC) - timedelta(minutes=2)
        return db.scalar(select(User.id).where(User.last_seen_at >= recent).limit(1)) is not None


def _poll_failed(session_factory: Callable[[], Session]) -> bool:
    with session_factory() as db:
        return any(
            row is not None and row.last_error
            for row in (
                db.get(TrackerNotificationCursor, _SCOPE_KEY),
                db.get(TrackerNotificationCursor, _CLOSURE_SCOPE_KEY),
            )
        )


def _closure_candidates(db: Session, position: str, limit: int, *, after: bool) -> list[str]:
    claim_boundary = TrackerClaim.issue_key > position if after else TrackerClaim.issue_key <= position
    review_boundary = TaskReview.issue_key > position if after else TaskReview.issue_key <= position
    claims = db.scalars(
        select(TrackerClaim.issue_key)
        .where(claim_boundary)
        .order_by(TrackerClaim.issue_key)
        .limit(limit)
    ).all()
    reviews = db.scalars(
        select(TaskReview.issue_key)
        .where(
            TaskReview.state == "closed",
            TaskReview.closed_at.is_(None),
            review_boundary,
        )
        .order_by(TaskReview.issue_key)
        .limit(limit)
    ).all()
    return sorted(set(claims) | set(reviews))[:limit]


def _advance_closure_cursor(
    session_factory: Callable[[], Session], key: str, *, error: str | None = None
) -> None:
    with session_factory() as db:
        cursor = db.get(TrackerNotificationCursor, _CLOSURE_SCOPE_KEY)
        if cursor is None:
            return
        cursor.cursor_value = key
        if error is not None:
            cursor.last_error = f"{error}:{key}"
        elif cursor.last_error and cursor.last_error.endswith(f":{key}"):
            cursor.last_error = None
        if error is None:
            cursor.last_success_at = datetime.now(UTC)
        db.commit()


def reconcile_closed_claims(
    session_factory: Callable[[], Session], *, limit: int = 25
) -> int:
    """Refresh a bounded, rotating set of locally owned or closing tickets."""
    if limit <= 0:
        return 0
    token, _queues = _query_context(session_factory)
    if not token:
        return 0
    with session_factory() as db:
        cursor = db.get(TrackerNotificationCursor, _CLOSURE_SCOPE_KEY)
        if cursor is None:
            cursor = TrackerNotificationCursor(scope_key=_CLOSURE_SCOPE_KEY)
            db.add(cursor)
        position = cursor.cursor_value or ""
        selected = _closure_candidates(db, position, limit, after=True)
        if len(selected) < limit:
            selected += _closure_candidates(db, position, limit - len(selected), after=False)
        db.commit()
    reconciled = 0
    for key in selected:
        try:
            issue = tracker_client.get_issue(token=token, key=key)
        except tracker_client.TrackerError:
            logger.warning("Tracker closure refresh failed", exc_info=True)
            _advance_closure_cursor(session_factory, key, error="tracker_unavailable")
            continue
        if issue is None or issue.get("key") != key:
            _advance_closure_cursor(session_factory, key)
            continue
        try:
            with session_factory() as db:
                before = db.get(TrackerClaim, key) is not None
                review = db.scalar(
                    select(TaskReview.id).where(
                        TaskReview.issue_key == key,
                        TaskReview.state == "closed",
                        TaskReview.closed_at.is_(None),
                    ).limit(1)
                )
                reconcile_external_closure(db, issue)
                after = db.get(TrackerClaim, key) is not None
                if tracker_issue_is_closed(issue) and (
                    (before and not after) or review is not None
                ):
                    reconciled += 1
        except Exception:
            logger.exception("Tracker closure reconciliation failed")
            _advance_closure_cursor(session_factory, key, error="poll_failed")
            continue
        _advance_closure_cursor(session_factory, key)
    return reconciled


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
) -> bool:
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
        result = db.execute(
            update(TrackerNotificationCursor)
            .where(
                TrackerNotificationCursor.scope_key == _SCOPE_KEY,
                TrackerNotificationCursor.lease_owner == owner_id,
            )
            .values(**values)
        )
        db.commit()
        return result.rowcount == 1


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
    created_clause = ""
    if cursor is not None:
        created = cursor[0].isoformat().replace("+00:00", "Z")
        created_clause = f'Created: > "{created}"'
        if cursor[1]:
            created_clause = (
                f'({created_clause} OR (Created: "{created}" '
                f"AND Key: > {tracker_client.ql_quote(cursor[1])}))"
            )
    return tracker_client.join_query(
        "Priority: blocker",
        tracker_client.open_issues_clause(),
        queue_clause,
        created_clause,
    )


def _resolve_active_park(db: Session, issue: dict) -> Park | None:
    tags = issue_tags(issue)
    tagged_parks = (
        list(db.scalars(select(Park).where(Park.tag.in_(tags)))) if tags else []
    )
    if tagged_parks:
        if len(tagged_parks) == 1 and tagged_parks[0].is_active:
            return tagged_parks[0]
        return None

    queue = str(issue.get("queue") or "").strip()
    if not queue:
        return None
    candidates = list(
        db.scalars(
            select(Park).where(
                Park.tracker_queue == queue,
                Park.is_active.is_(True),
            )
        )
    )
    return candidates[0] if len(candidates) == 1 else None


def poll_tracker_notifications(
    session_factory: Callable[[], Session],
    emit: Callable[..., dict | None],
    *,
    page_size: int,
    owner_id: str,
    lease_seconds: float = 300.0,
    poll_deadline_seconds: float = 45.0,
    max_operation_seconds: float = tracker_client.SEARCH_OPERATION_TIMEOUT_SECONDS,
) -> int:
    """Process one bounded Tracker page, returning emitted event count."""
    if lease_seconds < poll_deadline_seconds + max_operation_seconds:
        raise ValueError("tracker_notification_lease_too_short")
    deadline = monotonic() + poll_deadline_seconds
    claimed = _claim(
        session_factory,
        owner_id=owner_id,
        now=datetime.now(UTC),
        lease_seconds=lease_seconds,
    )
    if claimed is False:
        return 0
    cursor = _decode_cursor(claimed)
    if cursor is None:
        _update_state(
            session_factory,
            owner_id=owner_id,
            cursor=(datetime.now(UTC), ""),
            error=None,
            release=True,
            lease_seconds=lease_seconds,
        )
        return 0
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
        issues = tracker_cache.search_issue_page(
            token=token,
            query=_search_query(queues, cursor),
            limit=page_size,
            # The Tracker query asks for open tasks, but stale/local status
            # projection can still reject every row in a full raw page. Keep
            # raw rows so their keyset position advances the durable cursor.
            filter_open=False,
            order=["createdAt", "key"],
        )
        positioned = sorted(
            (position, issue)
            for issue in issues
            if (position := _issue_position(issue)) is not None and (cursor is None or position > cursor)
        )
        for position, issue in positioned:
            if monotonic() >= deadline:
                break
            if not _update_state(
                session_factory,
                owner_id=owner_id,
                error=None,
                lease_seconds=lease_seconds,
            ):
                break
            if tracker_filters.issue_status_bucket(issue) in {"new", "queued"}:
                with session_factory() as db:
                    park = _resolve_active_park(db, issue)
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
            if not _update_state(
                session_factory,
                owner_id=owner_id,
                cursor=cursor,
                error=None,
                lease_seconds=lease_seconds,
            ):
                break
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
    poll_deadline_seconds: float,
    max_operation_seconds: float,
) -> None:
    del interval_seconds  # Kept for configuration compatibility; scheduling is adaptive.
    owner_id = str(uuid4())
    failures = 0
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tracker-notification-poll")
    loop = asyncio.get_running_loop()
    try:
        while not stop_event.is_set():
            operation = partial(
                poll_tracker_notifications,
                session_factory,
                emit,
                page_size=page_size,
                owner_id=owner_id,
                lease_seconds=lease_seconds,
                poll_deadline_seconds=poll_deadline_seconds,
                max_operation_seconds=max_operation_seconds,
            )
            await loop.run_in_executor(executor, operation)
            await loop.run_in_executor(executor, partial(reconcile_closed_claims, session_factory))
            failed = await loop.run_in_executor(executor, partial(_poll_failed, session_factory))
            failures = failures + 1 if failed else 0
            active = await loop.run_in_executor(executor, partial(_active, session_factory))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    stop_event.wait(), timeout=poll_delay(active, failures).total_seconds()
                )
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
