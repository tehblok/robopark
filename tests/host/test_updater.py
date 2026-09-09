"""Exercise filesystem outcomes; only Docker/systemd are replaced by a fake."""

import json
import os
import subprocess
import sys
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from robopark_api.services.ops.archives import build_archive
from robopark_host.release import ReleaseError, UpdateRequest
from robopark_host.updater import SystemRunner, apply_release, reconcile_after_exit


class FakeRunner:
    """External host boundaries, with real filesystem effects for migration."""

    def __init__(self, paths):
        self.paths = paths
        self.commands = []
        self.health = True
        self.previous_health = True
        self.database_heads = ["new"]
        self.public_health = True
        self.fail_on = None
        self.observations = []

    def run(self, argv, *, timeout, cwd=None, env=None, capture=False):
        self.commands.append(list(map(str, argv)))
        if self.fail_on and self.fail_on in argv:
            raise RuntimeError("SECRET_KEY=never-log-this")
        if argv[:3] == ["docker", "image", "inspect"]:
            return ("sha256:" + ("3" if "api" in argv[-1] else "4") * 64).encode()
        if "--format" in argv:
            return json.dumps(
                {
                    "services": {
                        "api": {
                            "build": {"context": "../apps/api"},
                            "env_file": [{"path": str(self.paths.etc / "host.env")}],
                            "environment": {"DATABASE_URL": "sqlite:////data/robopark.db"},
                            "volumes": [
                                {
                                    "type": "bind",
                                    "source": str(self.paths.var / "data"),
                                    "target": "/data",
                                }
                            ],
                        },
                        "web": {
                            "build": {"context": "../apps/web"},
                            "ports": [{"published": "8080", "target": 80}],
                        },
                    }
                }
            ).encode()
        if argv[0] == "curl":
            return (
                b'{"status":"ready"}\n200' if self.public_health else b'{"status":"degraded"}\n503'
            )
        if any("SELECT version_num FROM alembic_version" in str(arg) for arg in argv):
            return json.dumps(self.database_heads).encode()
        if "upgrade" in argv:
            assert (self.paths.state / "maintenance.json").exists()
            (self.paths.var / "data/robopark.db").write_text("migrated")
        if "build" in argv or ("sh" in argv and "api" in argv):
            self.observations.append(self.paths.current.resolve().name)
        return b""

    def wait_ready(self, *, project, config, timeout):
        if project == "robopark":
            assert (self.paths.state / "maintenance.json").exists()
            if self.paths.current.resolve().name == "1.0.0":
                return self.previous_health
            return self.health
        return True


@pytest.fixture
def host(host_paths):
    class Host:
        paths = host_paths

        def package(self, version="2.0.0", *, meta=None, filename=None, omit=()):
            tree = self.paths.root / ("payload-" + str(uuid.uuid4()))
            contents = {
                "apps/api/Dockerfile": "FROM scratch\n",
                "deploy/Dockerfile.api-tests": "FROM scratch AS test\n",
                "apps/api/uv.lock": "version = 1\n",
                "apps/api/pyproject.toml": "[project]\nname='test'\n",
                "apps/web/Dockerfile": "FROM scratch AS build\n",
                "apps/web/package-lock.json": "{}\n",
                "apps/web/package.json": "{}\n",
                "scripts/verify.sh": "#!/bin/sh\nexit 0\n",
                "deploy/docker-compose.yml": "services: {}\n",
                "deploy/host/robopark": "import sys; sys.exit(0)\n",
                "deploy/host/robopark_host/__init__.py": "",
                "deploy/systemd/robopark.service": "[Service]\nExecStart=/bin/true\n",
                "deploy/systemd/robopark-tuna.service": "[Service]\nExecStart=/bin/true\n",
            }
            for name, body in contents.items():
                if name in omit:
                    continue
                p = tree / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body)
            blob = build_archive(
                kind="release",
                source_root=tree,
                app_version=version,
                release_meta={
                    "git_sha": "a" * 40,
                    "migration_head": "new",
                    "migration_compatibility": {"from_heads": ["old"], "reversible": True},
                    **(meta or {}),
                },
                signing_key=self.private,
            )
            artifact = self.paths.ops / "artifacts" / (filename or f"release-{version}.zip")
            artifact.write_bytes(blob)
            return artifact

        def request(self, _artifact=None, **changes):
            return UpdateRequest.from_dict(
                {
                    "job_id": str(uuid.uuid4()),
                    "kind": "update",
                    "artifact": (_artifact or self.artifact).name,
                    "actor_user_id": 7,
                    "created_at": datetime.now(UTC).isoformat(),
                    **changes,
                }
            )

    host = Host()
    for directory in (
        host_paths.etc,
        host_paths.releases,
        host_paths.ops / "artifacts",
        host_paths.var / "data",
        host_paths.state,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    host.private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    (host_paths.etc / "release-public-key.pem").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    (host_paths.etc / "host.env").write_text(
        "SECRET_KEY=never-log-this\nTUNA_TOKEN=also-secret\nCORS_ORIGINS=https://robopark.example.tuna.am\n"
    )
    old = host.package("1.0.0", meta={"migration_head": "old", "migration_compatibility": {}})
    previous = host_paths.releases / "1.0.0"
    previous.mkdir()
    with zipfile.ZipFile(old) as archive:
        archive.extractall(previous)
    host_paths.current.symlink_to(previous)
    (host_paths.opt / "host-tools").symlink_to(previous / "deploy/host")
    config = host_paths.state / "compose-1.0.0.json"
    config.write_text('{"services":{}}')
    (host_paths.state / "current-compose.json").symlink_to(config)
    unit = host_paths.root / "etc/systemd/system/robopark.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("old unit\n")
    (host_paths.var / "data/robopark.db").write_text("original")
    (host_paths.var / "data/attachment").write_bytes(b"attachment")
    host.artifact = host.package()
    host.runner = FakeRunner(host_paths)
    return host


@pytest.mark.parametrize(
    "change",
    [
        {"artifact": "../escape.zip"},
        {"artifact": "/tmp/a.zip"},
        {"artifact": "a\\b.zip"},
        {"artifact": "x\n.zip"},
        {"kind": "restore"},
        {"actor_user_id": True},
        {"actor_user_id": 0},
        {"actor_user_id": "7"},
        {"job_id": "../x"},
        {"created_at": "2026-01-01"},
        {"created_at": "tomorrow"},
        {"created_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        {"created_at": (datetime.now(UTC) - timedelta(days=2)).isoformat()},
        {"extra": "SECRET_KEY=secret"},
    ],
)
def test_approved_request_rejects_untrusted_fields(host, change):
    with pytest.raises(ReleaseError, match="invalid_request"):
        host.request(**change)


def test_request_rejects_duplicate_json_keys(host):
    request = host.paths.ops / "inbox.json"
    request.write_text('{"job_id":"one", "job_id":"two"}')
    with pytest.raises(ReleaseError, match="invalid_request"):
        UpdateRequest.from_file(request)


def test_artifact_symlink_cannot_escape_inbox(host):
    escaped = host.paths.root / "external.zip"
    escaped.write_bytes(host.artifact.read_bytes())
    host.artifact.unlink()
    host.artifact.symlink_to(escaped)
    result = apply_release(host.request(), host.paths, host.runner)
    assert result.error == "unsafe_artifact"
    assert host.runner.commands == []
    assert not (host.paths.state / "updater-journal.json").exists()


def test_signature_rejected_before_mutation_or_commands(host):
    with zipfile.ZipFile(host.artifact) as z:
        files = {name: z.read(name) for name in z.namelist()}
    files["manifest.sig"] = b"x" * 64
    with zipfile.ZipFile(host.artifact, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    result = apply_release(host.request(), host.paths, host.runner)
    assert result.error == "signature_invalid"
    assert host.runner.commands == []
    assert sorted(p.name for p in host.paths.releases.iterdir()) == ["1.0.0"]
    assert not (host.paths.state / "updater-journal.json").exists()


@pytest.mark.parametrize(
    "meta,error",
    [
        ({"min_installer_version": "99.0.0"}, "installer_incompatible"),
        ({"required_capabilities": ["unavailable"]}, "capability_missing"),
        ({"migration_compatibility": {}}, "migration_incompatible"),
        (
            {"migration_compatibility": {"from_heads": ["old"], "reversible": False}},
            "migration_incompatible",
        ),
    ],
)
def test_incompatible_release_does_not_stage(host, meta, error):
    artifact = host.package("3.0.0", meta=meta)
    result = apply_release(host.request(artifact), host.paths, host.runner)
    assert result.error == error
    assert host.runner.commands == []
    assert sorted(p.name for p in host.paths.releases.iterdir()) == ["1.0.0"]


def test_downgrade_is_rejected(host):
    result = apply_release(host.request(host.package("0.9.0")), host.paths, host.runner)
    assert result.error == "downgrade_rejected"


def test_build_failure_keeps_current_code_config_and_data(host):
    host.runner.fail_on = "build"
    result = apply_release(host.request(), host.paths, host.runner)
    assert result.error == "build_failed"
    assert host.paths.current.resolve().name == "1.0.0"
    assert (host.paths.var / "data/robopark.db").read_text() == "original"
    assert (host.paths.state / "current-compose.json").resolve().name == "compose-1.0.0.json"
    assert not (host.paths.state / "maintenance.json").exists()
    public = (host.paths.ops / "rebuild.result").read_text() + (
        host.paths.state / "updater-journal.json"
    ).read_text()
    assert "never-log-this" not in public
    assert "SECRET_KEY" not in public


def test_generic_command_failure_is_reported_as_the_active_ota_phase(host):
    original = host.runner.run

    def fail_build(argv, **kwargs):
        if "build" in argv:
            raise ReleaseError("command_failed")
        return original(argv, **kwargs)

    host.runner.run = fail_build
    result = apply_release(host.request(), host.paths, host.runner)

    assert result.error == "build_failed"


def test_production_images_are_built_sequentially_for_small_arm_hosts(host):
    apply_release(host.request(), host.paths, host.runner)

    builds = [
        command
        for command in host.runner.commands
        if command[:2] == ["docker", "compose"] and "build" in command
    ]
    assert [command[-1] for command in builds] == ["api", "web"]
    assert all(command[-3:-1] == ["build", "--pull"] for command in builds)


def test_ota_skips_redundant_test_images_and_uses_runtime_smoke(host):
    apply_release(host.request(), host.paths, host.runner)

    assert not any(command[:2] == ["docker", "run"] for command in host.runner.commands)
    assert not any("--target" in command for command in host.runner.commands)
    assert any(
        command[:2] == ["docker", "compose"] and "up" in command
        for command in host.runner.commands
    )
    assert host.runner.observations


def test_success_stages_isolated_compose_then_reconciles_after_worker_exit(host):
    request = host.request()
    result = apply_release(request, host.paths, host.runner)
    assert result.state == "awaiting_reconciliation"
    assert (host.paths.state / "maintenance.json").exists()
    assert host.runner.observations and set(host.runner.observations) == {"1.0.0"}
    assert all(
        "-p" in c and "-f" in c for c in host.runner.commands if c[:2] == ["docker", "compose"]
    )
    configs = list((host.paths.state / "compose").glob("*.json"))
    assert configs
    assert all(
        "never-log-this" not in p.read_text() and "also-secret" not in p.read_text()
        for p in configs
    )
    isolated = json.loads(next(p for p in configs if "smoke" in p.name).read_text())
    assert str(host.paths.var / "data") not in json.dumps(isolated)
    assert '"8080"' not in json.dumps(isolated)
    assert "host-repo" not in json.dumps(isolated)
    assert not any("daemon-reload" in c for c in host.runner.commands)
    done = reconcile_after_exit(host.paths, host.runner)
    assert done.state == "current_healthy"
    assert not (host.paths.state / "maintenance.json").exists()
    assert host.paths.current.resolve().name != "1.0.0"
    assert host.paths.previous.resolve().name == "1.0.0"
    assert (
        host.paths.state / "current-compose.json"
    ).resolve().name == request.job_id + "-production.json"
    assert (host.paths.opt / "host-tools").resolve() == host.paths.current.resolve() / "deploy/host"
    assert json.loads((host.paths.ops / "rebuild.result").read_text())["ok"] is True
    assert json.loads((host.paths.state / "last-backup.json").read_text())["status"] == "success"


def test_failed_pre_cutover_snapshot_records_failed_backup(host, monkeypatch):
    from robopark_host import updater

    monkeypatch.setattr(updater, "snapshot", lambda *_args: (_ for _ in ()).throw(OSError()))
    result = apply_release(host.request(), host.paths, host.runner)

    assert result.error == "update_failed"
    assert json.loads((host.paths.state / "last-backup.json").read_text())["status"] == "failed"


def test_failed_health_restores_previous_code_units_and_snapshot(host):
    host.runner.health = False
    result = apply_release(host.request(), host.paths, host.runner)
    assert result.error == "cutover_unhealthy"
    assert host.paths.current.resolve().name == "1.0.0"
    assert (host.paths.state / "current-compose.json").resolve().name == "compose-1.0.0.json"
    assert (host.paths.var / "data/robopark.db").read_text() == "original"
    assert (host.paths.var / "data/attachment").read_bytes() == b"attachment"
    assert (host.paths.root / "etc/systemd/system/robopark.service").read_text() == "old unit\n"
    assert (host.paths.opt / "host-tools").resolve() == host.paths.current.resolve() / "deploy/host"
    assert not (host.paths.state / "maintenance.json").exists()


def test_both_releases_unhealthy_keep_maintenance_and_snapshot(host):
    host.runner.health = host.runner.previous_health = False
    result = apply_release(host.request(), host.paths, host.runner)
    assert result.error == "manual_recovery_required"
    assert (host.paths.state / "maintenance.json").exists()
    assert list((host.paths.ops / "rollbacks").glob("*/data/robopark.db"))


def test_system_runner_bounds_output_and_timeout():
    runner = SystemRunner()
    with pytest.raises(ReleaseError, match="command_output_limit"):
        runner.run([sys.executable, "-c", "print('x'*3000000)"], timeout=5, capture=True)
    with pytest.raises(ReleaseError, match="command_timeout"):
        runner.run([sys.executable, "-c", "import time; time.sleep(3)"], timeout=0.1)
    assert runner.run([sys.executable, "-c", "print('ok')"], timeout=5, capture=True) == b"ok\n"


def test_system_runner_places_docker_config_in_writable_ops(host_paths, monkeypatch):
    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setenv("ROBOPARK_ROOT", str(host_paths.root))

    output = SystemRunner().run(
        [sys.executable, "-c", "import os; print(os.environ['DOCKER_CONFIG'])"],
        timeout=5,
        capture=True,
    )

    assert output.decode().strip() == str(host_paths.ops / "docker-config")


def test_system_runner_classifies_and_retains_root_only_failed_command_log(
    host_paths, monkeypatch
):
    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setenv("ROBOPARK_ROOT", str(host_paths.root))
    log = host_paths.root / "var/log/robopark/ota-update.log"

    with pytest.raises(ReleaseError, match="docker_out_of_memory"):
        SystemRunner(log).run(
            [sys.executable, "-c", "import sys; print('build exhausted memory'); sys.exit(137)"],
            timeout=5,
        )

    assert "build exhausted memory" in log.read_text()
    assert log.stat().st_mode & 0o777 == 0o600


def test_system_runner_cleanup_failure_does_not_replace_real_failure_log(
    host_paths, monkeypatch, tmp_path
):
    from robopark_host.updater import _cleanup_staging

    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setenv("ROBOPARK_ROOT", str(host_paths.root))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\necho 'No such image' >&2\nexit 1\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    log = host_paths.root / "var/log/robopark/ota-update.log"
    log.parent.mkdir(parents=True)
    log.write_text("real build failure\n")

    runner = SystemRunner(log)
    _cleanup_staging(host_paths, {"job_id": str(uuid.uuid4())}, runner)

    assert log.read_text() == "real build failure\n"


def test_retention_keeps_two_successes_after_third_update(host):
    apply_release(host.request(), host.paths, host.runner)
    reconcile_after_exit(host.paths, host.runner)
    second = host.paths.current.resolve()
    third = host.package("3.0.0", meta={"migration_head": "new", "migration_compatibility": {}})
    result = apply_release(host.request(third), host.paths, host.runner)
    assert result.state == "awaiting_reconciliation"
    reconcile_after_exit(host.paths, host.runner)
    assert host.paths.previous.resolve() == second
    assert sorted(p.name for p in host.paths.releases.iterdir()) == sorted(
        [second.name, host.paths.current.resolve().name]
    )
    assert len(list((host.paths.ops / "rollbacks").iterdir())) == 1


def test_failure_after_partial_restore_is_recoverable(host, monkeypatch):
    import robopark_host.rollback as rollback
    import robopark_host.updater as updater

    original = rollback.os.replace
    tripped = False

    def power_loss(src, dest):
        nonlocal tripped
        original(src, dest)
        if Path(src) == host.paths.var / "data" and not tripped:
            tripped = True
            raise KeyboardInterrupt()

    monkeypatch.setattr(rollback.os, "replace", power_loss)
    host.runner.health = False
    with pytest.raises(KeyboardInterrupt):
        apply_release(host.request(), host.paths, host.runner)
    result = updater.recover_interrupted_update(host.paths, host.runner)
    assert result.state == "previous_restored"
    assert (host.paths.var / "data/robopark.db").read_text() == "original"


def test_failed_candidate_is_removed_but_previous_material_retained(host):
    host.runner.health = False
    apply_release(host.request(), host.paths, host.runner)
    assert [p.name for p in host.paths.releases.iterdir()] == ["1.0.0"]
    assert list((host.paths.ops / "rollbacks").glob("*/data/robopark.db"))


def test_self_test_executes_without_system_state_mutation(host):
    result = subprocess.run(
        [sys.executable, "-B", "deploy/host/robopark", "--self-test"],
        env={**os.environ, "ROBOPARK_TESTING": "1", "ROBOPARK_ROOT": str(host.paths.root)},
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert not (host.paths.state / "updater-journal.json").exists()


def test_cli_can_process_explicit_request_and_reconcile(host, monkeypatch):
    from robopark_host import cli

    monkeypatch.setattr("robopark_host.updater.SystemRunner", lambda: host.runner)
    path = host.paths.ops / "approved.json"
    path.write_text(json.dumps(vars(host.request())))
    assert cli.main(["update", "--request", str(path), "--worker"]) == 0
    assert (host.paths.state / "maintenance.json").exists()
    assert cli.main(["update", "--reconcile"]) == 0
    assert not (host.paths.state / "maintenance.json").exists()


def test_stable_launcher_waits_for_old_worker_before_successor_reconciliation(host):
    from robopark_host.launcher import launch_update

    path = host.paths.ops / "approved.json"
    path.write_text(json.dumps(vars(host.request())))
    old = (host.paths.opt / "host-tools").resolve()

    class LauncherRunner:
        def __init__(self):
            self.called = []

        def run(self, argv, **kwargs):
            self.called.append(list(map(str, argv)))
            if "--worker" in argv:
                assert str(old / "robopark") in list(map(str, argv))
                apply_release(UpdateRequest.from_file(path), host.paths, host.runner)
            else:
                assert "--reconcile" in argv
                assert str(host.paths.current.resolve() / "deploy/host/robopark") in list(
                    map(str, argv)
                )
                assert old != (host.paths.opt / "host-tools").resolve()
                reconcile_after_exit(host.paths, host.runner)
            return b""

    runner = LauncherRunner()
    assert launch_update(host.paths, path, runner) == 0
    assert len(runner.called) == 2
    assert not (host.paths.state / "maintenance.json").exists()


def test_production_config_migrates_legacy_mounts_to_host_owned_data(host):
    original = host.runner.run

    def legacy(argv, **kwargs):
        data = original(argv, **kwargs)
        if "--format" in argv and "config" in argv:
            config = json.loads(data)
            config["services"]["api"]["volumes"] = [
                {"type": "volume", "source": "robopark_data", "target": "/data"},
                {"type": "bind", "source": "/live-checkout", "target": "/host-repo"},
            ]
            return json.dumps(config).encode()
        return data

    host.runner.run = legacy
    request = host.request()
    apply_release(request, host.paths, host.runner)
    config = json.loads(
        (host.paths.state / "compose" / (request.job_id + "-production.json")).read_text()
    )
    assert config["services"]["api"]["volumes"] == [
        {
            "type": "bind",
            "source": str(host.paths.etc / "release-public-key.pem"),
            "target": "/etc/robopark/release-public-key.pem",
            "read_only": True,
        },
        {"type": "bind", "source": str(host.paths.var / "data"), "target": "/data"},
        {"type": "bind", "source": str(host.paths.var / "api-ops"), "target": "/ops"},
        {
            "type": "bind",
            "source": str(host.paths.ops / "inbox"),
            "target": "/host-ops/inbox",
            "read_only": False,
        },
        {
            "type": "bind",
            "source": str(host.paths.ops / "artifacts"),
            "target": "/host-ops/artifacts",
            "read_only": False,
        },
        {
            "type": "bind",
            "source": str(host.paths.ops / "public"),
            "target": "/host-ops/public",
            "read_only": True,
        },
    ]
    assert config["services"]["api"]["environment"]["OPS_DIR"] == "/ops"


@pytest.mark.parametrize("external_error", ["SECRET_KEY=never-log-this", "lowercase_secret_token"])
def test_raw_external_error_cannot_enter_journal_or_status(host, external_error):
    original = host.runner.run

    def fail(argv, **kwargs):
        if "build" in argv:
            raise ReleaseError(external_error)
        return original(argv, **kwargs)

    host.runner.run = fail
    result = apply_release(host.request(), host.paths, host.runner)
    assert result.error == "build_failed"
    assert external_error not in (host.paths.state / "updater-journal.json").read_text()


def test_replaying_completed_request_does_not_rebuild_or_restart(host):
    request = host.request()
    apply_release(request, host.paths, host.runner)
    reconcile_after_exit(host.paths, host.runner)
    command_count = len(host.runner.commands)
    result = apply_release(request, host.paths, host.runner)
    assert result.state == "current_healthy"
    assert len(host.runner.commands) == command_count


def test_snapshot_restore_preserves_permissions(host):
    database = host.paths.var / "data/robopark.db"
    database.chmod(0o640)
    old = database.stat()
    host.runner.health = False
    apply_release(host.request(), host.paths, host.runner)
    restored = database.stat()
    assert restored.st_mode & 0o777 == 0o640
    assert (restored.st_uid, restored.st_gid) == (old.st_uid, old.st_gid)


def test_smoke_data_volume_inherits_image_nonroot_permissions(host):
    request = host.request()
    apply_release(request, host.paths, host.runner)
    config = json.loads(
        (host.paths.state / "compose" / (request.job_id + "-smoke.json")).read_text()
    )
    volume = config["services"]["api"]["volumes"][0]
    assert volume == {"type": "volume", "source": "candidate_data", "target": "/data"}
    assert config["volumes"] == {"candidate_data": {}}


def test_success_restarts_application_under_new_unit_before_opening_writes(host):
    apply_release(host.request(), host.paths, host.runner)
    start = len(host.runner.commands)
    reconcile_after_exit(host.paths, host.runner)
    commands = host.runner.commands[start:]
    reload_index = commands.index(["systemctl", "daemon-reload"])
    restart_index = commands.index(["systemctl", "restart", "robopark.service"])
    assert reload_index < restart_index


def test_low_disk_rejects_before_staging(host, monkeypatch):
    import shutil

    monkeypatch.setattr(
        "robopark_host.updater.shutil.disk_usage", lambda _p: shutil._ntuple_diskusage(100, 99, 1)
    )
    result = apply_release(host.request(), host.paths, host.runner)
    assert result.error == "insufficient_space"
    assert host.runner.commands == []
    assert not (host.paths.state / "updater-journal.json").exists()


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("checksum", "checksum_mismatch"),
        ("traversal", "unsafe_path"),
        ("symlink", "unsafe_path"),
        ("duplicate", "duplicate_member"),
    ],
)
def test_host_rejects_unsafe_archive_independently(host, mutation, error):
    import warnings

    with zipfile.ZipFile(host.artifact) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    if mutation == "checksum":
        members["scripts/verify.sh"] = b"unexpected"
    with zipfile.ZipFile(host.artifact, "w") as archive:
        for name, content in members.items():
            if mutation == "symlink" and name == "scripts/verify.sh":
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = 0o120777 << 16
                archive.writestr(info, content)
            else:
                archive.writestr(name, content)
        if mutation == "traversal":
            archive.writestr("../escaped", b"bad")
        if mutation == "duplicate":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr("manifest.json", members["manifest.json"])
    result = apply_release(host.request(), host.paths, host.runner)
    assert result.error == error
    assert host.runner.commands == []


def test_readable_public_bridge_contains_only_sanitized_status(host):
    apply_release(host.request(), host.paths, host.runner)
    assert json.loads((host.paths.ops / "public/maintenance.json").read_text()) == {
        "enabled": True,
        "reason": "update",
    }
    reconcile_after_exit(host.paths, host.runner)
    public = host.paths.ops / "public/host-status.json"
    assert public.stat().st_mode & 0o777 == 0o644
    data = json.loads(public.read_text())
    assert data["state"] == "current_healthy"
    assert set(data) == {"state", "error", "job_id"}
    assert not (host.paths.ops / "public/maintenance.json").exists()


def test_candidate_self_test_cannot_change_signed_files_before_cutover(host):
    original = host.runner.run

    def change_file(argv, **kwargs):
        result = original(argv, **kwargs)
        if "--self-test" in argv:
            script = Path(argv[2])
            (script.parents[2] / "scripts/verify.sh").write_text("changed by self-test")
        return result

    host.runner.run = change_file
    result = apply_release(host.request(), host.paths, host.runner)
    assert result.error == "checksum_mismatch"
    assert host.paths.current.resolve().name == "1.0.0"
    assert not any("upgrade" in c for c in host.runner.commands)


def test_cli_rejection_publishes_result_and_consumes_approval(host, monkeypatch):
    from robopark_host import cli

    monkeypatch.setattr("robopark_host.updater.SystemRunner", lambda: host.runner)
    request = host.request()
    path = host.paths.ops / "approved.json"
    path.write_text(json.dumps(vars(request)))
    host.artifact.write_bytes(b"invalid zip")
    assert cli.main(["update", "--request", str(path), "--worker"]) == 1
    assert not path.exists()
    assert json.loads((host.paths.ops / "rebuild.result").read_text()) == {
        "job_id": request.job_id,
        "ok": False,
        "error": "invalid_archive",
    }


@pytest.mark.parametrize("fifo_kind", ["request", "artifact"])
def test_cli_rejects_fifo_inputs_without_blocking(host, fifo_kind):
    request = host.request()
    path = host.paths.ops / "approved.json"
    path.write_text(json.dumps(vars(request)))
    fifo = path if fifo_kind == "request" else host.artifact
    fifo.unlink()
    os.mkfifo(fifo)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "deploy/host/robopark",
            "update",
            "--request",
            str(path),
            "--worker",
        ],
        env={**os.environ, "ROBOPARK_TESTING": "1", "ROBOPARK_ROOT": str(host.paths.root)},
        capture_output=True,
        timeout=2,
        check=False,
    )
    assert result.returncode == 1
    assert not (host.paths.state / "updater-journal.json").exists()


def test_host_publishes_result_in_readonly_public_bridge(host):
    apply_release(host.request(), host.paths, host.runner)
    reconcile_after_exit(host.paths, host.runner)
    result = host.paths.ops / "public/rebuild.result"
    assert result.stat().st_mode & 0o777 == 0o644
    assert json.loads(result.read_text())["ok"] is True


def test_public_bridge_remains_readable_under_private_systemd_umask(host):
    old = os.umask(0o077)
    try:
        apply_release(host.request(), host.paths, host.runner)
        assert (host.paths.ops / "public").stat().st_mode & 0o777 == 0o755
    finally:
        os.umask(old)


def test_changed_tuna_unit_is_restarted_after_local_readiness(host):
    apply_release(host.request(), host.paths, host.runner)
    reconcile_after_exit(host.paths, host.runner)
    assert ["systemctl", "restart", "robopark-tuna.service"] in host.runner.commands


def test_tuna_failure_marks_publication_degraded_without_database_rollback(host):
    apply_release(host.request(), host.paths, host.runner)
    host.runner.fail_on = "robopark-tuna.service"
    result = reconcile_after_exit(host.paths, host.runner)
    assert result.state == "current_healthy"
    assert (host.paths.var / "data/robopark.db").read_text() == "migrated"
    assert (
        json.loads((host.paths.ops / "public/host-status.json").read_text())["publication"]
        == "degraded"
    )


@pytest.mark.parametrize("missing", ["apps/api/uv.lock", "apps/web/package-lock.json"])
def test_missing_candidate_lockfile_is_rejected(host, missing):
    archive = host.package("3.0.0", omit=(missing,))
    result = apply_release(host.request(archive), host.paths, host.runner)
    assert result.error == "quality_gate_inputs_missing"
    assert host.runner.commands == []


def test_test_image_cleanup_still_runs_when_auto_removed_container_is_absent(host):
    original = host.runner.run

    def absent_container(argv, **kwargs):
        if list(argv[:2]) == ["docker", "rm"]:
            raise ReleaseError("command_failed")
        return original(argv, **kwargs)

    host.runner.run = absent_container
    apply_release(host.request(), host.paths, host.runner)
    result = reconcile_after_exit(host.paths, host.runner)
    assert result.state == "current_healthy"
    removed = [c for c in host.runner.commands if c[:3] == ["docker", "image", "rm"]]
    assert len(removed) == 2


@pytest.mark.parametrize("parent_exits", [True, False])
def test_timeout_kills_descendants_even_after_parent_exit(host, parent_exits):
    import time

    marker = host.paths.root / "delayed-child-marker"
    script = (
        "import os,signal,time,pathlib\n"
        "pid=os.fork()\n"
        "if pid == 0:\n"
        " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        " os.write(1,b'ready\\n')\n"
        " time.sleep(1)\n"
        f" pathlib.Path({str(marker)!r}).write_text('escaped')\n"
        " os._exit(0)\n" + ("os._exit(0)\n" if parent_exits else "time.sleep(5)\n")
    )
    began = time.monotonic()
    with pytest.raises(ReleaseError, match="command_timeout"):
        SystemRunner().run([sys.executable, "-c", script], timeout=0.15, capture=True)
    assert time.monotonic() - began < 1
    time.sleep(1.1)
    assert not marker.exists()


@pytest.mark.parametrize("heads", [["old"], ["new", "unexpected"], []])
def test_migration_must_reach_exact_signed_head(host, heads):
    host.runner.database_heads = heads
    result = apply_release(host.request(), host.paths, host.runner)
    assert result.error == "migration_head_mismatch"
    assert result.state == "previous_restored"
    assert host.paths.current.resolve().name == "1.0.0"
    assert (host.paths.var / "data/robopark.db").read_text() == "original"


def test_reconciliation_rechecks_actual_head_before_resuming_writes(host):
    apply_release(host.request(), host.paths, host.runner)
    host.runner.database_heads = ["unexpected"]
    result = reconcile_after_exit(host.paths, host.runner)
    assert result.error == "migration_head_mismatch"
    assert host.paths.current.resolve().name == "1.0.0"
    assert (host.paths.var / "data/robopark.db").read_text() == "original"


def test_successful_tuna_restart_with_unreachable_public_route_is_degraded(host, monkeypatch):
    import robopark_host.updater as updater

    monkeypatch.setattr(updater, "PUBLIC_READY_TIMEOUT", 0.02, raising=False)
    host.runner.public_health = False
    apply_release(host.request(), host.paths, host.runner)
    result = reconcile_after_exit(host.paths, host.runner)
    assert result.state == "current_healthy"
    assert (host.paths.var / "data/robopark.db").read_text() == "migrated"
    assert (
        json.loads((host.paths.ops / "public/host-status.json").read_text())["publication"]
        == "degraded"
    )
    probes = [argv for argv in host.runner.commands if argv[0] == "curl"]
    assert probes and probes[0][-1] == "https://robopark.example.tuna.am/api/health/ready"
    assert "also-secret" not in json.dumps(host.runner.commands)


def test_database_head_probe_reads_actual_database_without_modifying_it(host):
    import sqlite3

    from robopark_host.updater import _verify_database_head

    database = host.paths.root / "actual-head.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version(version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('actual-revision')")
    before = database.read_bytes()

    class LocalDatabaseRunner:
        def run(self, argv, *, timeout, capture):
            assert argv[:4] == ["docker", "compose", "-p", "robopark"]
            assert argv[6:13] == ["run", "--rm", "--no-deps", "--entrypoint", "python", "api", "-c"]
            return SystemRunner().run(
                [sys.executable, "-c", argv[-1]],
                timeout=timeout,
                capture=capture,
                env={"DATABASE_URL": f"sqlite:///{database}"},
            )

    runner = LocalDatabaseRunner()
    _verify_database_head(host.paths, runner, "actual-revision")
    with pytest.raises(ReleaseError, match="migration_head_mismatch"):
        _verify_database_head(host.paths, runner, "signed-but-not-applied")
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.tuna.am",
        "https://a.tuna.am,https://b.tuna.am",
        "https://user:token@example.tuna.am",
        "https://example.tuna.am/path",
        "https://example.tuna.am?token=hidden",
        "https://example.tuna.am#fragment",
        "https://example.tuna.am:bad",
        "",
        "https://example.tuna.am\\t.evil",
    ],
)
def test_public_origin_rejects_ambiguous_or_sensitive_values(host, origin):
    from robopark_host.updater import _wait_public_ready

    (host.paths.etc / "host.env").write_text(f"CORS_ORIGINS={origin}\nTUNA_TOKEN=secret\n")
    assert not _wait_public_ready(host.paths, host.runner, timeout=0.01)
    assert not host.runner.commands


def test_public_origin_requires_trusted_regular_file_and_directory(host):
    from robopark_host.updater import _wait_public_ready

    config = host.paths.etc / "host.env"
    config.chmod(0o666)
    assert not _wait_public_ready(host.paths, host.runner, timeout=0.01)
    config.chmod(0o600)
    host.paths.etc.chmod(0o777)
    assert not _wait_public_ready(host.paths, host.runner, timeout=0.01)
    host.paths.etc.chmod(0o755)
    other = host.paths.etc / "other.env"
    config.rename(other)
    config.symlink_to(other)
    assert not _wait_public_ready(host.paths, host.runner, timeout=0.01)
    assert not host.runner.commands


def test_public_origin_override_and_https_probe_contract(host):
    from robopark_host.updater import _wait_public_ready

    (host.paths.etc / "host.env").write_text(
        "PUBLIC_ORIGIN=https://custom.example/\nCORS_ORIGINS=https://fallback.tuna.am\nTUNA_TOKEN=secret\n"
    )
    assert _wait_public_ready(host.paths, host.runner, timeout=0.1)
    argv = host.runner.commands[-1]
    assert argv[:2] == ["curl", "--disable"]
    assert argv[-1] == "https://custom.example/api/health/ready"
    assert argv[argv.index("--proto") + 1] == "=https"
    assert "--insecure" not in argv and "--location" not in argv


@pytest.mark.parametrize(
    "response",
    [
        b'{"status":"ready"}\n302',
        b"wrong-site\n200",
        b'{"status":"degraded"}\n200',
        b"[]\n200",
        b'{"status":"ready"}\n503',
    ],
)
def test_public_probe_retries_with_bounded_budget_and_rejects_wrong_response(host, response):
    import time

    from robopark_host.updater import _wait_public_ready

    class ResponseRunner:
        calls = 0

        def run(self, argv, *, timeout, capture):
            self.calls += 1
            assert 0 < timeout <= 0.02
            return response

    runner = ResponseRunner()
    began = time.monotonic()
    assert not _wait_public_ready(host.paths, runner, timeout=0.02)
    assert 0.01 < time.monotonic() - began < 0.5
    assert runner.calls >= 1


def test_public_origin_rejects_file_owned_by_another_user(host, monkeypatch):
    from types import SimpleNamespace

    import robopark_host.updater as updater

    actual = os.fstat

    def foreign_owner(descriptor):
        metadata = actual(descriptor)
        return SimpleNamespace(
            st_mode=metadata.st_mode, st_uid=metadata.st_uid + 1, st_size=metadata.st_size
        )

    monkeypatch.setattr(updater.os, "fstat", foreign_owner)
    assert not updater._wait_public_ready(host.paths, host.runner, timeout=0.01)
    assert not host.runner.commands


def test_public_probe_retries_transient_failure_within_budget(host, monkeypatch):
    import robopark_host.updater as updater

    elapsed = 0.0

    def advance(duration):
        nonlocal elapsed
        elapsed += duration

    monkeypatch.setattr(updater.time, "monotonic", lambda: elapsed)
    monkeypatch.setattr(updater.time, "sleep", advance)

    class ResponseRunner:
        calls = 0

        def run(self, argv, *, timeout, capture):
            self.calls += 1
            if self.calls == 1:
                raise ReleaseError("command_timeout")
            return b'{"status":"ready"}\n200'

    runner = ResponseRunner()
    assert updater._wait_public_ready(host.paths, runner, timeout=3)
    assert runner.calls == 2
    assert elapsed == 1


@pytest.mark.parametrize("version", ["2.0.0-rc.1", "2.0.0+build.1"])
def test_prerelease_semver_can_complete_host_update_and_reconciliation(host, version):
    request = host.request(host.package(version, filename="semver.zip"))
    assert apply_release(request, host.paths, host.runner).state == "awaiting_reconciliation"
    assert host.paths.current.resolve().name == version + "-" + request.job_id
    assert reconcile_after_exit(host.paths, host.runner).state == "current_healthy"
    assert json.loads((host.paths.ops / "public/rebuild.result").read_text())["ok"] is True
