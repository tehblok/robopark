from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_admin
from robopark_api.models import AccessStatus, ParkRequest, User, UserPark
from robopark_api.routers.operator_parks import ParkRequestOut

router = APIRouter(
    prefix="/admin/park-requests",
    tags=["admin-park-requests"],
    dependencies=[Depends(require_admin)],
)


def _pending_request(db: Session, request_id: int) -> ParkRequest:
    park_request = db.get(ParkRequest, request_id)
    if park_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if park_request.status != AccessStatus.pending.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    return park_request


def _resolve(park_request: ParkRequest, admin: User, resolution: AccessStatus) -> None:
    park_request.status = resolution.value
    park_request.resolved_at = datetime.now(timezone.utc)
    park_request.resolved_by = admin.id


@router.get("", response_model=list[ParkRequestOut])
def list_park_requests(
    request_status: AccessStatus = Query(
        default=AccessStatus.pending,
        alias="status",
    ),
    db: Session = Depends(get_db),
) -> list[ParkRequest]:
    return list(
        db.scalars(
            select(ParkRequest)
            .where(ParkRequest.status == request_status.value)
            .order_by(ParkRequest.id)
        ).all()
    )


@router.post("/{request_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
def approve_park_request(
    request_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    park_request = _pending_request(db, request_id)
    _resolve(park_request, admin, AccessStatus.approved)
    db.add(
        UserPark(
            user_id=park_request.user_id,
            park_id=park_request.park_id,
        )
    )
    db.commit()


@router.post("/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_park_request(
    request_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    park_request = _pending_request(db, request_id)
    _resolve(park_request, admin, AccessStatus.rejected)
    db.commit()
