from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from robopark_ota import remove
from robopark_ota.credentials import TunaConfiguration
from robopark_ota.host_install import (
    HostInstallRuntime,
    extract_release,
    prepare_host_layout,
    validate_host_platform,
    write_host_configuration,
)
from robopark_ota.install import CleanInstallCoordinator
from robopark_ota.model import OtaRequirements
from robopark_ota.remove import (
    DockerCli,
    DockerTargets,
    RemovalPlan,
    remove_owned_installation,
)

from scripts.build_ota import build_ota


@pytest.fixture
def clean_host_profile(tmp_path, monkeypatch):
    from robopark_ota import host_install

    (tmp_path / "etc").mkdir()
    (tmp_path / "proc").mkdir()
    (tmp_path / "run/systemd/system").mkdir(parents=True)
    (tmp_path / "etc/os-release").write_text('ID=ubuntu\nVERSION_ID="22.04"\n')
    (tmp_path / "proc/meminfo").write_text("MemTotal:        7800000 kB\n")
    monkeypatch.setattr(host_install.platform, "system", lambda: "Linux")
    monkeypatch.setattr(host_install.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(host_install.shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)
    return tmp_path, OtaRequirements(
        python=">=3.10", systems=("armbian", "ubuntu"),
        architectures=("aarch64", "x86_64"), memory_profiles_mb=(8192, 32768, 65536),
    )


def test_clean_install_platform_preflight_accepts_vim4_and_armbian_orin(clean_host_profile, monkeypatch):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    validate_host_platform(root, requirements)
    (root / "etc/armbian-release").write_text("BOARD=jetson-agx-orin\n")
    (root / "etc/os-release").write_text('ID=debian\nVERSION_ID="12"\n')
    (root / "proc/meminfo").write_text("MemTotal:       32000000 kB\n")
    monkeypatch.setattr(host_install.platform, "machine", lambda: "arm64")
    validate_host_platform(root, requirements)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ("unsupported_os", "unsupported_os"),
        ("invalid_os_release", "unsupported_os"),
        ("old_ubuntu", "unsupported_os_release"),
        ("unsupported_arch", "unsupported_arch"),
        ("low_memory", "insufficient_memory"),
        ("unknown_memory", "memory_unavailable"),
        ("missing_systemd", "systemd_required"),
    ],
)
def test_clean_install_platform_preflight_rejects_unsupported_host(clean_host_profile, monkeypatch, change, error):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    if change == "unsupported_os":
        (root / "etc/os-release").write_text('ID=fedora\nVERSION_ID="40"\n')
    elif change == "invalid_os_release":
        (root / "etc/os-release").write_bytes(b"ID=ubuntu\n\xff")
    elif change == "old_ubuntu":
        (root / "etc/os-release").write_text('ID=ubuntu\nVERSION_ID="20.04"\n')
    elif change == "unsupported_arch":
        monkeypatch.setattr(host_install.platform, "machine", lambda: "riscv64")
    elif change == "low_memory":
        (root / "proc/meminfo").write_text("MemTotal:        6000000 kB\n")
    elif change == "unknown_memory":
        (root / "proc/meminfo").write_text("MemAvailable:   6000000 kB\n")
    else:
        (root / "run/systemd/system").rmdir()
    with pytest.raises(RuntimeError, match=error):
        validate_host_platform(root, requirements)


def test_clean_install_platform_rejection_precedes_docker_commands(monkeypatch):
    from robopark_ota import host_install

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = Path("/")
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=object(), required_free_bytes=1))
    runtime.tuna = TunaConfiguration()
    commands = []
    runtime._run = lambda command, **kwargs: commands.append(command)
    monkeypatch.setattr(host_install.os, "geteuid", lambda: 0)
    monkeypatch.setattr(host_install.sys, "version_info", (3, 12, 0))

    def reject_platform(*_args):
        raise RuntimeError("unsupported_arch")

    monkeypatch.setattr(host_install, "validate_host_platform", reject_platform)

    with pytest.raises(RuntimeError, match="unsupported_arch"):
        runtime.preflight()
    assert commands == []


@pytest.mark.parametrize("limited_area", ["opt", "var/lib", "var/lib/containerd"])
def test_clean_install_preflight_checks_each_receiving_filesystem(
    tmp_path, monkeypatch, limited_area
):
    from robopark_ota import host_install

    (tmp_path / "opt").mkdir()
    (tmp_path / "var/lib").mkdir(parents=True)
    (tmp_path / "var/lib/containerd").mkdir()
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(required_free_bytes=1024))
    runtime.tuna = TunaConfiguration()
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command == ["docker", "buildx", "version"]:
            return SimpleNamespace(stdout="github.com/docker/buildx v0.14.0 deadbeef\n")
        return SimpleNamespace(stdout="Usage: build\nOptions: --builder --max-used-space\n")

    runtime._run = run
    monkeypatch.setattr(host_install.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(host_install.shutil, "which", lambda _: "/usr/bin/docker")

    def disk_usage(path):
        free = 512 if Path(path) == tmp_path / limited_area else 10 * 1024**3
        return SimpleNamespace(total=20 * 1024**3, free=free)

    monkeypatch.setattr(host_install.shutil, "disk_usage", disk_usage)
    with pytest.raises(RuntimeError, match="ota_insufficient_space"):
        runtime.preflight()
    assert commands == []


def test_clean_install_preflight_rejects_full_custom_docker_root(tmp_path, monkeypatch):
    from robopark_ota import host_install

    docker_root = tmp_path / "mnt/docker-data"
    docker_root.mkdir(parents=True)
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(required_free_bytes=1024))
    runtime.tuna = TunaConfiguration()
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command == ["docker", "buildx", "version"]:
            return SimpleNamespace(stdout="github.com/docker/buildx v0.14.0 deadbeef\n")
        if command == ["docker", "info", "--format", "{{.DockerRootDir}}"]:
            return SimpleNamespace(stdout=str(docker_root) + "\n")
        return SimpleNamespace(stdout="Usage: build\nOptions: --builder --max-used-space\n")

    runtime._run = run
    monkeypatch.setattr(host_install.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(host_install.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        host_install.shutil, "disk_usage",
        lambda path: SimpleNamespace(
            total=20 * 1024**3,
            free=512 if Path(path) == docker_root else 10 * 1024**3,
        ),
    )

    with pytest.raises(RuntimeError, match="ota_insufficient_space"):
        runtime.preflight()
    assert ["docker", "info", "--format", "{{.DockerRootDir}}"] in commands


def test_managed_clean_install_preflight_rejects_noncanonical_live_docker_root(
    tmp_path, monkeypatch
):
    from robopark_ota import host_install

    docker_root = tmp_path / "mnt/docker-data"
    docker_root.mkdir(parents=True)
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(required_free_bytes=1))
    runtime.tuna = TunaConfiguration()

    def run(command, **kwargs):
        if command == ["docker", "buildx", "version"]:
            return SimpleNamespace(stdout="github.com/docker/buildx v0.14.0 deadbeef\n")
        if command == ["docker", "info", "--format", "{{.DockerRootDir}}"]:
            return SimpleNamespace(stdout=str(docker_root) + "\n")
        return SimpleNamespace(stdout="Usage: build\nOptions: --builder --max-used-space\n")

    runtime._run = run
    monkeypatch.setattr(host_install.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(host_install.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        host_install.storage,
        "require_storage",
        lambda _root: {"state": "ready", "mode": "emmc-nvme-data"},
    )

    with pytest.raises(RuntimeError, match="storage_custom_container_root"):
        runtime.preflight()


def test_managed_clean_install_preflight_accepts_canonical_root_in_synthetic_host(
    tmp_path, monkeypatch
):
    from robopark_ota import host_install

    (tmp_path / "var/lib/docker").mkdir(parents=True)
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(required_free_bytes=1))
    runtime.tuna = TunaConfiguration()

    def run(command, **kwargs):
        if command == ["docker", "buildx", "version"]:
            return SimpleNamespace(stdout="github.com/docker/buildx v0.14.0 deadbeef\n")
        if command == ["docker", "info", "--format", "{{.DockerRootDir}}"]:
            return SimpleNamespace(stdout="/var/lib/docker\n")
        return SimpleNamespace(stdout="Usage: build\nOptions: --builder --max-used-space\n")

    runtime._run = run
    monkeypatch.setattr(host_install.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(host_install.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        host_install.storage,
        "require_storage",
        lambda _root: {"state": "ready", "mode": "emmc-nvme-data"},
    )

    runtime.preflight()


@pytest.mark.parametrize(
    ("total_bytes", "free_bytes"),
    [(32 * 1024**3, 5 * 1024**3), (100 * 1024**3, 10 * 1024**3)],
)
def test_clean_install_preflight_preserves_update_storage_floor(
    tmp_path, monkeypatch, total_bytes, free_bytes
):
    from robopark_ota import host_install

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(required_free_bytes=512 * 1024**2))
    runtime.tuna = TunaConfiguration()
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command == ["docker", "buildx", "version"]:
            return SimpleNamespace(stdout="github.com/docker/buildx v0.14.0 deadbeef\n")
        if command == ["docker", "info", "--format", "{{.DockerRootDir}}"]:
            return SimpleNamespace(stdout=str(tmp_path) + "\n")
        return SimpleNamespace(stdout="Usage: build\nOptions: --builder --max-used-space\n")

    runtime._run = run
    monkeypatch.setattr(host_install.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(host_install.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        host_install.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=total_bytes, free=free_bytes),
    )

    with pytest.raises(RuntimeError, match="ota_insufficient_space"):
        runtime.preflight()
    assert commands == []


def test_clean_install_preflight_requires_buildx_before_extraction(tmp_path, monkeypatch):
    from robopark_ota import host_install

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    runtime.verified = SimpleNamespace(
        manifest=SimpleNamespace(required_free_bytes=1)
    )
    runtime.tuna = TunaConfiguration()
    commands = []

    def run(command, **kwargs):
        del kwargs
        commands.append(command)
        if command == ["docker", "buildx", "version"]:
            raise subprocess.CalledProcessError(1, command)

    runtime._run = run
    monkeypatch.setattr(host_install.shutil, "which", lambda _: "/usr/bin/docker")

    with pytest.raises(RuntimeError, match="buildx_unavailable"):
        runtime.preflight()

    assert commands == [["docker", "compose", "version"], ["docker", "buildx", "version"]]
    assert not (tmp_path / "opt/robopark").exists()


def test_clean_install_preflight_rejects_buildx_without_default_load(tmp_path, monkeypatch):
    from robopark_ota import host_install

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(required_free_bytes=1))
    runtime.tuna = TunaConfiguration()
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(stdout="github.com/docker/buildx v0.13.1 deadbeef\n")

    runtime._run = run
    monkeypatch.setattr(host_install.shutil, "which", lambda _: "/usr/bin/docker")

    with pytest.raises(RuntimeError, match="buildx_version_unsupported"):
        runtime.preflight()

    assert commands[-1][0] == ["docker", "buildx", "version"]
    assert not (tmp_path / "opt/robopark").exists()


def test_clean_install_preflight_accepts_buildx_with_default_load(tmp_path, monkeypatch):
    from robopark_ota import host_install

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(required_free_bytes=1))
    runtime.tuna = TunaConfiguration()
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))
        if command == ["docker", "buildx", "version"]:
            return SimpleNamespace(stdout="github.com/docker/buildx v0.14.0 deadbeef\n")
        if command == ["docker", "info", "--format", "{{.DockerRootDir}}"]:
            return SimpleNamespace(stdout=str(tmp_path) + "\n")
        return SimpleNamespace(stdout="Usage: build\nOptions: --builder --max-used-space\n")

    runtime._run = run
    monkeypatch.setattr(host_install.shutil, "which", lambda _: "/usr/bin/docker")

    runtime.preflight()

    assert (["docker", "buildx", "version"], {"capture_output": True}) in commands
    assert (["docker", "buildx", "prune", "--help"], {"capture_output": True}) in commands
    assert (["docker", "info"], {"capture_output": True, "timeout": 15}) in commands
    info_command, info_options = commands[-1]
    assert info_command == ["docker", "info", "--format", "{{.DockerRootDir}}"]
    assert info_options["timeout"] == 15


@pytest.mark.parametrize(
    ("missing_command", "error"),
    [
        (["docker", "compose", "build", "--help"], "compose_builder_unsupported"),
        (["docker", "buildx", "prune", "--help"], "buildx_budget_unsupported"),
    ],
)
def test_clean_install_preflight_rejects_missing_builder_capability(
    tmp_path, monkeypatch, missing_command, error
):
    from robopark_ota import host_install

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(required_free_bytes=1))
    runtime.tuna = TunaConfiguration()
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command == ["docker", "buildx", "version"]:
            return SimpleNamespace(stdout="github.com/docker/buildx v0.29.1 deadbeef\n")
        if command == missing_command:
            return SimpleNamespace(stdout="Usage: build\nOptions:\n")
        return SimpleNamespace(stdout="Usage: build\nOptions: --builder --max-used-space\n")

    runtime._run = run
    monkeypatch.setattr(host_install.shutil, "which", lambda _: "/usr/bin/docker")

    with pytest.raises(RuntimeError, match=error):
        runtime.preflight()

    assert missing_command in commands
    assert not (tmp_path / "opt/robopark").exists()


def test_clean_install_preflight_rejects_unreachable_docker_daemon(tmp_path, monkeypatch):
    from robopark_ota import host_install

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(required_free_bytes=1))
    runtime.tuna = TunaConfiguration()
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command == ["docker", "info"]:
            raise subprocess.CalledProcessError(1, command)
        if command == ["docker", "buildx", "version"]:
            return SimpleNamespace(stdout="github.com/docker/buildx v0.29.1 deadbeef\n")
        return SimpleNamespace(stdout="Usage: build\nOptions: --builder --max-used-space\n")

    runtime._run = run
    monkeypatch.setattr(host_install.shutil, "which", lambda _: "/usr/bin/docker")

    with pytest.raises(RuntimeError, match="docker_daemon_unavailable"):
        runtime.preflight()

    assert commands[-1] == ["docker", "info"]
    assert not (tmp_path / "opt/robopark").exists()


def test_clean_install_docker_commands_use_local_daemon(monkeypatch):
    from robopark_ota import host_install

    runtime = object.__new__(HostInstallRuntime)
    monkeypatch.setenv("DOCKER_CONTEXT", "remote-production")
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.invalid:2375")
    seen = []

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(host_install.subprocess, "run", run)
    runtime._run(["docker", "info"])

    command, options = seen[0]
    assert command == ["docker", "info"]
    assert options["env"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert "DOCKER_CONTEXT" not in options["env"]


def test_docker_resource_discovery_and_removal_ignore_remote_context(monkeypatch):
    monkeypatch.setenv("DOCKER_CONTEXT", "remote-production")
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.invalid:2375")
    seen = []

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(remove.subprocess, "run", run)
    docker = DockerCli()
    assert docker._lines(["docker", "ps", "-q"]) == ()
    docker.remove_containers(("robopark-api-1",))

    assert len(seen) == 2
    for _, options in seen:
        assert options["env"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"
        assert "DOCKER_CONTEXT" not in options["env"]


class FakeDocker:
    def __init__(self) -> None:
        self.removed: list[tuple[str, tuple[str, ...]]] = []

    def remove_containers(self, names: tuple[str, ...]) -> None:
        self.removed.append(("containers", names))

    def remove_volumes(self, names: tuple[str, ...]) -> None:
        self.removed.append(("volumes", names))

    def remove_networks(self, names: tuple[str, ...]) -> None:
        self.removed.append(("networks", names))

    def remove_images(self, names: tuple[str, ...]) -> None:
        self.removed.append(("images", names))


class DiscoveryDocker(DockerCli):
    def __init__(self, responses: dict[tuple[str, ...], tuple[str, ...]]) -> None:
        self.responses = responses

    def _lines(self, command: list[str]) -> tuple[str, ...]:
        return self.responses.get(tuple(command), ())


def test_discovery_removes_only_main_and_interrupted_candidate_projects():
    list_command = (
        "docker",
        "ps",
        "-aq",
        "--filter",
        "label=com.docker.compose.project",
    )
    template = '{{ index .Config.Labels "com.docker.compose.project" }}'
    docker = DiscoveryDocker(
        {
            list_command: ("main", "candidate", "other"),
            ("docker", "container", "inspect", "--format", template, "main"): (
                "robopark",
            ),
            (
                "docker",
                "container",
                "inspect",
                "--format",
                template,
                "candidate",
            ): ("robopark-candidate-66ee508b-0cfa-4de0-85e5-d02a6d9c115d",),
            ("docker", "container", "inspect", "--format", template, "other"): (
                "unrelated",
            ),
        }
    )

    targets = docker.discover_owned()
    assert targets.containers == ("main", "candidate")
    assert targets.volumes == ()


def test_discovery_rejects_an_unlabelled_postgres_volume_before_removal():
    docker = DiscoveryDocker(
        {
            ("docker", "volume", "ls", "-q"): ("robopark_robopark_postgres",),
        }
    )

    with pytest.raises(ValueError, match="unverified_robopark_volume"):
        docker.discover_owned()


@pytest.mark.parametrize(
    ("listing", "identity", "error"),
    [
        (
            ("docker", "ps", "-a", "--format", "{{.Names}}"),
            "robopark-api-1",
            "unverified_robopark_container",
        ),
        (
            ("docker", "network", "ls", "--format", "{{.Name}}"),
            "robopark_default",
            "unverified_robopark_network",
        ),
    ],
)
def test_discovery_rejects_unlabelled_name_collision(listing, identity, error):
    docker = DiscoveryDocker({listing: (identity,)})

    with pytest.raises(ValueError, match=error):
        docker.discover_owned()


def test_discovery_includes_an_existing_labelled_postgres_volume():
    label_query = (
        "docker",
        "volume",
        "ls",
        "-q",
        "--filter",
        "label=com.docker.compose.project",
    )
    template = '{{ index .Labels "com.docker.compose.project" }}'
    docker = DiscoveryDocker(
        {
            label_query: ("robopark_robopark_postgres",),
            (
                "docker",
                "volume",
                "inspect",
                "--format",
                template,
                "robopark_robopark_postgres",
            ): ("robopark",),
            ("docker", "volume", "ls", "-q"): (
                "robopark_robopark_postgres",
                "another-volume",
            ),
        }
    )

    assert docker.discover_owned().volumes == ("robopark_robopark_postgres",)


def test_removal_inventory_covers_current_bot_and_legacy_update_units(tmp_path):
    plan = RemovalPlan.for_root(tmp_path)
    for name in (
        "robopark-bot.service", "robopark-bot.path",
        "robopark-update-check.service", "robopark-update-check.timer",
    ):
        assert name in plan.services
        assert tmp_path / "etc/systemd/system" / name in plan.paths
    assert tmp_path / "var/backups/robopark" in plan.paths
    assert "robopark-bot:" in plan.image_prefixes


def test_discovery_includes_only_exact_robopark_bot_image_tags():
    docker = DiscoveryDocker({
        ("docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", "robopark-bot:*"):
            ("robopark-bot:candidate", "robopark-bot:release-1", "other-bot:latest"),
    })

    assert docker.discover_owned().images == (
        "robopark-bot:candidate", "robopark-bot:release-1"
    )


def test_clean_install_checks_docker_ownership_before_mutation(tmp_path, monkeypatch):
    from robopark_ota import host_install

    class UnverifiedDocker:
        def discover_owned(self):
            raise ValueError("unverified_robopark_volume")

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    calls = []
    runtime._run = lambda command, **kwargs: calls.append(command)
    monkeypatch.setattr(host_install, "DockerCli", UnverifiedDocker)

    with pytest.raises(ValueError, match="unverified_robopark_volume"):
        runtime.ensure_empty_host()

    assert calls == []


def test_clean_install_rejects_existing_database_without_removing_it(
    tmp_path, monkeypatch
):
    from robopark_ota import host_install

    class ExistingDocker:
        def discover_owned(self):
            return DockerTargets((), ("robopark_robopark_postgres",), (), ())

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    calls = []
    runtime._run = lambda command, **kwargs: calls.append(command)
    monkeypatch.setattr(host_install, "DockerCli", ExistingDocker)

    with pytest.raises(RuntimeError, match="clean_install_requires_empty_host") as blocked:
        runtime.ensure_empty_host()

    assert "robopark_robopark_postgres" in str(blocked.value)
    assert calls == []


def test_clean_install_starts_stopped_local_docker_before_discovery(
    tmp_path, monkeypatch
):
    from robopark_ota import host_install

    attempts = []
    commands = []

    class StoppedDocker:
        def discover_owned(self):
            attempts.append("discover")
            if len(attempts) == 1:
                raise subprocess.CalledProcessError(1, ["docker", "ps"])
            return DockerTargets.empty()

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    runtime._run = lambda command, **kwargs: commands.append(command)
    monkeypatch.setattr(host_install.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(host_install, "DockerCli", StoppedDocker)

    runtime.ensure_empty_host()

    assert attempts == ["discover", "discover"]
    assert commands == [["systemctl", "enable", "--now", "docker"]]


def test_clean_install_rejects_existing_files_without_removing_them(
    tmp_path, monkeypatch
):
    from robopark_ota import host_install

    owned = tmp_path / "var/lib/robopark/data/keep"
    owned.parent.mkdir(parents=True)
    owned.write_text("preserve")
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    monkeypatch.setattr(
        host_install, "DockerCli", lambda: pytest.fail("Docker must not run")
    )

    with pytest.raises(RuntimeError, match="clean_install_requires_empty_host") as blocked:
        runtime.ensure_empty_host()

    assert str(owned.parents[1]) in str(blocked.value)
    assert owned.read_text() == "preserve"


def test_clean_install_accepts_empty_host_before_docker_is_installed(tmp_path, monkeypatch):
    from robopark_ota import host_install

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    monkeypatch.setattr(host_install.shutil, "which", lambda name: None)
    monkeypatch.setattr(host_install, "DockerCli", lambda: pytest.fail("Docker must not run before installation"))

    runtime.ensure_empty_host()


def test_clean_install_refuses_unmanaged_docker_data_when_cli_is_missing(tmp_path, monkeypatch):
    from robopark_ota import host_install

    docker_data = tmp_path / "var/lib/docker/overlay2/keep"
    docker_data.parent.mkdir(parents=True)
    docker_data.write_text("preserve")
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    monkeypatch.setattr(host_install.shutil, "which", lambda name: None)
    monkeypatch.setattr(host_install, "DockerCli", lambda: pytest.fail("Docker must not run"))

    with pytest.raises(RuntimeError, match="docker_state_unknown"):
        runtime.ensure_empty_host()
    assert docker_data.read_text() == "preserve"


def test_clean_install_refuses_docker_data_file_without_cli(tmp_path, monkeypatch):
    from robopark_ota import host_install

    docker_data = tmp_path / "var/lib/docker"
    docker_data.parent.mkdir(parents=True)
    docker_data.write_text("preserve")
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    monkeypatch.setattr(host_install.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="docker_state_unknown"):
        runtime.ensure_empty_host()
    assert docker_data.read_text() == "preserve"


def test_clean_install_provisions_official_docker_packages_on_empty_supported_host(
    clean_host_profile, monkeypatch
):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = TunaConfiguration()
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    commands = []
    runtime._run = lambda command, **kwargs: commands.append((command, kwargs))
    monkeypatch.setattr(host_install.shutil, "disk_usage", lambda _: SimpleNamespace(total=64 * 1024**3, free=32 * 1024**3))
    monkeypatch.setattr(host_install.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(
        b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nTEST\n-----END PGP PUBLIC KEY BLOCK-----\n"
    ))
    monkeypatch.setattr(host_install, "_DOCKER_KEY_SHA256", hashlib.sha256(
        b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nTEST\n-----END PGP PUBLIC KEY BLOCK-----\n"
    ).hexdigest())

    runtime.prepare_missing_docker()

    source = (root / "etc/apt/sources.list.d/robopark-docker.sources").read_text()
    assert "https://download.docker.com/linux/ubuntu" in source
    assert "Suites: jammy" in source
    assert "Architectures: arm64" in source
    assert "Signed-By: /etc/apt/keyrings/robopark-docker.asc" in source
    assert (root / "etc/apt/keyrings/robopark-docker.asc").is_file()
    assert [command for command, _ in commands] == [
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "--no-remove", "--no-install-recommends", "docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"],
        ["systemctl", "enable", "--now", "docker"],
    ]


def test_armbian_26_ubuntu_base_selects_resolute_docker_repository(clean_host_profile, monkeypatch):
    from robopark_ota import host_install

    root, _requirements = clean_host_profile
    (root / "etc/armbian-release").write_text("VERSION=26.8.0\n")
    (root / "etc/os-release").write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
    monkeypatch.setattr(host_install.platform, "machine", lambda: "aarch64")

    assert host_install._docker_repository_target(root) == ("ubuntu", "resolute", "arm64")


def test_clean_install_rejects_unsupported_docker_repo_before_mutation(clean_host_profile, monkeypatch):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    (root / "etc/os-release").write_text('ID=ubuntu\nVERSION_ID="23.10"\n')
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = TunaConfiguration()
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    calls = []
    runtime._run = lambda command, **kwargs: calls.append(command)
    monkeypatch.setattr(host_install.shutil, "disk_usage", lambda _: SimpleNamespace(total=64 * 1024**3, free=32 * 1024**3))

    with pytest.raises(RuntimeError, match="docker_install_unsupported_release"):
        runtime.prepare_missing_docker()
    assert calls == []
    assert not (root / "etc/apt/sources.list.d/robopark-docker.sources").exists()


def test_clean_install_rejects_old_python_before_provisioning(clean_host_profile, monkeypatch):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = TunaConfiguration()
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    calls = []
    runtime._run = lambda command, **kwargs: calls.append(command)
    monkeypatch.setattr(host_install.sys, "version_info", (3, 9, 0))

    with pytest.raises(RuntimeError, match="python_3_10_required"):
        runtime.prepare_missing_docker()
    assert calls == []
    assert not (root / "etc/apt/sources.list.d/robopark-docker.sources").exists()


def test_clean_install_rejects_existing_docker_apt_source_before_mutation(clean_host_profile, monkeypatch):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    source = root / "etc/apt/sources.list.d/docker.sources"
    source.parent.mkdir(parents=True)
    source.write_text("URIs: https://download.docker.com/linux/ubuntu\n")
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = TunaConfiguration()
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    calls = []
    runtime._run = lambda command, **kwargs: calls.append(command)
    monkeypatch.setattr(host_install.shutil, "disk_usage", lambda _: SimpleNamespace(total=64 * 1024**3, free=32 * 1024**3))
    monkeypatch.setattr(host_install.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("Docker key must not download"))

    with pytest.raises(RuntimeError, match="docker_repo_conflict"):
        runtime.prepare_missing_docker()
    assert calls == []
    assert source.read_text() == "URIs: https://download.docker.com/linux/ubuntu\n"


def test_clean_install_rejects_untrusted_docker_key_before_writing_apt_files(
    clean_host_profile, monkeypatch
):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = TunaConfiguration()
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    runtime._run = lambda command, **kwargs: pytest.fail(f"must not run {command}")
    monkeypatch.setattr(host_install.shutil, "disk_usage", lambda _: SimpleNamespace(total=64 * 1024**3, free=32 * 1024**3))
    monkeypatch.setattr(host_install.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(
        b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nATTACKER\n-----END PGP PUBLIC KEY BLOCK-----\n"
    ))

    with pytest.raises(RuntimeError, match="docker_key_invalid"):
        runtime.prepare_missing_docker()
    assert not (root / "etc/apt/keyrings/robopark-docker.asc").exists()
    assert not (root / "etc/apt/sources.list.d/robopark-docker.sources").exists()


def test_clean_install_rejects_main_apt_list_docker_conflict_before_writing(
    clean_host_profile, monkeypatch
):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    source = root / "etc/apt/sources.list"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("deb https://download.docker.com/linux/ubuntu jammy stable\n")
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = TunaConfiguration()
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    runtime._run = lambda command, **kwargs: pytest.fail(f"must not run {command}")
    monkeypatch.setattr(host_install.shutil, "disk_usage", lambda _: SimpleNamespace(total=64 * 1024**3, free=32 * 1024**3))
    monkeypatch.setattr(host_install.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("must not download"))

    with pytest.raises(RuntimeError, match="docker_repo_conflict"):
        runtime.prepare_missing_docker()
    assert not (root / "etc/apt/keyrings/robopark-docker.asc").exists()
    assert source.read_text() == "deb https://download.docker.com/linux/ubuntu jammy stable\n"


def test_clean_install_retries_its_partial_docker_provisioning(clean_host_profile, monkeypatch):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    source = root / "etc/apt/sources.list.d/robopark-docker.sources"
    source.parent.mkdir(parents=True)
    source.write_text(
        "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\n"
        "Suites: jammy\nComponents: stable\nArchitectures: arm64\n"
        "Signed-By: /etc/apt/keyrings/robopark-docker.asc\n"
    )
    key = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nTEST\n-----END PGP PUBLIC KEY BLOCK-----\n"
    key_path = root / "etc/apt/keyrings/robopark-docker.asc"
    key_path.parent.mkdir(parents=True)
    key_path.write_bytes(key)
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = TunaConfiguration()
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    commands = []
    runtime._run = lambda command, **kwargs: commands.append(command)
    monkeypatch.setattr(host_install.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(host_install.shutil, "disk_usage", lambda _: SimpleNamespace(total=64 * 1024**3, free=32 * 1024**3))
    monkeypatch.setattr(host_install, "_DOCKER_KEY_SHA256", hashlib.sha256(key).hexdigest())
    monkeypatch.setattr(host_install.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(key))
    monkeypatch.setattr(host_install, "DockerCli", lambda: SimpleNamespace(discover_owned=lambda: DockerTargets((), (), (), ())))

    runtime.prepare_missing_docker()

    assert commands == [
        ["systemctl", "enable", "--now", "docker"],
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "--no-remove", "--no-install-recommends", "docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"],
        ["systemctl", "enable", "--now", "docker"],
    ]


def test_clean_install_refuses_existing_robopark_before_partial_docker_repair(
    clean_host_profile, monkeypatch
):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    source = root / "etc/apt/sources.list.d/robopark-docker.sources"
    source.parent.mkdir(parents=True)
    source.write_text(
        "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\n"
        "Suites: jammy\nComponents: stable\nArchitectures: arm64\n"
        "Signed-By: /etc/apt/keyrings/robopark-docker.asc\n"
    )
    owned = root / "var/lib/robopark/data/keep"
    owned.parent.mkdir(parents=True)
    owned.write_text("preserve")
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = TunaConfiguration()
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    runtime._run = lambda command, **kwargs: pytest.fail(f"must not run {command}")
    monkeypatch.setattr(host_install.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(host_install.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("must not download"))

    with pytest.raises(RuntimeError, match="clean_install_requires_empty_host"):
        runtime.prepare_missing_docker()
    assert owned.read_text() == "preserve"


def test_clean_install_provisions_tuna_cli_without_leaking_token(clean_host_profile, monkeypatch):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = SimpleNamespace(enabled=True)
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    commands = []
    runtime._run = lambda command, **kwargs: commands.append((command, kwargs))
    key = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nTEST\n-----END PGP PUBLIC KEY BLOCK-----\n"
    monkeypatch.setattr(host_install, "_TUNA_KEY_SHA256", hashlib.sha256(key).hexdigest(), raising=False)
    monkeypatch.setattr(host_install.shutil, "disk_usage", lambda _: SimpleNamespace(total=64 * 1024**3, free=32 * 1024**3))
    monkeypatch.setattr(host_install.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(key))

    runtime.prepare_missing_tuna()

    source = (root / "etc/apt/sources.list.d/robopark-tuna.sources").read_text()
    assert "https://code.tuna.am/api/packages/tuna/debian" in source
    assert "Signed-By: /etc/apt/keyrings/robopark-tuna.asc" in source
    assert [command for command, _ in commands] == [
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "--no-remove", "--no-install-recommends", "tuna-cli"],
    ]
    assert "TUNA_TOKEN" not in str(commands) + source


def test_clean_install_rejects_untrusted_tuna_key_before_writing(clean_host_profile, monkeypatch):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = SimpleNamespace(enabled=True)
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    runtime._run = lambda command, **kwargs: pytest.fail(f"must not run {command}")
    monkeypatch.setattr(host_install.shutil, "disk_usage", lambda _: SimpleNamespace(total=64 * 1024**3, free=32 * 1024**3))
    monkeypatch.setattr(host_install.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(
        b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nATTACKER\n-----END PGP PUBLIC KEY BLOCK-----\n"
    ))

    with pytest.raises(RuntimeError, match="tuna_key_invalid"):
        runtime.prepare_missing_tuna()
    assert not (root / "etc/apt/keyrings/robopark-tuna.asc").exists()
    assert not (root / "etc/apt/sources.list.d/robopark-tuna.sources").exists()


def test_clean_install_rejects_conflicting_tuna_source_before_download(clean_host_profile, monkeypatch):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    source = root / "etc/apt/sources.list"
    source.parent.mkdir(parents=True)
    source.write_text("deb https://code.tuna.am/api/packages/tuna/debian stable main\n")
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = SimpleNamespace(enabled=True)
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    runtime._run = lambda command, **kwargs: pytest.fail(f"must not run {command}")
    monkeypatch.setattr(host_install.shutil, "disk_usage", lambda _: SimpleNamespace(total=64 * 1024**3, free=32 * 1024**3))
    monkeypatch.setattr(host_install.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("must not download"))

    with pytest.raises(RuntimeError, match="tuna_repo_conflict"):
        runtime.prepare_missing_tuna()
    assert not (root / "etc/apt/keyrings/robopark-tuna.asc").exists()


def test_clean_install_preserves_preinstalled_tuna_cli(clean_host_profile, monkeypatch):
    from robopark_ota import host_install

    root, requirements = clean_host_profile
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.tuna = SimpleNamespace(enabled=True)
    runtime.verified = SimpleNamespace(manifest=SimpleNamespace(requirements=requirements, required_free_bytes=1))
    runtime._run = lambda command, **kwargs: pytest.fail(f"must not run {command}")
    monkeypatch.setattr(host_install.shutil, "which", lambda name: "/usr/bin/tuna" if name == "tuna" else None)

    runtime.prepare_missing_tuna()
    assert not (root / "etc/apt/sources.list.d/robopark-tuna.sources").exists()


def test_discovery_selects_owned_image_tags_instead_of_shared_image_ids():
    tag_format = "{{.Repository}}:{{.Tag}}"
    docker = DiscoveryDocker(
        {
            ("docker", "image", "ls", "--format", tag_format, "robopark-api:*"): (
                "robopark-api:current",
                "foreign:shared",
            ),
            ("docker", "image", "ls", "--format", tag_format, "robopark-web:*"): (
                "robopark-web:rollback",
            ),
            ("docker", "image", "ls", "-q", "robopark-api:*"): ("shared-image-id",),
        }
    )

    assert docker.discover_owned().images == (
        "robopark-api:current",
        "robopark-web:rollback",
    )


def test_image_removal_never_forces_or_accepts_a_foreign_tag(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "robopark_ota.remove.subprocess.run",
        lambda command, **kwargs: calls.append(command),
    )
    docker = DockerCli()

    with pytest.raises(ValueError, match="unverified_robopark_image"):
        docker.remove_images(("foreign:shared",))
    assert calls == []

    docker.remove_images(("robopark-api:current",))
    assert calls == [["docker", "image", "rm", "robopark-api:current"]]


def test_removal_plan_deletes_only_owned_paths_and_exact_docker_targets(tmp_path: Path):
    root = tmp_path / "host"
    owned = [
        root / "opt/robopark/release.txt",
        root / "etc/robopark/config",
        root / "var/lib/robopark/data",
        root / "var/log/robopark/app.log",
        root / "run/lock/robopark/install.lock",
        root / "etc/systemd/system/robopark.service",
        root / "etc/tmpfiles.d/robopark.conf",
    ]
    for path in owned:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("owned")
    unrelated = [
        root / "var/lib/postgresql/data",
        root / "opt/other/application",
        root / "etc/systemd/system/other.service",
    ]
    for path in unrelated:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep")
    docker = FakeDocker()
    targets = DockerTargets(
        containers=("robopark-api-1",),
        volumes=("robopark_robopark_postgres",),
        networks=("robopark_default",),
        images=("robopark-api:local",),
    )

    remove_owned_installation(RemovalPlan.for_root(root), docker, targets)

    assert all(not path.exists() for path in owned)
    assert all(path.read_text() == "keep" for path in unrelated)
    assert docker.removed == [
        ("containers", targets.containers),
        ("volumes", targets.volumes),
        ("networks", targets.networks),
        ("images", targets.images),
    ]


def test_removal_rejects_symlinked_owned_root(tmp_path: Path):
    root = tmp_path / "host"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("unrelated")
    (root / "var/lib").mkdir(parents=True)
    (root / "var/lib/robopark").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="unsafe_removal_path"):
        remove_owned_installation(
            RemovalPlan.for_root(root), FakeDocker(), DockerTargets.empty()
        )
    assert (outside / "keep").exists()


def test_removal_preview_lists_existing_paths_and_docker_volume_size(tmp_path: Path):
    root = tmp_path / "host"
    owned = root / "var/lib/robopark/data"
    owned.parent.mkdir(parents=True)
    owned.write_bytes(b"keep")
    volume = tmp_path / "docker-volume"
    volume.mkdir()
    (volume / "db").write_bytes(b"postgres")

    class PreviewDocker(FakeDocker):
        def volume_mountpoint(self, name):
            assert name == "robopark_robopark_postgres"
            return volume

        def container_size(self, name):
            return 13

        def image_size(self, name):
            return 17

    targets = DockerTargets(
        containers=("robopark-api-1",),
        volumes=("robopark_robopark_postgres",),
        networks=("robopark_default",),
        images=("robopark-api:current",),
    )

    preview = remove.preview_owned_installation(
        RemovalPlan.for_root(root), PreviewDocker(), targets
    )

    assert any(
        item.kind == "path" and item.path == owned.parent and item.bytes_used > 0
        for item in preview
    )
    assert any(
        item.kind == "volume"
        and item.name == "robopark_robopark_postgres"
        and item.path == volume
        and item.bytes_used > 0
        for item in preview
    )
    assert any(item.kind == "container" and item.bytes_used == 13 for item in preview)
    assert any(item.kind == "image" and item.bytes_used == 17 for item in preview)


def test_removal_preview_rejects_symlinked_owned_path_before_measurement(
    tmp_path: Path,
):
    root = tmp_path / "host"
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "var/lib").mkdir(parents=True)
    (root / "var/lib/robopark").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="unsafe_removal_path"):
        remove.preview_owned_installation(
            RemovalPlan.for_root(root), FakeDocker(), DockerTargets.empty()
        )


def test_full_removal_shows_exact_volume_and_size_before_confirmation(
    tmp_path: Path, monkeypatch, capsys
):
    from robopark_ota import cli

    root = tmp_path / "host"
    owned = root / "var/lib/robopark/data"
    owned.parent.mkdir(parents=True)
    owned.write_bytes(b"keep")
    mountpoint = tmp_path / "postgres-volume"
    mountpoint.mkdir()
    (mountpoint / "db").write_bytes(b"data")

    class PreviewDocker(FakeDocker):
        def discover_owned(self):
            return DockerTargets((), ("robopark_robopark_postgres",), (), ())

        def volume_mountpoint(self, name):
            return mountpoint

    monkeypatch.setattr(cli, "DockerCli", PreviewDocker)
    monkeypatch.setattr("builtins.input", lambda prompt: "отмена")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("host mutation before confirmation"),
    )

    with pytest.raises(RuntimeError, match="confirmation_required"):
        cli._remove(root)

    output = capsys.readouterr().out
    assert str(owned.parent) in output
    assert "robopark_robopark_postgres" in output
    assert str(mountpoint) in output
    assert "Б" in output


def test_removal_preview_rejects_a_symlinked_volume_mountpoint(tmp_path: Path):
    volume = tmp_path / "real-volume"
    volume.mkdir()
    (volume / "data").write_bytes(b"secret")
    shortcut = tmp_path / "volume-link"
    shortcut.symlink_to(volume, target_is_directory=True)

    class PreviewDocker(FakeDocker):
        def volume_mountpoint(self, name):
            return shortcut

    targets = DockerTargets((), ("robopark_robopark_postgres",), (), ())
    with pytest.raises(ValueError, match="unsafe_volume_mountpoint"):
        remove.preview_owned_installation(
            RemovalPlan.for_root(tmp_path / "host"), PreviewDocker(), targets
        )


def test_full_removal_refuses_targets_added_after_confirmation(
    tmp_path: Path, monkeypatch
):
    from robopark_ota import cli

    root = tmp_path / "host"
    calls = []

    class ChangingDocker(FakeDocker):
        def discover_owned(self):
            calls.append("discover")
            if calls.count("discover") == 1:
                return DockerTargets.empty()
            return DockerTargets(("new-container",), (), (), ())

    monkeypatch.setattr(cli, "DockerCli", ChangingDocker)
    monkeypatch.setattr("builtins.input", lambda prompt: "УДАЛИТЬ ВСЕ ДАННЫЕ")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("host mutation after stale preview"),
    )

    with pytest.raises(RuntimeError, match="removal_preview_changed"):
        cli._remove(root)


def test_full_removal_disables_owned_units_after_exact_confirmation(
    tmp_path: Path, monkeypatch
):
    from robopark_ota import cli

    root = tmp_path / "host"
    commands = []
    removed = []

    class EmptyDocker(FakeDocker):
        def discover_owned(self):
            return DockerTargets.empty()

    monkeypatch.setattr(cli, "DockerCli", EmptyDocker)
    monkeypatch.setattr(cli, "preview_owned_installation", lambda *_args: ())
    monkeypatch.setattr("builtins.input", lambda _prompt: "УДАЛИТЬ ВСЕ ДАННЫЕ")
    monkeypatch.setattr(cli.subprocess, "run", lambda command, **_kwargs: commands.append(command))
    monkeypatch.setattr(cli, "remove_owned_installation", lambda *_args: removed.append(True))

    cli._remove(root)

    assert commands == [
        ["systemctl", "disable", "--now", *RemovalPlan.for_root(root).services],
        ["systemctl", "daemon-reload"],
    ]
    assert removed == [True]


def test_full_removal_reloads_systemd_after_partial_docker_failure(
    tmp_path: Path, monkeypatch
):
    from robopark_ota import cli

    commands = []

    class EmptyDocker(FakeDocker):
        def discover_owned(self):
            return DockerTargets.empty()

    monkeypatch.setattr(cli, "DockerCli", EmptyDocker)
    monkeypatch.setattr(cli, "preview_owned_installation", lambda *_args: ())
    monkeypatch.setattr("builtins.input", lambda _prompt: "УДАЛИТЬ ВСЕ ДАННЫЕ")
    monkeypatch.setattr(cli.subprocess, "run", lambda command, **_kwargs: commands.append(command))
    monkeypatch.setattr(
        cli, "remove_owned_installation",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("docker_volume_in_use")),
    )

    with pytest.raises(RuntimeError, match="docker_volume_in_use"):
        cli._remove(tmp_path / "host")

    assert commands[-1] == ["systemctl", "daemon-reload"]


def test_clean_install_checks_existing_host_before_collecting_royal_secret(
    tmp_path: Path, monkeypatch
):
    from robopark_ota import cli

    class ExistingRuntime:
        def __init__(self, bundle, *, root, tuna):
            self.root = root

        def preflight(self):
            pass

        def prepare_missing_docker(self):
            self.ensure_empty_host()

        def prepare_missing_tuna(self):
            pytest.fail("Tuna must not prepare when the host is occupied")

        def ensure_empty_host(self):
            raise RuntimeError("clean_install_requires_empty_host")

    monkeypatch.setattr(
        cli,
        "collect_tuna_configuration",
        lambda: pytest.fail("Tuna secret requested before empty-host check"),
    )
    monkeypatch.setattr(cli, "HostInstallRuntime", ExistingRuntime)
    monkeypatch.setattr(
        cli,
        "collect_royal_credentials",
        lambda: pytest.fail("royal secret requested before empty-host check"),
    )

    with pytest.raises(RuntimeError, match="clean_install_requires_empty_host"):
        cli._clean_install(tmp_path / "fake.ota", tmp_path / "host")


def test_existing_docker_host_installs_system_cryptography_dependency(monkeypatch):
    from subprocess import CompletedProcess

    from robopark_ota.host_install import HostInstallRuntime

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = Path("/")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        code = 1 if command[:4] == ["/usr/bin/python3", "-I", "-c", "import cryptography"] else 0
        return CompletedProcess(command, code, "", "")

    runtime._run = run
    monkeypatch.setattr("robopark_ota.host_install.shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr("robopark_ota.host_install.os.geteuid", lambda: 0)

    runtime.prepare_missing_docker()

    assert [command for command, _ in calls] == [
        ["/usr/bin/python3", "-I", "-c", "import cryptography"],
        ["apt-get", "update"],
        [
            "apt-get", "install", "-y", "--no-remove",
            "--no-install-recommends", "python3-cryptography",
        ],
    ]


def test_clean_install_checks_platform_and_space_before_asking_tuna(
    tmp_path: Path, monkeypatch
):
    from robopark_ota import cli

    class NoSpaceRuntime:
        def __init__(self, bundle, *, root, tuna):
            pass

        def ensure_empty_host(self):
            pass

        def basic_preflight(self):
            raise RuntimeError("ota_insufficient_space")

    monkeypatch.setattr(cli, "HostInstallRuntime", NoSpaceRuntime)
    monkeypatch.setattr(cli, "collect_tuna_configuration", lambda: pytest.fail("Tuna prompted before disk check"))

    with pytest.raises(RuntimeError, match="ota_insufficient_space"):
        cli._clean_install(tmp_path / "fake.ota", tmp_path / "host")


def test_clean_install_refuses_concurrent_installer_before_host_calls(
    tmp_path: Path, monkeypatch
):
    import fcntl

    from robopark_ota import cli

    root = tmp_path / "host"
    lock_path = root / "run/lock/robopark-install.lock"
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+") as competing_installer:
        fcntl.flock(competing_installer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(
            cli,
            "collect_tuna_configuration",
            lambda: pytest.fail("configuration prompt ran during another install"),
        )
        monkeypatch.setattr(
            cli,
            "HostInstallRuntime",
            lambda *_args, **_kwargs: pytest.fail(
                "host inspection ran during another install"
            ),
        )

        with pytest.raises(RuntimeError, match="clean_install_in_progress"):
            cli._clean_install(tmp_path / "fake.ota", root)


def test_clean_install_cli_checks_empty_host_before_and_after_docker_prep(tmp_path, monkeypatch):
    from robopark_ota import cli

    calls = []

    class BareRuntime:
        def __init__(self, bundle, *, root, tuna):
            pass

        def ensure_empty_host(self):
            calls.append("empty")

        def basic_preflight(self):
            calls.append("basic")

        def prepare_missing_docker(self):
            calls.append("prepare")
            self.ensure_empty_host()

        def prepare_missing_tuna(self):
            calls.append("tuna")

        def preflight(self):
            calls.append("preflight")

    def stop_before_credentials():
        calls.append("credentials")
        raise RuntimeError("stop_before_credentials")

    monkeypatch.setattr(cli, "collect_tuna_configuration", TunaConfiguration)
    monkeypatch.setattr(cli, "HostInstallRuntime", BareRuntime)
    monkeypatch.setattr(cli, "collect_royal_credentials", stop_before_credentials)

    with pytest.raises(RuntimeError, match="stop_before_credentials"):
        cli._clean_install(tmp_path / "fake.ota", tmp_path / "host")
    assert calls == ["empty", "basic", "tuna", "prepare", "empty", "empty", "preflight", "credentials"]


def test_clean_install_prepares_missing_tuna_before_missing_docker(tmp_path, monkeypatch):
    from robopark_ota import cli

    class BareRuntime:
        def __init__(self, bundle, *, root, tuna):
            self.tuna_prepared = False

        def ensure_empty_host(self):
            pass

        def basic_preflight(self):
            pass

        def prepare_missing_tuna(self):
            self.tuna_prepared = True

        def prepare_missing_docker(self):
            if not self.tuna_prepared:
                raise RuntimeError("tuna_required")

        def preflight(self):
            pass

    monkeypatch.setattr(cli, "collect_tuna_configuration", TunaConfiguration)
    monkeypatch.setattr(cli, "HostInstallRuntime", BareRuntime)
    monkeypatch.setattr(
        cli, "collect_royal_credentials",
        lambda: (_ for _ in ()).throw(RuntimeError("stop_before_credentials")),
    )

    with pytest.raises(RuntimeError, match="stop_before_credentials"):
        cli._clean_install(tmp_path / "fake.ota", tmp_path / "host")


def test_removal_preview_formats_large_sizes_for_owner(capsys):
    from robopark_ota import cli

    size = 5 * 1024**3 + 7
    cli._print_removal_plan(
        (remove.RemovalPreviewEntry("volume", "database", None, size),)
    )

    output = capsys.readouterr().out
    assert "5.0 ГиБ" in output
    assert f"{size} Б" in output


class FakeInstallRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def preflight(self) -> None:
        self.calls.append("preflight")

    def ensure_empty_host(self) -> None:
        self.calls.append("empty")

    def extract_release(self) -> None:
        self.calls.append("extract")

    def configure(self) -> None:
        self.calls.append("configure")

    def install_knowledge(self) -> None:
        self.calls.append("knowledge")

    def start_database(self) -> None:
        self.calls.append("database")

    def migrate(self) -> None:
        self.calls.append("migrate")

    def seed_royal(self, credential_file: Path) -> None:
        assert credential_file.is_file()
        self.calls.append("seed")

    def start_application(self) -> None:
        self.calls.append("start")

    def wait_ready(self) -> None:
        self.calls.append("ready")

    def smoke_check(self) -> None:
        self.calls.append("smoke")

    def publish(self) -> None:
        self.calls.append("publish")

    def collect_diagnostics(self) -> Path:
        self.calls.append("diagnostics")
        return Path("/tmp/diagnostics.zip")


def test_clean_install_has_no_backup_branch_and_publishes_only_after_smoke(
    tmp_path: Path,
):
    runtime = FakeInstallRuntime()
    secret = tmp_path / "seed.json"
    secret.write_text("secret")

    CleanInstallCoordinator(runtime).run(secret)

    assert runtime.calls == [
        "preflight",
        "empty",
        "extract",
        "configure",
        "database",
        "migrate",
        "seed",
        "start",
        "ready",
        "smoke",
        "publish",
    ]


def test_clean_install_does_not_download_or_import_retired_knowledge(tmp_path: Path):
    from robopark_ota import cli

    runtime = FakeInstallRuntime()
    secret = tmp_path / "seed.json"
    secret.write_text("secret")
    cli._run_clean_install(runtime, secret)
    assert runtime.calls[-1] == "publish"
    assert "knowledge" not in runtime.calls


def test_clean_install_failure_collects_diagnostics_without_publishing(tmp_path: Path):
    runtime = FakeInstallRuntime()
    runtime.migrate = lambda: (_ for _ in ()).throw(RuntimeError("migration failed"))
    secret = tmp_path / "seed.json"
    secret.write_text("secret")

    with pytest.raises(RuntimeError, match="clean_install_failed") as caught:
        CleanInstallCoordinator(runtime).run(secret)

    assert "diagnostics.zip" in str(caught.value)
    assert "publish" not in runtime.calls
    assert runtime.calls[-1] == "diagnostics"


def test_clean_install_refusal_leaves_the_existing_host_unmodified(tmp_path: Path):
    runtime = FakeInstallRuntime()
    runtime.ensure_empty_host = lambda: (_ for _ in ()).throw(
        RuntimeError("clean_install_requires_empty_host")
    )
    secret = tmp_path / "seed.json"
    secret.write_text("secret")

    with pytest.raises(RuntimeError, match="clean_install_requires_empty_host"):
        CleanInstallCoordinator(runtime).run(secret)

    assert runtime.calls == ["preflight"]


def test_clean_install_allows_worker_health_on_arm_startup(tmp_path: Path):
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    calls = []
    runtime._run = lambda command, **kwargs: calls.append((command, kwargs))

    runtime.start_application()

    command, _kwargs = calls[-1]
    assert command[-9:] == [
        "up",
        "-d",
        "--no-build",
        "--wait",
        "--wait-timeout",
        "180",
        "api",
        "web",
        "worker",
    ]
    assert command[-1] == "worker"


def test_verified_release_extracts_under_fixed_release_root(tmp_path: Path):
    repository = Path(__file__).resolve().parents[2]
    version = (repository / "VERSION").read_text().strip()
    bundle = build_ota(repository, tmp_path / "artifact", git_sha="f" * 40)
    root = tmp_path / "host"

    target = extract_release(bundle, root=root)

    assert target == root / "opt/robopark/releases" / version
    assert (target / "VERSION").read_text().strip() == version
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["app_version"] == version
    assert manifest["package_sha256"]
    assert (target / "deploy/host/robopark").stat().st_mode & 0o111


def test_generated_host_configuration_has_no_seed_password(tmp_path: Path):
    root = tmp_path / "host"

    host_env = write_host_configuration(root, memory_mb=8192, cpu_count=4)

    content = host_env.read_text()
    assert "SEED_PASSWORD" not in content
    assert "GITHUB_" not in content
    assert "ROBOPARK_UPDATE_CHANNEL=manual" in content
    assert "ROBOPARK_HOST_PROFILE=vim4-safe" in content
    assert host_env.stat().st_mode & 0o077 == 0


def test_clean_install_prepares_host_bridge_layout(tmp_path: Path):
    root = tmp_path / "host"

    prepare_host_layout(root)

    expected_modes = {
        "var/lib/robopark": 0o750,
        "var/lib/robopark/ops": 0o750,
        "var/lib/robopark/ops/state": 0o700,
        "var/lib/robopark/ops/docker-config": 0o700,
        "var/lib/robopark/ops/inbox": 0o700,
        "var/lib/robopark/ops/artifacts": 0o700,
        "var/lib/robopark/ops/ota-uploads": 0o700,
        "var/lib/robopark/ops/public": 0o755,
        "var/lib/robopark/data": 0o700,
        "var/lib/robopark/api-ops": 0o700,
    }
    for relative, mode in expected_modes.items():
        assert (root / relative).is_dir()
        assert (root / relative).stat().st_mode & 0o777 == mode


def test_runtime_configures_pinned_host_bridge_before_start(tmp_path: Path):
    root = tmp_path / "host"
    release = root / "opt/robopark/releases/0.2.0-rc.8"
    (release / "deploy/host").mkdir(parents=True)
    (release / "deploy/host/robopark").write_text("#!/bin/sh\n")
    (release / "deploy/compose_secrets.py").write_text("")
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.release = release
    runtime.etc = root / "etc/robopark"
    runtime.tuna = TunaConfiguration(token="tt_private_value")
    calls = []
    runtime._run = lambda command, **kwargs: calls.append((command, kwargs))

    runtime.configure()

    assert (root / "opt/robopark/current").resolve() == release
    assert (root / "opt/robopark/host-tools").resolve() == release / "deploy/host"
    assert any(command[-1] == "bootstrap-compose" for command, _kwargs in calls)
    assert (
        "CORS_ORIGINS=https://robopark.ru.tuna.am"
        in (root / "etc/robopark/host.env").read_text()
    )
    assert runtime._compose_prefix()[-1] == str(
        root / "var/lib/robopark/ops/state/current-compose.json"
    )


def test_publish_enables_host_automation_and_optional_tuna(tmp_path: Path):
    root = tmp_path / "host"
    release = root / "opt/robopark/releases/0.2.0-rc.8"
    (release / "deploy/installer/lib").mkdir(parents=True)
    (release / "deploy/installer/lib/install-services.py").write_text("")
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.release = release
    runtime.etc = root / "etc/robopark"
    runtime.tuna = TunaConfiguration(token="tt_private_value")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["systemctl", "start", "robopark-doctor.service"]:
            projection = root / "var/lib/robopark/api-ops/host-health.json"
            projection.parent.mkdir(parents=True)
            projection.write_text(json.dumps({
                "services_checked_at": datetime.now(UTC).isoformat(),
                "services": {name: "unknown" for name in ("docker", "tuna", "internet", "wifi")},
            }))
            return subprocess.CompletedProcess(command, 1)

    runtime._run = run
    tuna_ready = []
    runtime._wait_tuna_ready = lambda: tuna_ready.append(True)

    runtime.publish()

    flattened = [" ".join(command) for command, _kwargs in calls]
    assert any("enable docker.service" in command for command in flattened)
    assert any("enable robopark.service" in command for command in flattened)
    assert any("enable robopark-commands.path" in command for command in flattened)
    assert any("enable robopark-backup.timer" in command for command in flattened)
    assert any("start robopark-backup.timer" in command for command in flattened)
    assert any("start robopark-watchdog.service" in command for command in flattened)
    assert any("start robopark-doctor.service" in command for command in flattened)
    assert any("enable --now robopark-tuna.service" in command for command in flattened)
    assert tuna_ready == [True]
    doctor_index = next(
        index
        for index, (command, _kwargs) in enumerate(calls)
        if "start robopark-doctor.service" in " ".join(command)
    )
    tuna_index = next(
        index
        for index, (command, _kwargs) in enumerate(calls)
        if "enable --now robopark-tuna.service" in " ".join(command)
    )
    recovery_key_index = next(
        index
        for index, (command, _kwargs) in enumerate(calls)
        if command[-2:] == ["recovery-key", "init"]
    )
    watchdog_index = next(
        index
        for index, (command, _kwargs) in enumerate(calls)
        if command == ["systemctl", "start", "robopark-watchdog.service"]
    )
    assert recovery_key_index < watchdog_index
    assert tuna_index < doctor_index
    assert calls[doctor_index][1]["check"] is False


def test_publish_rejects_missing_host_health_projection(tmp_path: Path):
    root = tmp_path / "host"
    release = root / "opt/robopark/releases/0.2.0-rc.8"
    (release / "deploy/installer/lib").mkdir(parents=True)
    (release / "deploy/installer/lib/install-services.py").write_text("")
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.release = release
    runtime.etc = root / "etc/robopark"
    runtime.tuna = TunaConfiguration()
    runtime._run = lambda _command, **_kwargs: None

    with pytest.raises(RuntimeError, match="host_health_projection_missing"):
        runtime.publish()


def test_publish_rejects_stale_host_health_projection(tmp_path: Path):
    root = tmp_path / "host"
    release = root / "opt/robopark/releases/0.2.0-rc.8"
    (release / "deploy/installer/lib").mkdir(parents=True)
    (release / "deploy/installer/lib/install-services.py").write_text("")
    projection = root / "var/lib/robopark/api-ops/host-health.json"
    projection.parent.mkdir(parents=True)
    projection.write_text(json.dumps({
        "services_checked_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "services": {name: "unknown" for name in ("docker", "tuna", "internet", "wifi")},
    }))
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.release = release
    runtime.etc = root / "etc/robopark"
    runtime.tuna = TunaConfiguration()
    runtime._run = lambda _command, **_kwargs: None

    with pytest.raises(RuntimeError, match="host_health_projection_missing"):
        runtime.publish()


def test_publish_rejects_nonregular_host_health_projection(tmp_path: Path):
    root = tmp_path / "host"
    release = root / "opt/robopark/releases/0.2.0-rc.8"
    (release / "deploy/installer/lib").mkdir(parents=True)
    (release / "deploy/installer/lib/install-services.py").write_text("")
    projection = root / "var/lib/robopark/api-ops/host-health.json"
    projection.parent.mkdir(parents=True)
    os.mkfifo(projection)
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.release = release
    runtime.etc = root / "etc/robopark"
    runtime.tuna = TunaConfiguration()
    runtime._run = lambda _command, **_kwargs: None

    with pytest.raises(RuntimeError, match="host_health_projection_missing"):
        runtime.publish()


def test_publish_rejects_projection_without_service_states(tmp_path: Path):
    root = tmp_path / "host"
    release = root / "opt/robopark/releases/0.2.0-rc.8"
    (release / "deploy/installer/lib").mkdir(parents=True)
    (release / "deploy/installer/lib/install-services.py").write_text("")
    runtime = object.__new__(HostInstallRuntime)
    runtime.root = root
    runtime.release = release
    runtime.etc = root / "etc/robopark"
    runtime.tuna = TunaConfiguration()

    def run(command, **_kwargs):
        if command == ["systemctl", "start", "robopark-doctor.service"]:
            projection = root / "var/lib/robopark/api-ops/host-health.json"
            projection.parent.mkdir(parents=True)
            projection.write_text(json.dumps({"services_checked_at": datetime.now(UTC).isoformat()}))

    runtime._run = run
    with pytest.raises(RuntimeError, match="host_health_projection_missing"):
        runtime.publish()
