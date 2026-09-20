from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, joinedload

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_royal, require_user
from robopark_api.models import AccessStatus, AuthSession, Park, Report, Role, User, UserPark
from robopark_api.schemas import ParkOut
from robopark_api.security import PasswordPolicyError, hash_password, validate_password
from robopark_api.services import audit, rbac
from robopark_api.services.user_activity import public_ip

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


class UserAdminOut(BaseModel):
    id: int
    username: str
    role: str
    role_id: int
    access_status: str
    is_active: bool
    tracker_login: str | None = None
    must_change_password: bool = False
    parks: list[ParkOut]
    permissions: list[str] = []
    role_permissions: list[str] = []
    last_seen_at: datetime | None = None
    last_ip: str | None = None
    last_device: str | None = None
    last_location: str | None = None
    location_source: str
    location_availability: str


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    role_slug: str = Field(min_length=2, max_length=32)
    park_ids: list[int] = Field(default_factory=list)
    tracker_login: str | None = Field(default=None, max_length=128)
    permissions: list[str] | None = None


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=1, max_length=128)
    role_slug: str | None = Field(default=None, min_length=2, max_length=32)
    park_ids: list[int] | None = None
    is_active: bool | None = None
    tracker_login: str | None = Field(default=None, max_length=128)
    must_change_password: bool | None = None
    access_status: str | None = None
    permissions: list[str] | None = None


class UserApproval(BaseModel):
    park_ids: list[int] = Field(default_factory=list)


def _can_manage_users(db: Session, actor: User) -> bool:
    if actor.access_status != AccessStatus.approved.value:
        return False
    return rbac.is_royal(actor) or rbac.has_permission(db, actor, rbac.PERMISSION_USERS_MANAGE)


def _user_out(
    db: Session, user: User, parks: list[Park], settings: Settings
) -> UserAdminOut:
    location_enabled = settings.ip_geo_provider == "ipwhois"
    location_availability = (
        "disabled"
        if not location_enabled
        else "no_ip"
        if public_ip(user.last_ip) is None
        else "available"
        if user.last_location
        else "unavailable"
    )
    return UserAdminOut(
        id=user.id,
        username=user.username,
        role=user.role,
        role_id=user.role_id,
        access_status=user.access_status,
        is_active=user.is_active,
        tracker_login=user.tracker_login,
        must_change_password=user.must_change_password,
        parks=[ParkOut.model_validate(park) for park in parks],
        permissions=sorted(rbac.permissions_for_user(db, user)),
        role_permissions=sorted(rbac.role_permission_keys(db, user)),
        last_seen_at=user.last_seen_at,
        last_ip=user.last_ip,
        last_device=user.last_device,
        last_location=user.last_location if settings.ip_geo_provider == "ipwhois" else None,
        location_source="ipwhois" if location_enabled else "disabled",
        location_availability=location_availability,
    )


def _load_user(db: Session, user_id: int) -> User | None:
    return db.scalar(select(User).options(joinedload(User.role_ref)).where(User.id == user_id))


def _user_parks(db: Session, user_id: int) -> list[Park]:
    return list(db.scalars(select(Park).join(UserPark).where(UserPark.user_id == user_id)).all())


def _set_parks(db: Session, user: User, park_ids: list[int]) -> None:
    if not park_ids:
        db.execute(delete(UserPark).where(UserPark.user_id == user.id))
        return
    parks = list(
        db.scalars(select(Park).where(Park.id.in_(park_ids), Park.is_active.is_(True))).all()
    )
    if len(parks) != len(set(park_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    db.execute(delete(UserPark).where(UserPark.user_id == user.id))
    for park in parks:
        db.add(UserPark(user_id=user.id, park_id=park.id))


def _check_password(password: str, settings: Settings, username: str | None) -> None:
    try:
        validate_password(
            password,
            min_length=settings.password_min_length,
            require_complexity=settings.password_require_complexity,
            username=username,
        )
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _revoke_sessions(db: Session, user_id: int) -> None:
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


def _assert_privileged_grant_allowed(actor: User, existing: set[str], desired: set[str]) -> None:
    if rbac.privileged_grant_blocked(actor, existing, desired):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="privileged_grant_forbidden",
        )


def _assert_role_assignment_allowed(actor: User, role: Role) -> None:
    if rbac.privileged_role_assignment_blocked(actor, role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="privileged_grant_forbidden",
        )


@router.get("", response_model=list[UserAdminOut])
def list_users(
    role: str | None = Query(default=None),
    access_status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    actor: User = Depends(require_user),
) -> list[UserAdminOut]:
    if not _can_manage_users(db, actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    query = select(User).options(joinedload(User.role_ref)).order_by(User.id)
    if role:
        query = query.join(Role).where(Role.slug == role)
    if access_status:
        query = query.where(User.access_status == access_status)
    users = db.scalars(query).all()
    out: list[UserAdminOut] = []
    for user in users:
        out.append(_user_out(db, user, _user_parks(db, user.id), settings))
    return out


@router.post("", response_model=UserAdminOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    actor: User = Depends(require_user),
) -> UserAdminOut:
    if not _can_manage_users(db, actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if payload.role_slug == rbac.RoleSlug.ROYAL and not rbac.is_royal(actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if db.scalar(select(User.id).where(User.username == payload.username)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    role = rbac.get_role_by_slug(db, payload.role_slug)
    if role is None or not role.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_role")
    _assert_role_assignment_allowed(actor, role)
    desired = rbac.proposed_user_permissions(role, payload.permissions)
    _assert_privileged_grant_allowed(actor, set(), desired)
    _check_password(payload.password, settings, payload.username)
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role_ref=role,
        access_status=AccessStatus.approved.value,
        tracker_login=(payload.tracker_login or "").strip() or None,
        is_active=True,
    )
    db.add(user)
    db.flush()
    _set_parks(db, user, payload.park_ids)
    if payload.permissions is not None:
        rbac.set_user_effective_permissions(db, user, sorted(desired))
    db.commit()
    user = _load_user(db, user.id)
    assert user is not None
    return _user_out(db, user, _user_parks(db, user.id), settings)


@router.patch("/{user_id}", response_model=UserAdminOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    actor: User = Depends(require_user),
) -> UserAdminOut:
    if not _can_manage_users(db, actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    user = _load_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if user.role == rbac.RoleSlug.ROYAL and not rbac.is_royal(actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    changes = payload.model_dump(exclude_unset=True)
    existing = rbac.permissions_for_user(db, user)
    role = user.role_ref
    if slug := changes.get("role_slug"):
        if slug == rbac.RoleSlug.ROYAL and not rbac.is_royal(actor):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        role = rbac.get_role_by_slug(db, slug)
        if role is None or not role.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_role")
        _assert_role_assignment_allowed(actor, role)
        if (
            user.role == rbac.RoleSlug.ROYAL
            and slug != rbac.RoleSlug.ROYAL
            and rbac.is_last_active_royal(db, user)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cannot_disable_last_royal",
            )
    desired: set[str] | None = None
    if "permissions" in changes:
        desired = rbac.proposed_user_permissions(role, changes["permissions"] or [])
    elif changes.get("role_slug"):
        desired = rbac.proposed_user_permissions(role)
    if desired is not None:
        _assert_privileged_grant_allowed(actor, existing, desired)
    if (access := changes.get("access_status")) is not None:
        if not rbac.is_royal(actor):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="royal_only")
        if access not in {item.value for item in AccessStatus}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
        if access != AccessStatus.approved.value and rbac.is_last_active_royal(db, user):
            raise HTTPException(status_code=400, detail="cannot_disable_last_royal")
    if changes.get("is_active") is False and rbac.is_last_active_royal(db, user):
        raise HTTPException(status_code=400, detail="cannot_disable_last_royal")
    if password := changes.get("password"):
        _check_password(password, settings, user.username)

    # All authorization uses the old effective permissions/role. Synchronize
    # the relationship as well as the FK before deriving new role overrides.
    user.role_ref = role
    if password := changes.get("password"):
        user.password_hash = hash_password(password)
        _revoke_sessions(db, user.id)
    if (is_active := changes.get("is_active")) is not None:
        user.is_active = is_active
        if not is_active:
            _revoke_sessions(db, user.id)
    if "tracker_login" in changes:
        raw = changes.get("tracker_login")
        user.tracker_login = raw.strip() if isinstance(raw, str) and raw.strip() else None
    if (must_change := changes.get("must_change_password")) is not None:
        user.must_change_password = bool(must_change)
    if (access := changes.get("access_status")) is not None:
        user.access_status = access
    if payload.park_ids is not None:
        _set_parks(db, user, payload.park_ids)
    db.flush()
    if desired is not None:
        rbac.set_user_effective_permissions(db, user, sorted(desired))
    db.commit()
    user = _load_user(db, user.id)
    assert user is not None
    audit.record(
        db,
        action=audit.ACTION_SETTINGS_CHANGED,
        actor=actor,
        target_type="user",
        target_id=str(user.id),
        detail="admin user updated",
    )
    return _user_out(db, user, _user_parks(db, user.id), settings)


@router.post("/{user_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
def approve_user(
    user_id: int,
    payload: UserApproval,
    db: Session = Depends(get_db),
    actor: User = Depends(require_royal),
) -> None:
    user = _load_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if user.access_status not in {AccessStatus.pending.value, AccessStatus.rejected.value}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    if payload.park_ids:
        _set_parks(db, user, payload.park_ids)
    user.access_status = AccessStatus.approved.value
    db.commit()
    audit.record(
        db,
        action="admin.user.approve",
        actor=actor,
        target_type="user",
        target_id=str(user.id),
    )


@router.post("/{user_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_user(
    user_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_royal),
) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if rbac.is_last_active_royal(db, user):
        raise HTTPException(status_code=400, detail="cannot_disable_last_royal")
    user.access_status = AccessStatus.rejected.value
    db.commit()


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_user),
) -> None:
    if not _can_manage_users(db, actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    user = _load_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if user.id == actor.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_delete_self")
    if user.role == rbac.RoleSlug.ROYAL:
        if not rbac.is_royal(actor):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        if rbac.is_last_active_royal(db, user):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cannot_delete_last_royal",
            )
    username = user.username
    db.execute(
        update(Report).where(Report.author_user_id == user.id).values(author_user_id=actor.id)
    )
    _revoke_sessions(db, user.id)
    db.delete(user)
    db.commit()
    audit.record(
        db,
        action="admin.user.delete",
        actor=actor,
        target_type="user",
        target_id=str(user_id),
        detail=username,
    )
