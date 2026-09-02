from sqlalchemy import delete, select

from robopark_api.models import Permission, Role, RolePermission
from robopark_api.services.rbac_seed import ensure_rbac_catalog


def test_ensure_rbac_catalog_does_not_wipe_custom_role_permissions(db_session):
    operator = db_session.scalar(select(Role).where(Role.slug == "operator"))
    extra = db_session.scalar(select(Permission).where(Permission.key == "nav.map"))
    default_perm = db_session.scalar(select(Permission).where(Permission.key == "tracker.read"))
    assert operator is not None
    assert extra is not None
    assert default_perm is not None

    db_session.add(RolePermission(role_id=operator.id, permission_id=extra.id))
    db_session.execute(
        delete(RolePermission).where(
            RolePermission.role_id == operator.id,
            RolePermission.permission_id == default_perm.id,
        )
    )
    db_session.commit()

    ensure_rbac_catalog(db_session)

    keys = set(
        db_session.scalars(
            select(Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == operator.id)
        )
    )
    assert "nav.map" in keys
    assert "tracker.read" in keys


def test_ensure_rbac_catalog_adds_missing_system_role_permissions(db_session):
    mechanic = db_session.scalar(select(Role).where(Role.slug == "mechanic"))
    extra = db_session.scalar(select(Permission).where(Permission.key == "nav.map"))
    write_perm = db_session.scalar(select(Permission).where(Permission.key == "tracker.write"))
    read_perm = db_session.scalar(select(Permission).where(Permission.key == "tracker.read"))
    assert mechanic is not None
    assert extra is not None
    assert write_perm is not None
    assert read_perm is not None

    db_session.execute(delete(RolePermission).where(RolePermission.role_id == mechanic.id))
    db_session.add(RolePermission(role_id=mechanic.id, permission_id=read_perm.id))
    db_session.add(RolePermission(role_id=mechanic.id, permission_id=extra.id))
    db_session.commit()

    ensure_rbac_catalog(db_session)

    keys = set(
        db_session.scalars(
            select(Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == mechanic.id)
        )
    )
    assert "tracker.write" in keys
    assert "tracker.read" in keys
    assert "nav.map" in keys


def test_ensure_rbac_catalog_adds_driver_overview_and_robot_search(db_session):
    driver = db_session.scalar(select(Role).where(Role.slug == "driver"))
    assert driver is not None
    removed_ids = list(
        db_session.scalars(
            select(Permission.id).where(Permission.key.in_(["nav.dashboard", "nav.robot_search"]))
        )
    )
    db_session.execute(
        delete(RolePermission).where(
            RolePermission.role_id == driver.id,
            RolePermission.permission_id.in_(removed_ids),
        )
    )
    db_session.commit()

    ensure_rbac_catalog(db_session)

    keys = set(
        db_session.scalars(
            select(Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == driver.id)
        )
    )
    assert {"nav.dashboard", "nav.robot_search", "nav.emergency"} <= keys
