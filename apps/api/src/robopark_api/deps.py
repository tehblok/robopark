from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.models import AccessStatus, AuthSession, Park, User, UserPark, UserRole
from robopark_api.security import hash_session_token


def require_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    session_token = request.cookies.get(settings.session_cookie_name)
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    user = db.scalar(
        select(User)
        .join(AuthSession)
        .where(
            AuthSession.token_hash == hash_session_token(session_token),
            AuthSession.expires_at > datetime.now(UTC),
            User.is_active.is_(True),
        )
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role not in (UserRole.royal.value, UserRole.admin.value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def require_approved_operator(user: User = Depends(require_user)) -> User:
    if user.role != UserRole.operator.value or user.access_status != AccessStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def require_approved_mechanic(user: User = Depends(require_user)) -> User:
    if user.role != UserRole.mechanic.value or user.access_status != AccessStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def require_emergency_viewer(user: User = Depends(require_user)) -> User:
    if user.role in (UserRole.royal.value, UserRole.admin.value):
        return user
    if (
        user.role in (UserRole.mechanic.value, UserRole.operator.value)
        and user.access_status == AccessStatus.approved.value
    ):
        return user
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def get_mechanic_park(db: Session, user: User) -> Park | None:
    parks = db.scalars(select(Park).join(UserPark).where(UserPark.user_id == user.id)).all()
    if len(parks) != 1:
        return None
    return parks[0]


def get_user_parks(db: Session, user: User) -> list[Park]:
    return list(
        db.scalars(
            select(Park).join(UserPark).where(UserPark.user_id == user.id).order_by(Park.id)
        ).all()
    )


def get_operator_parks(db: Session, user: User) -> list[Park]:
    return get_user_parks(db, user)


def require_operator_park(park_id: int, db: Session, user: User) -> Park:
    park = db.get(Park, park_id)
    if park is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    assigned = db.scalar(
        select(UserPark).where(
            UserPark.user_id == user.id,
            UserPark.park_id == park_id,
        )
    )
    if assigned is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return park


def require_dashboard_park(park_id: int, db: Session, user: User) -> Park:
    park = db.get(Park, park_id)
    if park is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if user.role in (UserRole.royal.value, UserRole.admin.value):
        return park

    if user.role == UserRole.operator.value:
        if user.access_status != AccessStatus.approved.value:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return require_operator_park(park_id, db, user)

    if user.role == UserRole.mechanic.value:
        if user.access_status != AccessStatus.approved.value:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        mechanic_park = get_mechanic_park(db, user)
        if mechanic_park is None or mechanic_park.id != park_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return park

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
