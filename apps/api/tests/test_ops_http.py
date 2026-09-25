from pathlib import Path

import pytest

from conftest import login_as, role_id_for
from robopark_api.models import User
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services.ops.jobs import STATE_RUNNING, new_job, save_job

pytestmark = pytest.mark.usefixtures("authorize_privileged_ops")


def test_admin_cannot_create_snapshot(client, seed_royal, db_session):
    admin = User(
        username="admin1",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.ADMIN),
        access_status="approved",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    login_as(client, "admin1", "secret")
    assert client.post("/admin/ops/snapshot").status_code == 403


def test_royal_legacy_snapshot_route_is_retired(client, seed_royal, test_settings):
    login_as(client, "royal", "secret")
    created = client.post("/admin/ops/snapshot")
    assert created.status_code == 410
    assert created.json()["detail"] == "typed_operation_required"


def test_maintenance_blocks_others_not_health(client, seed_royal, seed_mechanic, test_settings):
    login_as(client, "royal", "secret")
    job = new_job("snapshot", exempt_token_hash="not-this-session")
    job.state = STATE_RUNNING
    save_job(Path(test_settings.ops_dir), job)

    assert client.get("/health").status_code == 200
    status = client.get("/ops/maintenance").json()
    assert status["active"] is True
    assert status["operator"] is False

    login_as(client, "mech1", "secret")
    blocked = client.get("/auth/me")
    assert blocked.status_code == 503
    assert blocked.json()["detail"] == "maintenance"


def test_legacy_archive_update_route_is_retired(client, seed_royal, tmp_path, test_settings):
    login_as(client, "royal", "secret")
    updated = client.post(
        "/admin/ops/update",
        data={"confirm": "ОБНОВИТЬ"},
        files={"archive": ("release.zip", b"PK", "application/zip")},
    )
    assert updated.status_code == 404


def test_legacy_restore_is_retired_before_confirmation(client, seed_royal):
    login_as(client, "royal", "secret")
    response = client.post(
        "/admin/ops/restore",
        data={"confirm": "nope"},
        files={"archive": ("x.zip", b"PK\x03\x04", "application/zip")},
    )
    assert response.status_code == 410
    assert response.json()["detail"] == "typed_operation_required"


def test_legacy_abort_is_retired_and_preserves_maintenance(client, seed_royal, test_settings):
    from robopark_api.services.ops.jobs import is_maintenance_active, load_job

    login_as(client, "royal", "secret")
    headers = client.privileged_headers("abort", "abort")
    ops = Path(test_settings.ops_dir)
    job = new_job("update", exempt_token_hash="not-this-session")
    job.state = STATE_RUNNING
    job.phase = "awaiting_rebuild"
    job.extra = {"host_updater": True, "host_dispatch": "dispatched"}
    save_job(ops, job)
    response = client.post("/admin/ops/abort", headers=headers)
    assert response.status_code == 410
    assert response.json()["detail"] == "typed_operation_required"
    assert load_job(ops).state == STATE_RUNNING
    assert is_maintenance_active(ops)
