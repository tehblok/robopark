from __future__ import annotations

from pathlib import Path

import pytest
from robopark_ota.host_install import extract_release, write_host_configuration
from robopark_ota.install import CleanInstallCoordinator
from robopark_ota.remove import DockerTargets, RemovalPlan, remove_owned_installation
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
    bundle = build_ota(repository, tmp_path / "artifact", git_sha="f" * 40)
    root = tmp_path / "host"

    target = extract_release(bundle, root=root)

    assert target == root / "opt/robopark/releases/0.2.0-rc.6"
    assert (target / "VERSION").read_text().strip() == "0.2.0-rc.6"
    assert not (target / "manifest.json").exists()
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
