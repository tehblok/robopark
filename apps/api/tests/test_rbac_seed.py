from sqlalchemy import delete, select

from robopark_api.models import Permission, Role, RolePermission
from robopark_api.services.rbac_seed import ensure_rbac_catalog


def test_existing_role_receives_only_new_default_permissions(db_session):
    mechanic = db_session.scalar(select(Role).where(Role.slug == "mechanic"))
    assert mechanic is not None
    new_keys = {
        "inventory.stock.manage",
        "inventory.documents.post",
        "inventory.export",
        "inventory.catalog.manage",
    }
    new_permission_ids = list(
        db_session.scalars(select(Permission.id).where(Permission.key.in_(new_keys)))
    )
    revoked_old = db_session.scalar(select(Permission).where(Permission.key == "tracker.write"))
    assert revoked_old is not None
    db_session.execute(
        delete(RolePermission).where(RolePermission.permission_id.in_(new_permission_ids))
    )
    db_session.execute(delete(Permission).where(Permission.id.in_(new_permission_ids)))
    db_session.execute(
        delete(RolePermission).where(
            RolePermission.role_id == mechanic.id,
            RolePermission.permission_id == revoked_old.id,
        )
    )
    db_session.commit()

    ensure_rbac_catalog(db_session)

    keys = set(
        db_session.scalars(
            select(Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == mechanic.id)
        )
    )
    assert {
        "inventory.stock.manage",
        "inventory.documents.post",
        "inventory.export",
    } <= keys
    assert "inventory.catalog.manage" not in keys
    assert "tracker.write" not in keys


def test_ensure_rbac_catalog_does_not_wipe_custom_role_permissions(db_session):
    operator = db_session.scalar(select(Role).where(Role.slug == "operator"))
    extra = db_session.scalar(select(Permission).where(Permission.key == "nav.learning"))
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
    assert "nav.learning" in keys
    assert "tracker.read" not in keys


def test_ensure_rbac_catalog_preserves_removed_system_role_permissions(db_session):
    mechanic = db_session.scalar(select(Role).where(Role.slug == "mechanic"))
    extra = db_session.scalar(select(Permission).where(Permission.key == "nav.map"))
    if extra is None:
        extra = Permission(key="nav.map", category="nav", label="Карта", sort_order=50)
        db_session.add(extra)
        db_session.flush()
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
    assert "tracker.write" not in keys
    assert "tracker.read" in keys
    assert "nav.map" in keys


def test_ensure_rbac_catalog_preserves_driver_revocations(db_session):
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
    assert "nav.emergency" in keys
    assert not {"nav.dashboard", "nav.robot_search"} & keys


def test_ensure_rbac_catalog_preserves_role_metadata(db_session):
    driver = db_session.scalar(select(Role).where(Role.slug == "driver"))
    driver.name = "Edited name"
    driver.description = "Edited description"
    db_session.commit()
    ensure_rbac_catalog(db_session)
    db_session.refresh(driver)
    assert driver.name == "Edited name"
    assert driver.description == "Edited description"


def test_new_driver_defaults_include_read_tasks_and_reports_not_writes(db_session):
    keys = set(
        db_session.scalars(
            select(Permission.key).join(RolePermission).join(Role).where(Role.slug == "driver")
        )
    )
    assert {"nav.tasks", "tracker.read", "nav.reports", "reports.create"} <= keys
    assert not {"tracker.write", "tracker.attach", "reports.resolve"} & keys
