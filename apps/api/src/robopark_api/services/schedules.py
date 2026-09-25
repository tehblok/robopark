from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.orm import Session, aliased

from robopark_api.models import AccessStatus, Park, Role, User, UserPark
from robopark_api.schedule_models import ScheduleEntry
from robopark_api.schedule_schemas import (
    ScheduleBulkCreate,
    ScheduleCopy,
    ScheduleCreate,
    ScheduleOut,
    SchedulePatternCreate,
    ScheduleUpdate,
)
from robopark_api.services import reliable_actions
from robopark_api.services.database_locks import database_idempotency_lock

SCHEDULE_RETENTION_DAYS = 400
SCHEDULE_PAGE_LIMIT = 2000
SCHEDULE_COPY_LIMIT = 5000
MOSCOW = ZoneInfo("Europe/Moscow")

NOTIFICATION_ROLES = {
    "new_task": ({"mechanic", "driver"}, True),
    "return": ({"mechanic", "driver"}, True),
    "operator_comment": ({"mechanic", "driver"}, True),
    "report": ({"operator", "admin", "royal"}, False),
    "review_task": ({"operator"}, False),
    "problem": ({"operator", "admin", "royal"}, False),
    "anomaly": ({"operator", "admin", "royal"}, False),
    "integration_down": ({"royal"}, False),
    "disk_low": ({"royal"}, False),
    "update_failure": ({"royal"}, False),
    "server_problem": ({"royal"}, False),
}
CRITICAL_ROYAL_EVENTS = {"integration_down", "disk_low", "update_failure", "server_problem"}


@dataclass(frozen=True)
class RoutingEvent:
    db: Session
    event_type: str
    park_id: int | None
    target_user_ids: set[int] | None = None


def eligible_recipients(event: RoutingEvent, at: datetime) -> list[User]:
    """Route by role and park; leave wins over shift, except critical royal alerts."""
    role_rule = NOTIFICATION_ROLES.get(event.event_type)
    if role_rule is None:
        raise ValueError("unknown_event_type")
    roles, requires_shift = role_rule
    db = event.db
    users = list(
        db.scalars(
            select(User)
            .join(Role)
            .where(
                Role.slug.in_(roles),
                User.access_status == AccessStatus.approved.value,
                User.is_active.is_(True),
            )
            .order_by(User.id)
        )
    )
    if event.target_user_ids is not None:
        users = [user for user in users if user.id in event.target_user_ids]
    if not users:
        return []
    user_ids = [user.id for user in users]
    park_users = (
        set(
            db.scalars(
                select(UserPark.user_id).where(
                    UserPark.park_id == event.park_id, UserPark.user_id.in_(user_ids)
                )
            )
        )
        if event.park_id is not None
        else set()
    )
    zones = {}
    day_predicates = []
    fallback_open = set()
    for user in users:
        try:
            zone = ZoneInfo(user.timezone or "Europe/Moscow")
        except (ValueError, ZoneInfoNotFoundError):
            zone = MOSCOW
        zones[user.id] = zone
        local_at = at.astimezone(zone)
        if 9 <= local_at.hour < 21:
            fallback_open.add(user.id)
        day_start = datetime.combine(local_at.date(), datetime.min.time(), tzinfo=zone)
        series_tail = aliased(ScheduleEntry)
        pattern_span = and_(
            ScheduleEntry.series_id.is_not(None), ScheduleEntry.start_at < day_start + timedelta(days=1),
            select(series_tail.id).where(
                series_tail.series_id == ScheduleEntry.series_id,
                series_tail.owner_user_id == user.id,
                series_tail.end_at > day_start,
            ).limit(1).exists(),
        )
        day_predicates.append(and_(ScheduleEntry.owner_user_id == user.id, or_(
            and_(ScheduleEntry.start_at < day_start + timedelta(days=1), ScheduleEntry.end_at > day_start),
            pattern_span,
        )))
    state_rows = db.execute(
        select(
            ScheduleEntry.owner_user_id,
            func.max(case((and_(ScheduleEntry.kind == "shift", ScheduleEntry.start_at <= at, ScheduleEntry.end_at > at), 1), else_=0)),
            func.max(case((and_(ScheduleEntry.kind.in_(("vacation", "sick")), ScheduleEntry.start_at <= at, ScheduleEntry.end_at > at), 1), else_=0)),
            func.count(ScheduleEntry.id),
        ).where(ScheduleEntry.park_id == event.park_id, or_(*day_predicates)).group_by(ScheduleEntry.owner_user_id)
    ).all() if event.park_id is not None else []
    states = {owner: (bool(shift), bool(leave), bool(covered)) for owner, shift, leave, covered in state_rows}
    return [
        user
        for user in users
        if (event.park_id is None or user.role == "royal" or user.id in park_users)
        and (
            user.role == "royal"
            and event.event_type in CRITICAL_ROYAL_EVENTS
            or not states.get(user.id, (False, False, False))[1]
            and (
                not requires_shift
                or states.get(user.id, (False, False, False))[0]
                or not states.get(user.id, (False, False, False))[2]
                and user.id in fallback_open
            )
        )
    ]


@dataclass(frozen=True)
class SchedulePage:
    items: list[dict]
    next_start_at: datetime | None = None
    next_id: str | None = None


def resolve_active_operator(
    db: Session, *, park_id: int, at: datetime | None = None
) -> User | None:
    """Choose an approved park operator, preferring a shift covering ``at``."""
    moment = at or datetime.now(UTC)
    eligible = (
        select(User)
        .join(UserPark, UserPark.user_id == User.id)
        .join(Role, Role.id == User.role_id)
        .where(
            UserPark.park_id == park_id,
            Role.slug == "operator",
            User.access_status == AccessStatus.approved.value,
            User.is_active.is_(True),
        )
        .order_by(User.username, User.id)
    )
    scheduled = db.scalar(
        eligible.join(ScheduleEntry, ScheduleEntry.owner_user_id == User.id)
        .where(
            ScheduleEntry.park_id == park_id,
            ScheduleEntry.kind == "shift",
            ScheduleEntry.start_at <= moment,
            ScheduleEntry.end_at > moment,
        )
        .limit(1)
    )
    return scheduled or db.scalar(eligible.limit(1))


def _park_access(db: Session, actor: User, park_id: int) -> bool:
    if actor.role == "royal":
        return db.get(Park, park_id) is not None
    return (
        db.scalar(select(UserPark).where(UserPark.user_id == actor.id, UserPark.park_id == park_id))
        is not None
    )


def _owner_in_park(db: Session, owner_id: int, park_id: int) -> User:
    owner = db.get(User, owner_id)
    if owner is None:
        raise LookupError("user_not_found")
    if (
        db.scalar(select(UserPark).where(UserPark.user_id == owner_id, UserPark.park_id == park_id))
        is None
    ):
        raise PermissionError
    return owner


def _warnings(
    db: Session,
    owner_id: int,
    start_at: datetime,
    end_at: datetime,
    *,
    exclude_id: str | None = None,
) -> list[str]:
    statement = select(ScheduleEntry.id).where(
        ScheduleEntry.owner_user_id == owner_id,
        ScheduleEntry.start_at < end_at,
        ScheduleEntry.end_at > start_at,
    )
    if exclude_id:
        statement = statement.where(ScheduleEntry.id != exclude_id)
    return ["overlap"] if db.scalar(statement.limit(1)) else []


def prune_old_entries(
    db: Session, *, now: datetime | None = None, retention_days: int = SCHEDULE_RETENTION_DAYS
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    stale_ids = select(ScheduleEntry.id).where(ScheduleEntry.end_at < cutoff).limit(500)
    result = db.execute(delete(ScheduleEntry).where(ScheduleEntry.id.in_(stale_ids)))
    db.commit()
    return int(result.rowcount or 0)


def shell(db: Session, row: ScheduleEntry, *, warnings: list[str] | None = None) -> dict:
    return {
        "id": row.id,
        "owner_user_id": row.owner_user_id,
        "park_id": row.park_id,
        "kind": row.kind,
        "start_at": _moscow_datetime(row.start_at),
        "end_at": _moscow_datetime(row.end_at),
        "source": row.source,
        "series_id": row.series_id,
        "created_by_user_id": row.created_by_user_id,
        "updated_by_user_id": row.updated_by_user_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "warnings": warnings
        if warnings is not None
        else _warnings(db, row.owner_user_id, row.start_at, row.end_at, exclude_id=row.id),
    }


def _moscow_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=MOSCOW)
    return value.astimezone(MOSCOW)


def list_entries(
    db: Session,
    actor: User,
    *,
    park_id: int | None = None,
    owner_user_id: int | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = SCHEDULE_PAGE_LIMIT,
    after_start_at: datetime | None = None,
    after_id: str | None = None,
) -> list[dict]:
    return list_entries_page(
        db,
        actor,
        park_id=park_id,
        owner_user_id=owner_user_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        after_start_at=after_start_at,
        after_id=after_id,
    ).items


def list_entries_page(
    db: Session,
    actor: User,
    *,
    park_id: int | None = None,
    owner_user_id: int | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = SCHEDULE_PAGE_LIMIT,
    after_start_at: datetime | None = None,
    after_id: str | None = None,
) -> SchedulePage:
    current = datetime.now(UTC)
    window_start = start_at or current - timedelta(days=31)
    window_end = end_at or current + timedelta(days=93)
    if window_end <= window_start:
        raise ValueError("invalid_range")
    if (after_start_at is None) != (after_id is None):
        raise ValueError("invalid_cursor")
    if after_start_at is not None and after_start_at.tzinfo is None:
        raise ValueError("timezone_required")
    statement = select(ScheduleEntry).order_by(ScheduleEntry.start_at, ScheduleEntry.id)
    if actor.role == "royal":
        pass
    elif actor.role == "admin":
        allowed = select(UserPark.park_id).where(UserPark.user_id == actor.id)
        if park_id is not None and not _park_access(db, actor, park_id):
            raise PermissionError
        statement = statement.where(ScheduleEntry.park_id.in_(allowed))
    else:
        statement = statement.where(ScheduleEntry.owner_user_id == actor.id)
    if park_id is not None:
        statement = statement.where(ScheduleEntry.park_id == park_id)
    if owner_user_id is not None:
        if actor.role not in {"admin", "royal"} and owner_user_id != actor.id:
            raise PermissionError
        statement = statement.where(ScheduleEntry.owner_user_id == owner_user_id)
    statement = statement.where(
        ScheduleEntry.end_at > window_start, ScheduleEntry.start_at < window_end
    )
    if after_start_at is not None and after_id is not None:
        statement = statement.where(
            or_(
                ScheduleEntry.start_at > after_start_at,
                and_(ScheduleEntry.start_at == after_start_at, ScheduleEntry.id > after_id),
            )
        )
    statement = statement.limit(limit + 1)
    rows = list(db.scalars(statement))
    has_more = len(rows) > limit
    rows = rows[:limit]
    overlaps: set[str] = set()
    if rows:
        candidate = aliased(ScheduleEntry)
        overlap_exists = (
            select(candidate.id)
            .where(
                candidate.id != ScheduleEntry.id,
                candidate.owner_user_id == ScheduleEntry.owner_user_id,
                candidate.start_at < ScheduleEntry.end_at,
                candidate.end_at > ScheduleEntry.start_at,
            )
            .correlate(ScheduleEntry)
            .exists()
        )
        overlaps = set(
            db.scalars(
                select(ScheduleEntry.id).where(
                    ScheduleEntry.id.in_([row.id for row in rows]),
                    overlap_exists,
                )
            )
        )
    items = [shell(db, row, warnings=["overlap"] if row.id in overlaps else []) for row in rows]
    if not has_more or not rows:
        return SchedulePage(items=items)
    return SchedulePage(
        items=items,
        next_start_at=_moscow_datetime(rows[-1].start_at),
        next_id=rows[-1].id,
    )


def list_participants(db: Session, actor: User, *, park_id: int) -> list[dict]:
    if actor.role not in {"admin", "royal"} or not _park_access(db, actor, park_id):
        raise PermissionError
    rows = db.execute(
        select(User.id, User.username, Role.slug)
        .join(UserPark, UserPark.user_id == User.id)
        .join(Role, Role.id == User.role_id)
        .where(
            UserPark.park_id == park_id,
            User.is_active.is_(True),
            User.access_status == AccessStatus.approved.value,
            Role.slug.in_(("mechanic", "operator")),
        )
        .order_by(User.username, User.id)
    ).all()
    return [
        {"id": user_id, "display_name": username, "role": role} for user_id, username, role in rows
    ]


def create_entry(db: Session, actor: User, payload: ScheduleCreate) -> dict:
    if actor.role == "admin":
        raise PermissionError
    owner_id = payload.owner_user_id or actor.id
    owner = db.get(User, owner_id)
    if owner is None:
        raise LookupError("user_not_found")
    if owner_id != actor.id and actor.role != "royal":
        raise PermissionError
    if not _park_access(db, actor if owner_id == actor.id else owner, payload.park_id):
        raise PermissionError
    owner.timezone = payload.timezone
    row = ScheduleEntry(
        owner_user_id=owner_id,
        park_id=payload.park_id,
        kind=payload.kind,
        start_at=payload.start_at,
        end_at=payload.end_at,
        source="royal" if actor.role == "royal" and owner_id != actor.id else "self",
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return shell(db, row)


def create_bulk(db: Session, actor: User, payload: ScheduleBulkCreate) -> list[dict]:
    owner_ids = payload.owner_user_ids
    if actor.role == "admin" or (actor.role != "royal" and owner_ids != [actor.id]):
        raise PermissionError
    if db.get(Park, payload.park_id) is None:
        raise LookupError("park_not_found")
    for owner_id in owner_ids:
        _owner_in_park(db, owner_id, payload.park_id).timezone = payload.timezone
    series_id = str(uuid4())
    rows = []
    for owner_id in owner_ids:
        for offset in range(payload.repeat_count):
            delta = timedelta(days=offset * payload.repeat_every_days)
            row = ScheduleEntry(
                owner_user_id=owner_id,
                park_id=payload.park_id,
                kind=payload.kind,
                start_at=payload.start_at + delta,
                end_at=payload.end_at + delta,
                source="royal" if actor.role == "royal" and owner_id != actor.id else "self",
                series_id=series_id,
                created_by_user_id=actor.id,
                updated_by_user_id=actor.id,
            )
            db.add(row)
            rows.append(row)
    db.commit()
    return [shell(db, row) for row in rows]


def create_pattern(db: Session, actor: User, payload: SchedulePatternCreate) -> list[dict]:
    if actor.role == "admin" or (
        actor.role != "royal" and payload.owner_user_ids != [actor.id]
    ):
        raise PermissionError
    on_days, cycle_days = {
        "none": (1, None),
        "5/2": (5, 7),
        "2/2": (2, 4),
        "4/4": (4, 8),
    }[payload.pattern]
    day_count = (payload.end_date - payload.start_date).days + 1
    active_dates = [
        payload.start_date + timedelta(days=offset)
        for offset in range(day_count)
        if cycle_days is None
        and offset == 0
        or cycle_days is not None
        and offset % cycle_days < on_days
    ]
    if len(active_dates) * len(payload.owner_user_ids) > 5000:
        raise ValueError("too_many_entries")

    action_payload = payload.model_dump(mode="json", exclude={"idempotency_key"})
    lock_key = f"schedule-pattern:{actor.id}:{payload.park_id}:{payload.idempotency_key}"
    with database_idempotency_lock(db, lock_key):
        try:
            begin = reliable_actions.begin_action(
                db,
                actor=actor,
                resource_type="schedule_park",
                resource_id=str(payload.park_id),
                action="schedule_pattern",
                idempotency_key=payload.idempotency_key,
                payload=action_payload,
            )
            if begin.result is not None:
                return list(begin.result)
            if db.get(Park, payload.park_id) is None:
                raise LookupError("park_not_found")
            eligible_owner_ids = set(
                db.scalars(
                    select(User.id)
                    .join(UserPark, UserPark.user_id == User.id)
                    .join(Role, Role.id == User.role_id)
                    .where(
                        User.id.in_(payload.owner_user_ids),
                        UserPark.park_id == payload.park_id,
                        User.is_active.is_(True),
                        User.access_status == AccessStatus.approved.value,
                        Role.slug.in_(("mechanic", "operator")),
                    )
                )
            )
            if actor.role == "royal" and eligible_owner_ids != set(payload.owner_user_ids):
                raise PermissionError
            if actor.role != "royal" and not _park_access(db, actor, payload.park_id):
                raise PermissionError

            series_id = str(uuid4())
            local_timezone = ZoneInfo(payload.timezone)
            for owner_id in payload.owner_user_ids:
                db.get(User, owner_id).timezone = payload.timezone
            rows: list[ScheduleEntry] = []
            for owner_id in payload.owner_user_ids:
                for work_date in active_dates:
                    end_date = work_date + timedelta(days=payload.end_time <= payload.start_time)
                    start_at = _resolve_wall_time(work_date, payload.start_time, local_timezone)
                    end_at = _resolve_wall_time(end_date, payload.end_time, local_timezone)
                    row = ScheduleEntry(
                        owner_user_id=owner_id,
                        park_id=payload.park_id,
                        kind=payload.kind,
                        start_at=start_at,
                        end_at=end_at,
                        source=(
                            "royal"
                            if actor.role == "royal" and owner_id != actor.id
                            else "self"
                        ),
                        series_id=series_id,
                        created_by_user_id=actor.id,
                        updated_by_user_id=actor.id,
                    )
                    db.add(row)
                    rows.append(row)
            db.flush()
            result_ids = [row.id for row in rows]
            result = [
                ScheduleOut.model_validate(item).model_dump(mode="json")
                for item in _pattern_results(db, result_ids)
            ]
            reliable_actions.complete_action(db, begin.row, result)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return result


def _resolve_wall_time(work_date, wall_time, timezone: ZoneInfo) -> datetime:
    naive = datetime.combine(work_date, wall_time)
    candidates = []
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=timezone, fold=fold)
        if candidate.astimezone(UTC).astimezone(timezone).replace(tzinfo=None) == naive:
            candidates.append(candidate)
    if not candidates:
        raise ValueError("nonexistent_local_time")
    return min(candidates, key=lambda value: value.astimezone(UTC))


def _pattern_results(db: Session, result_ids: list[str]) -> list[dict]:
    if not result_ids:
        return []
    target_rows = list(db.scalars(select(ScheduleEntry).where(ScheduleEntry.id.in_(result_ids))))
    targets_by_id = {row.id: row for row in target_rows}
    if len(targets_by_id) != len(result_ids):
        raise LookupError("schedule_pattern_result_not_found")
    owner_ids = {row.owner_user_id for row in target_rows}
    window_start = min(row.start_at for row in target_rows)
    window_end = max(row.end_at for row in target_rows)
    candidates = list(
        db.scalars(
            select(ScheduleEntry)
            .where(
                ScheduleEntry.owner_user_id.in_(owner_ids),
                ScheduleEntry.start_at < window_end,
                ScheduleEntry.end_at > window_start,
            )
            .order_by(ScheduleEntry.owner_user_id, ScheduleEntry.start_at, ScheduleEntry.id)
        )
    )
    candidates_by_owner: dict[int, list[ScheduleEntry]] = {}
    for candidate in candidates:
        candidates_by_owner.setdefault(candidate.owner_user_id, []).append(candidate)
    result = []
    for entry_id in result_ids:
        row = targets_by_id[entry_id]
        overlaps = any(
            candidate.id != row.id
            and candidate.start_at < row.end_at
            and candidate.end_at > row.start_at
            for candidate in candidates_by_owner.get(row.owner_user_id, [])
        )
        result.append(shell(db, row, warnings=["overlap"] if overlaps else []))
    return result


def copy_period(db: Session, actor: User, payload: ScheduleCopy) -> list[dict]:
    if actor.role == "admin":
        raise PermissionError
    owners = payload.owner_user_ids or [actor.id]
    if actor.role != "royal" and owners != [actor.id]:
        raise PermissionError
    if payload.idempotency_key is None:
        try:
            result = _copy_period_once(db, actor, payload, owners)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise

    action_payload = payload.model_dump(mode="json", exclude={"idempotency_key"})
    lock_key = f"schedule-copy:{actor.id}:{payload.park_id}:{payload.idempotency_key}"
    with database_idempotency_lock(db, lock_key):
        try:
            begin = reliable_actions.begin_action(
                db,
                actor=actor,
                resource_type="schedule_park",
                resource_id=str(payload.park_id),
                action="schedule_copy",
                idempotency_key=payload.idempotency_key,
                payload=action_payload,
            )
            if begin.result is not None:
                return list(begin.result)
            result = _copy_period_once(db, actor, payload, owners)
            reliable_actions.complete_action(db, begin.row, result)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise


def _copy_period_once(
    db: Session, actor: User, payload: ScheduleCopy, owners: list[int]
) -> list[dict]:
    for owner_id in owners:
        _owner_in_park(db, owner_id, payload.park_id).timezone = payload.timezone
    source = list(
        db.scalars(
            select(ScheduleEntry)
            .where(
                ScheduleEntry.owner_user_id.in_(owners),
                ScheduleEntry.park_id == payload.park_id,
                ScheduleEntry.start_at >= payload.source_start,
                ScheduleEntry.start_at < payload.source_end,
            )
            .order_by(ScheduleEntry.start_at, ScheduleEntry.id)
            .limit(SCHEDULE_COPY_LIMIT + 1)
        )
    )
    if len(source) > SCHEDULE_COPY_LIMIT:
        raise ValueError("too_many_entries")
    delta = payload.target_start - payload.source_start
    series_id = str(uuid4())
    rows = []
    for original in source:
        row = ScheduleEntry(
            owner_user_id=original.owner_user_id,
            park_id=original.park_id,
            kind=original.kind,
            start_at=original.start_at + delta,
            end_at=original.end_at + delta,
            source="royal" if actor.role == "royal" else "self",
            series_id=series_id,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    result = [
        ScheduleOut.model_validate(item).model_dump(mode="json")
        for item in _pattern_results(db, [row.id for row in rows])
    ]
    return result


def delete_series(db: Session, actor: User, series_id: str) -> int:
    rows = list(
        db.scalars(select(ScheduleEntry).where(ScheduleEntry.series_id == series_id).limit(1000))
    )
    if not rows:
        raise LookupError("series_not_found")
    if actor.role == "admin" or (
        actor.role != "royal" and any(row.owner_user_id != actor.id for row in rows)
    ):
        raise PermissionError
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)


def update_entry(db: Session, actor: User, entry_id: str, payload: ScheduleUpdate) -> dict:
    row = db.get(ScheduleEntry, entry_id)
    if row is None:
        raise LookupError("schedule_not_found")
    if actor.role != "royal" and (actor.role == "admin" or row.owner_user_id != actor.id):
        raise PermissionError
    values = payload.model_dump(exclude_none=True, exclude={"timezone"})
    if payload.timezone is not None:
        db.get(User, row.owner_user_id).timezone = payload.timezone
    for key, value in values.items():
        setattr(row, key, value)
    if row.end_at <= row.start_at:
        raise ValueError("invalid_range")
    row.updated_by_user_id = actor.id
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return shell(db, row)


def delete_entry(db: Session, actor: User, entry_id: str) -> None:
    row = db.get(ScheduleEntry, entry_id)
    if row is None:
        raise LookupError("schedule_not_found")
    if actor.role != "royal" and (actor.role == "admin" or row.owner_user_id != actor.id):
        raise PermissionError
    db.delete(row)
    db.commit()
