import runpy
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session

from robopark_api.models import Permission, Role, RolePermission, User, UserPermission
from robopark_api.services import rbac
from robopark_api.services.rbac_seed import ensure_rbac_catalog

API_DIR = Path(__file__).parents[1]
DRIVER_DELTA = {"nav.tasks", "tracker.read", "nav.reports", "reports.create"}


def test_fresh_alembic_install_seeds_all_precreated_system_roles(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    command.upgrade(Config(API_DIR / "alembic.ini"), "head")
    engine = create_engine(sqlite_database_url)
    with Session(engine) as db:
        # Migrations must leave the never-initialized catalog empty.
        assert db.scalar(select(Permission.id).limit(1)) is None
        ensure_rbac_catalog(db)
        expected = {
            "admin": {"users.manage", "roles.manage", "reports.create"},
            "operator": {"tracker.write", "reports.resolve", "reports.create"},
            "mechanic": {"tracker.write", "reports.create"},
            "driver": DRIVER_DELTA,
        }
        for slug, keys in expected.items():
            role = db.scalar(select(Role).where(Role.slug == slug))
            assert keys <= {permission.key for permission in role.permissions}


def test_existing_driver_upgrade_preserves_user_denies_and_other_role_revokes(
    sqlite_database_url, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(API_DIR / "alembic.ini")
    command.upgrade(config, "0016_history_definition")
    engine = create_engine(sqlite_database_url)
    with Session(engine) as db:
        ensure_rbac_catalog(db)
        driver = db.scalar(select(Role).where(Role.slug == "driver"))
        mechanic = db.scalar(select(Role).where(Role.slug == "mechanic"))
        perm_ids = dict(db.execute(select(Permission.key, Permission.id)).all())
        # Recreate the pre-upgrade driver defaults, including an owner revoke.
        db.execute(
            delete(RolePermission).where(
                RolePermission.role_id == driver.id,
                RolePermission.permission_id.in_(
                    [perm_ids[key] for key in DRIVER_DELTA | {"nav.dashboard"}]
                ),
            )
        )
        db.execute(
            delete(RolePermission).where(
                RolePermission.role_id == mechanic.id,
                RolePermission.permission_id == perm_ids["reports.create"],
            )
        )
        user_id = db.execute(
            text("INSERT INTO users (username, password_hash, role_id, access_status, must_change_password, is_active) "
                 "VALUES ('driver-denied', 'unused', :role_id, 'approved', 0, 1)"),
            {"role_id": driver.id},
        ).lastrowid
        db.add(
            UserPermission(user_id=user_id, permission_id=perm_ids["reports.create"], granted=False)
        )
        db.commit()

    command.upgrade(config, "head")
    with Session(engine) as db:
        driver = db.scalar(select(Role).where(Role.slug == "driver"))
        keys = {permission.key for permission in driver.permissions}
        assert keys >= DRIVER_DELTA
        assert not {"tracker.write", "tracker.attach", "reports.resolve", "nav.dashboard"} & keys
        user = db.get(User, user_id)
        assert "reports.create" not in rbac.permissions_for_user(db, user)
        assert rbac.overrides_for_user(db, user_id)["reports.create"] is False
        mechanic = db.scalar(select(Role).where(Role.slug == "mechanic"))
        assert "reports.create" not in {permission.key for permission in mechanic.permissions}
        ensure_rbac_catalog(db)
        assert "reports.create" not in rbac.permissions_for_user(db, user)

    # Executing the delta twice (not just Alembic's revision check) is safe.
    migration = runpy.run_path(str(API_DIR / "alembic/versions/0017_driver_work_reports.py"))
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        migration["upgrade"]()
