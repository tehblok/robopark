"""Immutable observed state, collected independently from cumulative flow counters."""

import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, delete, or_, select
from sqlalchemy.orm import Session

from robopark_api.models import (
    AnalyticsObservation,
    AnalyticsSnapshot,
    Park,
    TrackerHistoryBackfillCursor,
    TrackerIssueHistoryState,
)
from robopark_api.services import platform_settings, sla_clock, tracker_client, tracker_history
from robopark_api.services.blocker_history import align_bucket_start
from robopark_api.services.operations import age_hours, as_utc
from robopark_api.services.tracker_filters import issue_status_bucket, park_priority_type
from robopark_api.services.tracker_policy import (
    issue_authorization_status,
    issue_tags,
    park_tag_matches,
)

logger = logging.getLogger(__name__)
MAX_HISTORY_READS_PER_SCAN = 2
MISSING_HISTORY_RECHECK = timedelta(hours=6)
BACKFILL_LOOKBACK = timedelta(days=30)
MAX_BACKFILL_PAGES = 200  # Tracker query pagination stops before 10,000 results.


def _establish_history_states(
    db: Session, observed_parks: dict[str, int]
) -> dict[str, TrackerIssueHistoryState]:
    """Atomically establish shared projection rows without replacing verified fields."""
    rows = [
        {"issue_key": key, "observed_park_id": observed_parks[key], "history_state": "unknown"}
        for key in sorted(observed_parks)
        if key and len(key) <= 128
    ]
    if not rows:
        return {}
    statement = tracker_history._state_insert(db)(TrackerIssueHistoryState).values(rows)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["issue_key"],
            set_={"observed_park_id": statement.excluded.observed_park_id},
            where=TrackerIssueHistoryState.observed_park_id.is_distinct_from(
                statement.excluded.observed_park_id
            ),
        )
    )
    db.commit()
    keys = [row["issue_key"] for row in rows]
    return {
        state.issue_key: state
        for state in db.scalars(
            select(TrackerIssueHistoryState)
            .where(TrackerIssueHistoryState.issue_key.in_(keys))
            .execution_options(populate_existing=True)
        ).all()
    }


def _closed_status(item: dict) -> bool:
    return any(
        str(item.get(field) or "").strip().lower().replace("ё", "е")
        in {"closed", "resolved", "закрыт", "закрыта", "закрыто", "решен", "решена", "решено"}
        for field in ("status_key", "status")
    )


def _discover_closed_history(
    db: Session,
    *,
    token: str,
    parks: dict[int, Park],
    candidates: dict[int, tuple[Park, list[tuple[datetime, str, dict]]]],
    now: datetime,
) -> None:
    """Read one resumable search page globally, sharing the existing changelog budget."""
    if not parks:
        return
    cursors = {
        row.park_id: row
        for row in db.scalars(
            select(TrackerHistoryBackfillCursor).where(
                TrackerHistoryBackfillCursor.park_id.in_(parks)
            )
        ).all()
    }
    park_id = min(
        parks,
        key=lambda key: (
            as_utc(cursors[key].last_attempt_at)
            if key in cursors and cursors[key].last_attempt_at
            else datetime.min.replace(tzinfo=UTC),
            key,
        ),
    )
    park = parks[park_id]
    cursor = cursors.get(park_id)
    if cursor is None:
        cursor = TrackerHistoryBackfillCursor(
            park_id=park_id,
            scan_since=now - BACKFILL_LOOKBACK,
            scan_until=now,
            next_page=1,
        )
        db.add(cursor)
    cursor.last_attempt_at = now
    db.commit()
    window_since = as_utc(cursor.scan_since)
    window_until = as_utc(cursor.scan_until)
    priority, issue_type = park_priority_type(park)
    query = tracker_client.build_closed_blockers_query(
        park.tracker_queue,
        park.tag,
        priority=priority,
        issue_type=issue_type,
        since=window_since,
        until=window_until,
    )
    try:
        items = tracker_client.search_closed_history_page(
            token=token,
            query=query,
            page=cursor.next_page,
        )
    except tracker_client.TrackerError:
        cursor.search_failed = True
        db.commit()
        logger.warning(
            "Closed Tracker history discovery failed for park %s", park_id, exc_info=True
        )
        return
    if any(
        item.get("queue") == park.tracker_queue
        and park_tag_matches(park.tag, issue_tags(item))
        and _closed_status(item)
        and _issue_updated(item) is None
        for item in items
    ):
        cursor.search_failed = True
        db.commit()
        logger.warning("Closed Tracker history page lacks update time for park %s", park_id)
        return
    cursor.search_failed = False
    cursor.next_page += 1
    if len(items) < tracker_client.API_PAGE_SIZE or cursor.next_page > MAX_BACKFILL_PAGES:
        if cursor.next_page > MAX_BACKFILL_PAGES:
            cursor.page_cap_reached = True
            logger.warning("Closed Tracker history search page cap reached for park %s", park_id)
        else:
            cursor.page_cap_reached = False
        cursor.scan_since = now - BACKFILL_LOOKBACK
        cursor.scan_until = now
        cursor.next_page = 1
    eligible = {
        str(item.get("key") or "").strip(): item
        for item in items
        if item.get("queue") == park.tracker_queue
        and park_tag_matches(park.tag, issue_tags(item))
        and _closed_status(item)
        and (updated := _issue_updated(item)) is not None
        and window_since <= updated <= window_until
    }
    if eligible:
        states = _establish_history_states(db, dict.fromkeys(eligible, park_id))
        pending = candidates.setdefault(park_id, (park, []))[1]
        scheduled = {key for _, key, _ in pending}
        for key, item in eligible.items():
            if not key or len(key) > 128 or key in scheduled:
                continue
            state = states.get(key)
            if state is None:
                continue
            checked = (
                as_utc(state.history_checked_at)
                if state.history_checked_at
                else datetime.min.replace(tzinfo=UTC)
            )
            updated = _issue_updated(item)
            if state.history_state == "forbidden" and checked > now - timedelta(days=1):
                continue
            if state.history_state in {"unknown", "retry", "forbidden"} or (
                updated and updated > checked
            ):
                pending.append((checked, key, item))
    db.commit()


def _issue_updated(issue: dict) -> datetime | None:
    raw = issue.get("updated") or issue.get("updatedAt")
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else None


def record_observation(
    db: Session,
    *,
    park: Park,
    issues: list[dict],
    observed_at: datetime,
    target_hours: int | None,
) -> AnalyticsSnapshot:
    observed_at = as_utc(observed_at)
    bucket = align_bucket_start(observed_at)
    existing = db.get(AnalyticsSnapshot, (park.id, bucket))
    if existing is not None:
        return existing
    snapshot = AnalyticsSnapshot(
        park_id=park.id, bucket_start=bucket, observed_at=observed_at, target_hours=target_hours
    )
    db.add(snapshot)
    db.flush()
    seen = set()
    for item in issues:
        key = str(item.get("key") or "").strip()
        if (
            not key
            or key in seen
            or item.get("queue") != park.tracker_queue
            or not park_tag_matches(park.tag, issue_tags(item))
        ):
            continue
        seen.add(key)
        status = str(item.get("status_key") or item.get("status") or "unknown")
        db.add(
            AnalyticsObservation(
                park_id=park.id,
                bucket_start=bucket,
                issue_key=key,
                status=status,
                status_bucket=issue_status_bucket(item),
                authorization_status=issue_authorization_status(item),
                age_hours=age_hours(item, observed_at),
            )
        )
    db.commit()
    return snapshot


def scan_all_parks_once(db: Session, *, now: datetime | None = None) -> int:
    # Retention is independent of collection: commit before checking credentials
    # or contacting Tracker so an unavailable source cannot extend retention.
    # Explicit child deletion also supports SQLite without FK enforcement.
    cutoff = align_bucket_start(now or datetime.now(UTC)) - timedelta(days=30)
    db.execute(delete(AnalyticsObservation).where(AnalyticsObservation.bucket_start < cutoff))
    db.execute(delete(AnalyticsSnapshot).where(AnalyticsSnapshot.bucket_start < cutoff))
    db.commit()
    token = platform_settings.get_tracker_token(db)
    if not token:
        return 0
    scanned = 0
    candidates: dict[int, tuple[Park, list[tuple[datetime, str, dict]]]] = {}
    eligible_parks: dict[int, Park] = {}
    for park in db.scalars(select(Park).where(Park.is_active.is_(True))).all():
        if not park.feature_blockers or not park.tracker_queue or not park.tag:
            continue
        if db.get(AnalyticsSnapshot, (park.id, align_bucket_start(now or datetime.now(UTC)))):
            continue
        priority, issue_type = park_priority_type(park)
        try:
            issues = tracker_client.fetch_park_blockers(
                token=token,
                queue=park.tracker_queue,
                park_tag=park.tag,
                priority=priority,
                issue_type=issue_type,
            )
            current_bucket = align_bucket_start(now or datetime.now(UTC))
            previous_bucket = db.scalar(
                select(AnalyticsSnapshot.bucket_start)
                .where(
                    AnalyticsSnapshot.park_id == park.id,
                    AnalyticsSnapshot.bucket_start < current_bucket,
                )
                .order_by(AnalyticsSnapshot.bucket_start.desc())
                .limit(1)
            )
            previously_live = (
                set(
                    db.scalars(
                        select(AnalyticsObservation.issue_key).where(
                            AnalyticsObservation.park_id == park.id,
                            AnalyticsObservation.bucket_start == previous_bucket,
                        )
                    ).all()
                )
                if previous_bucket is not None
                else set()
            )
            record_observation(
                db,
                park=park,
                issues=issues,
                observed_at=now or datetime.now(UTC),
                target_hours=sla_clock.SLA_TARGET_HOURS,
            )
            live = {
                str(item.get("key") or "").strip(): item
                for item in issues
                if item.get("key")
                and len(str(item.get("key") or "").strip()) <= 128
                and item.get("queue") == park.tracker_queue
                and park_tag_matches(park.tag, issue_tags(item))
            }
            states = _establish_history_states(db, dict.fromkeys(live, park.id))
            pending = []
            for key, item in live.items():
                state = states.get(key)
                checked = (
                    as_utc(state.history_checked_at)
                    if state and state.history_checked_at
                    else datetime.min.replace(tzinfo=UTC)
                )
                updated = _issue_updated(item)
                retry_denied = state.history_state == "forbidden" and checked <= as_utc(
                    now or datetime.now(UTC)
                ) - timedelta(days=1)
                if (
                    state.history_state in {"unknown", "retry"}
                    or retry_denied
                    or (updated and updated > checked)
                ):
                    pending.append((checked, key, item))
            missing = db.scalars(
                select(TrackerIssueHistoryState)
                .where(
                    TrackerIssueHistoryState.observed_park_id == park.id,
                    TrackerIssueHistoryState.terminal_at.is_(None),
                    or_(
                        TrackerIssueHistoryState.history_checked_at.is_(None),
                        TrackerIssueHistoryState.issue_key.in_(previously_live),
                        and_(
                            TrackerIssueHistoryState.history_state == "forbidden",
                            TrackerIssueHistoryState.history_checked_at
                            <= as_utc(now or datetime.now(UTC)) - timedelta(days=1),
                        ),
                        and_(
                            TrackerIssueHistoryState.history_state != "forbidden",
                            TrackerIssueHistoryState.history_checked_at
                            <= as_utc(now or datetime.now(UTC)) - MISSING_HISTORY_RECHECK,
                        ),
                    ),
                )
                .order_by(
                    case((TrackerIssueHistoryState.issue_key.in_(previously_live), 0), else_=1),
                    TrackerIssueHistoryState.history_checked_at,
                )
                .limit(200)
            ).all()
            for state in missing:
                if state.issue_key not in live:
                    checked = (
                        as_utc(state.history_checked_at)
                        if state.history_checked_at
                        else datetime.min.replace(tzinfo=UTC)
                    )
                    pending.append((checked, state.issue_key, {}))
            pending.sort(
                key=lambda row: (row[1] not in live, row[1] not in previously_live, row[0], row[1])
            )
            if pending:
                candidates[park.id] = (park, pending)
        except tracker_client.TrackerError:
            logger.warning("Analytics observation failed for park %s", park.id, exc_info=True)
            continue
        eligible_parks[park.id] = park
        scanned += 1
    _discover_closed_history(
        db,
        token=token,
        parks=eligible_parks,
        candidates=candidates,
        now=as_utc(now or datetime.now(UTC)),
    )
    # One already-fetched park list discovers candidates. Round-robin prevents
    # a busy park from monopolising the two extra changelog reads per cycle.
    used_parks: set[int] = set()
    for _ in range(MAX_HISTORY_READS_PER_SCAN):
        active = [(park_id, entry) for park_id, entry in candidates.items() if entry[1]]
        if not active:
            break
        fair = [entry for entry in active if entry[0] not in used_parks] or active
        park_id, (park, pending) = min(fair, key=lambda entry: (entry[1][1][0][0], entry[0]))
        used_parks.add(park_id)
        _checked, key, issue = pending.pop(0)
        try:
            if not issue:
                issue = tracker_client.get_issue(token=token, key=key)
                if issue is None:
                    raise tracker_client.TrackerError("history_issue_not_found")
            history = tracker_client.get_issue_status_history(token=token, key=key, issue=issue)
            tracker_history.ingest_status_history(
                db,
                issue_key=key,
                park=park,
                history=history,
                current_tags=issue_tags(issue),
                checked_at=now or datetime.now(UTC),
            )
        except tracker_client.TrackerError as exc:
            db.rollback()
            state = _establish_history_states(db, {key: park_id})[key]
            state.history_state = (
                "forbidden"
                if re.search(r"\b(?:401|403)\b|forbidden|unauthorized", str(exc), re.IGNORECASE)
                else "retry"
            )
            state.history_checked_at = now or datetime.now(UTC)
            db.commit()
            logger.warning("Tracker status history %s for %s", state.history_state, key)
    return scanned
