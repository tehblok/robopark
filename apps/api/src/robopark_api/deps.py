from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.models import AccessStatus, AuthSession, Park, Role, User, UserPark
from robopark_api.security import hash_session_token
from robopark_api.services import rbac

#: Paths allowed while ``must_change_password`` is set (SPA + API).
_MUST_CHANGE_PASSWORD_ALLOW = frozenset(
    {
        "/auth/me",
        "/auth/change-password",
        "/auth/logout",
        "/ops/maintenance",
        "/health",
        "/health/ready",
    }
)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _touch_session(
    db: Session, auth_session: AuthSession, settings: Settings, now: datetime
) -> None:
    next_idle = now + timedelta(seconds=settings.session_idle_seconds)
    remaining = (_aware(auth_session.expires_at) - now).total_seconds()
    if remaining >= settings.session_idle_seconds - settings.session_slide_min_interval_seconds:
        return
    auth_session.expires_at = next_idle
    db.commit()


def require_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    session_token = request.cookies.get(settings.session_cookie_name)
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    now = datetime.now(UTC)
    auth_session = db.scalar(
        select(AuthSession)
        .options(
            joinedload(AuthSession.user).joinedload(User.role_ref).joinedload(Role.permissions)
        )
        .where(AuthSession.token_hash == hash_session_token(session_token))
    )
    if auth_session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    user = auth_session.user
    created = _aware(auth_session.created_at)
    expires = _aware(auth_session.expires_at)
    absolute_deadline = created + timedelta(seconds=settings.session_absolute_ttl_seconds)
    if user is None or not user.is_active or expires <= now or now >= absolute_deadline:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    _touch_session(db, auth_session, settings, now)

    if user.must_change_password and request.url.path not in _MUST_CHANGE_PASSWORD_ALLOW:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="must_change_password",
        )
    return user


def require_admin(user: User = Depends(require_user), db: Session = Depends(get_db)) -> User:
    rbac.assert_approved(user)
    if not rbac.has_permission(db, user, rbac.PERMISSION_NAV_ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def require_royal(user: User = Depends(require_user)) -> User:
    return rbac.require_royal(user)


def require_approved(user: User = Depends(require_user)) -> User:
    if user.access_status != AccessStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def require_approved_operator(user: User = Depends(require_user)) -> User:
    if user.role != rbac.RoleSlug.OPERATOR or user.access_status != AccessStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def require_approved_mechanic(user: User = Depends(require_user)) -> User:
    if user.role != rbac.RoleSlug.MECHANIC or user.access_status != AccessStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def require_permission(permission: str):
    return rbac.require_permission(permission)


def require_emergency_viewer(
    user: User = Depends(require_user), db: Session = Depends(get_db)
) -> User:
    if not rbac.has_permission(db, user, rbac.PERMISSION_NAV_EMERGENCY):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    rbac.assert_approved(user)
    return user


def get_mechanic_park(db: Session, user: User) -> Park | None:
    parks = get_user_parks(db, user)
    return parks[0] if parks else None


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

    if rbac.is_admin_or_royal(user):
        return park

    slug = rbac.role_slug(user)
    if slug in {rbac.RoleSlug.OPERATOR, rbac.RoleSlug.MECHANIC}:
        if user.access_status != AccessStatus.approved.value:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return require_operator_park(park_id, db, user)

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
