"""Installed bridge authority, durable approval and safe host projections."""

import ast
import io
import json
from pathlib import Path

import pytest

from conftest import login_as, role_id_for
from robopark_api.models import User
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services.ops import host_bridge, operation_registry
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


@pytest.mark.parametrize("role", [rbac.RoleSlug.ADMIN, rbac.RoleSlug.OPERATOR])
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "system-health"),
        ("post", "diagnostics"),
        ("post", "repair"),
        ("get", "diagnostic-artifact"),
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
    assert ctx.config_files["host.env"] == Path(test_settings.ops_host_env_path)
    assert ctx.config_targets == {}


def test_exact_operation_status_is_actor_bound_and_sanitized(
    client,
    seed_royal,
    installed,
    test_settings,
    db_session,
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
        db_session,
        operation_id=job.id,
        actor_user_id=seed_royal.id,
        kind=job.kind,
    )

    response = client.get(f"/admin/ops/operations/{job.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": job.id,
        "kind": "diagnostics",
        "receipt_state": "accepted",
        "state": "running",
        "phase": "awaiting_host",
        "error": None,
        "host_result": {
            "before": [],
            "after": [],
            "performed": ["restart_tuna"],
            "failed": [],
            "devices": [],
        },
        "progress_percent": None,
    }
    assert "SECRET" not in response.text
    assert (
        client.get("/admin/ops/operations/22222222-2222-4222-8222-222222222222").status_code == 404
    )

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


def test_diagnostics_download_only_completed_exact_job_artifact(
    client, seed_royal, installed, test_settings
):
    import zipfile

    login_as(client, "royal", "secret")
    assert client.post("/admin/ops/diagnostics").status_code == 410
    job = new_job("diagnostics", exempt_token_hash="session")
    job.state = "running"
    job.phase = "running"
    job.extra = {
        "host_updater": True,
        "host_dispatch": "dispatched",
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


def test_public_result_projects_only_sanitized_usb_device_selection_metadata():
    device_uuid = "11111111-1111-4111-8111-111111111111"
    result = host_bridge.public_result(
        {
            "detail": {
                "devices": [
                    {
                        "device_uuid": device_uuid,
                        "removable": True,
                        "mounted": False,
                        "path": "/dev/secret",
                    },
                    {"device_uuid": "not-a-uuid", "removable": True, "mounted": False},
                ]
            },
            "secret": "LEAK",
        }
    ).model_dump(mode="json")

    assert result["devices"] == [
        {
            "device_uuid": device_uuid,
            "removable": True,
            "mounted": False,
        }
    ]
    assert "LEAK" not in json.dumps(result)
    assert host_bridge.public_result(result).model_dump(mode="json")["devices"] == result["devices"]


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
