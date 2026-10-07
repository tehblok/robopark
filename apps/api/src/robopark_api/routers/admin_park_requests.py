from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_admin
from robopark_api.models import AccessStatus, User
from robopark_api.routers.operator_parks import ParkRequestOut
from robopark_api.services import access_requests

router = APIRouter(
    prefix="/admin/park-requests",
    tags=["admin-park-requests"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=list[ParkRequestOut])
def list_park_requests(
    request_status: AccessStatus = Query(
        default=AccessStatus.pending,
        alias="status",
    ),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[ParkRequestOut]:
    rows = access_requests.for_manager(db, admin, request_status.value)
    if not rows:
        return []
    names = dict(
        db.execute(
            select(User.id, User.username).where(User.id.in_({row.user_id for row in rows}))
        ).all()
    )
    return [
        ParkRequestOut(
            id=row.id,
            revision=row.revision,
            user_id=row.user_id,
            park_id=row.park_id,
            status=row.status,
            created_at=row.created_at,
            resolved_at=row.resolved_at,
            resolved_by=row.resolved_by,
            username=names.get(row.user_id),
        )
        for row in rows
    ]


@router.post("/{request_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
def approve_park_request(
    request_id: int,
    revision: int | None = Query(default=None, ge=1),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    try:
        access_requests.decide(db, admin, request_id, approve=True, revision=revision)
    except HTTPException as exc:
        if exc.detail == "park_inactive":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc
        if exc.detail == "request_already_resolved":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc
        raise


@router.post("/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_park_request(
    request_id: int,
    revision: int | None = Query(default=None, ge=1),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    try:
        access_requests.decide(db, admin, request_id, approve=False, revision=revision)
    except HTTPException as exc:
        if exc.detail == "request_already_resolved":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc
        raise
