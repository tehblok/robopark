from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from robopark_api.models import Park, User, UserPark
from robopark_api.schedule_models import ScheduleEntry
from robopark_api.schedule_schemas import ScheduleBulkCreate, ScheduleCopy, ScheduleCreate, ScheduleUpdate


def _park_access(db: Session, actor: User, park_id: int) -> bool:
    if actor.role == "royal":
        return db.get(Park, park_id) is not None
    return db.scalar(select(UserPark).where(UserPark.user_id == actor.id, UserPark.park_id == park_id)) is not None


def _owner_in_park(db: Session, owner_id: int, park_id: int) -> User:
    owner = db.get(User, owner_id)
    if owner is None:
        raise LookupError("user_not_found")
    if db.scalar(select(UserPark).where(UserPark.user_id == owner_id, UserPark.park_id == park_id)) is None:
        raise PermissionError
    return owner


def _warnings(db: Session, owner_id: int, start_at: datetime, end_at: datetime, *, exclude_id: str | None = None) -> list[str]:
    statement = select(ScheduleEntry.id).where(ScheduleEntry.owner_user_id == owner_id, ScheduleEntry.start_at < end_at, ScheduleEntry.end_at > start_at)
    if exclude_id:
        statement = statement.where(ScheduleEntry.id != exclude_id)
    return ["overlap"] if db.scalar(statement.limit(1)) else []


def shell(db: Session, row: ScheduleEntry) -> dict:
    return {"id": row.id, "owner_user_id": row.owner_user_id, "park_id": row.park_id, "kind": row.kind, "start_at": row.start_at, "end_at": row.end_at, "source": row.source, "series_id": row.series_id, "created_by_user_id": row.created_by_user_id, "updated_by_user_id": row.updated_by_user_id, "created_at": row.created_at, "updated_at": row.updated_at, "warnings": _warnings(db, row.owner_user_id, row.start_at, row.end_at, exclude_id=row.id)}


def list_entries(db: Session, actor: User, *, park_id: int | None = None, owner_user_id: int | None = None) -> list[dict]:
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
    return [shell(db, row) for row in db.scalars(statement)]


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
    row = ScheduleEntry(owner_user_id=owner_id, park_id=payload.park_id, kind=payload.kind, start_at=payload.start_at, end_at=payload.end_at, source="royal" if actor.role == "royal" and owner_id != actor.id else "self", created_by_user_id=actor.id, updated_by_user_id=actor.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return shell(db, row)


def create_bulk(db: Session, actor: User, payload: ScheduleBulkCreate) -> list[dict]:
    if actor.role != "royal":
        raise PermissionError
    if db.get(Park, payload.park_id) is None:
        raise LookupError("park_not_found")
    for owner_id in payload.owner_user_ids:
        _owner_in_park(db, owner_id, payload.park_id)
    series_id = str(uuid4())
    rows = []
    for owner_id in payload.owner_user_ids:
        for offset in range(payload.repeat_count):
            delta = timedelta(days=offset * payload.repeat_every_days)
            row = ScheduleEntry(owner_user_id=owner_id, park_id=payload.park_id, kind=payload.kind, start_at=payload.start_at + delta, end_at=payload.end_at + delta, source="royal", series_id=series_id, created_by_user_id=actor.id, updated_by_user_id=actor.id)
            db.add(row)
            rows.append(row)
    db.commit()
    return [shell(db, row) for row in rows]


def copy_period(db: Session, actor: User, payload: ScheduleCopy) -> list[dict]:
    if actor.role == "admin":
        raise PermissionError
    owners = payload.owner_user_ids or [actor.id]
    if actor.role != "royal" and owners != [actor.id]:
        raise PermissionError
    for owner_id in owners:
        _owner_in_park(db, owner_id, payload.park_id)
    if payload.source_end <= payload.source_start:
        raise ValueError("invalid_range")
    source = list(db.scalars(select(ScheduleEntry).where(ScheduleEntry.owner_user_id.in_(owners), ScheduleEntry.park_id == payload.park_id, ScheduleEntry.start_at >= payload.source_start, ScheduleEntry.start_at < payload.source_end).order_by(ScheduleEntry.start_at).limit(100)))
    delta = payload.target_start - payload.source_start
    series_id = str(uuid4())
    rows = []
    for original in source:
        row = ScheduleEntry(owner_user_id=original.owner_user_id, park_id=original.park_id, kind=original.kind, start_at=original.start_at + delta, end_at=original.end_at + delta, source="royal" if actor.role == "royal" else "self", series_id=series_id, created_by_user_id=actor.id, updated_by_user_id=actor.id)
        db.add(row); rows.append(row)
    db.commit()
    return [shell(db, row) for row in rows]


def delete_series(db: Session, actor: User, series_id: str) -> int:
    rows = list(db.scalars(select(ScheduleEntry).where(ScheduleEntry.series_id == series_id).limit(1000)))
    if not rows:
        raise LookupError("series_not_found")
    if actor.role == "admin" or (actor.role != "royal" and any(row.owner_user_id != actor.id for row in rows)):
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
    values = payload.model_dump(exclude_none=True)
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
