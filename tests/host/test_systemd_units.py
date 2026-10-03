"""Boot ordering, sandbox and immutable Compose runtime contract."""

import configparser
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def unit(name):
    path = REPO / "deploy/systemd" / name
    assert path.exists(), f"missing boot unit {name}"
    parsed = configparser.ConfigParser(interpolation=None, strict=False)
    parsed.read(path)
    return parsed


def test_tuna_requires_ready_application_and_bounded_restart():
    tuna = unit("robopark-tuna.service")
    assert "robopark.service" in tuna["Unit"]["Requires"].split()
    assert {"network-online.target", "robopark.service"} <= set(
        tuna["Unit"]["After"].split()
    )
    assert tuna["Service"]["Restart"] == "on-failure"
    assert int(tuna["Service"]["TimeoutStartSec"]) <= 180
    assert int(tuna["Unit"]["StartLimitBurst"]) <= 5


def test_boot_never_builds_or_uses_a_mutable_compose_config():
    service = unit("robopark.service")
    app = service["Service"]
    assert app["ExecStartPre"].endswith("robopark restore --boot-recover")
    assert app.get("User", "root") == "root"
    assert int(app["TimeoutStartSec"]) >= 1800
    assert service["Install"]["WantedBy"] == "multi-user.target"
    assert "-/run/lock/robopark" in app["ReadWritePaths"].split()
    for directive in ("ExecStart", "ExecStop"):
        command = app[directive].split()
        assert command[command.index("--project-name") + 1] == "robopark"
        assert (
            command[command.index("--file") + 1]
            == "/var/lib/robopark/ops/state/current-compose.json"
        )
    assert "--no-build" in app["ExecStart"].split()
    assert "--wait" in app["ExecStart"].split()


def test_worker_start_failure_does_not_block_api_and_web_boot():
    app = unit("robopark.service")["Service"]
    start = app["ExecStart"].split()
    worker = app["ExecStartPost"].split()
    assert start[-3:] == ["db", "api", "web"]
    assert worker[-1] == "worker"
    assert worker[0].startswith("-")
    assert "--wait" not in worker
    assert app["ExecStop"].split()[-3:] == ["worker", "web", "api"]


def test_command_consumer_can_open_the_shared_stable_host_lock():
    service = unit("robopark-commands.service")["Service"]
    assert "-/run/lock/robopark" in service["ReadWritePaths"].split()


def test_command_consumer_can_write_only_supported_backup_mount_roots():
    service = unit("robopark-commands.service")["Service"]
    writable = set(service["ReadWritePaths"].split())

    assert {
        "/var/backups/robopark",
        "-/mnt",
        "-/media",
        "-/run/media",
    } <= writable
    assert "/usr" not in writable
    assert "/var/lib/docker/volumes" not in writable


def test_boot_recreates_stable_lock_directory_before_sandboxed_units_start():
    tmpfiles = REPO / "deploy/tmpfiles.d/robopark.conf"
    assert tmpfiles.read_text() == "d /run/lock/robopark 0700 root root -\n"
    installer = (REPO / "deploy/installer/lib/install-services.py").read_text()
    assert '"deploy/tmpfiles.d/robopark.conf"' in installer
    assert '"etc/tmpfiles.d/robopark.conf"' in installer
    updater = (REPO / "deploy/host/robopark_host/updater.py").read_text()
    assert "deploy/tmpfiles.d/robopark.conf" in updater
    assert "etc/tmpfiles.d/robopark.conf" in updater


@pytest.mark.parametrize(
    "name", ["robopark-updater.service", "robopark-commands.service"]
)
def test_units_that_replay_update_activation_can_publish_tmpfiles_policy(name):
    service = unit(name)["Service"]

    assert service["ProtectSystem"] == "strict"
    writable = service["ReadWritePaths"].split()
    assert "/etc/tmpfiles.d" in writable
    target = "/etc/tmpfiles.d/robopark.conf"
    assert any(
        target == allowed.rstrip("/") or target.startswith(allowed.rstrip("/") + "/")
        for allowed in writable
    )


@pytest.mark.parametrize(
    "name,command",
    [
        ("updater", "update"),
        ("doctor", "doctor"),
        ("watchdog", "watchdog"),
    ],
)
def test_privileged_units_use_trusted_launcher_and_sandbox(name, command):
    service = unit(f"robopark-{name}.service")["Service"]
    assert (
        service["ExecStart"]
        == f"/usr/bin/python3 -I /opt/robopark/host-tools/robopark {command}"
    )
    assert service["NoNewPrivileges"] == "true"
    assert service["PrivateTmp"] == "true"
    assert service["ProtectSystem"] == "strict"
    assert service["ProtectHome"] == "true"
    assert "-/run/lock/robopark" in service["ReadWritePaths"].split()
    timeout = int(service["TimeoutStartSec"])
    if name == "updater":
        assert 14400 <= timeout <= 18000
        assert "/var/log/robopark" in service["ReadWritePaths"].split()
    else:
        assert timeout <= 1800


@pytest.mark.parametrize(
    "name,key,value",
    [
        ("doctor", "OnCalendar", "*:0/15"),
        ("watchdog", "OnUnitActiveSec", "2min"),
    ],
)
def test_timers_are_bounded_and_persistent(name, key, value):
    timer = unit(f"robopark-{name}.timer")["Timer"]
    assert timer[key] == value
    assert timer["Persistent"] == "true"


def test_bootstrap_pins_fresh_images_and_restricts_mounts(host_paths):
    from robopark_host.runtime import bootstrap_compose

    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "host.env").write_text("UVICORN_WORKERS=2\n")
    release = host_paths.releases / "1.0.0"
    (release / "deploy").mkdir(parents=True)
    (release / "deploy/docker-compose.yml").write_text("services: {}")
    (release / "manifest.json").write_text('{"git_sha":"' + "a" * 40 + '"}')
    host_paths.current.symlink_to(release)
    calls = []
    built = False

    def run(command):
        nonlocal built
        calls.append(command)
        if command[:3] == ["docker", "buildx", "inspect"]:
            return f"Name: {command[-1]}\nDriver: docker-container\n"
        if command[:2] == ["docker", "inspect"]:
            builder = command[-1].removeprefix("buildx_buildkit_").removesuffix("0")
            return json.dumps(["ROBOPARK_BUILDER_OWNER=" + builder])
        if command[:3] == ["docker", "buildx", "prune"]:
            return ""
        if command[:3] == ["docker", "buildx", "du"]:
            return ""
        if "config" in command:
            return json.dumps(
                {
                    "services": {
                            "api": {
                                "build": {"context": str(release / "apps/api")},
                                "environment": {"UVICORN_WORKERS": "2"},
                                "volumes": ["..:/host-repo"],
                            },
                            "worker": {
                                "image": "robopark-api:local",
                                "command": ["python", "-m", "robopark_api.worker"],
                                "environment": {},
                                "healthcheck": {"disable": True},
                            },
                        "web": {
                            "build": {"context": str(release / "apps/web")},
                            "ports": [
                                {
                                    "host_ip": "127.0.0.1",
                                    "published": "8080",
                                    "target": 80,
                                }
                            ],
                        },
                        "ops-agent": {},
                    }
                }
            )
        if "build" in command:
            built = True
            return ""
        if "pull" in command:
            return ""
        if command[:3] == ["docker", "image", "inspect"]:
            assert built, "must build before resolving image identity"
            return "sha256:" + ("1" if "api" in command[-1] else "2") * 64
        raise AssertionError(command)

    bootstrap_compose(host_paths, run)
    build_calls = [command for command in calls if "build" in command]
    builder = json.loads((host_paths.state / "buildkit-builder.json").read_text())["name"]
    pull = [command for command in calls if "pull" in command]
    assert len(pull) == 1 and pull[0][-2:] == ["pull", "db"]
    assert calls.index(pull[0]) < calls.index(["docker", "image", "inspect", "--format", "{{.Id}}", "postgres:17.11-alpine"])
    assert [command[-4:] for command in build_calls] == [
        ["build", "--builder", builder, "api"],
        ["build", "--builder", builder, "web"],
    ]
    assert [command for command in calls if command[:3] == ["docker", "buildx", "prune"]] == [
        ["docker", "buildx", "prune", "--builder", builder, "-f", "--all", "--max-used-space", "2000000000"]
    ]
    target = host_paths.state / "current-compose.json"
    document = json.loads(target.read_text())
    assert document["x-robopark-release"] == str(release.resolve())
    assert target.stat().st_mode & 0o777 == 0o600
    assert set(document["services"]) == {"db", "api", "worker", "web"}
    api = document["services"]["api"]
    assert api["image"] == "sha256:" + "1" * 64
    worker = document["services"]["worker"]
    assert worker["image"] == api["image"]
    assert worker["command"] == ["python", "-m", "robopark_api.worker"]
    assert worker["environment"]["DATABASE_URL"] == api["environment"]["DATABASE_URL"]
    assert worker["depends_on"] == {
        "db": {"condition": "service_healthy"},
        "api": {"condition": "service_healthy"},
    }
    assert worker["healthcheck"] == {
        "test": [
            "CMD",
            "python",
            "-m",
            "robopark_api.worker_healthcheck",
            "--max-age-seconds",
            "120",
        ],
        "interval": "15s",
        "timeout": "15s",
        "start_period": "45s",
        "retries": 3,
    }
    assert "worker" not in api.get("depends_on", {})
    assert document["services"]["web"]["image"] == "sha256:" + "2" * 64
    assert document["services"]["db"]["image"] == "sha256:" + "2" * 64
    assert "build" not in api
    assert "UVICORN_WORKERS" not in api["environment"]
    assert api["environment"]["HOST_DATA_PATH"] == "/data"
    assert api["environment"]["HOST_HEALTH_PATH"] == "/ops/host-health.json"
    assert api["environment"]["LIVE_MERGE_DIR"] == "/data/live-merge"
    assert api["environment"]["STAGED_ATTACHMENTS_DIR"] == "/data/task-attachments"
    mounts = {v["target"]: v for v in api["volumes"]}
    assert mounts["/ops"]["source"] == str(host_paths.var / "api-ops")
    assert mounts["/data"]["source"] == str(host_paths.var / "data")
    assert mounts["/host-ops/public"]["read_only"] is True
    assert mounts["/run/secrets/pgpass"] == {
        "type": "bind",
        "source": str(host_paths.etc / "pgpass"),
        "target": "/run/secrets/pgpass",
        "read_only": True,
    }
    for mount_target in ("/run/robopark/snapshot.env", "/host-repo/deploy/host.env"):
        assert mounts[mount_target] == {
            "type": "bind",
            "source": str(host_paths.etc / "snapshot.env"),
            "target": mount_target,
            "read_only": True,
        }
    assert set(mounts) == {
        "/ops",
        "/data",
        "/host-ops/inbox",
            "/host-ops/artifacts",
            "/host-ops/ota-uploads",
            "/host-ops/public",
        "/run/secrets/pgpass",
        "/run/robopark/snapshot.env",
        "/host-repo/deploy/host.env",
    }
    for command in calls:
        if command[:2] == ["docker", "compose"]:
            assert command[command.index("--project-name") + 1] == "robopark"
            assert "--file" in command
    before = target.read_bytes()
    bootstrap_compose(
        host_paths, lambda _: pytest.fail("existing runtime must not be rebuilt")
    )
    assert target.read_bytes() == before

    next_release = host_paths.releases / "1.0.1"
    (next_release / "deploy").mkdir(parents=True)
    (next_release / "deploy/docker-compose.yml").write_text("services: {}")
    (next_release / "manifest.json").write_text('{"git_sha":"' + "b" * 40 + '"}')
    host_paths.current.unlink()
    host_paths.current.symlink_to(next_release)
    calls.clear()
    built = False

    bootstrap_compose(host_paths, run)

    build_calls = [command for command in calls if "build" in command]
    assert [command[-4:] for command in build_calls] == [
        ["build", "--builder", builder, "api"],
        ["build", "--builder", builder, "web"],
    ]
    assert json.loads(target.read_text())["x-robopark-release"] == str(
        next_release.resolve()
    )


def test_runtime_bootstrap_source_compose_receives_complete_external_file_contract(
    host_paths, monkeypatch
):
    from robopark_host.runtime import bootstrap_compose

    host_paths.etc.mkdir(parents=True)
    for name, contents in (
        ("host.env", "UVICORN_WORKERS=2\n"),
        ("postgres-password", "password\n"),
        ("pgpass", "db:5432:robopark:robopark:password\n"),
        ("snapshot.env", "UVICORN_WORKERS=2\n"),
    ):
        (host_paths.etc / name).write_text(contents)
    release = host_paths.releases / "1.0.0"
    (release / "deploy").mkdir(parents=True)
    (release / "apps/api").mkdir(parents=True)
    (release / "apps/web").mkdir(parents=True)
    (release / "deploy/docker-compose.yml").write_bytes(
        (REPO / "deploy/docker-compose.yml").read_bytes()
    )
    (release / "manifest.json").write_text('{"git_sha":"' + "a" * 40 + '"}')
    host_paths.current.symlink_to(release)

    def run(command):
        if command[:3] == ["docker", "buildx", "inspect"]:
            return f"Name: {command[-1]}\nDriver: docker-container\n"
        if command[:3] == ["docker", "inspect", "--type"]:
            builder = command[-1].removeprefix("buildx_buildkit_").removesuffix("0")
            return json.dumps(["ROBOPARK_BUILDER_OWNER=" + builder])
        if "config" in command:
            for key, name in (
                ("ROBOPARK_POSTGRES_PASSWORD_FILE", "postgres-password"),
                ("ROBOPARK_PGPASS_FILE", "pgpass"),
                ("ROBOPARK_SNAPSHOT_CONFIG_FILE", "snapshot.env"),
            ):
                assert os.environ[key] == str(host_paths.etc / name)
            return subprocess.run(
                command, check=True, capture_output=True, text=True
            ).stdout
        if "build" in command:
            return ""
        if "pull" in command:
            return ""
        if command[:3] == ["docker", "image", "inspect"]:
            return "sha256:" + ("1" if "api" in command[-1] else "2") * 64
        raise AssertionError(command)

    bootstrap_compose(host_paths, run)


def test_runtime_runner_streams_and_retains_failed_docker_build_output(
    tmp_path, capsys
):
    from robopark_host import runtime

    log = tmp_path / "runtime-bootstrap.log"
    command = [
        sys.executable,
        "-c",
        "import sys; print('error TS2322: diagnostic marker', flush=True); sys.exit(2)",
        "build",
    ]
    with pytest.raises(subprocess.CalledProcessError) as caught:
        runtime._run(command, build_log=log)

    assert "error TS2322: diagnostic marker" in capsys.readouterr().out
    assert "error TS2322: diagnostic marker" in caught.value.stderr
    assert "error TS2322: diagnostic marker" in log.read_text()
    assert log.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "stderr,expected",
    [
        ("unknown flag: --no-env-resolution", "compose_version_unsupported"),
        ("write failed: no space left on device", "docker_disk_full"),
        ("failed to solve: network is unreachable", "docker_network_failed"),
        ("dns error: failed to lookup address information", "docker_network_failed"),
        ("src/App.tsx(3,2): error TS2322: bad type", "frontend_typescript_failed"),
        (
            "Cannot find module @rollup/rollup-linux-arm64-musl",
            "frontend_arm_dependency_failed",
        ),
        ("FATAL ERROR: JavaScript heap out of memory", "docker_out_of_memory"),
        ("process exited", "docker_command_failed"),
    ],
)
def test_runtime_failure_reason_is_safe_and_actionable(stderr, expected):
    from robopark_host.runtime import explain_process_failure

    error = subprocess.CalledProcessError(
        17,
        ["docker", "compose", "config", "--no-env-resolution"],
        stderr=stderr,
    )
    assert explain_process_failure(error) == expected


def test_tuna_script_never_starts_tunnel_until_readiness(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "commands"
    for name, code in {
        "python3": 'echo "$*" >> "$TEST_LOG"; exit "${UNREADY:-0}"',
        "sleep": ":",
        "tuna": 'echo STARTED >> "$TEST_LOG"',
    }.items():
        path = fake / name
        path.write_text("#!/bin/sh\n" + code + "\n")
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(fake) + ":" + os.environ["PATH"],
        "TEST_LOG": str(log),
        "UNREADY": "1",
    }
    result = subprocess.run(
        ["sh", str(REPO / "deploy/tuna-http.sh")],
        env=env,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "STARTED" not in log.read_text()
    assert "/api/health/ready" in log.read_text()
    result = subprocess.run(
        ["sh", str(REPO / "deploy/tuna-http.sh")],
        env={**env, "UNREADY": "0"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert log.read_text().endswith("STARTED\n")


def test_tuna_script_does_not_require_curl(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "commands"
    for name, code in {
        "python3": 'echo READY >> "$TEST_LOG"',
        "tuna": 'echo STARTED >> "$TEST_LOG"',
        "curl": 'echo CURL_USED >> "$TEST_LOG"; exit 1',
        "sleep": ":",
    }.items():
        path = fake / name
        path.write_text("#!/bin/sh\n" + code + "\n")
        path.chmod(0o755)
    result = subprocess.run(
        ["sh", str(REPO / "deploy/tuna-http.sh")],
        env={**os.environ, "PATH": str(fake) + ":" + os.environ["PATH"], "TEST_LOG": str(log)},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert log.read_text() == "READY\nSTARTED\n"


def test_api_gate_image_uses_repository_context_and_canonical_entrypoint():
    path = REPO / "deploy/Dockerfile.api-tests"
    assert path.exists(), "missing isolated API verification target"
    lines = path.read_text().splitlines()
    assert any(
        line.startswith("FROM python:3.12-slim@sha256:") and line.endswith(" AS test")
        for line in lines
    )
    assert "WORKDIR /repo" in lines
    assert "COPY . ." in lines
    assert 'CMD ["sh", "scripts/verify.sh", "api"]' in lines
    assert "RUN uv sync --directory apps/api --frozen --extra dev" in lines
    assert "AS build" in (REPO / "apps/web/Dockerfile").read_text()


def test_bootstrap_rejects_unresolved_images_without_publishing_state(host_paths):
    from robopark_host.runtime import bootstrap_compose

    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "host.env").write_text("UVICORN_WORKERS=2\n")
    release = host_paths.releases / "1.0.0"
    (release / "deploy").mkdir(parents=True)
    (release / "manifest.json").write_text("{}")
    host_paths.current.symlink_to(release)

    def run(command):
        if command[:3] == ["docker", "buildx", "inspect"]:
            return f"Name: {command[-1]}\nDriver: docker-container\n"
        if command[:3] == ["docker", "inspect", "--type"]:
            builder = command[-1].removeprefix("buildx_buildkit_").removesuffix("0")
            return json.dumps(["ROBOPARK_BUILDER_OWNER=" + builder])
        if "config" in command:
            return json.dumps(
                {
                    "services": {
                        "api": {"build": {}, "environment": {}},
                        "web": {"build": {}},
                    }
                }
            )
        if command[:3] == ["docker", "image", "inspect"]:
            return "robopark-api:latest"
        return ""

    with pytest.raises(ValueError, match="invalid_image_id"):
        bootstrap_compose(host_paths, run)
    assert not (host_paths.state / "current-compose.json").exists()
    assert not (host_paths.state / "bootstrap-compose.json").exists()


def test_docker_readiness_failure_has_bounded_attempts_and_command_timeouts():
    import runpy

    helper = REPO / "deploy/installer/lib/ensure-docker.py"
    assert helper.exists(), "missing bounded Docker daemon preparation"
    module = runpy.run_path(str(helper))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["docker", "info"]:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    with pytest.raises(RuntimeError, match="docker_not_ready"):
        module["ensure_docker"](run=run, sleep=lambda _: None)
    assert [command for command, _ in calls[:2]] == [
        ["systemctl", "enable", "docker.service"],
        ["systemctl", "start", "docker.service"],
    ]
    assert len([command for command, _ in calls if command == ["docker", "info"]]) == 10
    assert all(0 < options["timeout"] <= 60 for _, options in calls)
