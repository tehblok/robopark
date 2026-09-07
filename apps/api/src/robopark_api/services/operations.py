"""Current scoped load and observed flow; no inference of historical snapshots."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

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
    WorkloadOut,
)
from robopark_api.routers._blockers import blocker_out
from robopark_api.services import platform_settings, rbac, tracker_cache, tracker_client
from robopark_api.services.blocker_history import BUCKET_SECONDS, align_bucket_start
from robopark_api.services.tracker_filters import (
    count_status_buckets,
    filter_issues_by_status,
    issue_status_bucket,
    park_priority_type,
    sort_issues_oldest_first,
)
from robopark_api.services.tracker_policy import is_issue_status_visible, issue_tags

TASK_LIMIT = 200
SLA_TIMEZONE = ZoneInfo("Europe/Moscow")
SLA_DAY_START = time(9, 0)
SLA_DAY_END = time(21, 0)
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
ROLE_STATUSES = {"driver": {"new", "moving"}, "mechanic": {"queued", "diagnostics"}}
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


def queued_working_hours(item: dict, now: datetime) -> float | None:
    """Working hours since creation for tasks currently in the queued stage."""
    if issue_status_bucket(item) != "queued":
        return None
    raw = item.get("created")
    if not raw:
        return None
    try:
        created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if created.tzinfo is None:
        return None
    start = created.astimezone(SLA_TIMEZONE)
    end = as_utc(now).astimezone(SLA_TIMEZONE)
    if end < start:
        return None
    total = 0.0
    day = start.date()
    while day <= end.date():
        window_start = datetime.combine(day, SLA_DAY_START, SLA_TIMEZONE)
        window_end = datetime.combine(day, SLA_DAY_END, SLA_TIMEZONE)
        left, right = max(start, window_start), min(end, window_end)
        if right > left:
            total += (right - left).total_seconds() / 3600
        day += timedelta(days=1)
    return total


def calculate_sla(items: list[dict], *, target_hours: int | None, now: datetime) -> SlaOut:
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
        age = queued_working_hours(item, now)
        if age is None:
            continue
        evaluated += 1
        if age > target_hours:
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
    items: list[dict], *, target_hours: int | None, now: datetime
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
            age for item in group if (age := queued_working_hours(item, now)) is not None
        ]
        result.append(
            WorkloadOut(
                login=login,
                display=display,
                open_count=len(group),
                overdue_count=sum(age > target_hours for age in sla_ages)
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


def sla_policy_key(park_id: int) -> str:
    return f"operations.sla.park.{park_id}"


def get_sla_target(db: Session, park_id: int) -> int | None:
    row = platform_settings.get_setting(db, sla_policy_key(park_id))
    if row is None:
        return 4
    try:
        value = json.loads(row.value)
    except (ValueError, TypeError):
        return 4
    return value if type(value) is int and 1 <= value <= 8760 else 4


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


def require_operations_read(db: Session, user: User, *, policy_only: bool = False) -> None:
    rbac.assert_approved(user)
    permissions = rbac.permissions_for_user(db, user)
    sections = {rbac.PERMISSION_NAV_DASHBOARD, rbac.PERMISSION_NAV_ANALYTICS}
    if policy_only:
        sections.add(rbac.PERMISSION_PARKS_MANAGE)
    if not permissions & sections or (
        not policy_only and rbac.PERMISSION_TRACKER_READ not in permissions
    ):
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
        )
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=502, detail="tracker_upstream_error") from exc
    # Cache contains the park source, not a principal's authorization decision.
    # The client paginates the complete source or raises; no source hard cap.
    items = [
        item
        for item in snapshot
        if item.get("queue") == park.tracker_queue
        and park.tag in issue_tags(item)
        and is_issue_status_visible(user, item)
        and issue_status_bucket(item) in allowed
    ]
    ordered = sort_issues_oldest_first(sorted(items, key=lambda item: item["key"]))
    tasks = filter_issues_by_status(ordered, selected_status)
    target = get_sla_target(db, park.id)
    workload = (
        calculate_workload(items, target_hours=target, now=now) if user.role in LEADERSHIP else None
    )
    return OperationsOverviewOut(
        park_id=park.id,
        generated_at=now,
        status_options=[
            StatusOptionOut(key=key, label=label)
            for key, label in STATUS_LABELS.items()
            if key == "all" or key in allowed
        ],
        selected_status=selected_status,
        counts=count_status_buckets(items),
        tasks=[blocker_out(item) for item in tasks[:TASK_LIMIT]],
        tasks_total=len(tasks),
        tasks_truncated=len(tasks) > TASK_LIMIT,
        flow=flow_history(db, park_id=park.id, days=days, now=now),
        sla=calculate_sla(items, target_hours=target, now=now),
        workload=workload,
        operators=operator_loads(db, park.id, workload or [], target)
        if rbac.is_admin_or_royal(user)
        else None,
    )
