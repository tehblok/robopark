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
    assert {"network-online.target", "robopark.service"} <= set(tuna["Unit"]["After"].split())
    assert tuna["Service"]["Restart"] == "on-failure"
    assert int(tuna["Service"]["TimeoutStartSec"]) <= 180
    assert int(tuna["Unit"]["StartLimitBurst"]) <= 5


def test_boot_never_builds_or_uses_a_mutable_compose_config():
    app = unit("robopark.service")["Service"]
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


def test_command_consumer_can_open_the_shared_stable_host_lock():
    service = unit("robopark-commands.service")["Service"]
    assert "-/run/lock/robopark" in service["ReadWritePaths"].split()


@pytest.mark.parametrize(
    "name,command",
    [
        ("updater", "update"),
        ("update-check", "check-update"),
        ("doctor", "doctor"),
        ("watchdog", "watchdog"),
    ],
)
def test_privileged_units_use_trusted_launcher_and_sandbox(name, command):
    service = unit(f"robopark-{name}.service")["Service"]
    assert (
        service["ExecStart"] == f"/usr/bin/python3 -I /opt/robopark/host-tools/robopark {command}"
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
        ("update-check", "OnUnitActiveSec", "6h"),
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
        if "config" in command:
            return json.dumps(
                {
                    "services": {
                        "api": {
                            "build": {"context": str(release / "apps/api")},
                            "environment": {"UVICORN_WORKERS": "2"},
                            "volumes": ["..:/host-repo"],
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
        if command[:3] == ["docker", "image", "inspect"]:
            assert built, "must build before resolving image identity"
            return "sha256:" + ("1" if "api" in command[-1] else "2") * 64
        raise AssertionError(command)

    bootstrap_compose(host_paths, run)
    build_calls = [command for command in calls if "build" in command]
    assert [command[-2:] for command in build_calls] == [
        ["build", "api"],
        ["build", "web"],
    ]
    target = host_paths.state / "current-compose.json"
    document = json.loads(target.read_text())
    assert document["x-robopark-release"] == str(release.resolve())
    assert target.stat().st_mode & 0o777 == 0o600
    assert set(document["services"]) == {"db", "api", "web"}
    api = document["services"]["api"]
    assert api["image"] == "sha256:" + "1" * 64
    assert document["services"]["web"]["image"] == "sha256:" + "2" * 64
    assert document["services"]["db"]["image"] == "sha256:" + "2" * 64
    assert "build" not in api
    assert "UVICORN_WORKERS" not in api["environment"]
    mounts = {v["target"]: v for v in api["volumes"]}
    assert mounts["/ops"]["source"] == str(host_paths.var / "api-ops")
    assert mounts["/data"]["source"] == str(host_paths.var / "data")
    assert mounts["/host-ops/public"]["read_only"] is True
    key_path = api["environment"]["OPS_RELEASE_PUBLIC_KEY_PATH"]
    assert key_path == "/etc/robopark/release-public-key.pem"
    assert mounts[key_path]["source"] == str(host_paths.etc / "release-public-key.pem")
    assert mounts[key_path]["read_only"] is True
    assert mounts["/run/secrets/pgpass"] == {
        "type": "bind",
        "source": str(host_paths.etc / "pgpass"),
        "target": "/run/secrets/pgpass",
        "read_only": True,
    }
    assert set(mounts) == {
        "/ops",
        "/data",
        "/host-ops/inbox",
        "/host-ops/artifacts",
        "/host-ops/public",
        "/etc/robopark/release-public-key.pem",
        "/run/secrets/pgpass",
    }
    for command in calls:
        if command[:2] == ["docker", "compose"]:
            assert command[command.index("--project-name") + 1] == "robopark"
            assert "--file" in command
    before = target.read_bytes()
    bootstrap_compose(host_paths, lambda _: pytest.fail("existing runtime must not be rebuilt"))
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
    assert [command[-2:] for command in build_calls] == [
        ["build", "api"],
        ["build", "web"],
    ]
    assert json.loads(target.read_text())["x-robopark-release"] == str(next_release.resolve())


def test_runtime_runner_streams_and_retains_failed_docker_build_output(tmp_path, capsys):
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
        "curl": 'echo "$*" >> "$TEST_LOG"; exit "${UNREADY:-0}"',
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
