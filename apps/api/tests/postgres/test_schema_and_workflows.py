from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from robopark_api import main
from robopark_api.collaboration_models import TrackerPresence
from robopark_api.config import Settings, get_settings
from robopark_api.db import configure_engine, get_db
from robopark_api.models import (
    AccessStatus,
    AuthThrottleState,
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
    UserPark,
)
from robopark_api.routers.tracker_collaboration import _presence_insert
from robopark_api.security import hash_password
from robopark_api.services import inventory_exports, inventory_stock, task_timeline
from robopark_api.services.login_throttle import LoginThrottle
from robopark_api.services.ops.snapshot import restore_snapshot_tree
from robopark_api.services.rbac import RoleSlug
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


def test_audit_remediation_state_has_postgresql_upsert_and_cleanup_indexes(
    migrated_engine: Engine,
) -> None:
    database = inspect(migrated_engine)
    assert database.get_pk_constraint("tracker_notification_cursors")["constrained_columns"] == [
        "scope_key"
    ]
    assert database.get_pk_constraint("auth_throttle_states")["constrained_columns"] == ["key_hash"]
    assert {index["name"] for index in database.get_indexes("system_incident_occurrences")} >= {
        "uq_system_incident_active_key",
        "ix_system_incident_cleanup",
    }
    assert {index["name"] for index in database.get_indexes("auth_throttle_states")} >= {
        "ix_auth_throttle_expiry"
    }


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
                "0036_audit_remediation_state"
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


@contextmanager
def _postgres_http_client(migrated_engine: Engine, tmp_path: Path, monkeypatch):
    """Exercise HTTP routes on PostgreSQL while replacing long-lived jobs with fakes."""
    factory = sessionmaker(bind=migrated_engine, future=True)
    host_env = tmp_path / "host.env"
    host_env.write_text("SECRET_KEY=postgres-contract-test\n", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        database_url=str(migrated_engine.url),
        secret_key="postgres-contract-test",
        seed_username=None,
        seed_password=None,
        report_attachments_dir=str(tmp_path / "reports"),
        staged_attachments_dir=str(tmp_path / "uploads"),
        ops_dir=str(tmp_path / "ops"),
        ops_apply_root=str(tmp_path / "apply"),
        ops_host_env_path=str(host_env),
        ops_sync=True,
    )

    async def idle(stop_event, **_kwargs):
        await stop_event.wait()

    async def idle_with_factory(_session_factory, stop_event, **_kwargs):
        await stop_event.wait()

    for name in (
        "run_keepalive_loop",
        "run_blocker_history_loop",
        "run_session_cleanup_loop",
        "run_cache_cleanup_loop",
        "run_system_notification_loop",
    ):
        monkeypatch.setattr(main, name, idle)
    for name in (
        "run_tracker_outbox_loop",
        "run_campaign_refresh_loop",
        "run_tracker_notification_loop",
    ):
        monkeypatch.setattr(main, name, idle_with_factory)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "live_merge_enabled", lambda: False)

    app = main.create_app()

    def database_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, factory


def _seed_http_mechanic(factory) -> tuple[int, int, str]:
    suffix = uuid4().hex[:10]
    with factory() as db:
        ensure_rbac_catalog(db)
        role = db.scalar(select(Role).where(Role.slug == RoleSlug.MECHANIC))
        assert role is not None
        park = Park(name=f"HTTP park {suffix}", tag=f"http-{suffix}", tracker_queue="HTTP")
        user = User(
            username=f"http-mech-{suffix}",
            password_hash=hash_password("secret"),
            role_id=role.id,
            access_status=AccessStatus.approved.value,
            is_active=True,
        )
        db.add_all([park, user])
        db.flush()
        db.add(UserPark(user_id=user.id, park_id=park.id))
        db.commit()
        return user.id, park.id, user.username


def test_postgresql_http_concurrent_sync_replay_dispatches_once_and_returns_one_receipt(
    migrated_engine: Engine, tmp_path: Path, monkeypatch
) -> None:
    from robopark_api.services import offline_sync
    from robopark_api.task_workflow_models import OfflineSyncReceipt

    calls: list[str] = []
    dispatch_entered = threading.Event()
    allow_dispatch_to_finish = threading.Event()

    def dispatch(_db, _user, item):
        calls.append(item.client_action_id)
        dispatch_entered.set()
        assert allow_dispatch_to_finish.wait(timeout=1)
        return {"message_id": "pg-1"}

    monkeypatch.setattr(
        offline_sync,
        "dispatch_action",
        dispatch,
    )
    with _postgres_http_client(migrated_engine, tmp_path, monkeypatch) as (client, factory):
        _user_id, park_id, username = _seed_http_mechanic(factory)
        assert (
            client.post(
                "/auth/login", json={"username": username, "password": "secret"}
            ).status_code
            == 204
        )
        body = {
            "device_id": "postgres-device",
            "known_revisions": {"work": 0},
            "actions": [
                {
                    "client_action_id": "pg-replay",
                    "resource_type": "tracker_issue",
                    "resource_id": "HTTP-1",
                    "action": "comment",
                    "idempotency_key": "pg-replay-idempotency",
                    "base_revision": None,
                    "park_id": park_id,
                    "dependencies": [],
                    "payload": {"text": "one"},
                }
            ],
        }
        start_requests = threading.Barrier(3)

        def post_batch():
            start_requests.wait(timeout=1)
            return client.post("/sync/batch", json=body)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(post_batch)
            second_future = executor.submit(post_batch)
            start_requests.wait(timeout=1)
            assert dispatch_entered.wait(timeout=1)
            allow_dispatch_to_finish.set()
            first = first_future.result(timeout=2)
            second = second_future.result(timeout=2)
        with factory() as db:
            receipt_count = (
                db.query(OfflineSyncReceipt).filter_by(client_action_id="pg-replay").count()
            )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert calls == ["pg-replay"]
    assert receipt_count == 1


def test_postgresql_http_concurrent_media_completion_returns_one_stable_result(
    migrated_engine: Engine, tmp_path: Path, monkeypatch
) -> None:
    import hashlib

    from robopark_api.services import media_uploads

    upload_root = tmp_path / "uploaded-media"
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: upload_root)
    original_replace = Path.replace
    replace_entered = threading.Event()
    allow_replace = threading.Event()

    def hold_replace(path: Path, target: Path):
        if path.suffix == ".part":
            replace_entered.set()
            assert allow_replace.wait(timeout=1)
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", hold_replace)
    with _postgres_http_client(migrated_engine, tmp_path, monkeypatch) as (client, factory):
        _user_id, _park_id, username = _seed_http_mechanic(factory)
        assert (
            client.post(
                "/auth/login", json={"username": username, "password": "secret"}
            ).status_code
            == 204
        )
        content = b"\xff\xd8\xffpostgres-contract"
        started = client.post(
            "/media/uploads",
            json={
                "media_id": "pg-media-1",
                "issue_key": "HTTP-1",
                "name": "robot.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        )
        assert started.status_code == 201
        upload_id = started.json()["upload_id"]
        assert (
            client.put(
                f"/media/uploads/{upload_id}/chunks/0",
                content=content,
                headers={"X-Chunk-SHA256": hashlib.sha256(content).hexdigest()},
            ).status_code
            == 200
        )
        start_requests = threading.Barrier(3)

        def complete_upload():
            start_requests.wait(timeout=1)
            return client.post(f"/media/uploads/{upload_id}/complete")

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(complete_upload)
            second_future = executor.submit(complete_upload)
            start_requests.wait(timeout=1)
            assert replace_entered.wait(timeout=1)
            allow_replace.set()
            completed = first_future.result(timeout=2)
            replayed = second_future.result(timeout=2)

    assert completed.status_code == replayed.status_code == 200
    assert completed.json() == replayed.json()
    assert completed.json()["completed"] is True


def test_postgresql_http_schedule_and_push_subscription_persist_for_same_user(
    migrated_engine: Engine, tmp_path: Path, monkeypatch
) -> None:
    from robopark_api.schedule_models import PushSubscription

    endpoint = "https://push.example/postgres"
    with _postgres_http_client(migrated_engine, tmp_path, monkeypatch) as (client, factory):
        user_id, park_id, username = _seed_http_mechanic(factory)
        assert (
            client.post(
                "/auth/login", json={"username": username, "password": "secret"}
            ).status_code
            == 204
        )
        now = datetime.now(UTC)
        schedule = client.post(
            "/schedules",
            json={
                "park_id": park_id,
                "kind": "shift",
                "start_at": (now - timedelta(minutes=1)).isoformat(),
                "end_at": (now + timedelta(hours=1)).isoformat(),
            },
        )
        subscription = client.post(
            "/push/subscriptions",
            json={"endpoint": endpoint, "p256dh": "key", "auth": "auth"},
        )
        event = client.app.state.push_service.emit_for_tests(
            event_type="new_task", park_id=park_id, protected_text="private task"
        )
        inbox = client.get("/push/inbox")
        with factory() as db:
            persisted_subscription = db.scalar(
                select(PushSubscription).where(
                    PushSubscription.endpoint_hash == sha256(endpoint.encode()).hexdigest()
                )
            )

    assert schedule.status_code == 201
    assert subscription.status_code == 201
    assert persisted_subscription is not None
    assert persisted_subscription.user_id == user_id
    assert event["event_id"]
    assert user_id in event["internal_recipient_ids"]
    assert inbox.status_code == 200
    assert inbox.json()[0]["event_type"] == "new_task"


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
    assert head == "0036_audit_remediation_state"


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


def test_auth_throttle_concurrent_failures_lock_once_across_postgresql_workers(
    migrated_engine: Engine,
) -> None:
    factory = sessionmaker(bind=migrated_engine, future=True)
    throttle = LoginThrottle(
        session_factory=factory,
        max_attempts=8,
        window_seconds=60,
        lockout_seconds=300,
    )
    key = "login|concurrent-user|192.0.2.44"
    key_hash = sha256(key.encode("utf-8")).hexdigest()
    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    with factory() as db:
        db.query(AuthThrottleState).filter_by(key_hash=key_hash).delete()
        db.commit()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: throttle.register_failure(key, now=now), range(8)))

    assert throttle.retry_after(key, now=now) == 300
    with factory() as db:
        row = db.get(AuthThrottleState, key_hash)
        assert row is not None
        assert row.failure_count == 0
        assert row.locked_until == now + timedelta(seconds=300)
        assert row.expires_at == now + timedelta(seconds=300)
        db.delete(row)
        db.commit()


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
