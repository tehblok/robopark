from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_admin
from robopark_api.models import Park

router = APIRouter(
    prefix="/parks",
    tags=["parks"],
    dependencies=[Depends(require_admin)],
)


class ParkCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    tag: str = Field(min_length=1, max_length=64)


class ParkUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    tag: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None


class ParkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    tag: str
    is_active: bool


def _tag_exists(db: Session, tag: str, *, exclude_id: int | None = None) -> bool:
    query = select(Park.id).where(Park.tag == tag)
    if exclude_id is not None:
        query = query.where(Park.id != exclude_id)
    return db.scalar(query) is not None


@router.get("", response_model=list[ParkOut])
def list_parks(db: Session = Depends(get_db)) -> list[Park]:
    return list(db.scalars(select(Park).order_by(Park.id)).all())


@router.post(
    "",
    response_model=ParkOut,
    status_code=status.HTTP_201_CREATED,
)
def create_park(payload: ParkCreate, db: Session = Depends(get_db)) -> Park:
    if _tag_exists(db, payload.tag):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)

    park = Park(name=payload.name, tag=payload.tag, is_active=True)
    db.add(park)
    db.commit()
    db.refresh(park)
    return park


@router.patch("/{park_id}", response_model=ParkOut)
def update_park(
    park_id: int,
    payload: ParkUpdate,
    db: Session = Depends(get_db),
) -> Park:
    park = db.get(Park, park_id)
    if park is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    changes = payload.model_dump(exclude_unset=True)
    if (tag := changes.get("tag")) is not None and _tag_exists(
        db, tag, exclude_id=park_id
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)

    for field, value in changes.items():
        setattr(park, field, value)
    db.commit()
    db.refresh(park)
    return park
