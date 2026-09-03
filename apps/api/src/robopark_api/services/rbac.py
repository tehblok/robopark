"""Role-based access control: permissions catalog and helpers."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import AccessStatus, Permission, Role, User, UserPermission


class RoleSlug:
    ROYAL = "royal"
    ADMIN = "admin"
    OPERATOR = "operator"
    MECHANIC = "mechanic"
    DRIVER = "driver"

    SYSTEM = frozenset({ROYAL, ADMIN, OPERATOR, MECHANIC, DRIVER})
    #: Admins/royals are created by an existing royal — never self-serve.
    SELF_REGISTER = frozenset({OPERATOR, MECHANIC, DRIVER})


# Permission keys referenced across API and frontend nav.
PERMISSION_NAV_DASHBOARD = "nav.dashboard"
PERMISSION_NAV_TASKS = "nav.tasks"
PERMISSION_NAV_ROBOT_SEARCH = "nav.robot_search"
PERMISSION_NAV_EMERGENCY = "nav.emergency"
PERMISSION_NAV_MAP = "nav.map"
PERMISSION_NAV_ANALYTICS = "nav.analytics"
PERMISSION_NAV_REPORTS = "nav.reports"
PERMISSION_NAV_LEARNING = "nav.learning"
PERMISSION_NAV_HELP = "nav.help"
PERMISSION_NAV_ADMIN = "nav.admin"
PERMISSION_NAV_ADMIN_TRACKER = "nav.admin.tracker"
PERMISSION_NAV_ADMIN_EMERGENCY = "nav.admin.emergency"

PERMISSION_TRACKER_READ = "tracker.read"
PERMISSION_TRACKER_WRITE = "tracker.write"
PERMISSION_TRACKER_ATTACH = "tracker.attach"

PERMISSION_REPORTS_CREATE = "reports.create"
PERMISSION_REPORTS_RESOLVE = "reports.resolve"

PERMISSION_ROLES_MANAGE = "roles.manage"
PERMISSION_USERS_MANAGE = "users.manage"
PERMISSION_USERS_APPROVE = "users.approve"
PERMISSION_PARKS_MANAGE = "parks.manage"

ALL_PERMISSIONS: tuple[str, ...] = (
    PERMISSION_NAV_DASHBOARD,
    PERMISSION_NAV_TASKS,
    PERMISSION_NAV_ROBOT_SEARCH,
    PERMISSION_NAV_EMERGENCY,
    PERMISSION_NAV_MAP,
    PERMISSION_NAV_ANALYTICS,
    PERMISSION_NAV_REPORTS,
    PERMISSION_NAV_LEARNING,
    PERMISSION_NAV_HELP,
    PERMISSION_NAV_ADMIN,
    PERMISSION_NAV_ADMIN_TRACKER,
    PERMISSION_NAV_ADMIN_EMERGENCY,
    PERMISSION_TRACKER_READ,
    PERMISSION_TRACKER_WRITE,
    PERMISSION_TRACKER_ATTACH,
    PERMISSION_REPORTS_CREATE,
    PERMISSION_REPORTS_RESOLVE,
    PERMISSION_ROLES_MANAGE,
    PERMISSION_USERS_MANAGE,
    PERMISSION_USERS_APPROVE,
    PERMISSION_PARKS_MANAGE,
)

PRIVILEGED_PERMISSIONS: frozenset[str] = frozenset(
    {
        PERMISSION_NAV_ADMIN,
        PERMISSION_NAV_ADMIN_TRACKER,
        PERMISSION_NAV_ADMIN_EMERGENCY,
        PERMISSION_ROLES_MANAGE,
        PERMISSION_USERS_MANAGE,
        PERMISSION_USERS_APPROVE,
        PERMISSION_PARKS_MANAGE,
    }
)


def privileged_grant_blocked(actor: User, existing: set[str], desired: set[str]) -> bool:
    if is_royal(actor):
        return False
    return bool((desired - existing) & PRIVILEGED_PERMISSIONS)


@dataclass(frozen=True)
class PermissionDef:
    key: str
    category: str
    label: str
    sort_order: int


PERMISSION_CATALOG: tuple[PermissionDef, ...] = (
    PermissionDef(PERMISSION_NAV_DASHBOARD, "nav", "Дашборд", 10),
    PermissionDef(PERMISSION_NAV_TASKS, "nav", "Задачи", 20),
    PermissionDef(PERMISSION_NAV_ROBOT_SEARCH, "nav", "Поиск робота", 30),
    PermissionDef(PERMISSION_NAV_EMERGENCY, "nav", "Проверка робота", 40),
    PermissionDef(PERMISSION_NAV_MAP, "nav", "Карта", 50),
    PermissionDef(PERMISSION_NAV_ANALYTICS, "nav", "Аналитика", 60),
    PermissionDef(PERMISSION_NAV_REPORTS, "nav", "Обращения", 70),
    PermissionDef(PERMISSION_NAV_LEARNING, "nav", "Обучение", 80),
    PermissionDef(PERMISSION_NAV_HELP, "nav", "Помощь", 90),
    PermissionDef(PERMISSION_NAV_ADMIN, "nav", "Админ-панель", 100),
    PermissionDef(PERMISSION_NAV_ADMIN_TRACKER, "nav", "Tracker workspace", 110),
    PermissionDef(PERMISSION_NAV_ADMIN_EMERGENCY, "nav", "Emergency config", 120),
    PermissionDef(PERMISSION_TRACKER_READ, "action", "Чтение тикетов", 200),
    PermissionDef(PERMISSION_TRACKER_WRITE, "action", "Запись в тикеты", 210),
    PermissionDef(PERMISSION_TRACKER_ATTACH, "action", "Фото во тикеты", 220),
    PermissionDef(PERMISSION_REPORTS_CREATE, "action", "Создание обращений", 230),
    PermissionDef(PERMISSION_REPORTS_RESOLVE, "action", "Закрытие обращений", 240),
    PermissionDef(PERMISSION_ROLES_MANAGE, "action", "Управление ролями", 300),
    PermissionDef(PERMISSION_USERS_MANAGE, "action", "Управление пользователями", 310),
    PermissionDef(PERMISSION_USERS_APPROVE, "action", "Одобрение регистраций", 320),
    PermissionDef(PERMISSION_PARKS_MANAGE, "action", "Управление парками", 330),
)


DEFAULT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    RoleSlug.ROYAL: frozenset(ALL_PERMISSIONS),
    RoleSlug.ADMIN: frozenset(perm for perm in ALL_PERMISSIONS if perm != PERMISSION_USERS_APPROVE),
    RoleSlug.OPERATOR: frozenset(
        {
            PERMISSION_NAV_DASHBOARD,
            PERMISSION_NAV_TASKS,
            PERMISSION_NAV_ROBOT_SEARCH,
            PERMISSION_NAV_EMERGENCY,
            PERMISSION_NAV_ANALYTICS,
            PERMISSION_NAV_REPORTS,
            PERMISSION_TRACKER_READ,
            PERMISSION_TRACKER_WRITE,
            PERMISSION_TRACKER_ATTACH,
            PERMISSION_REPORTS_CREATE,
            PERMISSION_REPORTS_RESOLVE,
        }
    ),
    RoleSlug.MECHANIC: frozenset(
        {
            PERMISSION_NAV_DASHBOARD,
            PERMISSION_NAV_TASKS,
            PERMISSION_NAV_ROBOT_SEARCH,
            PERMISSION_NAV_EMERGENCY,
            PERMISSION_NAV_REPORTS,
            PERMISSION_TRACKER_READ,
            PERMISSION_TRACKER_WRITE,
            PERMISSION_TRACKER_ATTACH,
            PERMISSION_REPORTS_CREATE,
        }
    ),
    RoleSlug.DRIVER: frozenset(
        {
            PERMISSION_NAV_DASHBOARD,
            PERMISSION_NAV_TASKS,
            PERMISSION_NAV_ROBOT_SEARCH,
            PERMISSION_NAV_EMERGENCY,
            PERMISSION_NAV_REPORTS,
            PERMISSION_TRACKER_READ,
            PERMISSION_REPORTS_CREATE,
        }
    ),
}


def role_slug(user: User) -> str:
    if user.role_ref is None:
        return ""
    return user.role_ref.slug


def is_royal(user: User) -> bool:
    return role_slug(user) == RoleSlug.ROYAL


def is_admin_or_royal(user: User) -> bool:
    slug = role_slug(user)
    return slug in {RoleSlug.ROYAL, RoleSlug.ADMIN}


def load_user_with_role(db: Session, user_id: int) -> User | None:
    return db.scalar(
        select(User)
        .options(joinedload(User.role_ref).joinedload(Role.permissions))
        .where(User.id == user_id)
    )


def role_permission_keys(db: Session, user: User) -> set[str]:
    if is_royal(user):
        return set(ALL_PERMISSIONS)
    if user.role_ref is None:
        user = load_user_with_role(db, user.id) or user
    if user.role_ref is None:
        return set()
    return {perm.key for perm in user.role_ref.permissions}


def proposed_user_permissions(role: Role, keys: list[str] | None = None) -> set[str]:
    """Compute a proposed effective grant without changing the target user."""
    if role.slug == RoleSlug.ROYAL:
        return set(ALL_PERMISSIONS)
    desired = {perm.key for perm in role.permissions} if keys is None else set(keys)
    return desired & (set(ALL_PERMISSIONS) - {PERMISSION_USERS_APPROVE})


def overrides_for_user(db: Session, user_id: int) -> dict[str, bool]:
    rows = db.execute(
        select(Permission.key, UserPermission.granted)
        .select_from(UserPermission)
        .join(Permission, Permission.id == UserPermission.permission_id)
        .where(UserPermission.user_id == user_id)
    ).all()
    return {key: bool(granted) for key, granted in rows}


def permissions_for_user(db: Session, user: User) -> set[str]:
    if is_royal(user):
        return set(ALL_PERMISSIONS)
    perms = role_permission_keys(db, user)
    for key, granted in overrides_for_user(db, user.id).items():
        if granted:
            perms.add(key)
        else:
            perms.discard(key)
    perms.discard(PERMISSION_USERS_APPROVE)
    return perms


def set_user_effective_permissions(db: Session, user: User, keys: list[str]) -> None:
    db.execute(delete(UserPermission).where(UserPermission.user_id == user.id))
    if is_royal(user):
        return
    allowed = set(ALL_PERMISSIONS) - {PERMISSION_USERS_APPROVE}
    desired = {key for key in keys if key in allowed}
    role_keys = role_permission_keys(db, user) - {PERMISSION_USERS_APPROVE}
    perm_ids = dict(db.execute(select(Permission.key, Permission.id)).all())
    for key in allowed:
        in_desired = key in desired
        in_role = key in role_keys
        if in_desired == in_role:
            continue
        perm_id = perm_ids.get(key)
        if perm_id is None:
            continue
        db.add(UserPermission(user_id=user.id, permission_id=perm_id, granted=in_desired))


def has_permission(db: Session, user: User, permission: str) -> bool:
    return permission in permissions_for_user(db, user)


def assert_approved(user: User) -> None:
    """Every privileged API caller must be approved — including admin/royal."""
    from robopark_api.models import AccessStatus

    if user.access_status != AccessStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def assert_approved_or_staff(user: User) -> None:
    """Deprecated alias — staff no longer bypass approval."""
    assert_approved(user)


def require_approved_permission(db: Session, user: User, permission: str) -> None:
    assert_approved_or_staff(user)
    if not has_permission(db, user, permission):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def count_active_royals(db: Session, *, exclude_user_id: int | None = None) -> int:
    royal = get_role_by_slug(db, RoleSlug.ROYAL)
    if royal is None:
        return 0
    query = select(func.count()).where(
        User.role_id == royal.id,
        User.is_active.is_(True),
        User.access_status == AccessStatus.approved.value,
    )
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    return int(db.scalar(query) or 0)


def is_last_active_royal(db: Session, user: User) -> bool:
    if (
        role_slug(user) != RoleSlug.ROYAL
        or not user.is_active
        or user.access_status != AccessStatus.approved.value
    ):
        return False
    return count_active_royals(db) <= 1


def require_permission(permission: str):
    def dep(user: User = Depends(require_user), db: Session = Depends(get_db)) -> User:
        assert_approved(user)
        if not has_permission(db, user, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user

    return dep


def require_royal(user: User = Depends(require_user)) -> User:
    assert_approved(user)
    if not is_royal(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def require_admin_panel(user: User = Depends(require_user), db: Session = Depends(get_db)) -> User:
    assert_approved(user)
    if not has_permission(db, user, PERMISSION_NAV_ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def get_role_by_slug(db: Session, slug: str) -> Role | None:
    return db.scalar(select(Role).where(Role.slug == slug))


def assign_role(db: Session, user: User, slug: str) -> None:
    role = get_role_by_slug(db, slug)
    if role is None:
        raise ValueError(f"unknown role slug: {slug}")
    user.role_id = role.id


def user_has_cabinet(user: User) -> bool:
    """Whether the user gets the main app shell after approval."""
    slug = role_slug(user)
    if slug == RoleSlug.DRIVER:
        return True
    if slug in {RoleSlug.ROYAL, RoleSlug.ADMIN}:
        return True
    if slug in {RoleSlug.OPERATOR, RoleSlug.MECHANIC}:
        from robopark_api.models import AccessStatus

        return user.access_status == AccessStatus.approved.value
    return False


def user_needs_park_for_shell(user: User) -> bool:
    return role_slug(user) == RoleSlug.MECHANIC
