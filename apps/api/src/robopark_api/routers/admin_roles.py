from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from robopark_api.db import get_db
from robopark_api.deps import require_royal, require_user
from robopark_api.models import Permission, Role, RolePermission, User
from robopark_api.services import audit, rbac
from robopark_api.services.rbac import PERMISSION_CATALOG

router = APIRouter(prefix="/admin/roles", tags=["admin-roles"])


class PermissionOut(BaseModel):
    key: str
    category: str
    label: str
    sort_order: int


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    description: str
    is_system: bool
    is_active: bool
    permissions: list[str]
    user_count: int = 0


class RoleCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    is_active: bool | None = None
    permissions: list[str] | None = None


def _role_out(role: Role, user_count: int = 0) -> RoleOut:
    return RoleOut(
        id=role.id,
        slug=role.slug,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        is_active=role.is_active,
        permissions=[perm.key for perm in role.permissions],
        user_count=user_count,
    )


def _load_role(db: Session, role_id: int) -> Role | None:
    return (
        db.scalars(select(Role).options(joinedload(Role.permissions)).where(Role.id == role_id))
        .unique()
        .one_or_none()
    )


def _set_role_permissions(db: Session, role: Role, keys: list[str]) -> None:
    allowed = {item.key for item in PERMISSION_CATALOG}
    unknown = [key for key in keys if key not in allowed]
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_permissions")
    if role.slug == rbac.RoleSlug.ROYAL and rbac.PERMISSION_USERS_APPROVE not in keys:
        keys = [*keys, rbac.PERMISSION_USERS_APPROVE]
    db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    if keys:
        perms = db.scalars(select(Permission).where(Permission.key.in_(keys))).all()
        for perm in perms:
            db.add(RolePermission(role_id=role.id, permission_id=perm.id))


def _require_catalog_reader(
    user: User = Depends(require_user), db: Session = Depends(get_db)
) -> User:
    rbac.assert_approved(user)
    if not rbac.permissions_for_user(db, user) & {
        rbac.PERMISSION_USERS_MANAGE,
        rbac.PERMISSION_ROLES_MANAGE,
    }:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


@router.get("/permissions/catalog", response_model=list[PermissionOut])
def list_permission_catalog(
    _user: User = Depends(_require_catalog_reader),
) -> list[PermissionOut]:
    return [
        PermissionOut(
            key=item.key, category=item.category, label=item.label, sort_order=item.sort_order
        )
        for item in PERMISSION_CATALOG
    ]


@router.get("", response_model=list[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    _user: User = Depends(_require_catalog_reader),
) -> list[RoleOut]:
    counts = dict(db.execute(select(User.role_id, func.count()).group_by(User.role_id)).all())
    roles = (
        db.scalars(select(Role).options(joinedload(Role.permissions)).order_by(Role.id))
        .unique()
        .all()
    )
    return [_role_out(role, int(counts.get(role.id, 0))) for role in roles]


@router.post("", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_user),
) -> RoleOut:
    rbac.assert_approved(actor)
    if not rbac.is_royal(actor) and not rbac.has_permission(
        db, actor, rbac.PERMISSION_ROLES_MANAGE
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if payload.slug in rbac.RoleSlug.SYSTEM:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role_slug_reserved")
    if db.scalar(select(Role.id).where(Role.slug == payload.slug)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    existing: set[str] = set()
    desired = set(payload.permissions or [])
    if rbac.privileged_grant_blocked(actor, existing, desired):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="privileged_grant_forbidden",
        )
    role = Role(
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        is_system=False,
        is_active=True,
    )
    db.add(role)
    db.flush()
    _set_role_permissions(db, role, payload.permissions)
    db.commit()
    role = _load_role(db, role.id)
    assert role is not None
    audit.record(
        db,
        action="admin.role.create",
        actor=actor,
        target_type="role",
        target_id=str(role.id),
        detail=role.slug,
    )
    return _role_out(role)


@router.patch("/{role_id}", response_model=RoleOut)
def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_user),
) -> RoleOut:
    rbac.assert_approved(actor)
    if not rbac.is_royal(actor) and not rbac.has_permission(
        db, actor, rbac.PERMISSION_ROLES_MANAGE
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    role = _load_role(db, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if role.slug == rbac.RoleSlug.ROYAL and not rbac.is_royal(actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is not None:
        role.name = changes["name"]
    if "description" in changes and changes["description"] is not None:
        role.description = changes["description"]
    if "is_active" in changes and changes["is_active"] is not None:
        if role.is_system and changes["is_active"] is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="system_role_protected",
            )
        role.is_active = changes["is_active"]
    if payload.permissions is not None:
        if role.slug == rbac.RoleSlug.ROYAL and not rbac.is_royal(actor):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        existing = {perm.key for perm in role.permissions}
        desired = set(payload.permissions or [])
        if rbac.privileged_grant_blocked(actor, existing, desired):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="privileged_grant_forbidden",
            )
        _set_role_permissions(db, role, payload.permissions)
    db.commit()
    role = _load_role(db, role.id)
    assert role is not None
    count = db.scalar(select(func.count()).where(User.role_id == role.id)) or 0
    return _role_out(role, int(count))


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_royal),
) -> None:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if role.is_system:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="system_role_protected")
    in_use = db.scalar(select(func.count()).where(User.role_id == role.id)) or 0
    if in_use:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="role_in_use")
    db.delete(role)
    db.commit()
