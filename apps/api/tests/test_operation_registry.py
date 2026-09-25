from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from robopark_api.models import HostOperationStatus, User
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services.ops import operation_registry
from robopark_api.services.ops.jobs import new_job, save_job


def test_exact_status_survives_session_rotation_and_host_slot_replacement(
    client,
    db_session,
    seed_royal,
    test_settings,
):
    operation_id = str(uuid4())
    operation_registry.reserve(
        db_session,
        operation_id=operation_id,
        actor_user_id=seed_royal.id,
        kind="diagnostics",
    )
    operation_registry.mark_accepted(
        db_session,
        operation_id=operation_id,
        phase="awaiting_host",
    )
    login_as(client, "royal", "secret")
    client.post("/auth/logout")
    login_as(client, "royal", "secret")
    replacement = new_job("diagnostics", exempt_token_hash="new-session")
    replacement.id = str(uuid4())
    replacement.state = "running"
    replacement.extra = {"host_updater": True, "host_request": {"actor_user_id": seed_royal.id}}
    save_job(Path(test_settings.ops_dir), replacement)

    response = client.get(f"/admin/ops/operations/{operation_id}")
    assert response.status_code == 200
    assert response.json()["receipt_state"] == "accepted"
    assert response.json()["state"] == "running"


def test_rejected_post_has_a_durable_terminal_receipt(client, seed_royal):
    login_as(client, "royal", "secret")
    operation_id = str(uuid4())
    rejected = client.post(
        "/admin/ops/operations",
        json={
            "operation_id": operation_id,
            "kind": "diagnostics",
            "capability_revision": "a" * 64,
            "confirmation": "ЗАПУСТИТЬ DIAGNOSTICS",
        },
    )
    assert rejected.status_code == 503

    receipt = client.get(f"/admin/ops/operations/{operation_id}")
    assert receipt.status_code == 200
    assert receipt.json()["receipt_state"] == "terminal"
    assert receipt.json()["state"] == "failed"


def test_exact_status_is_not_exposed_to_a_different_royal(
    client,
    db_session,
    seed_royal,
):
    operation_id = str(uuid4())
    operation_registry.reserve(
        db_session,
        operation_id=operation_id,
        actor_user_id=seed_royal.id,
        kind="diagnostics",
    )
    other = User(
        username="other-royal",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.ROYAL),
        access_status="approved",
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()
    login_as(client, "other-royal", "secret")

    assert client.get(f"/admin/ops/operations/{operation_id}").status_code == 404


def test_registry_retention_deletes_only_expired_terminal_rows(db_session, seed_royal, monkeypatch):
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    monkeypatch.setattr(operation_registry, "MAX_OPERATION_ROWS", 3)
    old = HostOperationStatus(
        operation_id=str(uuid4()),
        actor_user_id=seed_royal.id,
        kind="diagnostics",
        receipt_state="terminal",
        state="failed",
        phase="rejected",
        created_at=now - timedelta(days=9),
        updated_at=now - timedelta(days=9),
        terminal_at=now - timedelta(days=9),
    )
    db_session.add(old)
    for offset in range(5):
        db_session.add(
            HostOperationStatus(
                operation_id=str(uuid4()),
                actor_user_id=seed_royal.id,
                kind="diagnostics",
                receipt_state="terminal",
                state="succeeded",
                phase="completed",
                created_at=now - timedelta(minutes=offset),
                updated_at=now - timedelta(minutes=offset),
                terminal_at=now,
            )
        )
    db_session.commit()

    removed = operation_registry.prune(db_session, now=now)
    rows = db_session.scalars(
        select(HostOperationStatus).order_by(HostOperationStatus.created_at.desc())
    ).all()

    assert removed == 1
    assert len(rows) == 5
    assert old not in rows


def test_operation_uuid_is_bound_to_exact_canonical_request(db_session, seed_royal):
    operation_id = str(uuid4())
    first = {
        "operation_id": operation_id,
        "kind": "usb-select",
        "capability_revision": "a" * 64,
        "device_uuid": "11111111-1111-4111-8111-111111111111",
        "confirmation": "ЗАПУСТИТЬ USB-SELECT",
    }
    digest = operation_registry.request_digest(first)
    operation_registry.reserve(
        db_session,
        operation_id=operation_id,
        actor_user_id=seed_royal.id,
        kind="usb-select",
        request_digest=digest,
    )

    replay = operation_registry.reserve(
        db_session,
        operation_id=operation_id,
        actor_user_id=seed_royal.id,
        kind="usb-select",
        request_digest=operation_registry.request_digest(
            {
                **first,
                "confirmation": "a different non-operational phrase",
            }
        ),
    )
    assert replay.operation_id == operation_id

    with pytest.raises(operation_registry.OperationIdentityConflict):
        operation_registry.reserve(
            db_session,
            operation_id=operation_id,
            actor_user_id=seed_royal.id,
            kind="usb-select",
            request_digest=operation_registry.request_digest(
                {
                    **first,
                    "device_uuid": "22222222-2222-4222-8222-222222222222",
                }
            ),
        )


@pytest.mark.parametrize(
    ("kind", "field", "before", "after"),
    [
        (
            "backup-verify",
            "backup_id",
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        ),
        ("package-inspect", "package", "openssl", "curl"),
    ],
)
def test_operation_digest_changes_for_every_operational_payload(
    kind,
    field,
    before,
    after,
):
    base = {
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "kind": kind,
        "capability_revision": "a" * 64,
        field: before,
    }
    assert operation_registry.request_digest(base) != operation_registry.request_digest(
        {
            **base,
            field: after,
        }
    )


def test_terminal_job_is_snapshotted_before_current_slot_is_replaced(
    client,
    db_session,
    seed_royal,
    test_settings,
):
    operation_id = str(uuid4())
    request = {
        "operation_id": operation_id,
        "kind": "diagnostics",
        "capability_revision": "a" * 64,
    }
    operation_registry.reserve(
        db_session,
        operation_id=operation_id,
        actor_user_id=seed_royal.id,
        kind="diagnostics",
        request_digest=operation_registry.request_digest(request),
    )
    first = new_job("diagnostics", exempt_token_hash="session")
    first.id = operation_id
    first.state = "succeeded"
    first.phase = "completed"
    first.extra = {"host_result": {"performed": ["restart_tuna"]}}
    save_job(Path(test_settings.ops_dir), first)

    operation_registry.snapshot_current_job(db_session, Path(test_settings.ops_dir))
    login_as(client, "royal", "secret")
    replacement = new_job("diagnostics", exempt_token_hash="session")
    replacement.id = str(uuid4())
    replacement.state = "running"
    replacement.phase = "awaiting_host"
    save_job(Path(test_settings.ops_dir), replacement)

    receipt = client.get(f"/admin/ops/operations/{operation_id}")
    assert receipt.status_code == 200
    assert receipt.json()["receipt_state"] == "terminal"
    assert receipt.json()["state"] == "succeeded"
    assert receipt.json()["host_result"]["performed"] == ["restart_tuna"]


def test_prune_never_deletes_live_rows_and_rejects_admission_at_capacity(
    db_session,
    seed_royal,
    monkeypatch,
):
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    monkeypatch.setattr(operation_registry, "MAX_OPERATION_ROWS", 3)
    for state in ("received", "accepted", "accepted"):
        db_session.add(
            HostOperationStatus(
                operation_id=str(uuid4()),
                actor_user_id=seed_royal.id,
                kind="diagnostics",
                request_digest="a" * 64,
                receipt_state=state,
                state="queued" if state == "received" else "running",
                phase="request_received" if state == "received" else "awaiting_host",
                created_at=now - timedelta(days=30),
                updated_at=now - timedelta(days=30),
            )
        )
    db_session.commit()

    assert operation_registry.prune(db_session, now=now) == 0
    with pytest.raises(operation_registry.OperationRegistryFull):
        operation_registry.reserve(
            db_session,
            operation_id=str(uuid4()),
            actor_user_id=seed_royal.id,
            kind="diagnostics",
            request_digest="b" * 64,
        )
    assert len(db_session.scalars(select(HostOperationStatus)).all()) == 3


def test_prune_removes_all_expired_terminal_overflow_in_one_pass(
    db_session,
    seed_royal,
    monkeypatch,
):
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    monkeypatch.setattr(operation_registry, "MAX_OPERATION_ROWS", 500)
    for index in range(501):
        db_session.add(
            HostOperationStatus(
                operation_id=str(uuid4()),
                actor_user_id=seed_royal.id,
                kind="diagnostics",
                request_digest=f"{index:064x}",
                receipt_state="terminal",
                state="failed",
                phase="rejected",
                created_at=now - timedelta(days=8),
                updated_at=now - timedelta(days=8),
                terminal_at=now - timedelta(days=8),
            )
        )
    db_session.commit()

    assert operation_registry.prune(db_session, now=now) == 501
    assert (
        db_session.scalar(select(operation_registry.func.count()).select_from(HostOperationStatus))
        == 0
    )


def test_concurrent_admission_at_last_slot_accepts_one_and_rejects_one(
    db_engine,
    db_session,
    seed_royal,
):
    db_session.execute(
        insert(HostOperationStatus),
        [
            {
                "operation_id": str(uuid4()),
                "actor_user_id": seed_royal.id,
                "kind": "diagnostics",
                "request_digest": f"{index:064x}",
                "receipt_state": "accepted",
                "state": "running",
                "phase": "awaiting_host",
            }
            for index in range(operation_registry.MAX_OPERATION_ROWS - 1)
        ],
    )
    db_session.commit()

    start = Barrier(3)
    outcomes: list[str] = []

    def admit() -> None:
        with Session(db_engine) as session:
            start.wait()
            try:
                operation_registry.reserve(
                    session,
                    operation_id=str(uuid4()),
                    actor_user_id=seed_royal.id,
                    kind="diagnostics",
                    request_digest="f" * 64,
                )
                outcomes.append("accepted")
            except operation_registry.OperationRegistryFull:
                outcomes.append("full")

    threads = [Thread(target=admit), Thread(target=admit)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["accepted", "full"]
    db_session.expire_all()
    assert (
        db_session.scalar(select(operation_registry.func.count()).select_from(HostOperationStatus))
        == operation_registry.MAX_OPERATION_ROWS
    )


def test_registry_admission_uses_one_dedicated_lock_key(
    db_session,
    seed_royal,
    monkeypatch,
):
    entered: list[str] = []

    class RecordingLock:
        def __init__(self, _db, key: str):
            entered.append(key)

        def __enter__(self):
            return None

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(operation_registry, "database_idempotency_lock", RecordingLock)
    operation_registry.reserve(
        db_session,
        operation_id=str(uuid4()),
        actor_user_id=seed_royal.id,
        kind="diagnostics",
        request_digest="a" * 64,
    )

    assert entered == ["host-operation-registry-admission"]
