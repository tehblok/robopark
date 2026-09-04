"""Idempotent seed for roles and permissions catalog."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import Permission, Role, RolePermission
from robopark_api.services.rbac import (
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_CATALOG,
    RoleSlug,
)

SYSTEM_ROLE_META: dict[str, tuple[str, str]] = {
    RoleSlug.ROYAL: ("Владелец", "Полный доступ, одобрение регистраций"),
    RoleSlug.ADMIN: ("Администратор", "Управление парками и пользователями"),
    RoleSlug.OPERATOR: ("Оператор", "Операционный мониторинг парков"),
    RoleSlug.MECHANIC: ("Механик", "Работа с задачами на площадке"),
    RoleSlug.DRIVER: (
        "Водитель",
        "Обзор, чтение задач, поиск робота и создание обращений",
    ),
}


def ensure_rbac_catalog(db: Session) -> None:
    # 0013 creates roles before the app's first startup. An empty permission
    # catalog identifies that bootstrap, not a role whose owner revoked grants.
    initial_catalog = db.scalar(select(Permission.id).limit(1)) is None
    perm_by_key: dict[str, Permission] = {}
    for item in PERMISSION_CATALOG:
        row = db.scalar(select(Permission).where(Permission.key == item.key))
        if row is None:
            row = Permission(
                key=item.key,
                category=item.category,
                label=item.label,
                sort_order=item.sort_order,
            )
            db.add(row)
            db.flush()
        else:
            row.category = item.category
            row.label = item.label
            row.sort_order = item.sort_order
        perm_by_key[item.key] = row

    for slug, (name, description) in SYSTEM_ROLE_META.items():
        role = db.scalar(select(Role).where(Role.slug == slug))
        is_new_role = role is None
        if role is None:
            role = Role(
                slug=slug,
                name=name,
                description=description,
                is_system=True,
                is_active=True,
            )
            db.add(role)
            db.flush()
        if not is_new_role and not initial_catalog:
            continue

        existing_keys = set(
            db.scalars(
                select(Permission.key)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
            )
        )
        desired = DEFAULT_ROLE_PERMISSIONS.get(slug, frozenset())
        for key in desired:
            if key in existing_keys:
                continue
            perm = perm_by_key.get(key)
            if perm is not None:
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))

    db.commit()
