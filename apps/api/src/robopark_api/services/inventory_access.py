from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.services.rbac import (
    PERMISSION_INVENTORY_CATALOG_MANAGE,
    PERMISSION_INVENTORY_STOCK_MANAGE,
    PERMISSION_NAV_INVENTORY,
    has_permission,
)


def can_view_inventory(db: Session, user: User) -> bool:
    return bool(
        user.is_active
        and user.access_status == AccessStatus.approved.value
        and has_permission(db, user, PERMISSION_NAV_INVENTORY)
    )


def accessible_park_ids(db: Session, user: User) -> set[int]:
    if not can_view_inventory(db, user):
        return set()
    statement = select(Park.id).where(Park.is_active.is_(True))
    if user.role not in {"admin", "royal", "operator"}:
        statement = statement.join(UserPark).where(UserPark.user_id == user.id)
    return set(db.scalars(statement))


def can_view_park(db: Session, user: User, park_id: int) -> bool:
    """Check one park without materializing every accessible park on each hint poll."""
    if not can_view_inventory(db, user):
        return False
    statement = select(Park.id).where(Park.id == park_id, Park.is_active.is_(True))
    if user.role not in {"admin", "royal", "operator"}:
        statement = statement.join(UserPark).where(UserPark.user_id == user.id)
    return db.scalar(statement) is not None


def require_park(db: Session, user: User, park_id: int, *, manage: bool = False) -> Park:
    if not can_view_inventory(db, user):
        raise PermissionError("forbidden")
    statement = (
        select(Park)
        .options(load_only(Park.id, Park.tag))
        .where(Park.id == park_id, Park.is_active.is_(True))
    )
    if user.role not in {"admin", "royal", "operator"}:
        statement = statement.join(UserPark).where(UserPark.user_id == user.id)
    park = db.scalar(statement)
    if park is None:
        raise PermissionError("forbidden")
    if manage and not has_permission(db, user, PERMISSION_INVENTORY_STOCK_MANAGE):
        raise PermissionError("forbidden")
    return park


def require_catalog_manage(db: Session, user: User) -> None:
    if not can_view_inventory(db, user) or not has_permission(
        db, user, PERMISSION_INVENTORY_CATALOG_MANAGE
    ):
        raise PermissionError("forbidden")
