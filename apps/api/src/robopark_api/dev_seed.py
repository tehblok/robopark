"""Local development fixtures — idempotent demo users and a demo park.

Enable with ``DEV_SEED=true`` in ``apps/api/.env``. Never turn this on in
production deploys.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import Settings
from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.rbac_seed import ensure_rbac_catalog

DEV_PARK_TAG = "Demo"
DEV_PARK_NAME = "Demo Park"
DEV_PARK_QUEUE = "ROBOPARK"


@dataclass(frozen=True, slots=True)
class DevAccount:
    username: str
    password: str
    role_slug: str
    access_status: AccessStatus = AccessStatus.approved
    tracker_login: str | None = None
    park_tag: str | None = DEV_PARK_TAG


# Dev-only credentials documented in README and docs/DEV-ACCOUNTS.md.
DEV_ACCOUNTS: tuple[DevAccount, ...] = (
    DevAccount("royal", "RoboparkRoyal!1", RoleSlug.ROYAL),
    DevAccount("admin", "RoboparkAdmin!1", RoleSlug.ADMIN),
    DevAccount("operator", "RoboparkOperator!1", RoleSlug.OPERATOR),
    DevAccount(
        "mechanic",
        "RoboparkMechanic!1",
        RoleSlug.MECHANIC,
        tracker_login="mechanic.dev",
    ),
    DevAccount("driver", "RoboparkDriver!1", RoleSlug.DRIVER, park_tag=None),
    DevAccount(
        "operator_pending",
        "RoboparkPending!1",
        RoleSlug.OPERATOR,
        access_status=AccessStatus.pending,
        park_tag=None,
    ),
    DevAccount(
        "operator_rejected",
        "RoboparkRejected!1",
        RoleSlug.OPERATOR,
        access_status=AccessStatus.rejected,
        park_tag=None,
    ),
)


def ensure_dev_seed(db: Session, settings: Settings) -> None:
    if not settings.dev_seed:
        return

    ensure_rbac_catalog(db)
    park = _ensure_demo_park(db)
    for account in DEV_ACCOUNTS:
        user = _ensure_user(db, account)
        if account.park_tag and park is not None:
            _ensure_user_park(db, user.id, park.id)


def _ensure_demo_park(db: Session) -> Park | None:
    park = db.scalar(select(Park).where(Park.tag == DEV_PARK_TAG))
    if park is not None:
        return park

    park = Park(
        name=DEV_PARK_NAME,
        tag=DEV_PARK_TAG,
        is_active=True,
        tracker_queue=DEV_PARK_QUEUE,
        feature_blockers=True,
        feature_reports=True,
        feature_sla_repair=True,
    )
    db.add(park)
    db.commit()
    db.refresh(park)
    return park


def _ensure_user(db: Session, account: DevAccount) -> User:
    existing = db.scalar(select(User).where(User.username == account.username))
    if existing is not None:
        return existing

    role = rbac.get_role_by_slug(db, account.role_slug)
    assert role is not None

    user = User(
        username=account.username,
        password_hash=hash_password(account.password),
        role_id=role.id,
        access_status=account.access_status.value,
        tracker_login=account.tracker_login,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _ensure_user_park(db: Session, user_id: int, park_id: int) -> None:
    link = db.scalar(
        select(UserPark).where(UserPark.user_id == user_id, UserPark.park_id == park_id)
    )
    if link is not None:
        return
    db.add(UserPark(user_id=user_id, park_id=park_id))
    db.commit()
