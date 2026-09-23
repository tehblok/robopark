from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import User
from robopark_api.schedule_schemas import (
    ScheduleBulkCreate,
    ScheduleCopy,
    ScheduleCreate,
    ScheduleOut,
    ScheduleParticipantOut,
    ScheduleUpdate,
)
from robopark_api.services import schedules

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _run(fn):
    try:
        return fn()
    except PermissionError:
        raise HTTPException(403, "forbidden") from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("", response_model=list[ScheduleOut])
def list_schedules(
    park_id: int | None = None,
    owner_user_id: int | None = None,
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return _run(
        lambda: schedules.list_entries(
            db,
            user,
            park_id=park_id,
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
        )
    )


@router.get("/participants", response_model=list[ScheduleParticipantOut])
def list_schedule_participants(
    park_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return _run(lambda: schedules.list_participants(db, user, park_id=park_id))


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
def create_schedule(
    payload: ScheduleCreate, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    return _run(lambda: schedules.create_entry(db, user, payload))


@router.post("/bulk", response_model=list[ScheduleOut], status_code=status.HTTP_201_CREATED)
def create_schedule_bulk(
    payload: ScheduleBulkCreate, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    return _run(lambda: schedules.create_bulk(db, user, payload))


@router.post("/copy", response_model=list[ScheduleOut], status_code=status.HTTP_201_CREATED)
def copy_schedule(
    payload: ScheduleCopy, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    return _run(lambda: schedules.copy_period(db, user, payload))


@router.delete("/series/{series_id}")
def delete_schedule_series(
    series_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    return {"deleted": _run(lambda: schedules.delete_series(db, user, series_id))}


@router.patch("/{entry_id}", response_model=ScheduleOut)
def update_schedule(
    entry_id: str,
    payload: ScheduleUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return _run(lambda: schedules.update_entry(db, user, entry_id, payload))


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    entry_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    _run(lambda: schedules.delete_entry(db, user, entry_id))
