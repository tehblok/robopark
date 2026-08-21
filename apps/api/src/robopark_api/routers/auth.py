from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import AuthSession, User
from robopark_api.schemas import LoginRequest, UserOut
from robopark_api.security import (
    hash_session_token,
    new_session_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
def login(
    credentials: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    user = db.scalar(select(User).where(User.username == credentials.username))
    if (
        user is None
        or not user.is_active
        or not verify_password(credentials.password, user.password_hash)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    raw_token = new_session_token()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_session_token(raw_token),
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=settings.session_ttl_seconds),
        )
    )
    db.commit()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_ttl_seconds,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        db.execute(
            delete(AuthSession).where(
                AuthSession.token_hash == hash_session_token(raw_token)
            )
        )
        db.commit()

    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(require_user)) -> User:
    return user
