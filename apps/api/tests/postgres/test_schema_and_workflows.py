from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from robopark_api.collaboration_models import TrackerPresence
from robopark_api.db import configure_engine
from robopark_api.models import (
    Base,
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryMovement,
    InventoryParkStock,
    Park,
    Permission,
    Role,
    RolePermission,
    User,
)
from robopark_api.routers.tracker_collaboration import _presence_insert
from robopark_api.services import inventory_exports, inventory_stock, task_timeline
from robopark_api.services.ops.snapshot import restore_snapshot_tree
from robopark_api.services.rbac_seed import ensure_rbac_catalog
from robopark_api.task_workflow_models import ReliableAction, TaskMessage

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def migrated_engine(postgres_database_url: str) -> Engine:
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = postgres_database_url
    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")
        engine = configure_engine(postgres_database_url)
        try:
            yield engine
        finally:
            engine.dispose()
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


def test_postgresql_engine_uses_bounded_pre_ping_pool(postgres_database_url: str) -> None:
    engine = configure_engine(postgres_database_url)
    try:
        assert isinstance(engine.pool, QueuePool)
        assert engine.pool.size() == 5
        assert engine.pool._max_overflow == 5
        assert engine.pool._pre_ping is True
        assert engine.pool._recycle == 300
    finally:
        engine.dispose()


def test_alembic_head_matches_model_tables_and_indexes(migrated_engine: Engine) -> None:
    database = inspect(migrated_engine)
    assert set(Base.metadata.tables) <= set(database.get_table_names())
    for table in Base.metadata.sorted_tables:
        expected = {index.name for index in table.indexes}
        actual = {index["name"] for index in database.get_indexes(table.name)}
        assert expected <= actual, table.name


def test_postgresql_17_upgrades_operator_inventory_grants_to_read_only(
    postgres_container_name: str,
    postgres_database_url: str,
) -> None:
    database_name = "operator_inventory_upgrade"
    subprocess.run(
        ["docker", "exec", postgres_container_name, "createdb", "-U", "robopark", database_name],
        check=True,
        capture_output=True,
        text=True,
    )
    database_url = f"{postgres_database_url.rsplit('/', 1)[0]}/{database_name}"
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    engine = None
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "0031_postgresql_runtime")
        engine = configure_engine(database_url)
        with Session(engine) as db:
            ensure_rbac_catalog(db)
            operator = db.scalar(select(Role).where(Role.slug == "operator"))
            permissions = list(
                db.scalars(
                    select(Permission).where(
                        Permission.key.in_(
                            {
                                "inventory.stock.manage",
                                "inventory.documents.post",
                                "inventory.export",
                            }
                        )
                    )
                )
            )
            db.add_all(
                RolePermission(role_id=operator.id, permission_id=permission.id)
                for permission in permissions
            )
            db.commit()

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0035_schedules_and_push"
            )
            assert (
                connection.scalar(
                    text("""
                    SELECT count(*) FROM role_permissions
                    JOIN roles ON roles.id = role_permissions.role_id
                    JOIN permissions ON permissions.id = role_permissions.permission_id
                    WHERE roles.slug = 'operator'
                      AND permissions.key IN (
                        'inventory.stock.manage',
                        'inventory.documents.post',
                        'inventory.export'
                      )
                """)
                )
                == 0
            )
    finally:
        if engine is not None:
            engine.dispose()
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        subprocess.run(
            ["docker", "exec", postgres_container_name, "dropdb", "-U", "robopark", database_name],
            check=False,
            capture_output=True,
            text=True,
        )


def test_stock_postgres_17_accepts_configured_user_restore_command(
    migrated_engine: Engine, postgres_container_name: str, tmp_path: Path
) -> None:
    tree = tmp_path / "snapshot"
    dump = tree / "data/robopark.dump"
    dump.parent.mkdir(parents=True)
    subprocess.run(
        [
            "docker",
            "exec",
            postgres_container_name,
            "pg_dump",
            "--format=custom",
            "--username=robopark",
            "--dbname=robopark",
            "--file=/tmp/robopark.dump",
        ],
        check=True,
    )
    subprocess.run(
        ["docker", "cp", f"{postgres_container_name}:/tmp/robopark.dump", str(dump)],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", postgres_container_name, "createdb", "-U", "robopark", "candidate"],
        check=True,
    )

    def execute(argv, **_kwargs):
        translated = ["/tmp/robopark.dump" if value == str(dump) else value for value in argv]
        return subprocess.run(
            ["docker", "exec", postgres_container_name, *translated],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    restore_snapshot_tree(
        tree,
        database_url="postgresql://robopark:robopark-test@127.0.0.1/candidate",
        config_targets={},
        run=execute,
    )
    head = subprocess.run(
        [
            "docker",
            "exec",
            postgres_container_name,
            "psql",
            "-U",
            "robopark",
            "-d",
            "candidate",
            "-tAc",
            "SELECT version_num FROM alembic_version",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "0035_schedules_and_push"


def _seed_inventory(engine: Engine) -> tuple[int, int, int]:
    with Session(engine) as db:
        existing = db.scalar(
            select(InventoryCatalogPart).where(
                InventoryCatalogPart.normalized_article == "pg-motor"
            )
        )
        if existing is not None:
            stock = db.scalar(
                select(InventoryParkStock).where(InventoryParkStock.catalog_part_id == existing.id)
            )
            return stock.updated_by, stock.park_id, existing.id
        role_id = db.scalar(select(Role.id).where(Role.slug == "royal"))
        user = User(username="pg-worker", password_hash="unused", role_id=role_id)
        park = Park(name="PostgreSQL park", tag="pg-park")
        component = InventoryCatalogComponent(
            name="Motors", normalized_name="motors", is_active=True
        )
        db.add_all([user, park, component])
        db.flush()
        part = InventoryCatalogPart(
            component_id=component.id,
            name="Drive motor",
            normalized_name="drive motor",
            article="PG-MOTOR",
            normalized_article="pg-motor",
            is_active=True,
        )
        db.add(part)
        db.flush()
        db.add(
            InventoryParkStock(
                park_id=park.id,
                catalog_part_id=part.id,
                quantity=2,
                minimum_quantity=0,
                is_active=True,
                updated_by=user.id,
            )
        )
        db.commit()
        return user.id, park.id, part.id


def test_concurrent_inventory_decrement_and_tracker_message_are_idempotent(
    migrated_engine: Engine,
) -> None:
    user_id, park_id, part_id = _seed_inventory(migrated_engine)
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)

    def decrement() -> int:
        with factory() as db:
            user = db.get(User, user_id)
            movement = inventory_stock.apply_stock_delta(
                db,
                user=user,
                park_id=park_id,
                catalog_part_id=part_id,
                delta=-1,
                kind="issue_use",
                source_kind="tracker_issue",
                source_id="PG-1",
                note=None,
                idempotency_key="pg-stock-decrement",
            )
            db.commit()
            return movement.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        movement_ids = list(executor.map(lambda _index: decrement(), range(2)))

    with Session(migrated_engine) as db:
        user = db.get(User, user_id)
        first = task_timeline.append_user_message(
            db,
            issue_key="PG-1",
            actor=user,
            text="Installed one motor",
            idempotency_key="pg-comment-0001",
        )
        with pytest.raises(HTTPException, match="reliable_action_uncertain"):
            task_timeline.append_user_message(
                db,
                issue_key="PG-1",
                actor=user,
                text="Installed one motor",
                idempotency_key="pg-comment-0001",
            )
        stock = db.scalar(
            select(InventoryParkStock).where(
                InventoryParkStock.park_id == park_id,
                InventoryParkStock.catalog_part_id == part_id,
            )
        )
        assert movement_ids[0] == movement_ids[1]
        assert stock.quantity == 1
        assert (
            db.query(InventoryMovement).filter_by(idempotency_key="pg-stock-decrement").count() == 1
        )
        assert db.query(ReliableAction).filter_by(idempotency_key="pg-comment-0001").count() == 1
        assert db.query(TaskMessage).filter_by(action_id=first.action_id).count() == 1


def test_tracker_presence_uses_postgresql_conflict_update(migrated_engine: Engine) -> None:
    user_id, _park_id, _part_id = _seed_inventory(migrated_engine)
    with Session(migrated_engine) as db:
        for expires_at in (10.0, 20.0):
            db.execute(
                _presence_insert(db)
                .values(issue_key="PG-1", actor_id=user_id, expires_at=expires_at)
                .on_conflict_do_update(
                    index_elements=["issue_key", "actor_id"],
                    set_={"expires_at": expires_at},
                )
            )
            db.commit()
        rows = list(db.scalars(select(TrackerPresence).where(TrackerPresence.issue_key == "PG-1")))
        assert len(rows) == 1
        assert rows[0].expires_at == 20.0


def test_export_session_keeps_a_repeatable_read_snapshot(migrated_engine: Engine) -> None:
    _seed_inventory(migrated_engine)
    factory = sessionmaker(bind=migrated_engine)
    with factory() as request_db, inventory_exports._export_session(request_db) as snapshot_db:
        before = snapshot_db.scalar(select(InventoryParkStock.quantity).limit(1))
        with factory() as writer:
            stock = writer.scalar(select(InventoryParkStock).limit(1).with_for_update())
            stock.quantity += 1
            writer.commit()
        after = snapshot_db.scalar(select(InventoryParkStock.quantity).limit(1))
    assert after == before
