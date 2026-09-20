from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from robopark_api.models import Permission, Role, RolePermission
from robopark_api.services.rbac_seed import ensure_rbac_catalog

API_DIR = Path(__file__).parents[1]
WRITE_PERMISSIONS = {
    "inventory.stock.manage",
    "inventory.documents.post",
    "inventory.export",
}


def test_upgrade_revokes_operator_inventory_writes_only(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(API_DIR / "alembic.ini")
    command.upgrade(config, "0031_postgresql_runtime")
    engine = create_engine(sqlite_database_url)

    with Session(engine) as db:
        ensure_rbac_catalog(db)
        operator = db.scalar(select(Role).where(Role.slug == "operator"))
        mechanic = db.scalar(select(Role).where(Role.slug == "mechanic"))
        permissions = {
            permission.key: permission
            for permission in db.scalars(
                select(Permission).where(Permission.key.in_(WRITE_PERMISSIONS))
            )
        }
        for permission in permissions.values():
            db.add(RolePermission(role_id=operator.id, permission_id=permission.id))
        db.commit()

    command.upgrade(config, "head")

    with Session(engine) as db:
        operator = db.scalar(select(Role).where(Role.slug == "operator"))
        mechanic = db.scalar(select(Role).where(Role.slug == "mechanic"))
        assert not WRITE_PERMISSIONS & {permission.key for permission in operator.permissions}
        assert {permission.key for permission in mechanic.permissions} >= WRITE_PERMISSIONS
