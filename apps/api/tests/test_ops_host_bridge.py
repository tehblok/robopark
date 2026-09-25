"""Installed bridge authority, durable approval and safe host projections."""

import ast
import io
import json
from pathlib import Path

import pytest

from conftest import login_as, role_id_for
from robopark_api.models import AuditLog, User
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services.ops import host_bridge, operation_registry
from robopark_api.services.ops.archives import KIND_RELEASE, build_archive
from robopark_api.services.ops.context import build_ops_context
from robopark_api.services.ops.jobs import load_job, new_job, save_job

pytestmark = pytest.mark.usefixtures("authorize_privileged_ops")


@pytest.fixture
def installed(test_settings, tmp_path):
    host = tmp_path / "host-ops"
    for name in ("inbox", "artifacts", "public"):
        (host / name).mkdir(parents=True)
    object.__setattr__(test_settings, "ops_host_root", str(host))
    return host


def inspect(client, tmp_path, keys):
    root = tmp_path / "release"
    root.mkdir(exist_ok=True)
    (root / "README.md").write_text("release\n")
    blob = build_archive(
        kind=KIND_RELEASE,
        source_root=root,
        app_version="1.2.3",
        release_meta={
            "git_sha": "a" * 40,
            "migration_head": "0017_driver_work_reports",
            "update_notes": "Safe update",
        },
        signing_key=keys[0],
    )
    return client.post(
        "/admin/ops/update/inspect",
        files={"archive": ("../../client.zip", blob, "application/zip")},
    )


@pytest.mark.parametrize("role", [rbac.RoleSlug.ADMIN, rbac.RoleSlug.OPERATOR])
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "system-health"),
        ("post", "diagnostics"),
        ("post", "repair"),
        ("get", "diagnostic-artifact"),
        ("post", "update/inspect"),
        ("post", "update/approve"),
        ("get", "operations/11111111-1111-4111-8111-111111111111"),
    ],
)
def test_host_routes_require_royal(client, seed_royal, db_session, role, method, path):
    db_session.add(
        User(
            username="limited",
            password_hash=hash_password("secret"),
            role_id=role_id_for(db_session, role),
            access_status="approved",
            is_active=True,
        )
    )
    db_session.commit()
    login_as(client, "limited", "secret")
    assert getattr(client, method)("/admin/ops/" + path).status_code == 403


def test_installed_context_has_separate_host_root(installed, test_settings):
    ctx = build_ops_context(test_settings)
    assert ctx.use_host_updater is True
    assert ctx.host_ops_dir == installed
    assert ctx.ops_dir == Path(test_settings.ops_dir)
    assert ctx.config_files == {"host.env": Path(test_settings.ops_host_env_path)}
    assert ctx.config_targets == {}


def test_exact_operation_status_is_actor_bound_and_sanitized(
    client, seed_royal, installed, test_settings, db_session,
):
    login_as(client, "royal", "secret")
    job = new_job("diagnostics", exempt_token_hash="old-session")
    job.id = "11111111-1111-4111-8111-111111111111"
    job.state = "running"
    job.phase = "awaiting_host"
    job.log = "SECRET internal log"
    job.extra = {
        "host_updater": True,
        "host_request": {"actor_user_id": seed_royal.id},
        "host_result": {"performed": ["restart_tuna", "SECRET"]},
    }
    save_job(Path(test_settings.ops_dir), job)
    operation_registry.reserve(
        db_session, operation_id=job.id, actor_user_id=seed_royal.id, kind=job.kind,
    )

    response = client.get(f"/admin/ops/operations/{job.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": job.id, "kind": "diagnostics", "receipt_state": "accepted", "state": "running",
        "phase": "awaiting_host", "error": None,
        "host_result": {"before": [], "after": [], "performed": ["restart_tuna"], "failed": [], "devices": []},
        "progress_percent": None,
    }
    assert "SECRET" not in response.text
    assert client.get("/admin/ops/operations/22222222-2222-4222-8222-222222222222").status_code == 404

    saved = load_job(Path(test_settings.ops_dir))
    saved.exempt_token_hash = "different-session"
    save_job(Path(test_settings.ops_dir), saved)
    assert client.get(f"/admin/ops/operations/{job.id}").status_code == 200


@pytest.mark.parametrize(
    ("phase", "percent"),
    [
        ("unpacking", 10),
        ("building", 25),
        ("smoking", 45),
        ("snapshotting", 60),
        ("publishing", 70),
        ("migrating", 82),
        ("starting", 90),
        ("health_check", 96),
        ("rolling_back", 50),
    ],
)
def test_update_job_projects_only_matching_allowlisted_host_progress(
    client, seed_royal, installed, test_settings, phase, percent
):
    login_as(client, "royal", "secret")
    job = new_job("update", exempt_token_hash="session")
    job.state = "running"
    job.extra = {"host_updater": True}
    save_job(Path(test_settings.ops_dir), job)
    (installed / "public/host-status.json").write_text(
        json.dumps(
            {
                "state": "updating",
                "job_id": job.id,
                "phase": phase,
                "error": "SECRET",
                "unexpected": {"token": "LEAK"},
            }
        )
    )

    response = client.get("/admin/ops/job")

    assert response.status_code == 200
    assert response.json()["progress_phase"] == phase
    assert response.json()["progress_percent"] == percent
    assert "SECRET" not in response.text
    assert "LEAK" not in response.text


@pytest.mark.parametrize(
    "status",
    [
        {},
        {"state": "updating", "job_id": "other", "phase": "building"},
        {"state": "idle", "phase": "building"},
        {"state": "updating", "phase": "validating"},
        {"state": "updating", "phase": "unknown"},
        {"state": [], "job_id": [], "phase": []},
    ],
)
def test_update_job_rejects_untrusted_or_unrelated_host_progress(
    client, seed_royal, installed, test_settings, status
):
    login_as(client, "royal", "secret")
    job = new_job("update", exempt_token_hash="session")
    job.state = "running"
    job.extra = {"host_updater": True}
    save_job(Path(test_settings.ops_dir), job)
    if status:
        status = {**status}
        if status.get("state") == "updating" and "job_id" not in status:
            status["job_id"] = job.id
        (installed / "public/host-status.json").write_text(json.dumps(status))

    payload = client.get("/admin/ops/job").json()

    assert payload["progress_phase"] is None
    assert payload["progress_percent"] is None


def test_update_progress_allowlist_matches_durable_updater_phases():
    updater = Path(__file__).resolve().parents[3] / "deploy/host/robopark_host/updater.py"
    module = ast.parse(updater.read_text(encoding="utf-8"))
    phases = next(
        node.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "PHASES" for target in node.targets)
    )

    assert set(host_bridge.UPDATE_PROGRESS_PERCENT) == ast.literal_eval(phases)


def test_missing_bridge_fails_closed(client, seed_royal):
    login_as(client, "royal", "secret")
    assert client.post("/admin/ops/repair").status_code == 503


def test_health_allowlist_never_echoes_arbitrary_nested_json(client, seed_royal, installed):
    login_as(client, "royal", "secret")
    (installed / "public/system-health.json").write_text(
        json.dumps(
            {
                "version": "1.2.3",
                "git_sha": "a" * 40,
                "generated_at": "2026-09-07T00:00:00+00:00",
                "overall": "degraded",
                "checks": [
                    {
                        "code": "tuna_inactive",
                        "status": "failed",
                        "message": "Authorization: Bearer LEAK",
                        "repair": "restart_tuna",
                        "env": "LEAK",
                    }
                ],
                "update": {"state": "idle", "token": "LEAK"},
                "last_backup": {"status": "success", "path": "/root/LEAK"},
                "TUNA_TOKEN": "LEAK",
            }
        )
    )
    response = client.get("/admin/ops/system-health")
    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "version",
        "git_sha",
        "generated_at",
        "overall",
        "checks",
        "update",
        "last_backup",
    }
    assert "LEAK" not in response.text
    assert response.json()["checks"][0]["code"] == "tuna_inactive"


def test_signed_inspection_approval_is_bound_and_idempotent(
    client, seed_royal, installed, tmp_path, release_key_pair, test_settings, db_session
):
    login_as(client, "royal", "secret")
    inspected = inspect(client, tmp_path, release_key_pair)
    assert inspected.status_code == 200, inspected.text
    body = inspected.json()
    assert body["version"] == "1.2.3"
    assert not (installed / "inbox/approved.json").exists()
    assert len(list((installed / "artifacts").glob("*.zip"))) == 1
    bad = client.post(
        "/admin/ops/update/approve",
        json={"inspection_id": body["inspection_id"], "confirm": " ОБНОВИТЬ"},
    )
    assert bad.status_code == 400
    approved = client.post(
        "/admin/ops/update/approve",
        json={"inspection_id": body["inspection_id"], "confirm": "ОБНОВИТЬ"},
    )
    assert approved.status_code == 200, approved.text
    request_path = installed / "inbox/approved.json"
    raw = request_path.read_bytes()
    command = json.loads(raw)
    assert set(command) == {"job_id", "kind", "artifact", "actor_user_id", "created_at"}
    assert command["actor_user_id"] == seed_royal.id
    assert command["kind"] == "update"
    assert command["artifact"].startswith("update-")
    assert command["job_id"] == approved.json()["id"]
    assert (
        client.post(
            "/admin/ops/update/approve",
            json={"inspection_id": body["inspection_id"], "confirm": "ОБНОВИТЬ"},
        ).status_code
        == 200
    )
    assert request_path.read_bytes() == raw
    assert client.post("/admin/ops/repair").status_code == 409
    assert client.post("/admin/ops/abort").status_code == 409
    assert load_job(Path(test_settings.ops_dir)).extra["host_dispatch"] == "dispatched"
    assert {a.action for a in db_session.query(AuditLog)} >= {
        "admin.ops.update.inspect",
        "admin.ops.update.approve",
    }


def test_diagnostics_download_only_completed_exact_job_artifact(client, seed_royal, installed, test_settings):
    import zipfile

    login_as(client, "royal", "secret")
    assert client.post("/admin/ops/diagnostics").status_code == 410
    job = new_job("diagnostics", exempt_token_hash="session")
    job.state = "running"
    job.phase = "running"
    job.extra = {
        "host_updater": True, "host_dispatch": "dispatched",
        "host_request": {"actor_user_id": seed_royal.id},
    }
    save_job(Path(test_settings.ops_dir), job)
    job_id = job.id
    assert client.get("/admin/ops/diagnostic-artifact").status_code == 404
    artifacts = installed / "public/artifacts"
    artifacts.mkdir()
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        archive.writestr("report.json", "{}")
    (artifacts / (job_id + ".zip")).write_bytes(blob.getvalue())
    (installed / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "kind": "diagnostics",
                "actor_user_id": seed_royal.id,
                "state": "succeeded",
                "artifact": job_id + ".zip",
                "before": [],
                "after": [],
                "performed": [],
                "failed": [],
                "error": None,
            }
        )
    )
    response = client.get("/admin/ops/diagnostic-artifact")
    assert response.status_code == 200, response.text
    assert response.content == blob.getvalue()
    (artifacts / (job_id + ".zip")).unlink()
    (artifacts / (job_id + ".zip")).symlink_to(installed / "inbox/approved.json")
    assert client.get("/admin/ops/diagnostic-artifact").status_code == 404


def test_approval_retries_exact_saved_request_after_publication_crash(
    client, seed_royal, installed, test_settings, tmp_path, release_key_pair, monkeypatch
):
    import os

    login_as(client, "royal", "secret")
    identity = inspect(client, tmp_path, release_key_pair).json()["inspection_id"]
    original = os.link
    monkeypatch.setattr(
        "robopark_api.services.ops.host_bridge.os.link",
        lambda *args: (_ for _ in ()).throw(OSError("crash")),
    )
    data = {"inspection_id": identity, "confirm": "ОБНОВИТЬ"}
    response = client.post("/admin/ops/update/approve", json=data)
    assert response.status_code == 200
    saved = load_job(Path(test_settings.ops_dir)).extra["host_request"]
    assert not (installed / "inbox/approved.json").exists()
    assert client.post("/admin/ops/abort").status_code == 409
    monkeypatch.setattr("robopark_api.services.ops.host_bridge.os.link", original)
    assert client.post("/admin/ops/update/approve", json=data).status_code == 200
    assert json.loads((installed / "inbox/approved.json").read_text()) == saved
    (installed / "inbox/approved.json").unlink()
    (installed / "public/command-claim.json").write_text(
        json.dumps({"job_id": saved["job_id"], "active": True})
    )
    assert client.post("/admin/ops/update/approve", json=data).status_code == 200
    assert not (installed / "inbox/approved.json").exists()


def test_approval_rejects_different_royal_and_changed_artifact(
    client, seed_royal, installed, tmp_path, release_key_pair, db_session
):
    login_as(client, "royal", "secret")
    identity = inspect(client, tmp_path, release_key_pair).json()["inspection_id"]
    db_session.add(
        User(
            username="royal2",
            password_hash=hash_password("secret"),
            role_id=role_id_for(db_session, rbac.RoleSlug.ROYAL),
            access_status="approved",
            is_active=True,
        )
    )
    db_session.commit()
    login_as(client, "royal2", "secret")
    payload = {"inspection_id": identity, "confirm": "ОБНОВИТЬ"}
    assert client.post("/admin/ops/update/approve", json=payload).status_code == 400
    login_as(client, "royal", "secret")
    next((installed / "artifacts").glob("*.zip")).write_bytes(b"tampered")
    assert (
        client.post("/admin/ops/update/approve", json=payload).json()["detail"]
        == "artifact_changed"
    )
    assert not (installed / "inbox/approved.json").exists()


def test_host_maintenance_blocks_even_when_api_job_state_is_missing(client, seed_royal, installed):
    login_as(client, "royal", "secret")
    (installed / "public/maintenance.json").write_text(
        '{"enabled":true,"reason":"update","token":"LEAK"}'
    )
    assert client.get("/auth/me").status_code == 200
    assert client.get("/ops/maintenance").json()["active"] is True
    assert "LEAK" not in client.get("/ops/maintenance").text
    assert client.get("/health/ready").status_code == 200


def test_host_health_rejects_symlink_public_root(client, seed_royal, installed):
    login_as(client, "royal", "secret")
    (installed / "public").rmdir()
    (installed / "public").symlink_to(installed / "inbox", target_is_directory=True)
    assert client.get("/admin/ops/system-health").status_code == 503


def test_host_status_error_cannot_leak_through_job_route(
    client, seed_royal, installed, tmp_path, release_key_pair
):
    login_as(client, "royal", "secret")
    identity = inspect(client, tmp_path, release_key_pair).json()["inspection_id"]
    job = client.post(
        "/admin/ops/update/approve", json={"inspection_id": identity, "confirm": "ОБНОВИТЬ"}
    ).json()
    (installed / "public/rebuild.result").write_text(
        json.dumps({"job_id": job["id"], "ok": False, "error": "/root/token=LEAK Traceback"})
    )
    response = client.get("/admin/ops/job")
    assert response.json()["state"] == "failed"
    assert "LEAK" not in response.text


def test_nested_host_values_cannot_break_public_health_projection(client, seed_royal, installed):
    login_as(client, "royal", "secret")
    (installed / "public/system-health.json").write_text(
        json.dumps(
            {
                "overall": {"secret": "LEAK"},
                "checks": [
                    {"code": {"secret": "LEAK"}, "status": "ok"},
                    {"code": "tuna_inactive", "status": "failed", "repair": {"secret": "LEAK"}},
                ],
                "last_backup": {"status": []},
            }
        )
    )
    (installed / "public/host-status.json").write_text('{"state":[],"publication":{}}')
    response = client.get("/admin/ops/system-health")
    assert response.status_code == 200
    assert "LEAK" not in response.text


def test_approved_inspection_cannot_dispatch_again_after_another_job(
    client, seed_royal, installed, test_settings, tmp_path, release_key_pair
):
    from robopark_api.services.ops.jobs import new_job, save_job

    login_as(client, "royal", "secret")
    identity = inspect(client, tmp_path, release_key_pair).json()["inspection_id"]
    payload = {"inspection_id": identity, "confirm": "ОБНОВИТЬ"}
    job = client.post("/admin/ops/update/approve", json=payload).json()
    (installed / "public/rebuild.result").write_text(
        json.dumps({"job_id": job["id"], "ok": True, "error": None})
    )
    assert client.get("/admin/ops/job").json()["state"] == "succeeded"
    (installed / "inbox/approved.json").unlink()
    later = new_job("snapshot", exempt_token_hash="")
    later.state = "succeeded"
    save_job(Path(test_settings.ops_dir), later)
    assert client.post("/admin/ops/update/approve", json=payload).status_code == 409
    assert not (installed / "inbox/approved.json").exists()


def test_repair_job_exposes_only_sanitized_before_after_outcome(client, seed_royal, installed):
    login_as(client, "royal", "secret")
    job = client.post("/admin/ops/repair").json()
    result = {
        "job_id": job["id"],
        "kind": "repair",
        "actor_user_id": seed_royal.id,
        "state": "succeeded",
        "artifact": None,
        "before": [{"code": "tuna_inactive", "status": "failed", "message": "LEAK"}],
        "after": [{"code": "tuna_inactive", "status": "ok", "env": "LEAK"}],
        "performed": ["restart_tuna", "LEAK"],
        "failed": [],
        "error": None,
        "env": "LEAK",
    }
    (installed / "public/command-result.json").write_text(json.dumps(result))
    response = client.get("/admin/ops/job")
    assert response.json()["host_result"]["performed"] == ["restart_tuna"]
    assert response.json()["host_result"]["before"][0]["status"] == "failed"
    assert response.json()["host_result"]["after"][0]["status"] == "ok"
    assert "LEAK" not in response.text


def test_public_result_projects_only_sanitized_usb_device_selection_metadata():
    device_uuid = "11111111-1111-4111-8111-111111111111"
    result = host_bridge.public_result({
        "detail": {"devices": [
            {"device_uuid": device_uuid, "removable": True, "mounted": False, "path": "/dev/secret"},
            {"device_uuid": "not-a-uuid", "removable": True, "mounted": False},
        ]},
        "secret": "LEAK",
    }).model_dump(mode="json")

    assert result["devices"] == [{
        "device_uuid": device_uuid, "removable": True, "mounted": False,
    }]
    assert "LEAK" not in json.dumps(result)
    assert host_bridge.public_result(result).model_dump(mode="json")["devices"] == result["devices"]


def test_snapshot_conflicts_with_active_host_work_without_local_job(client, seed_royal, installed):
    login_as(client, "royal", "secret")
    (installed / "public/command-claim.json").write_text('{"active":true,"kind":"update"}')
    assert client.post("/admin/ops/snapshot").status_code == 409


def available_fixture(installed):
    from datetime import UTC, datetime

    (installed / "public/available-update.json").write_text(
        json.dumps(
            {
                "state": "available",
                "checked_at": datetime.now(UTC).isoformat(),
                "release": {
                    "release_id": 101,
                    "version": "1.3.0-rc.1",
                    "git_sha": "a" * 40,
                    "size": 1000,
                    "sha256": "b" * 64,
                    "token": "LEAK",
                    "browser_download_url": "https://evil/?token=LEAK",
                },
                "token": "LEAK",
            }
        )
    )


@pytest.mark.parametrize("role", [rbac.RoleSlug.ADMIN, rbac.RoleSlug.OPERATOR])
@pytest.mark.parametrize(
    "method,path", [("get", "available-update"), ("post", "github-update/approve")]
)
def test_github_routes_require_royal(client, seed_royal, db_session, role, method, path):
    test_host_routes_require_royal(client, seed_royal, db_session, role, method, path)


def test_github_availability_projection_and_explicit_approval(
    client, seed_royal, installed, test_settings
):
    login_as(client, "royal", "secret")
    available_fixture(installed)
    response = client.get("/admin/ops/available-update")
    assert response.status_code == 200
    assert "LEAK" not in response.text
    assert response.json()["release"]["release_id"] == 101
    for body in (
        {"release_id": 101, "confirm": " ОБНОВИТЬ"},
        {"release_id": 102, "confirm": "ОБНОВИТЬ"},
    ):
        assert client.post("/admin/ops/github-update/approve", json=body).status_code == 400
    assert (
        client.post(
            "/admin/ops/github-update/approve",
            json={"release_id": 101, "confirm": "ОБНОВИТЬ", "url": "https://evil"},
        ).status_code
        == 422
    )
    body = {"release_id": 101, "confirm": "ОБНОВИТЬ"}
    approved = client.post("/admin/ops/github-update/approve", json=body)
    assert approved.status_code == 200, approved.text
    command = json.loads((installed / "inbox/approved.json").read_text())
    assert set(command) == {"job_id", "kind", "release_id", "actor_user_id", "created_at"}
    assert command["release_id"] == 101 and command["actor_user_id"] == seed_royal.id
    assert command["kind"] == "github-update"
    again = client.post("/admin/ops/github-update/approve", json=body)
    assert again.status_code == 200 and again.json()["id"] == approved.json()["id"]
    (installed / "public/rebuild.result").write_text(
        json.dumps({"job_id": command["job_id"], "ok": True})
    )
    client.get("/admin/ops/system-health")
    assert load_job(Path(test_settings.ops_dir)).state == "succeeded"


def test_stale_github_availability_is_not_approvable(client, seed_royal, installed):
    login_as(client, "royal", "secret")
    available_fixture(installed)
    path = installed / "public/available-update.json"
    value = json.loads(path.read_text())
    value["checked_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(value))
    response = client.get("/admin/ops/available-update")
    assert response.status_code == 200 and response.json()["state"] == "discovery_stale"
    assert (
        client.post(
            "/admin/ops/github-update/approve", json={"release_id": 101, "confirm": "ОБНОВИТЬ"}
        ).status_code
        == 400
    )


def test_new_explicit_github_approval_can_retry_failed_network_download(
    client, seed_royal, installed
):
    login_as(client, "royal", "secret")
    available_fixture(installed)
    body = {"release_id": 101, "confirm": "ОБНОВИТЬ"}
    first = client.post("/admin/ops/github-update/approve", json=body)
    assert first.status_code == 200
    (installed / "inbox/approved.json").unlink()
    (installed / "public/rebuild.result").write_text(
        json.dumps({"job_id": first.json()["id"], "ok": False})
    )
    # A fresh host check republishes the same immutable, not-yet-consumed release.
    available_fixture(installed)
    second = client.post("/admin/ops/github-update/approve", json=body)
    assert second.status_code == 200
    assert second.json()["id"] != first.json()["id"]
    assert (
        json.loads((installed / "inbox/approved.json").read_text())["job_id"] == second.json()["id"]
    )


def test_royal_reload_bootstrap_is_read_only_during_host_maintenance(
    client, seed_royal, installed, db_session
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import event

    from robopark_api.models import AuthSession

    login_as(client, "royal", "secret")
    session = db_session.query(AuthSession).one()
    session.expires_at = datetime.now(UTC) + timedelta(minutes=2)
    db_session.commit()
    before = session.expires_at
    (installed / "public/maintenance.json").write_text('{"enabled":true}')
    writes = []
    engine = db_session.get_bind()

    def track(c, cur, statement, params, ctx, many):
        if statement.split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", track)
    try:
        for _ in range(2):  # fresh-page identity bootstrap and reconnect
            response = client.get("/auth/me")
            assert response.status_code == 200, response.text
            assert response.json()["role"] == "royal"
            assert client.get("/admin/ops/job").status_code == 200
        assert client.post("/auth/logout").status_code == 503
        assert client.post("/parks", json={"name": "blocked", "tag": "blocked"}).status_code == 503
    finally:
        event.remove(engine, "before_cursor_execute", track)
    db_session.expire_all()
    assert db_session.query(AuthSession).one().expires_at == before
    assert not writes


def test_public_health_displays_rc_and_real_rollback_state(client, seed_royal, installed):
    login_as(client, "royal", "secret")
    (installed / "public/system-health.json").write_text('{"version":"1.2.3-rc.2"}')
    (installed / "public/host-status.json").write_text('{"state":"previous_restored"}')
    result = client.get("/admin/ops/system-health").json()
    assert result["version"] == "1.2.3-rc.2"
    assert result["update"]["state"] == "rolled_back"


def test_real_snapshot_publishes_backup_receipt(client, seed_royal, test_settings):
    login_as(client, "royal", "secret")
    response = client.post("/admin/ops/snapshot")
    assert response.status_code == 200
    result = client.get("/admin/ops/job").json()
    assert result["state"] == "succeeded", result
    receipt = Path(test_settings.ops_dir) / "last-backup.json"
    assert receipt.exists()
    payload = json.loads(receipt.read_text())
    assert set(payload) == {"status", "completed_at"}
    assert payload["status"] == "success"


@pytest.mark.parametrize("pressure", ["bytes", "count", "disk"])
def test_inspection_admission_bounds_disk_without_removing_existing_uploads(
    client, seed_royal, installed, tmp_path, release_key_pair, monkeypatch, pressure
):
    from types import SimpleNamespace

    from robopark_api.services.ops import host_bridge

    login_as(client, "royal", "secret")
    existing = installed / "artifacts/update-existing.zip"
    existing.write_bytes(b"keep")
    if pressure == "bytes":
        monkeypatch.setattr(host_bridge, "MAX_UPLOAD_STORAGE", 4, raising=False)
    elif pressure == "count":
        monkeypatch.setattr(host_bridge, "MAX_UPLOAD_COUNT", 1, raising=False)
    else:
        monkeypatch.setattr("shutil.disk_usage", lambda path: SimpleNamespace(free=1))
    response = inspect(client, tmp_path, release_key_pair)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "artifact_storage_full"
    assert list((installed / "artifacts").iterdir()) == [existing]
    assert existing.read_bytes() == b"keep"
