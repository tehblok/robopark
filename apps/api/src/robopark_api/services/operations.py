"""Current scoped load and observed flow; no inference of historical snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import Park, ParkBlockerHistory, Role, User, UserPark
from robopark_api.operations_schemas import (
    FlowOut,
    FlowPointOut,
    OperationsOverviewOut,
    OperatorLoadOut,
    OverdueTaskOut,
    SlaOut,
    StatusOptionOut,
    TaskTimingOut,
    WorkloadOut,
)
from robopark_api.routers._blockers import blocker_out
from robopark_api.services import (
    platform_settings,
    rbac,
    sla_clock,
    tracker_cache,
    tracker_client,
    tracker_history,
)
from robopark_api.services.blocker_history import BUCKET_SECONDS, align_bucket_start
from robopark_api.services.tracker_claims import local_assignees
from robopark_api.services.tracker_filters import (
    count_status_buckets,
    filter_issues_by_status,
    issue_status_bucket,
    park_priority_type,
    sort_issues_oldest_first,
)
from robopark_api.services.tracker_policy import (
    is_issue_status_visible,
    issue_tags,
    park_tag_matches,
)

TASK_LIMIT = 200
STATUS_LABELS = {
    "all": "Все",
    "new": "Новые",
    "moving": "Перемещение",
    "queued": "В очереди",
    "diagnostics": "Диагностика",
    "waiting_team": "Ждём смежников",
    "waiting_parts": "Ожидание поставки",
    "other": "Другие",
}
ROLE_STATUSES = {"driver": {"new", "moving"}}
LEADERSHIP = {rbac.RoleSlug.OPERATOR, rbac.RoleSlug.ADMIN, rbac.RoleSlug.ROYAL}


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def age_hours(item: dict, now: datetime) -> float | None:
    """UTC calendar hours from creation, never the cached rounded Tracker age."""
    raw = item.get("created")
    if not raw:
        return None
    try:
        created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    # Tracker provides an offset. A bare timestamp cannot establish elapsed UTC.
    if created.tzinfo is None:
        return None
    hours = (as_utc(now) - as_utc(created)).total_seconds() / 3600
    return hours if hours >= 0 else None


def _sla_timezone(item: dict, fallback: str) -> str | None:
    # An explicitly unknown historical park must not inherit today's park zone.
    value = item.get("sla_anchor_timezone") if "sla_anchor_timezone" in item else fallback
    return str(value) if value else None


def queued_working_hours(item: dict, now: datetime, *, timezone: str) -> float | None:
    """Working hours since queue entry, through later nonterminal statuses."""
    raw = tracker_client.repair_sla_fields(item)["queued_at"]
    if not raw:
        return None
    try:
        created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if created.tzinfo is None:
        return None
    if as_utc(now) < as_utc(created):
        return None
    anchor_timezone = _sla_timezone(item, timezone)
    if anchor_timezone is None:
        return None
    try:
        return sla_clock.elapsed_working_hours(created, now, timezone=anchor_timezone)
    except (ValueError, ZoneInfoNotFoundError):
        return None


def task_timing(item: dict, now: datetime, *, timezone: str) -> TaskTimingOut:
    raw = tracker_client.repair_sla_fields(item)["queued_at"]
    queue_started_at = None
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is not None and as_utc(parsed) <= now:
                queue_started_at = as_utc(parsed)
        except (TypeError, ValueError):
            pass
    anchor_timezone = _sla_timezone(item, timezone)
    sla_deadline = sla_working_hours = None
    if queue_started_at and anchor_timezone:
        try:
            sla_deadline = sla_clock.deadline(queue_started_at, timezone=anchor_timezone)
            sla_working_hours = sla_clock.elapsed_working_hours(
                queue_started_at, now, timezone=anchor_timezone
            )
        except (ValueError, ZoneInfoNotFoundError):
            sla_deadline = sla_working_hours = None
    return TaskTimingOut(
        issue_key=str(item.get("key") or ""),
        queue_started_at=queue_started_at,
        sla_deadline=sla_deadline,
        sla_working_hours=sla_working_hours,
        sla_timezone=anchor_timezone if sla_deadline is not None else None,
        downtime_hours=(now - queue_started_at).total_seconds() / 3600
        if queue_started_at
        else None,
    )


def _is_sla_overdue(
    item: dict, *, age: float, target_hours: int, now: datetime, timezone: str
) -> bool:
    if age != target_hours:
        return age > target_hours
    # At a 21:00 deadline the working clock remains exactly at five hours
    # overnight, while the absolute due time has already passed.
    due_at = task_timing(item, now, timezone=timezone).sla_deadline
    return due_at is not None and as_utc(now) > as_utc(due_at)


def calculate_sla(
    items: list[dict], *, target_hours: int | None, now: datetime, timezone: str
) -> SlaOut:
    if target_hours is None:
        return SlaOut(
            target_hours=None,
            evaluated_count=0,
            unknown_count=len(items),
            at_risk_count=None,
            overdue_count=None,
            overdue=[],
            overdue_truncated=False,
        )
    evaluated = 0
    at_risk = 0
    overdue: list[tuple[dict, float]] = []
    for item in items:
        age = queued_working_hours(item, now, timezone=timezone)
        if age is None:
            continue
        evaluated += 1
        if _is_sla_overdue(item, age=age, target_hours=target_hours, now=now, timezone=timezone):
            overdue.append((item, age))
        elif age >= target_hours * 0.8:
            at_risk += 1
    overdue.sort(key=lambda pair: (-pair[1], str(pair[0].get("key") or "")))
    return SlaOut(
        target_hours=target_hours,
        evaluated_count=evaluated,
        unknown_count=len(items) - evaluated,
        at_risk_count=at_risk,
        overdue_count=len(overdue),
        overdue_truncated=len(overdue) > TASK_LIMIT,
        overdue=[
            OverdueTaskOut(
                **blocker_out(item).model_dump(), age_hours=age, overdue_hours=age - target_hours
            )
            for item, age in overdue[:TASK_LIMIT]
        ],
    )


def calculate_workload(
    items: list[dict], *, target_hours: int | None, now: datetime, timezone: str
) -> list[WorkloadOut]:
    grouped: dict[tuple[str | None, str], list[dict]] = {}
    for item in items:
        assignee = item.get("assignee") or {}
        login = str(assignee.get("login") or "").strip() or None
        display = str(assignee.get("display") or "").strip() or login or "Без ответственного"
        # Login is the identity; differing Tracker display names must not split it.
        identity = (login, "" if login else display)
        grouped.setdefault(identity, []).append(item)
    result = []
    for (login, fallback), group in grouped.items():
        display = str((group[0].get("assignee") or {}).get("display") or login or fallback)
        ages = [age for item in group if (age := age_hours(item, now)) is not None]
        sla_ages = [
            (item, age)
            for item in group
            if (age := queued_working_hours(item, now, timezone=timezone)) is not None
        ]
        result.append(
            WorkloadOut(
                login=login,
                display=display,
                open_count=len(group),
                overdue_count=sum(
                    _is_sla_overdue(
                        item,
                        age=age,
                        target_hours=target_hours,
                        now=now,
                        timezone=timezone,
                    )
                    for item, age in sla_ages
                )
                if target_hours is not None
                else None,
                oldest_hours=max(ages) if ages else None,
            )
        )
    return sorted(result, key=lambda row: (-row.open_count, row.login or "", row.display))


def flow_history(db: Session, *, park_id: int, days: int, now: datetime) -> FlowOut:
    end = align_bucket_start(now)
    start = end - timedelta(days=days)
    rows = db.scalars(
        select(ParkBlockerHistory)
        .where(
            ParkBlockerHistory.park_id == park_id,
            ParkBlockerHistory.bucket_start >= start,
            ParkBlockerHistory.bucket_start < end,
        )
        .order_by(ParkBlockerHistory.bucket_start)
    ).all()
    observed = [
        row
        for row in rows
        if row.definition_version == 2
        and row.scanned_at is not None
        and as_utc(row.bucket_start) == align_bucket_start(row.bucket_start)
    ]
    expected = int((end - start).total_seconds() / BUCKET_SECONDS)
    return FlowOut(
        window_start=start,
        window_end=end,
        expected_buckets=expected,
        observed_buckets=len(observed),
        complete=len(observed) == expected,
        legacy_buckets=sum(row.definition_version == 1 for row in rows),
        points=[
            FlowPointOut(
                bucket_start=as_utc(row.bucket_start),
                arrived_count=row.arrived_count,
                departed_count=row.departed_count,
            )
            for row in observed
        ],
    )


def require_operations_park(db: Session, user: User, park_id: int) -> Park:
    rbac.assert_approved(user)
    park = db.get(Park, park_id)
    if park is None:
        raise HTTPException(status_code=404, detail="park_not_found")
    if not park.is_active:
        raise HTTPException(status_code=403, detail="park_inactive")
    if not rbac.is_admin_or_royal(user) and db.get(UserPark, (user.id, park_id)) is None:
        raise HTTPException(status_code=403, detail="park_forbidden")
    return park


def require_operations_read(db: Session, user: User) -> None:
    rbac.assert_approved(user)
    permissions = rbac.permissions_for_user(db, user)
    sections = {rbac.PERMISSION_NAV_DASHBOARD, rbac.PERMISSION_NAV_ANALYTICS}
    if not permissions & sections or rbac.PERMISSION_TRACKER_READ not in permissions:
        raise HTTPException(status_code=403)


def operator_loads(
    db: Session, park_id: int, workload: list[WorkloadOut], target_hours: int | None
) -> list[OperatorLoadOut]:
    users = db.scalars(
        select(User)
        .join(Role)
        .join(UserPark)
        .where(
            Role.slug == rbac.RoleSlug.OPERATOR,
            User.is_active.is_(True),
            User.access_status == "approved",
            UserPark.park_id == park_id,
        )
        .order_by(User.username, User.id)
    ).all()
    by_login = {row.login: row for row in workload if row.login}
    result = []
    for user in users:
        login = (user.tracker_login or "").strip() or None
        load = by_login.get(login)
        result.append(
            OperatorLoadOut(
                user_id=user.id,
                username=user.username,
                tracker_login=login,
                open_count=load.open_count if load else (0 if login else None),
                overdue_count=(load.overdue_count if load else 0)
                if login and target_hours is not None
                else None,
                oldest_hours=load.oldest_hours if load else None,
            )
        )
    return result


def build_overview(
    db: Session,
    user: User,
    park: Park,
    *,
    days: int,
    selected_status: str,
    now: datetime | None = None,
) -> OperationsOverviewOut:
    now = as_utc(now or datetime.now(UTC))
    allowed = ROLE_STATUSES.get(user.role, set(STATUS_LABELS) - {"all"})
    if selected_status not in STATUS_LABELS:
        raise HTTPException(status_code=400, detail="invalid_status")
    if selected_status != "all" and selected_status not in allowed:
        raise HTTPException(status_code=403, detail="operations_status_forbidden")
    if not park.feature_blockers or not park.tracker_queue or not park.tag:
        raise HTTPException(status_code=409, detail="blockers_disabled_for_park")
    token = platform_settings.get_tracker_token(db)
    if not token:
        raise HTTPException(status_code=503, detail="tracker_token_not_configured")
    priority, issue_type = park_priority_type(park)
    try:
        snapshot = tracker_cache.fetch_park_blockers(
            token=token,
            queue=park.tracker_queue,
            park_tag=park.tag,
            priority=priority,
            issue_type=issue_type,
            max_age_seconds=5.0,
            allow_stale=False,
        )
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=502, detail="tracker_upstream_error") from exc
    # Cache contains the park source, not a principal's authorization decision.
    # The client paginates the complete source or raises; no source hard cap.
    items = [
        item
        for item in snapshot
        if item.get("queue") == park.tracker_queue
        and park_tag_matches(park.tag, issue_tags(item))
        and is_issue_status_visible(user, item)
        and issue_status_bucket(item) in allowed
    ]
    items = tracker_history.hydrate_visible_history(db, token=token, issues=items, parks=[park])
    claims = local_assignees(db, [str(item.get("key") or "") for item in items])
    items = [
        {**item, "assignee": claims.get(str(item.get("key") or ""), item.get("assignee"))}
        for item in items
    ]
    ordered = sort_issues_oldest_first(sorted(items, key=lambda item: item["key"]))
    tasks = filter_issues_by_status(ordered, selected_status)
    target = sla_clock.SLA_TARGET_HOURS
    workload = (
        calculate_workload(items, target_hours=target, now=now, timezone=park.timezone)
        if user.role in LEADERSHIP
        else None
    )
    sla = calculate_sla(items, target_hours=target, now=now, timezone=park.timezone)
    visible_timing_items = {str(item.get("key") or ""): item for item in tasks[:TASK_LIMIT]}
    overdue_keys = {row.key for row in sla.overdue}
    visible_timing_items.update(
        {
            str(item.get("key") or ""): item
            for item in items
            if str(item.get("key") or "") in overdue_keys
        }
    )
    return OperationsOverviewOut(
        park_id=park.id,
        generated_at=now,
        timezone=park.timezone,
        status_options=[
            StatusOptionOut(key=key, label=label)
            for key, label in STATUS_LABELS.items()
            if key == "all" or key in allowed
        ],
        selected_status=selected_status,
        counts=count_status_buckets(items),
        tasks=[blocker_out(item) for item in tasks[:TASK_LIMIT]],
        task_timing=[
            task_timing(item, now, timezone=park.timezone) for item in visible_timing_items.values()
        ],
        tasks_total=len(tasks),
        tasks_truncated=len(tasks) > TASK_LIMIT,
        flow=flow_history(db, park_id=park.id, days=days, now=now),
        sla=sla,
        workload=workload,
        operators=operator_loads(db, park.id, workload or [], target)
        if rbac.is_admin_or_royal(user)
        else None,
    )
