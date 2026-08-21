from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_admin
from robopark_api.models import AccessStatus, Park, User, UserRole

router = APIRouter(
    prefix="/admin/access-requests",
    tags=["admin-access"],
    dependencies=[Depends(require_admin)],
)


class AccessRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    access_status: str


class AccessApproval(BaseModel):
    park_ids: list[int] = Field(min_length=1)


def _pending_operator(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if (
        user.role != UserRole.operator.value
        or user.access_status != AccessStatus.pending.value
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    return user


@router.get("", response_model=list[AccessRequestOut])
def list_access_requests(db: Session = Depends(get_db)) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .where(
                User.role == UserRole.operator.value,
                User.access_status.in_(
                    [AccessStatus.pending.value, AccessStatus.rejected.value]
                ),
            )
            .order_by(User.id)
        ).all()
    )


@router.post("/{user_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
def approve_access_request(
    user_id: int,
    payload: AccessApproval,
    db: Session = Depends(get_db),
) -> None:
    user = _pending_operator(db, user_id)
    requested_ids = set(payload.park_ids)
    parks = list(
        db.scalars(
            select(Park).where(
                Park.id.in_(requested_ids),
                Park.is_active.is_(True),
            )
        ).all()
    )
    if len(parks) != len(requested_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    user.parks = parks
    user.access_status = AccessStatus.approved.value
    db.commit()


@router.post("/{user_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_access_request(
    user_id: int,
    db: Session = Depends(get_db),
) -> None:
    user = _pending_operator(db, user_id)
    user.access_status = AccessStatus.rejected.value
    db.commit()
