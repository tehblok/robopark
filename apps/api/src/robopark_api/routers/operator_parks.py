from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_approved_operator
from robopark_api.models import AccessStatus, Park, ParkRequest, User, UserPark
from robopark_api.routers.parks import ParkOut

router = APIRouter(
    prefix="/operator",
    tags=["operator-parks"],
    dependencies=[Depends(require_approved_operator)],
)


class ParkRequestCreate(BaseModel):
    park_id: int


class ParkRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    park_id: int
    status: str
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: int | None
    username: str | None = None


@router.get("/parks", response_model=list[ParkOut])
def list_assigned_parks(
    operator: User = Depends(require_approved_operator),
    db: Session = Depends(get_db),
) -> list[Park]:
    return list(
        db.scalars(
            select(Park).join(UserPark).where(UserPark.user_id == operator.id).order_by(Park.id)
        ).all()
    )


@router.get("/available-parks", response_model=list[ParkOut])
def list_available_parks(
    operator: User = Depends(require_approved_operator),
    db: Session = Depends(get_db),
) -> list[Park]:
    assigned = select(UserPark.user_id).where(
        UserPark.user_id == operator.id,
        UserPark.park_id == Park.id,
    )
    pending = select(ParkRequest.id).where(
        ParkRequest.user_id == operator.id,
        ParkRequest.park_id == Park.id,
        ParkRequest.status == AccessStatus.pending.value,
    )
    return list(
        db.scalars(
            select(Park)
            .where(
                Park.is_active.is_(True),
                ~assigned.exists(),
                ~pending.exists(),
            )
            .order_by(Park.id)
        ).all()
    )


@router.get("/park-requests", response_model=list[ParkRequestOut])
def list_park_requests(
    operator: User = Depends(require_approved_operator),
    db: Session = Depends(get_db),
) -> list[ParkRequest]:
    return list(
        db.scalars(
            select(ParkRequest).where(ParkRequest.user_id == operator.id).order_by(ParkRequest.id)
        ).all()
    )


@router.post(
    "/park-requests",
    response_model=ParkRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def create_park_request(
    payload: ParkRequestCreate,
    operator: User = Depends(require_approved_operator),
    db: Session = Depends(get_db),
) -> ParkRequest:
    park = db.get(Park, payload.park_id)
    if park is None or not park.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    membership = db.get(UserPark, (operator.id, payload.park_id))
    if membership is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    pending = db.scalar(
        select(ParkRequest.id).where(
            ParkRequest.user_id == operator.id,
            ParkRequest.park_id == payload.park_id,
            ParkRequest.status == AccessStatus.pending.value,
        )
    )
    if pending is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)

    park_request = ParkRequest(
        user_id=operator.id,
        park_id=payload.park_id,
        status=AccessStatus.pending.value,
    )
    db.add(park_request)
    db.commit()
    db.refresh(park_request)
    return park_request
