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
    actual = response.json()
    assert actual.pop("created_at")
    assert actual.pop("updated_at")
    assert actual == {
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
            "cleanup_preview": None,
            "cleanup_result": None,
            "docker_image_preview": None,
            "docker_image_result": None,
            "builder_cache_preview": None,
            "builder_cache_result": None,
            "package_result": None,
            "backup_result": None,
            "usb_result": None,
            "action_result": None,
            "diagnostics_ready": False,
        },
        "progress_percent": None,
        "artifact_ready": False,
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


def test_ota_progress_is_monotonic_in_runtime_execution_order():
    phases = (
        "accepted",
        "verified",
        "staged",
        "snapshot_done",
        "migration_started",
        "migration_done",
        "cutover_started",
        "health_checked",
        "published",
    )

    values = [host_bridge.OTA_PROGRESS_PERCENT[phase] for phase in phases]

    assert values == sorted(values)
    assert len(values) == len(set(values))


def test_missing_or_invalid_host_health_snapshot_reports_source_failure(
    client, seed_royal, installed
):
    login_as(client, "royal", "secret")
    for contents in (None, "{invalid"):
        path = installed / "public/system-health.json"
        if contents is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(contents)
        response = client.get("/admin/ops/system-health")
        assert response.status_code == 200
        body = response.json()
        assert body["overall"] == "degraded"
        assert body["checks"] == [
            {
                "code": "host_health_unavailable",
                "status": "failed",
                "message": "Снимок проверки хоста отсутствует или повреждён",
                "repair": None,
            }
        ]


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
    assert client.get("/admin/ops/diagnostic-artifact").status_code == 410
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
    assert response.status_code == 410, response.text
    assert response.json()["detail"] == "typed_operation_required"
    (artifacts / (job_id + ".zip")).unlink()
    (artifacts / (job_id + ".zip")).symlink_to(installed / "inbox/approved.json")
    assert client.get("/admin/ops/diagnostic-artifact").status_code == 410


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


def test_public_result_projects_only_exact_cleanup_preview_targets():
    plan_id = "11111111-1111-4111-8111-111111111111"
    projected = host_bridge.public_result(
        {
            "kind": "cleanup-preview",
            "state": "succeeded",
            "detail": {
                "plan_id": plan_id,
                "blocked": False,
                "planned": [
                    {
                        "category": "logs",
                        "path": "update.log",
                        "bytes": 123,
                        "fingerprint": "x" * 64,
                    },
                    {"category": "releases", "path": "../outside", "bytes": 1000},
                ],
            },
        }
    ).model_dump(mode="json")

    assert projected["cleanup_preview"] == {
        "plan_id": plan_id,
        "blocked": True,
        "planned": [{"category": "logs", "path": "update.log", "bytes": 123}],
        "total_bytes": 123,
    }
    assert "fingerprint" not in json.dumps(projected)
    assert "../outside" not in json.dumps(projected)

    assert (
        host_bridge.public_result(projected).model_dump(mode="json")["cleanup_preview"]
        == (projected["cleanup_preview"])
    )


def test_ota_cache_is_an_allowlisted_cleanup_preview_target():
    from robopark_api.ops_schemas import HostCleanupPreviewIn

    identity = "11111111-1111-4111-8111-111111111111"
    digest = "a" * 64
    operation = HostCleanupPreviewIn.model_validate(
        {
            "operation_id": identity,
            "kind": "cleanup-preview",
            "capability_revision": "b" * 64,
            "confirmation": "ЗАПУСТИТЬ CLEANUP-PREVIEW",
            "categories": ["ota_cache"],
        }
    )
    assert operation.categories == ["ota_cache"]

    projected = host_bridge.public_result(
        {
            "kind": "cleanup-preview",
            "state": "succeeded",
            "detail": {
                "plan_id": identity,
                "blocked": False,
                "planned": [
                    {
                        "category": "ota_cache",
                        "path": f"{digest}.ota",
                        "bytes": 4096,
                        "fingerprint": "private",
                    },
                ],
            },
        }
    ).model_dump(mode="json")["cleanup_preview"]

    assert projected == {
        "plan_id": identity,
        "blocked": False,
        "total_bytes": 4096,
        "planned": [{"category": "ota_cache", "path": f"{digest}.ota", "bytes": 4096}],
    }


def test_cleanup_execution_projects_confirmed_deletions_and_uncertain_target():
    projected = host_bridge.public_result(
        {
            "kind": "cleanup-execute",
            "state": "failed",
            "error": "cleanup_partial",
            "detail": {
                "deleted": [
                    {
                        "category": "diagnostics",
                        "path": "first.log",
                        "bytes": 4096,
                        "fingerprint": "private",
                    }
                ],
                "deleted_count": 99,
                "uncertain_target": {
                    "category": "ota_cache",
                    "path": f"{'a' * 64}.ota",
                    "bytes": 8192,
                    "fingerprint": "private",
                },
            },
        }
    ).model_dump(mode="json")
    assert projected["cleanup_result"] == {
        "deleted": [{"category": "diagnostics", "path": "first.log", "bytes": 4096}],
        "deleted_count": 1,
        "uncertain_target": {"category": "ota_cache", "path": f"{'a' * 64}.ota", "bytes": 8192},
    }
    assert "private" not in json.dumps(projected)


def test_owned_docker_image_preview_projects_only_robopark_tags_and_reported_sizes():
    tag = "robopark-api:11111111-1111-4111-8111-111111111111"
    projected = host_bridge.public_result(
        {
            "kind": "docker-image-preview",
            "state": "succeeded",
            "detail": {
                "blocked": False,
                "unverified_tags": 1,
                "total_reported_bytes": 4096,
                "planned": [
                    {"tag": tag, "image_id": "sha256:" + "a" * 64, "reported_bytes": 4096},
                    {"tag": "foreign-app:latest", "image_id": "private", "reported_bytes": 8192},
                ],
            },
        }
    ).model_dump(mode="json")
    assert projected["docker_image_preview"] == {
        "plan_id": None,
        "blocked": True,
        "unverified_tags": 1,
        "total_reported_bytes": 4096,
        "planned": [{"tag": tag, "reported_bytes": 4096}],
    }
    assert "foreign-app" not in json.dumps(projected)
    assert "image_id" not in json.dumps(projected)
    assert (
        host_bridge.public_result(projected).model_dump(mode="json")["docker_image_preview"]
        == projected["docker_image_preview"]
    )


def test_owned_docker_image_plan_and_partial_result_project_only_safe_fields():
    plan_id = "00000000-0000-4000-8000-000000000003"
    tag = "robopark-api:11111111-1111-4111-8111-111111111111"
    preview = host_bridge.public_result(
        {
            "kind": "docker-image-preview",
            "state": "succeeded",
            "detail": {
                "plan_id": plan_id,
                "blocked": False,
                "unverified_tags": 0,
                "total_reported_bytes": 4096,
                "planned": [{"tag": tag, "image_id": "sha256:" + "a" * 64, "reported_bytes": 4096}],
            },
        }
    ).model_dump(mode="json")
    assert preview["docker_image_preview"]["plan_id"] == plan_id
    assert "image_id" not in json.dumps(preview)

    result = host_bridge.public_result(
        {
            "kind": "docker-image-execute",
            "state": "failed",
            "error": "image_cleanup_partial",
            "detail": {
                "deleted": [{"tag": tag, "image_id": "private", "reported_bytes": 4096}],
                "deleted_count": 1,
                "uncertain_target": {"tag": "foreign-app:latest", "reported_bytes": 8192},
            },
        }
    ).model_dump(mode="json")
    assert result["docker_image_result"] == {
        "deleted": [{"tag": tag, "reported_bytes": 4096}],
        "deleted_count": 1,
        "uncertain_target": None,
    }
    assert "private" not in json.dumps(result)


def test_owned_buildkit_plan_projects_one_exact_record_without_builder_identity():
    plan_id = "00000000-0000-4000-8000-000000000003"
    preview = host_bridge.public_result(
        {
            "kind": "builder-cache-preview",
            "state": "succeeded",
            "detail": {
                "plan_id": plan_id,
                "blocked": False,
                "builder": "robopark-buildkit-" + "a" * 32,
                "planned": [
                    {"id": "private", "reported_bytes": 8192, "Description": "secret path"}
                ],
                "total_reported_bytes": 8192,
                "other_candidates": 2,
            },
        }
    ).model_dump(mode="json")
    assert preview["builder_cache_preview"] == {
        "plan_id": plan_id,
        "blocked": False,
        "planned": [{"id": "private", "reported_bytes": 8192}],
        "total_reported_bytes": 8192,
        "other_candidates": 2,
    }
    assert "secret path" not in json.dumps(preview)
    assert "robopark-buildkit" not in json.dumps(preview)

    invalid = host_bridge.public_result(
        {
            "kind": "builder-cache-preview",
            "state": "succeeded",
            "detail": {
                "plan_id": plan_id,
                "blocked": False,
                "planned": [{"id": "foreign/id", "reported_bytes": 8192}],
                "total_reported_bytes": 8192,
                "other_candidates": 0,
            },
        }
    ).model_dump(mode="json")
    assert invalid["builder_cache_preview"]["blocked"] is True
    assert invalid["builder_cache_preview"]["plan_id"] is None


def test_buildkit_partial_result_projects_only_exact_record():
    projected = host_bridge.public_result(
        {
            "kind": "builder-cache-execute",
            "state": "failed",
            "error": "builder_cleanup_partial",
            "detail": {
                "deleted": [],
                "deleted_count": 9,
                "uncertain_target": {
                    "id": "private",
                    "reported_bytes": 8192,
                    "Description": "secret path",
                },
            },
        }
    ).model_dump(mode="json")
    assert projected["builder_cache_result"] == {
        "deleted": [],
        "deleted_count": 0,
        "uncertain_target": {"id": "private", "reported_bytes": 8192},
    }
    assert "secret path" not in json.dumps(projected)


def test_buildkit_partial_error_survives_host_reconciliation(installed, test_settings):
    job = new_job("builder-cache-execute", exempt_token_hash="session")
    job.state = "running"
    job.phase = "running"
    job.extra = {"host_updater": True, "host_request": {"actor_user_id": 1}}
    ops = Path(test_settings.ops_dir)
    save_job(ops, job)
    (installed / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": job.id,
                "kind": "builder-cache-execute",
                "actor_user_id": 1,
                "state": "failed",
                "error": "builder_cleanup_partial",
                "detail": {
                    "deleted": [],
                    "deleted_count": 0,
                    "uncertain_target": {"id": "private", "reported_bytes": 8192},
                },
            }
        )
    )

    reconciled = host_bridge.reconcile_host_job(ops, installed)

    assert reconciled.error == "builder_cleanup_partial"
    assert (
        reconciled.extra["host_result"]["builder_cache_result"]["uncertain_target"]["id"]
        == "private"
    )


def test_cleanup_partial_error_survives_host_job_reconciliation(installed, test_settings):
    job = new_job("cleanup-execute", exempt_token_hash="session")
    job.state = "running"
    job.phase = "running"
    job.extra = {"host_updater": True, "host_request": {"actor_user_id": 1}}
    ops = Path(test_settings.ops_dir)
    save_job(ops, job)
    (installed / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": job.id,
                "kind": "cleanup-execute",
                "actor_user_id": 1,
                "state": "failed",
                "error": "cleanup_partial",
                "detail": {
                    "deleted": [{"category": "diagnostics", "path": "first.log", "bytes": 4096}],
                    "deleted_count": 1,
                    "uncertain_target": None,
                },
            }
        )
    )

    reconciled = host_bridge.reconcile_host_job(ops, installed)
    assert reconciled.error == "cleanup_partial"
    assert reconciled.extra["host_result"]["cleanup_result"]["deleted_count"] == 1


def test_image_cleanup_partial_reports_confirmed_tags_and_uncertain_target(
    installed, test_settings
):
    job = new_job("docker-image-execute", exempt_token_hash="session")
    job.state = "running"
    job.phase = "running"
    job.extra = {"host_updater": True, "host_request": {"actor_user_id": 1}}
    ops = Path(test_settings.ops_dir)
    save_job(ops, job)
    first = "robopark-api:11111111-1111-4111-8111-111111111111"
    second = "robopark-web:11111111-1111-4111-8111-111111111111"
    (installed / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": job.id,
                "kind": "docker-image-execute",
                "actor_user_id": 1,
                "state": "failed",
                "error": "image_cleanup_partial",
                "detail": {
                    "deleted": [{"tag": first, "reported_bytes": 4096}],
                    "deleted_count": 1,
                    "uncertain_target": {"tag": second, "reported_bytes": 4096},
                },
            }
        )
    )

    reconciled = host_bridge.reconcile_host_job(ops, installed)

    assert reconciled.error == "image_cleanup_partial"
    assert reconciled.extra["host_result"]["docker_image_result"]["deleted_count"] == 1
    assert (
        reconciled.extra["host_result"]["docker_image_result"]["uncertain_target"]["tag"] == second
    )


def test_changed_image_plan_is_reported_without_claiming_any_deletion(installed, test_settings):
    job = new_job("docker-image-execute", exempt_token_hash="session")
    job.state = "running"
    job.phase = "running"
    job.extra = {"host_updater": True, "host_request": {"actor_user_id": 1}}
    ops = Path(test_settings.ops_dir)
    save_job(ops, job)
    (installed / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": job.id,
                "kind": "docker-image-execute",
                "actor_user_id": 1,
                "state": "failed",
                "error": "image_plan_changed",
                "detail": {},
            }
        )
    )

    reconciled = host_bridge.reconcile_host_job(ops, installed)

    assert reconciled.state == "failed"
    assert reconciled.error == "image_plan_changed"
    assert reconciled.extra["host_result"]["docker_image_result"] is None


def test_ota_rollback_is_reported_only_from_matching_host_status(installed, test_settings):
    job = new_job("ota-update", exempt_token_hash="session")
    job.state = "running"
    job.phase = "running"
    job.extra = {"host_updater": True, "host_request": {"actor_user_id": 1}}
    ops = Path(test_settings.ops_dir)
    save_job(ops, job)
    (installed / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": job.id,
                "kind": "ota-update",
                "actor_user_id": 1,
                "state": "failed",
                "error": "ota_update_failed",
                "detail": {},
            }
        )
    )
    (installed / "public/ota-status.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "operation_id": job.id,
                "phase": "rolled_back",
                "error": "ota_health_check_failed",
                "version": "0.2.0-rc.8",
                "sha256": "a" * 64,
            }
        )
    )

    reconciled = host_bridge.reconcile_host_job(ops, installed)

    assert reconciled.state == "failed"
    assert reconciled.error == "ota_rolled_back"


def test_ota_failure_does_not_use_another_operations_rollback_status(installed, test_settings):
    job = new_job("ota-update", exempt_token_hash="session")
    job.state = "running"
    job.phase = "running"
    job.extra = {"host_updater": True, "host_request": {"actor_user_id": 1}}
    ops = Path(test_settings.ops_dir)
    save_job(ops, job)
    (installed / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": job.id,
                "kind": "ota-update",
                "actor_user_id": 1,
                "state": "failed",
                "error": "ota_update_failed",
                "detail": {},
            }
        )
    )
    (installed / "public/ota-status.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "operation_id": "another-operation",
                "phase": "rolled_back",
                "error": "ota_health_check_failed",
            }
        )
    )

    reconciled = host_bridge.reconcile_host_job(ops, installed)

    assert reconciled.state == "failed"
    assert reconciled.error == "host_operation_failed"


@pytest.mark.parametrize(
    ("schema", "operation_id", "status_error", "expected"),
    [
        (1, "matching", "ota_stage_failed", "ota_stage_failed"),
        (1, "matching", "ota_unsafe_current", "ota_unsafe_current"),
        (1, "matching", "database_password=secret", "host_operation_failed"),
        (1, "matching", [], "host_operation_failed"),
        (1, "matching", {}, "host_operation_failed"),
        (1, "another-operation", "ota_stage_failed", "host_operation_failed"),
        (2, "matching", "ota_stage_failed", "host_operation_failed"),
        (True, "matching", "ota_stage_failed", "host_operation_failed"),
    ],
)
def test_ota_failed_status_exposes_only_stable_error_codes(
    installed, test_settings, schema, operation_id, status_error, expected
):
    job = new_job("ota-update", exempt_token_hash="session")
    job.state = "running"
    job.phase = "running"
    job.extra = {"host_updater": True, "host_request": {"actor_user_id": 1}}
    ops = Path(test_settings.ops_dir)
    save_job(ops, job)
    (installed / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": job.id,
                "kind": "ota-update",
                "actor_user_id": 1,
                "state": "failed",
                "error": "ota_update_failed",
                "detail": {},
            }
        )
    )
    (installed / "public/ota-status.json").write_text(
        json.dumps(
            {
                "schema": schema,
                "operation_id": job.id if operation_id == "matching" else operation_id,
                "phase": "failed",
                "error": status_error,
                "version": "0.2.0-rc.16",
                "sha256": "a" * 64,
            }
        )
    )

    reconciled = host_bridge.reconcile_host_job(ops, installed)

    assert reconciled.state == "failed"
    assert reconciled.error == expected


def test_ota_rollback_failure_requires_manual_recovery(installed, test_settings):
    job = new_job("ota-update", exempt_token_hash="session")
    job.state = "running"
    job.phase = "running"
    job.extra = {"host_updater": True, "host_request": {"actor_user_id": 1}}
    ops = Path(test_settings.ops_dir)
    save_job(ops, job)
    (installed / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": job.id,
                "kind": "ota-update",
                "actor_user_id": 1,
                "state": "failed",
                "error": "ota_update_failed",
                "detail": {},
            }
        )
    )
    (installed / "public/ota-status.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "operation_id": job.id,
                "phase": "failed",
                "error": "ota_rollback_failed",
            }
        )
    )

    reconciled = host_bridge.reconcile_host_job(ops, installed)

    assert reconciled.state == "failed"
    assert reconciled.error == "ota_rollback_failed"


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
