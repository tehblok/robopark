import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import AccessStatus, AuthSession, Park, User, UserPark, UserRole
from robopark_api.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    ParkOut,
    RegisterOut,
    RegisterRequest,
    UserOut,
)
from robopark_api.security import (
    PasswordPolicyError,
    hash_password,
    hash_session_token,
    new_session_token,
    shared_passwords_match,
    validate_password,
    verify_password,
)
from robopark_api.services import audit
from robopark_api.services.login_throttle import (
    client_ip,
    get_login_throttle,
    get_register_throttle,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _too_many_requests(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="too_many_attempts",
        headers={"Retry-After": str(retry_after)},
    )


def _enforce_password_policy(
    password: str, settings: Settings, username: str | None = None
) -> None:
    try:
        validate_password(
            password,
            min_length=settings.password_min_length,
            require_complexity=settings.password_require_complexity,
            username=username,
        )
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def purge_expired_sessions(db: Session) -> int:
    """Delete sessions past their expiry; returns the number removed."""
    result = db.execute(
        delete(AuthSession).where(AuthSession.expires_at <= datetime.now(UTC))
    )
    db.commit()
    return int(result.rowcount or 0)


@router.post(
    "/register",
    response_model=RegisterOut,
    status_code=status.HTTP_201_CREATED,
)
def register(
    registration: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RegisterOut:
    throttle = get_register_throttle(settings)
    throttle_key = client_ip(request)
    retry_after = throttle.retry_after(throttle_key)
    if retry_after:
        raise _too_many_requests(retry_after)

    if not shared_passwords_match(
        registration.shared_password, settings.operator_shared_password
    ):
        # Rate-limited: the shared password is otherwise brute-forceable.
        throttle.register_failure(throttle_key)
        logger.warning("Rejected registration attempt from %s", throttle_key)
        audit.record(
            db,
            action=audit.ACTION_REGISTER,
            actor_username=registration.username,
            outcome=audit.OUTCOME_DENIED,
            detail="wrong shared password",
            client_ip=throttle_key,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    _enforce_password_policy(
        registration.password, settings, username=registration.username
    )

    existing_user = db.scalar(
        select(User).where(User.username == registration.username)
    )
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)

    throttle.reset(throttle_key)
    user = User(
        username=registration.username,
        password_hash=hash_password(registration.password),
        role=UserRole.operator.value,
        access_status=AccessStatus.pending.value,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    audit.record(
        db,
        action=audit.ACTION_REGISTER,
        actor=user,
        target_type="user",
        target_id=str(user.id),
        client_ip=throttle_key,
    )
    return RegisterOut(
        id=user.id,
        username=user.username,
        role=user.role,
        access_status=user.access_status,
        parks=[],
    )


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    throttle = get_login_throttle(settings)
    # Keyed by username *and* address: neither a single account nor a single
    # host can be hammered, and one attacker cannot lock out every user.
    throttle_key = f"{credentials.username.lower()}|{client_ip(request)}"
    retry_after = throttle.retry_after(throttle_key)
    if retry_after:
        audit.record(
            db,
            action=audit.ACTION_LOGIN_BLOCKED,
            actor_username=credentials.username,
            outcome=audit.OUTCOME_DENIED,
            detail=f"locked out, retry after {retry_after}s",
            client_ip=client_ip(request),
        )
        raise _too_many_requests(retry_after)

    user = db.scalar(select(User).where(User.username == credentials.username))
    if (
        user is None
        or not user.is_active
        or not verify_password(credentials.password, user.password_hash)
    ):
        throttle.register_failure(throttle_key)
        logger.info("Failed login for %r from %s", credentials.username, client_ip(request))
        audit.record(
            db,
            action=audit.ACTION_LOGIN_FAILED,
            actor_username=credentials.username,
            outcome=audit.OUTCOME_FAILURE,
            client_ip=client_ip(request),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    throttle.reset(throttle_key)
    audit.record(
        db,
        action=audit.ACTION_LOGIN_SUCCESS,
        actor=user,
        client_ip=client_ip(request),
    )
    # Opportunistic cleanup: expired rows would otherwise accumulate forever.
    purge_expired_sessions(db)

    raw_token = new_session_token()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_session_token(raw_token),
            expires_at=datetime.now(UTC)
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
def me(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> UserOut:
    parks = db.scalars(
        select(Park).join(UserPark).where(UserPark.user_id == user.id)
    ).all()
    return UserOut(
        id=user.id,
        username=user.username,
        role=user.role,
        access_status=user.access_status,
        tracker_login=user.tracker_login,
        must_change_password=user.must_change_password,
        parks=[ParkOut.model_validate(park) for park in parks],
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    _enforce_password_policy(payload.new_password, settings, username=user.username)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    db.commit()
