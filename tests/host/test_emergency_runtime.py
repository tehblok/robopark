from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

API_IMAGE = "sha256:" + "a" * 64


def _source_compose() -> dict:
    return {
        "services": {
            "api": {
                "build": {"context": "../apps/api"},
                "environment": {},
            },
            "web": {
                "build": {"context": "../apps/web"},
                "depends_on": {"api": {"condition": "service_healthy"}},
            },
            "emergency-bridge": {
                "build": {"context": "../apps/api"},
                "command": ["python", "-m", "robopark_api.emergency_bridge"],
                "environment": {},
                "ports": [{"published": "8091", "target": 8091}],
                "restart": "unless-stopped",
                "init": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "stop_grace_period": "30s",
                "healthcheck": {
                    "test": [
                        "CMD",
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/health', timeout=4)",
                    ],
                    "interval": "15s",
                    "timeout": "5s",
                    "start_period": "10s",
                    "retries": 3,
                },
            },
        },
        "networks": {"default": {"name": "candidate_default"}},
    }


def _active_compose(release: Path, *, bridge: bool = False) -> dict:
    services = {
        "db": {
            "image": "sha256:" + "d" * 64,
            "volumes": ["robopark_postgres:/var/lib/postgresql/data"],
        },
        "api": {
            "image": API_IMAGE,
            "environment": {"EXISTING": "literal"},
            "volumes": ["/data:/data"],
        },
        "web": {
            "image": "sha256:" + "b" * 64,
            "depends_on": {"api": {"condition": "service_healthy"}},
        },
        "worker": {"image": API_IMAGE, "command": ["worker"]},
    }
    if bridge:
        services["emergency-bridge"] = {
            "image": API_IMAGE,
            "command": ["already", "current"],
        }
    return {
        "name": "robopark",
        "x-robopark-release": str(release),
        "services": services,
        "volumes": {"robopark_postgres": {}},
        "secrets": {"postgres-password": {"file": "/etc/robopark/password"}},
    }


class ComposeRunner:
    def __init__(self, source: dict, *, fail_validation: bool = False):
        self.source = source
        self.fail_validation = fail_validation
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, command, *, env):
        self.calls.append((list(command), dict(env)))
        if command[-4:] == ["config", "--format", "json", "--no-env-resolution"]:
            return json.dumps(self.source)
        if command[-2:] == ["config", "--quiet"]:
            if self.fail_validation:
                raise subprocess.CalledProcessError(1, command)
            return ""
        raise AssertionError(command)


def _installed_runtime(host_paths, *, enabled: bool, bridge: bool = False):
    release = host_paths.releases / "0.2.0-rc.44"
    tools = release / "deploy/host"
    tools.mkdir(parents=True)
    (release / "deploy/docker-compose.yml").write_text("services: {}\n")
    host_paths.opt.mkdir(parents=True, exist_ok=True)
    host_paths.current.symlink_to(release)
    (host_paths.opt / "host-tools").symlink_to(tools)
    host_paths.etc.mkdir(parents=True)
    host_env = host_paths.etc / "host.env"
    host_env.write_text(
        "UVICORN_WORKERS=2\n"
        f"EMERGENCY_BRIDGE_ENABLED={'true' if enabled else 'false'}\n"
    )
    compose_dir = host_paths.state / "compose"
    compose_dir.mkdir(parents=True)
    original = compose_dir / "release-rc43.json"
    original.write_text(json.dumps(_active_compose(release, bridge=bridge)) + "\n")
    original.chmod(0o600)
    current = host_paths.state / "current-compose.json"
    current.symlink_to(original)
    return release, tools, host_env, original, current


@pytest.mark.parametrize("enabled", [False, True])
def test_rc43_compose_is_repaired_without_rewriting_core_services_or_host_env(
    host_paths, enabled: bool
):
    from robopark_host.emergency_runtime import reconcile_emergency_runtime

    release, _tools, host_env, original, current = _installed_runtime(
        host_paths, enabled=enabled
    )
    original_bytes = original.read_bytes()
    original_document = json.loads(original_bytes)
    host_env_bytes = host_env.read_bytes()
    pointer = os.readlink(current)
    runner = ComposeRunner(_source_compose())

    assert reconcile_emergency_runtime(host_paths, run=runner) is True

    overlay_path = host_paths.state / "emergency-compose-overlay.json"
    overlay = json.loads(overlay_path.read_text())
    assert current.resolve(strict=True) == original
    assert os.readlink(current) == pointer
    assert overlay_path.stat().st_mode & 0o777 == 0o600
    assert original.read_bytes() == original_bytes
    assert host_env.read_bytes() == host_env_bytes
    assert json.loads(original.read_bytes()) == original_document
    assert set(overlay) == {"services"}
    assert set(overlay["services"]) == (
        {"emergency-bridge", "web"} if enabled else {"emergency-bridge"}
    )
    bridge = overlay["services"]["emergency-bridge"]
    assert bridge["image"] == API_IMAGE
    assert bridge["command"] == ["python", "-m", "robopark_api.emergency_bridge"]
    assert bridge["env_file"] == [str(host_paths.etc / "host.env")]
    assert bridge["environment"] == {
        "DATABASE_URL": "postgresql+psycopg://robopark@db:5432/robopark",
        "PGPASSFILE": "/run/secrets/pgpass",
    }
    assert bridge["volumes"] == [
        {
            "type": "bind",
            "source": str(host_paths.etc / "pgpass"),
            "target": "/run/secrets/pgpass",
            "read_only": True,
        }
    ]
    assert bridge["depends_on"] == {"db": {"condition": "service_healthy"}}
    assert bridge["read_only"] is True
    assert bridge["tmpfs"] == ["/tmp:size=32m,mode=1777"]
    assert bridge["mem_limit"] == "512m"
    assert bridge["pids_limit"] == 128
    assert bridge["healthcheck"]["retries"] == 3
    assert "ports" not in bridge
    dependency = (
        overlay["services"].get("web", {}).get("depends_on", {}).get("emergency-bridge")
    )
    assert dependency == ({"condition": "service_healthy"} if enabled else None)
    assert len(runner.calls) == 2
    assert all(
        "build" not in command and "pull" not in command for command, _ in runner.calls
    )
    assert runner.calls[0][0][-4:] == [
        "config",
        "--format",
        "json",
        "--no-env-resolution",
    ]
    assert runner.calls[1][0][-2:] == ["config", "--quiet"]
    assert str(current) in runner.calls[1][0]
    assert "--file" in runner.calls[1][0]
    assert overlay_path not in [Path(value) for value in runner.calls[1][0]]
    assert original_document["x-robopark-release"] == str(release)


def test_existing_bridge_and_repeated_repair_are_noops(host_paths):
    from robopark_host.emergency_runtime import reconcile_emergency_runtime

    _release, _tools, _host_env, original, current = _installed_runtime(
        host_paths, enabled=False
    )
    runner = ComposeRunner(_source_compose())
    pointer = os.readlink(current)

    assert reconcile_emergency_runtime(host_paths, run=runner) is True
    assert reconcile_emergency_runtime(host_paths, run=runner) is False
    assert current.resolve(strict=True) == original
    assert os.readlink(current) == pointer
    assert len(runner.calls) == 4
    assert runner.calls[-1][0][-2:] == ["config", "--quiet"]

    other_root = host_paths.root / "already-current"
    from robopark_host.paths import HostPaths

    other_paths = HostPaths.from_root(other_root)
    _installed_runtime(other_paths, enabled=False, bridge=True)
    untouched = other_paths.state / "current-compose.json"
    before = os.readlink(untouched)
    existing = ComposeRunner(_source_compose())
    assert reconcile_emergency_runtime(other_paths, run=existing) is True
    assert os.readlink(untouched) == before
    assert json.loads(
        (other_paths.state / "emergency-compose-overlay.json").read_text()
    ) == {"services": {}}
    assert len(existing.calls) == 1
    assert existing.calls[0][0][-2:] == ["config", "--quiet"]


def test_native_rc44_base_replaces_legacy_bridge_overlay_with_neutral_overlay(
    host_paths,
):
    from robopark_host.emergency_runtime import reconcile_emergency_runtime

    release, _tools, _host_env, original, current = _installed_runtime(
        host_paths, enabled=False
    )
    assert (
        reconcile_emergency_runtime(host_paths, run=ComposeRunner(_source_compose()))
        is True
    )
    overlay = host_paths.state / "emergency-compose-overlay.json"
    assert API_IMAGE in overlay.read_text()

    native = _active_compose(release, bridge=True)
    native_image = "sha256:" + "c" * 64
    native["services"]["api"]["image"] = native_image
    native["services"]["worker"]["image"] = native_image
    native["services"]["emergency-bridge"]["image"] = native_image
    original.write_text(json.dumps(native) + "\n")
    original.chmod(0o600)
    runner = ComposeRunner(_source_compose())

    assert reconcile_emergency_runtime(host_paths, run=runner) is True

    assert json.loads(overlay.read_text()) == {"services": {}}
    assert API_IMAGE not in overlay.read_text()
    assert current.resolve(strict=True) == original
    assert len(runner.calls) == 1
    assert runner.calls[0][0][-2:] == ["config", "--quiet"]


@pytest.mark.parametrize(
    "damage",
    [
        "current_not_symlink",
        "host_tools_other_release",
        "compose_not_symlink",
        "compose_outside_state",
        "compose_public",
        "release_mismatch",
        "mutable_api_image",
        "non_string_api_image",
    ],
)
def test_invalid_release_paths_private_metadata_and_api_image_are_rejected(
    host_paths, damage: str
):
    from robopark_host.emergency_runtime import reconcile_emergency_runtime

    release, _tools, _host_env, original, current = _installed_runtime(
        host_paths, enabled=False
    )
    if damage == "current_not_symlink":
        host_paths.current.unlink()
        host_paths.current.mkdir()
    elif damage == "host_tools_other_release":
        (host_paths.opt / "host-tools").unlink()
        other = host_paths.releases / "other/deploy/host"
        other.mkdir(parents=True)
        (host_paths.opt / "host-tools").symlink_to(other)
    elif damage == "compose_not_symlink":
        current.unlink()
        current.write_text(original.read_text())
    elif damage == "compose_outside_state":
        outside = host_paths.var / "outside.json"
        outside.write_text(original.read_text())
        outside.chmod(0o600)
        current.unlink()
        current.symlink_to(outside)
    elif damage == "compose_public":
        original.chmod(0o644)
    else:
        document = json.loads(original.read_text())
        if damage == "release_mismatch":
            document["x-robopark-release"] = str(host_paths.releases / "other")
        elif damage == "non_string_api_image":
            document["services"]["api"]["image"] = 42
        else:
            document["services"]["api"]["image"] = "robopark-api:mutable"
        original.write_text(json.dumps(document))
        original.chmod(0o600)

    before = os.readlink(current) if current.is_symlink() else None
    with pytest.raises(ValueError, match="invalid_emergency_runtime"):
        reconcile_emergency_runtime(host_paths, run=ComposeRunner(_source_compose()))
    if before is not None:
        assert os.readlink(current) == before
    assert original.parent == host_paths.state / "compose"
    assert release.parent == host_paths.releases


def test_compose_validation_failure_keeps_original_rollback_config_active(host_paths):
    from robopark_host.emergency_runtime import reconcile_emergency_runtime

    _release, _tools, _host_env, original, current = _installed_runtime(
        host_paths, enabled=True
    )
    original_bytes = original.read_bytes()
    before = os.readlink(current)

    with pytest.raises(ValueError, match="emergency_compose_validation_failed"):
        reconcile_emergency_runtime(
            host_paths,
            run=ComposeRunner(_source_compose(), fail_validation=True),
        )

    assert os.readlink(current) == before
    assert current.resolve(strict=True) == original
    assert original.read_bytes() == original_bytes
    assert not (host_paths.state / "emergency-compose-overlay.json").exists()


def test_managed_storage_is_rechecked_before_write_and_atomic_switch(
    host_paths, monkeypatch
):
    from robopark_host import emergency_runtime

    _release, _tools, _host_env, original, current = _installed_runtime(
        host_paths, enabled=False
    )
    before = os.readlink(current)
    checks = []

    def require_storage(paths, *, check_space):
        assert paths == host_paths
        assert check_space is False
        checks.append(current.resolve(strict=True))
        if len(checks) == 2:
            raise ValueError("storage_detached")

    monkeypatch.setattr(
        "robopark_host.storage_compatibility.require_storage_operations",
        require_storage,
    )

    with pytest.raises(ValueError, match="storage_detached"):
        emergency_runtime.reconcile_emergency_runtime(
            host_paths, run=ComposeRunner(_source_compose())
        )

    assert checks == [original, original]
    assert os.readlink(current) == before
    assert current.resolve(strict=True) == original
    assert not (host_paths.state / "emergency-compose-overlay.json").exists()


def test_concurrent_base_change_never_gets_overwritten_by_overlay_helper(
    host_paths,
):
    from robopark_host.emergency_runtime import reconcile_emergency_runtime

    release, _tools, _host_env, original, current = _installed_runtime(
        host_paths, enabled=False
    )
    replacement = original.with_name("concurrent-production.json")
    replacement.write_text(json.dumps(_active_compose(release)) + "\n")
    replacement.chmod(0o600)

    class ConcurrentRunner(ComposeRunner):
        def __call__(self, command, *, env):
            result = super().__call__(command, env=env)
            if command[-2:] == ["config", "--quiet"]:
                current.unlink()
                current.symlink_to(replacement)
            return result

    with pytest.raises(ValueError, match="invalid_emergency_runtime"):
        reconcile_emergency_runtime(host_paths, run=ConcurrentRunner(_source_compose()))

    assert current.resolve(strict=True) == replacement
    assert original.is_file()
    assert not (host_paths.state / "emergency-compose-overlay.json").exists()


def test_core_unit_repairs_before_start_and_starts_bridge_fail_soft():
    root = Path(__file__).resolve().parents[2]
    service = (root / "deploy/systemd/robopark.service").read_text()
    repair = (
        "ExecStartPre=/usr/bin/python3 -I "
        "/opt/robopark/host-tools/robopark-emergency-runtime"
    )
    storage = "robopark-storage-reconcile --check"
    start = next(line for line in service.splitlines() if line.startswith("ExecStart="))
    bridge = next(
        line
        for line in service.splitlines()
        if line.startswith("ExecStartPost=-/usr/bin/docker")
        and line.endswith("emergency-bridge")
    )

    assert service.index(storage) < service.index(repair) < service.index(start)
    assert start.endswith("db api web")
    assert "--no-build --wait --wait-timeout 60 emergency-bridge" in bridge
    overlay = "/var/lib/robopark/ops/state/emergency-compose-overlay.json"
    for command in (
        start,
        bridge,
        next(line for line in service.splitlines() if line.startswith("ExecStop=")),
        next(
            line
            for line in service.splitlines()
            if line.startswith("ExecStopPost=-/usr/bin/docker")
        ),
    ):
        assert command.count("--file") == 2
        assert overlay in command


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI unavailable")
@pytest.mark.parametrize("native_bridge", [False, True])
def test_published_overlay_is_accepted_by_real_docker_compose(
    host_paths, native_bridge: bool
):
    from robopark_host.emergency_runtime import reconcile_emergency_runtime

    _release, _tools, _host_env, _original, current = _installed_runtime(
        host_paths, enabled=True, bridge=native_bridge
    )
    reconcile_emergency_runtime(host_paths, run=ComposeRunner(_source_compose()))
    overlay = host_paths.state / "emergency-compose-overlay.json"

    completed = subprocess.run(
        [
            shutil.which("docker"),
            "compose",
            "--project-name",
            "robopark-overlay-test",
            "--file",
            str(current),
            "--file",
            str(overlay),
            "config",
            "--quiet",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "PATH": os.environ["PATH"],
            "HOST_ENV_FILE": str(host_paths.etc / "host.env"),
        },
    )

    assert completed.returncode == 0, completed.stderr
