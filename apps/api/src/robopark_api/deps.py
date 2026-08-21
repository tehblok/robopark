from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.models import AccessStatus, AuthSession, User, UserRole
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
            AuthSession.expires_at > datetime.now(timezone.utc),
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
    if (
        user.role != UserRole.operator.value
        or user.access_status != AccessStatus.approved.value
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
