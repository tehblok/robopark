from pathlib import Path

from conftest import login_as, role_id_for
from robopark_api.models import User
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services.ops.archives import KIND_RELEASE, build_archive
from robopark_api.services.ops.jobs import STATE_RUNNING, new_job, save_job
from robopark_api.services.ops.runner import UPDATE_PHRASE


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


def test_royal_snapshot_download(client, seed_royal, test_settings):
    login_as(client, "royal", "secret")
    created = client.post("/admin/ops/snapshot")
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["kind"] == "snapshot"
    assert body["state"] == "succeeded"
    assert body["artifact_ready"] is True
    artifact = client.get("/admin/ops/artifact")
    assert artifact.status_code == 200
    assert artifact.headers["content-type"].startswith("application/zip")
    assert artifact.content[:2] == b"PK"


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


def test_update_rejects_snapshot_zip(client, seed_royal, tmp_path, test_settings):
    login_as(client, "royal", "secret")
    snap = client.post("/admin/ops/snapshot")
    assert snap.status_code == 200
    zip_bytes = client.get("/admin/ops/artifact").content
    files = {"archive": ("snap.zip", zip_bytes, "application/zip")}
    updated = client.post(
        "/admin/ops/update",
        data={"confirm": UPDATE_PHRASE},
        files=files,
    )
    assert updated.status_code == 200
    assert updated.json()["state"] == "failed"
    assert updated.json()["error"] == "unexpected_kind"


def test_restore_bad_confirm_is_400(client, seed_royal):
    login_as(client, "royal", "secret")
    response = client.post(
        "/admin/ops/restore",
        data={"confirm": "nope"},
        files={"archive": ("x.zip", b"PK\x03\x04", "application/zip")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "confirm_required"


def test_release_tests_fail_leaves_apply_root_empty(client, seed_royal, tmp_path, test_settings):
    login_as(client, "royal", "secret")
    root = tmp_path / "rel"
    api = root / "apps" / "api"
    (api / "tests").mkdir(parents=True)
    (api / "tests" / "test_ok.py").write_text("def test_ok():\n    assert False\n", encoding="utf-8")
    blob = build_archive(kind=KIND_RELEASE, source_root=root, app_version="9")
    updated = client.post(
        "/admin/ops/update",
        data={"confirm": UPDATE_PHRASE},
        files={"archive": ("rel.zip", blob, "application/zip")},
    )
    assert updated.json()["error"] == "tests_failed"
    apply = Path(test_settings.ops_apply_root)
    assert not (apply / "apps").exists()
