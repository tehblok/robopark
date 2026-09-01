"""Security hardening from the full-system review."""

from pathlib import Path

from starlette.requests import Request

from conftest import VALID_PASSWORD, login_as, role_id_for
from robopark_api.models import AccessStatus, User
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services.login_throttle import client_ip
from robopark_api.services.ops.jobs import (
    STATE_FAILED,
    STATE_RUNNING,
    abort_job,
    expire_stale_job,
    new_job,
    save_job,
)
from robopark_api.services.ops.runner import artifact_path


def test_admin_not_in_self_register():
    assert rbac.RoleSlug.ADMIN not in rbac.RoleSlug.SELF_REGISTER
    assert rbac.RoleSlug.ROYAL not in rbac.RoleSlug.SELF_REGISTER


def test_register_rejects_admin_role(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    response = client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": "evil_admin",
            "password": VALID_PASSWORD,
            "role_slug": "admin",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_role_slug"


def test_pending_admin_cannot_use_admin_api(client, db_session, seed_royal):
    pending = User(
        username="pending_admin",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.ADMIN),
        access_status=AccessStatus.pending.value,
        is_active=True,
    )
    db_session.add(pending)
    db_session.commit()

    assert login_as(client, "pending_admin", "secret").status_code == 204
    blocked = client.get("/admin/users")
    assert blocked.status_code == 403


def test_must_change_password_blocks_api(client, db_session, seed_mechanic):
    seed_mechanic.must_change_password = True
    db_session.commit()
    assert login_as(client, "mech1", "secret").status_code == 204
    assert client.get("/auth/me").status_code == 200
    assert client.get("/mechanic/tasks").status_code == 403
    assert client.get("/mechanic/tasks").json()["detail"] == "must_change_password"


def test_client_ip_prefers_x_real_ip_ignores_xff():
    scope = {
        "type": "http",
        "asgi": {"version": "3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [
            (b"x-forwarded-for", b"1.2.3.4"),
            (b"x-real-ip", b"10.0.0.9"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    request = Request(scope)
    assert client_ip(request) == "10.0.0.9"


def test_client_ip_ignores_spoofed_xff_without_real_ip():
    scope = {
        "type": "http",
        "asgi": {"version": "3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"9.9.9.9")],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    request = Request(scope)
    assert client_ip(request) == "127.0.0.1"


def test_artifact_path_rejects_traversal(tmp_path):
    from robopark_api.services.ops.jobs import ensure_ops_dir

    ops = tmp_path / "ops"
    ensure_ops_dir(ops)
    secret = tmp_path / "secret.txt"
    secret.write_text("leak", encoding="utf-8")
    job = new_job("snapshot", exempt_token_hash="x")
    job.state = "succeeded"
    job.artifact_name = "../secret.txt"
    save_job(ops, job)
    assert artifact_path(ops, job) is None


def test_abort_clears_running_job(tmp_path):
    ops = tmp_path / "ops"
    job = new_job("update", exempt_token_hash="x")
    job.state = STATE_RUNNING
    save_job(ops, job)
    aborted = abort_job(ops)
    assert aborted is not None
    assert aborted.state == STATE_FAILED
    assert aborted.error == "aborted"


def test_expire_stale_job(tmp_path):
    ops = tmp_path / "ops"
    job = new_job("snapshot", exempt_token_hash="x")
    job.state = STATE_RUNNING
    job.created_at = "2000-01-01T00:00:00+00:00"
    save_job(ops, job)
    expired = expire_stale_job(ops, ttl_seconds=60)
    assert expired is not None
    assert expired.error == "job_expired"


def test_ops_abort_http(client, seed_royal, test_settings):
    login_as(client, "royal", "secret")
    job = new_job("snapshot", exempt_token_hash="x")
    job.state = STATE_RUNNING
    save_job(Path(test_settings.ops_dir), job)
    response = client.post("/admin/ops/abort")
    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert response.json()["error"] == "aborted"
    assert client.get("/ops/maintenance").json()["active"] is False
