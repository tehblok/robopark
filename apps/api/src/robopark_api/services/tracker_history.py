"""Persist only verified Tracker status transitions and their SLA anchor."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from concurrent.futures import wait
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session

from robopark_api.models import Park, TrackerIssueHistoryState, TrackerIssueStatusEvent
from robopark_api.services import tracker_client
from robopark_api.services.tracker_filters import status_bucket
from robopark_api.services.tracker_policy import issue_tags, park_tag_identity, park_tag_matches


def _timestamp(raw: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    # SQLite round-trips timezone-aware columns as naive UTC in local tests.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _state_insert(db: Session):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert


def _history_evidence(
    issue_key: str, history: list[dict], current_tags: set[str]
) -> tuple[
    list[tuple[str, datetime, str, str, str | None, frozenset[str] | None, int]],
    list[tuple[datetime, frozenset[str] | None]],
]:
    current_tags = {park_tag_identity(tag) for tag in current_tags}

    def tag_set(raw: object) -> frozenset[str] | None:
        if not isinstance(raw, list) or len(raw) > 256:
            return None
        values = []
        for item in raw:
            value = (
                item
                if isinstance(item, str)
                else (
                    item.get("name") or item.get("display") or item.get("key") or item.get("id")
                    if isinstance(item, dict)
                    else None
                )
            )
            if not isinstance(value, str) or not value.strip() or len(value) > 480:
                return None
            values.append(park_tag_identity(value))
        return frozenset(values)

    records: list[
        tuple[
            datetime,
            str | None,
            int,
            list[tuple[str, str, str | None]],
            tuple[frozenset[str] | None, frozenset[str] | None] | None,
        ]
    ] = []
    for index, item in enumerate(history):
        if (
            not isinstance(item, dict)
            or (at := _timestamp(item.get("updatedAt") or item.get("createdAt"))) is None
        ):
            continue
        statuses: list[tuple[str, str, str | None]] = []
        tag_change = None
        for change in item.get("fields") or []:
            if not isinstance(change, dict):
                continue
            field = change.get("field") or {}
            name = field.get("id") or field.get("key") if isinstance(field, dict) else field
            field_id = str(name or change.get("fieldId") or "").strip().lower()
            if field_id == "tags":
                if tag_change is not None:
                    tag_change = (None, None)
                else:
                    tag_change = (tag_set(change.get("from")), tag_set(change.get("to")))
                continue
            if field_id != "status":
                continue
            target = change.get("to") or change.get("newValue") or {}
            source = change.get("from") or {}
            key = str(target.get("key") or "").strip() if isinstance(target, dict) else ""
            display = (
                str(target.get("display") or "").strip()
                if isinstance(target, dict)
                else str(target)
            )
            from_key = str(source.get("key") or "").strip() if isinstance(source, dict) else ""
            if not key and not display:
                continue
            statuses.append((key, display, from_key or None))
        records.append((at, str(item.get("id") or "") or None, index, statuses, tag_change))
    records.sort(key=lambda row: (row[0], row[1] or "", row[2]))
    tags_at_record: dict[int, frozenset[str] | None] = {}
    tags: frozenset[str] | None = (
        frozenset(current_tags)
        if len(current_tags) <= 256 and all(0 < len(tag) <= 480 for tag in current_tags)
        else None
    )
    tag_change_counts: dict[datetime, int] = {}
    for at, _, _, _, change in records:
        if change is not None:
            tag_change_counts[at] = tag_change_counts.get(at, 0) + 1
    tag_change_times = set(tag_change_counts)
    assignments: list[tuple[datetime, frozenset[str] | None]] = []
    for at, _, index, _, change in reversed(records):
        tags_at_record[index] = None if at in tag_change_times else tags
        if change is not None:
            before, after = change
            verified = tag_change_counts[at] == 1 and after is not None and after == tags
            assignments.append((at, after if verified and before is not None else None))
            tags = before if verified else None

    candidates: list[
        tuple[datetime, str, str, str | None, str | None, int, frozenset[str] | None, int]
    ] = []
    for at, source_id, index, statuses, _ in records:
        for status_ordinal, (key, display, from_key) in enumerate(statuses):
            candidates.append(
                (
                    at,
                    key,
                    display,
                    from_key,
                    source_id,
                    status_ordinal,
                    tags_at_record[index],
                    index,
                )
            )
    candidates.sort(key=lambda row: (row[0], row[4] or "", row[5], row[1], row[2], row[3] or ""))
    counts: dict[str, int] = {}
    result = []
    for at, key, display, from_key, source_id, status_ordinal, tags, record_index in candidates:
        fingerprint = "\x00".join((issue_key, at.isoformat(), from_key or "", key, display))
        ordinal = counts.get(fingerprint, 0)
        counts[fingerprint] = ordinal + 1
        source_identity = f"source\x00{issue_key}\x00{source_id}"
        if status_ordinal:
            source_identity += f"\x00{status_ordinal}"
        event_key = hashlib.sha256(
            (source_identity if source_id else f"fallback\x00{fingerprint}\x00{ordinal}").encode()
        ).hexdigest()
        result.append((event_key, at, key, display, from_key, tags, record_index))
    return result, list(reversed(assignments))


def ingest_status_history(
    db: Session,
    *,
    issue_key: str,
    park: Park,
    history: list[dict],
    current_tags: set[str] | None = None,
    checked_at: datetime | None = None,
) -> TrackerIssueHistoryState:
    """A complete changelog read is idempotent; a reopen never resets its first queue."""
    if not issue_key or len(issue_key) > 128:
        raise ValueError("tracker_issue_key_invalid")
    events, assignments = _history_evidence(
        issue_key, history, current_tags if current_tags is not None else {park.tag}
    )
    event_tags = {tag for _, _, _, _, _, tags, _ in events if tags for tag in tags}
    event_tags.update(tag for _, tags in assignments if tags for tag in tags)
    # SQL lower() is not Unicode casefold (notably for Cyrillic on SQLite).
    # Restrict the scan to the ticket queue and compare the same Tracker identity.
    parks_by_tag: dict[str, list[Park]] = {}
    for item in db.scalars(select(Park).where(Park.tracker_queue == park.tracker_queue)):
        identity = park_tag_identity(item.tag)
        if identity in event_tags:
            parks_by_tag.setdefault(identity, []).append(item)

    def matching_parks(tags):
        return {item for tag in tags or () for item in parks_by_tag.get(tag, ())}

    def event_park(tags: frozenset[str] | None) -> Park | None:
        matches = matching_parks(tags)
        return next(iter(matches)) if len(matches) == 1 else None

    # API workers and the history worker can ingest the same ticket. Establish
    # its row atomically, then serialize projection updates before reading events.
    db.execute(
        _state_insert(db)(TrackerIssueHistoryState)
        .values(issue_key=issue_key, history_state="unknown")
        .on_conflict_do_nothing(index_elements=["issue_key"])
    )
    state = db.scalar(
        select(TrackerIssueHistoryState)
        .where(TrackerIssueHistoryState.issue_key == issue_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    state.observed_park_id = park.id
    known = {
        row.event_key: row
        for row in db.scalars(
            select(TrackerIssueStatusEvent).where(TrackerIssueStatusEvent.issue_key == issue_key)
        ).all()
    }
    for event_key, at, key, display, from_key, tags, _ in events:
        attributed = event_park(tags)
        if event_key in known:
            known[event_key].park_id = attributed.id if attributed else None
            continue
        db.add(
            TrackerIssueStatusEvent(
                issue_key=issue_key,
                event_key=event_key,
                park_id=attributed.id if attributed else None,
                occurred_at=at,
                from_status_key=from_key,
                to_status_key=key,
                to_status_display=display,
            )
        )
    queued = [event for event in events if status_bucket(event[2], event[3]) == "queued"]
    first = min((event[1] for event in queued), default=None)
    existing = state.first_queued_at
    earlier_queue_found = first is not None and (existing is None or first < _stored_utc(existing))
    if earlier_queue_found:
        state.first_queued_at = first
    if (
        first is not None
        and state.first_queued_at is not None
        and first == _stored_utc(state.first_queued_at)
    ):
        anchor_parks = {event_park(event[5]) for event in queued if event[1] == first}
        anchor = next(iter(anchor_parks)) if len(anchor_parks) == 1 else None
        queue_tags = [event[5] for event in queued if event[1] == first]
        ambiguous_queue_park = any(len(matching_parks(tags)) > 1 for tags in queue_tags)
        if anchor is None and not ambiguous_queue_park:
            # A late park assignment chooses the timezone, never a new queue start.
            # Stop at uncertain evidence instead of silently choosing a later transfer.
            for at, tags in assignments:
                if at <= first:
                    continue
                matches = matching_parks(tags)
                if tags is None or len(matches) > 1:
                    break
                if matches:
                    anchor = next(iter(matches))
                    break
        anchor_id = anchor.id if anchor else None
        if earlier_queue_found or state.anchor_park_id is None:
            state.anchor_park_id = anchor_id
            state.anchor_timezone = anchor.timezone if anchor else None
        elif (
            state.anchor_timezone is None
            and anchor is not None
            and state.anchor_park_id == anchor_id
        ):
            state.anchor_timezone = anchor.timezone
    if events:
        _, at, key, display, _, _, _ = events[-1]
        previous_at = state.latest_status_at
        if previous_at is None or at >= _stored_utc(previous_at):
            ambiguous = len({event[6] for event in events if event[1] == at}) > 1
            state.latest_status_key = None if ambiguous else key or display
            state.latest_status_at = at
            state.terminal_at = (
                at
                if not ambiguous
                and not tracker_client.is_issue_open_item({"status_key": key, "status": display})
                else None
            )
    state.history_state = "complete" if state.first_queued_at is not None else "no_queue"
    state.history_checked_at = checked_at or datetime.now(UTC)
    db.commit()
    return state


def attach_verified_history(db: Session, issues: list[dict]) -> list[dict]:
    """Add only proven queue anchors to scoped Tracker cards in one DB query."""
    keys = {str(issue.get("key") or "") for issue in issues}
    if not keys:
        return list(issues)
    states = {}
    ordered_keys = sorted(keys)
    for start in range(0, len(ordered_keys), 500):
        for row in db.scalars(
            select(TrackerIssueHistoryState).where(
                TrackerIssueHistoryState.issue_key.in_(ordered_keys[start : start + 500])
            )
        ):
            states[row.issue_key] = row
    result = []
    for issue in issues:
        state = states.get(str(issue.get("key") or ""))
        if state is None or state.first_queued_at is None:
            result.append(issue)
            continue
        result.append(
            {
                **issue,
                "queued_at": _stored_utc(state.first_queued_at).isoformat().replace("+00:00", "Z"),
                "sla_source": "status_history",
                "sla_anchor_timezone": state.anchor_timezone,
            }
        )
    return result


HISTORY_REQUEST_BUDGET_SECONDS = 0.25
HISTORY_STARTS_PER_REQUEST = 2
logger = logging.getLogger(__name__)


def current_issue_park(issue: dict, parks: list[Park], selected_tag: str | None = None):
    matches = [
        park
        for park in parks
        if park.tracker_queue == issue.get("queue")
        and park_tag_matches(park.tag, issue_tags(issue))
        and (not selected_tag or park_tag_matches(park.tag, {selected_tag}))
    ]
    return matches[0] if len(matches) == 1 else None


def register_pending_history(db: Session, issues: list[dict], parks: list[Park], selected_tag=None):
    """Durable discovery queue shared by all users; it stores no credentials or ACLs."""
    rows = {}
    queued_keys = []
    changed_keys = {}
    for issue in issues:
        if issue.get("sla_source") == "status_history" and issue.get("sla_anchor_timezone"):
            continue
        park = current_issue_park(issue, parks, selected_tag)
        key = str(issue.get("key") or "")
        if park and key and len(key) <= 128:
            rows[key] = {"issue_key": key, "observed_park_id": park.id, "history_state": "unknown"}
            if status_bucket(issue.get("status_key", ""), issue.get("status", "")) == "queued":
                queued_keys.append(key)
            updated = _timestamp(issue.get("updated") or issue.get("updatedAt"))
            if updated is not None:
                changed_keys[key] = updated
    if not rows:
        return
    insert = _state_insert(db)
    # PostgreSQL locks conflicting rows even when the UPDATE WHERE is false.
    # Every request must acquire them in the same order across UI sort modes.
    rows = [rows[key] for key in sorted(rows)]
    for offset in range(0, len(rows), 500):
        batch = rows[offset : offset + 500]
        batch_keys = {row["issue_key"] for row in batch}
        reactivated = and_(
            TrackerIssueHistoryState.history_state == "no_queue",
            TrackerIssueHistoryState.first_queued_at.is_(None),
            or_(
                TrackerIssueHistoryState.issue_key.in_(batch_keys.intersection(queued_keys)),
                *(
                    and_(
                        TrackerIssueHistoryState.issue_key == key,
                        TrackerIssueHistoryState.history_checked_at < updated,
                    )
                    for key, updated in changed_keys.items()
                    if key in batch_keys
                ),
            ),
        )
        statement = insert(TrackerIssueHistoryState).values(batch)
        db.execute(
            statement.on_conflict_do_update(
                index_elements=["issue_key"],
                set_={
                    "observed_park_id": statement.excluded.observed_park_id,
                    "history_state": case(
                        (reactivated, "unknown"), else_=TrackerIssueHistoryState.history_state
                    ),
                    "history_checked_at": case(
                        (reactivated, None), else_=TrackerIssueHistoryState.history_checked_at
                    ),
                },
                where=or_(
                    TrackerIssueHistoryState.observed_park_id.is_distinct_from(
                        statement.excluded.observed_park_id
                    ),
                    reactivated,
                ),
            )
        )
    db.commit()


def hydrate_visible_history(
    db: Session,
    *,
    token: str,
    issues: list[dict],
    parks: list[Park],
    selected_tag=None,
    budget_seconds=HISTORY_REQUEST_BUDGET_SECONDS,
    max_starts=HISTORY_STARTS_PER_REQUEST,
):
    """Use durable anchors first; missing reads share the bounded Tracker executor."""
    deadline = time.monotonic() + budget_seconds
    verified = attach_verified_history(db, issues)
    register_pending_history(db, verified, parks, selected_tag)
    pending = []
    histories = {}
    starts = 0
    for index, issue in enumerate(verified):
        if issue.get("sla_source") == "status_history" and issue.get("sla_anchor_timezone"):
            continue
        embedded = issue.get("status_history")
        if isinstance(embedded, list) and embedded:
            histories[index] = embedded
            continue
        future, started = tracker_client.schedule_issue_status_history(
            token=token,
            key=str(issue.get("key") or ""),
            issue=issue,
            allow_start=starts < max_starts,
        )
        starts += int(started)
        if future is not None:
            pending.append((index, future))
    ready, _ = (
        wait({future for _, future in pending}, timeout=max(0, deadline - time.monotonic()))
        if pending
        else (set(), set())
    )
    for index, future in pending:
        if future in ready:
            history = future.result()
            # The bounded loader returns [] on an upstream failure. Never mark
            # that as complete evidence or overwrite an existing queue anchor.
            if history:
                histories[index] = history
    projected = list(verified)
    for index, history in histories.items():
        issue = verified[index]
        park = current_issue_park(issue, parks, selected_tag)
        if park is not None:
            ingest_status_history(
                db,
                issue_key=str(issue.get("key") or ""),
                park=park,
                history=history,
                current_tags=issue_tags(issue),
            )
        else:
            projected[index] = {
                **issue,
                **tracker_client.repair_sla_fields(issue, status_history=history),
            }
    return attach_verified_history(db, projected)


def drain_pending_history(db: Session, *, now: datetime | None = None, limit: int = 2):
    """Two pending ticket histories per worker pass, with retry cooldown and no park scan."""
    from robopark_api.services import platform_settings, tracker_cache

    now = now or datetime.now(UTC)
    token = platform_settings.get_tracker_token(db)
    if not token:
        return 0
    candidates = db.scalars(
        select(TrackerIssueHistoryState)
        .where(
            TrackerIssueHistoryState.observed_park_id.is_not(None),
            (
                TrackerIssueHistoryState.first_queued_at.is_(None)
                | TrackerIssueHistoryState.anchor_timezone.is_(None)
            ),
            # A negative changelog read is not a permanent source watermark:
            # the issue cache can lag an externally performed queue transition.
            # Recheck it after the same bounded cooldown instead of starving it.
            or_(
                TrackerIssueHistoryState.history_checked_at.is_(None),
                and_(
                    TrackerIssueHistoryState.history_state == "forbidden",
                    TrackerIssueHistoryState.history_checked_at <= now - timedelta(days=1),
                ),
                and_(
                    TrackerIssueHistoryState.history_state != "forbidden",
                    TrackerIssueHistoryState.history_checked_at <= now - timedelta(minutes=2),
                ),
            ),
        )
        .order_by(
            TrackerIssueHistoryState.history_checked_at.asc().nulls_first(),
            TrackerIssueHistoryState.issue_key,
        )
        .limit(min(max(limit, 0), 2))
    ).all()
    parks = list(db.scalars(select(Park).where(Park.is_active.is_(True))).all())
    processed = 0
    for state in candidates:
        checked = _stored_utc(state.history_checked_at) if state.history_checked_at else None
        cooldown = timedelta(days=1) if state.history_state == "forbidden" else timedelta(minutes=2)
        if checked and now - checked < cooldown:
            continue
        if processed >= min(max(limit, 0), 2):
            break
        processed += 1
        try:
            issue = tracker_cache.get_issue(token=token, key=state.issue_key)
            park = current_issue_park(issue or {}, parks)
            if park is None:
                state.history_state = "retry"
                state.history_checked_at = now
                db.commit()
                continue
            history = tracker_client.get_issue_status_history(
                token=token, key=state.issue_key, issue=issue
            )
            ingest_status_history(
                db,
                issue_key=state.issue_key,
                park=park,
                history=history,
                current_tags=issue_tags(issue),
                checked_at=now,
            )
        except tracker_client.TrackerError as exc:
            db.rollback()
            state = db.get(TrackerIssueHistoryState, state.issue_key)
            state.history_state = (
                "forbidden"
                if re.search(r"\b(?:401|403)\b|forbidden|unauthorized", str(exc), re.I)
                else "retry"
            )
            state.history_checked_at = now
            db.commit()
            logger.warning(
                "SLA history read deferred for %s (%s)", state.issue_key, state.history_state
            )
    return processed
