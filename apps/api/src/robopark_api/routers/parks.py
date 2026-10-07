from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import Park, User, UserPark
from robopark_api.schemas import ParkCreate, ParkOut, ParkUpdate
from robopark_api.services import rbac

router = APIRouter(
    prefix="/parks",
    tags=["parks"],
)


def _tag_exists(db: Session, tag: str, *, exclude_id: int | None = None) -> bool:
    query = select(Park.id).where(Park.tag == tag)
    if exclude_id is not None:
        query = query.where(Park.id != exclude_id)
    return db.scalar(query) is not None


def _require_parks_reader(user: User = Depends(require_user)) -> User:
    rbac.assert_approved(user)
    return user


@router.get("", response_model=list[ParkOut])
def list_parks(
    db: Session = Depends(get_db), actor: User = Depends(_require_parks_reader)
) -> list[Park]:
    permissions = rbac.permissions_for_user(db, actor)
    fleet_permissions = {
        rbac.PERMISSION_NAV_ADMIN,
        rbac.PERMISSION_USERS_MANAGE,
        rbac.PERMISSION_PARKS_MANAGE,
    }
    if permissions & fleet_permissions:
        return list(db.scalars(select(Park).order_by(Park.id)).all())
    return list(
        db.scalars(
            select(Park)
            .join(UserPark, UserPark.park_id == Park.id)
            .where(UserPark.user_id == actor.id, Park.is_active.is_(True))
            .order_by(Park.id)
        ).all()
    )


def _require_parks_manage(
    user: User = Depends(require_user), db: Session = Depends(get_db)
) -> User:
    rbac.require_approved_permission(db, user, rbac.PERMISSION_PARKS_MANAGE)
    return user


@router.post(
    "",
    response_model=ParkOut,
    status_code=status.HTTP_201_CREATED,
)
def create_park(
    payload: ParkCreate,
    db: Session = Depends(get_db),
    _actor: User = Depends(_require_parks_manage),
) -> Park:
    if _tag_exists(db, payload.tag):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)

    park = Park(**payload.model_dump(), is_active=True)
    db.add(park)
    db.commit()
    db.refresh(park)
    return park


@router.patch("/{park_id}", response_model=ParkOut)
def update_park(
    park_id: int,
    payload: ParkUpdate,
    db: Session = Depends(get_db),
    _actor: User = Depends(_require_parks_manage),
) -> Park:
    park = db.get(Park, park_id)
    if park is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    changes = payload.model_dump(exclude_unset=True)
    if "chat_id" in changes and changes["chat_id"] != park.chat_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="telegram_destination_use_native_api",
        )
    if (tag := changes.get("tag")) is not None and _tag_exists(db, tag, exclude_id=park_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)

    for field, value in changes.items():
        setattr(park, field, value)
    db.commit()
    db.refresh(park)
    return park
