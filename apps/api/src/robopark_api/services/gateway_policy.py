"""Central ownership policy for corporate Mac gateways."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.services import rbac

GatewayCapability = Literal["emergency", "inventory", "tracker"]

_CAPABILITY_PERMISSION: dict[GatewayCapability, str] = {
    "emergency": rbac.PERMISSION_NAV_EMERGENCY,
    # External SDC Inventory follows the Emergency robot/VIN ACL. Warehouse
    # stock permissions are a separate local subsystem and never grant this.
    "inventory": rbac.PERMISSION_NAV_EMERGENCY,
    "tracker": rbac.PERMISSION_TRACKER_READ,
}


def requester_can_gateway(db: Session, user: User, capability: GatewayCapability) -> bool:
    """Return whether this active approved account has the capability at all."""
    return bool(
        user.is_active
        and user.access_status == AccessStatus.approved.value
        and rbac.has_permission(db, user, _CAPABILITY_PERMISSION[capability])
    )


def eligible_gateway_owner_ids(
    db: Session,
    requester: User,
    capability: GatewayCapability,
) -> frozenset[int]:
    """Return authoritative Mac owners allowed to serve this requester.

    Operators, admins and royals are deliberately self-only. Mechanics may
    use approved operators assigned to at least one of the same active parks.
    The query is evaluated for every dispatch so account and park changes take
    effect without a policy cache.
    """
    if not requester_can_gateway(db, requester, capability):
        return frozenset()
    role = rbac.role_slug(requester)
    if role in {rbac.RoleSlug.OPERATOR, rbac.RoleSlug.ADMIN, rbac.RoleSlug.ROYAL}:
        return frozenset({requester.id})
    if role != rbac.RoleSlug.MECHANIC:
        return frozenset()

    requester_park_ids = (
        select(UserPark.park_id)
        .join(Park)
        .where(
            UserPark.user_id == requester.id,
            Park.is_active.is_(True),
        )
    )
    possible_owners = db.scalars(
        select(User)
        .join(UserPark, UserPark.user_id == User.id)
        .join(Park, Park.id == UserPark.park_id)
        .where(
            UserPark.park_id.in_(requester_park_ids),
            Park.is_active.is_(True),
            User.is_active.is_(True),
            User.access_status == AccessStatus.approved.value,
        )
    ).all()
    return frozenset(
        owner.id
        for owner in possible_owners
        if rbac.role_slug(owner) == rbac.RoleSlug.OPERATOR
        and requester_can_gateway(db, owner, capability)
    )
