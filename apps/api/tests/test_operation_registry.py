from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.models import HostOperationStatus, User
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services.ops import operation_registry
from robopark_api.services.ops.jobs import new_job, save_job


def test_exact_status_survives_session_rotation_and_host_slot_replacement(
    client, db_session, seed_royal, test_settings,
):
    operation_id = str(uuid4())
    operation_registry.reserve(
        db_session, operation_id=operation_id, actor_user_id=seed_royal.id,
        kind="diagnostics",
    )
    operation_registry.mark_accepted(
        db_session, operation_id=operation_id, phase="awaiting_host",
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
    rejected = client.post("/admin/ops/operations", json={
        "operation_id": operation_id,
        "kind": "diagnostics",
        "capability_revision": "a" * 64,
        "confirmation": "ЗАПУСТИТЬ DIAGNOSTICS",
    })
    assert rejected.status_code == 503

    receipt = client.get(f"/admin/ops/operations/{operation_id}")
    assert receipt.status_code == 200
    assert receipt.json()["receipt_state"] == "terminal"
    assert receipt.json()["state"] == "failed"


def test_exact_status_is_not_exposed_to_a_different_royal(
    client, db_session, seed_royal,
):
    operation_id = str(uuid4())
    operation_registry.reserve(
        db_session, operation_id=operation_id, actor_user_id=seed_royal.id,
        kind="diagnostics",
    )
    other = User(
        username="other-royal", password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.ROYAL),
        access_status="approved", is_active=True,
    )
    db_session.add(other)
    db_session.commit()
    login_as(client, "other-royal", "secret")

    assert client.get(f"/admin/ops/operations/{operation_id}").status_code == 404


def test_registry_retention_is_bounded_by_age_and_count(db_session, seed_royal, monkeypatch):
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    monkeypatch.setattr(operation_registry, "MAX_OPERATION_ROWS", 3)
    old = HostOperationStatus(
        operation_id=str(uuid4()), actor_user_id=seed_royal.id, kind="diagnostics",
        receipt_state="terminal", state="failed", phase="rejected",
        created_at=now - timedelta(days=9), updated_at=now - timedelta(days=9),
        terminal_at=now - timedelta(days=9),
    )
    db_session.add(old)
    for offset in range(5):
        db_session.add(HostOperationStatus(
            operation_id=str(uuid4()), actor_user_id=seed_royal.id, kind="diagnostics",
            receipt_state="terminal", state="succeeded", phase="completed",
            created_at=now - timedelta(minutes=offset),
            updated_at=now - timedelta(minutes=offset), terminal_at=now,
        ))
    db_session.commit()

    removed = operation_registry.prune(db_session, now=now)
    rows = db_session.scalars(
        select(HostOperationStatus).order_by(HostOperationStatus.created_at.desc())
    ).all()

    assert removed == 3
    assert len(rows) == 3
    assert old not in rows
