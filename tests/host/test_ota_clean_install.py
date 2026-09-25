from __future__ import annotations

import json
from pathlib import Path

import pytest
from robopark_ota.credentials import TunaConfiguration
from robopark_ota.host_install import (
    HostInstallRuntime,
    extract_release,
    prepare_host_layout,
    write_host_configuration,
)
from robopark_ota.install import CleanInstallCoordinator
from robopark_ota.remove import (
    DockerCli,
    DockerTargets,
    RemovalPlan,
    remove_owned_installation,
)

from scripts.build_ota import build_ota


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

    assert docker.discover_owned().containers == ("main", "candidate")


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
        remove_owned_installation(RemovalPlan.for_root(root), FakeDocker(), DockerTargets.empty())
    assert (outside / "keep").exists()


class FakeInstallRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def preflight(self) -> None:
        self.calls.append("preflight")

    def stop_and_remove(self) -> None:
        self.calls.append("remove")

    def extract_release(self) -> None:
        self.calls.append("extract")

    def configure(self) -> None:
        self.calls.append("configure")

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


def test_clean_install_has_no_backup_branch_and_publishes_only_after_smoke(tmp_path: Path):
    runtime = FakeInstallRuntime()
    secret = tmp_path / "seed.json"
    secret.write_text("secret")

    CleanInstallCoordinator(runtime).run(secret)

    assert runtime.calls == [
        "preflight",
        "remove",
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
    assert "CORS_ORIGINS=https://robopark.ru.tuna.am" in (
        root / "etc/robopark/host.env"
    ).read_text()
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
    runtime._run = lambda command, **kwargs: calls.append((command, kwargs))
    tuna_ready = []
    runtime._wait_tuna_ready = lambda: tuna_ready.append(True)

    runtime.publish()

    flattened = [" ".join(command) for command, _kwargs in calls]
    assert any("enable docker.service" in command for command in flattened)
    assert any("enable robopark.service" in command for command in flattened)
    assert any("enable robopark-commands.path" in command for command in flattened)
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
    assert tuna_index < doctor_index
    assert calls[doctor_index][1]["check"] is False
