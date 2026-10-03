import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


def test_prepared_mounts_are_allowed_only_when_empty(tmp_path, monkeypatch):
    from robopark_ota import host_install, storage

    runtime = object.__new__(host_install.HostInstallRuntime)
    runtime.root = tmp_path
    opt = tmp_path / "opt/robopark"
    opt.mkdir(parents=True)
    monkeypatch.setattr(
        storage,
        "require_storage",
        lambda *_args, **_kw: {"state": "ready", "mode": "emmc-nvme-data"},
    )
    runtime._ensure_empty_robopark_paths()
    (opt / "existing-file").write_text("keep")
    with pytest.raises(RuntimeError, match="clean_install_requires_empty_host"):
        runtime._ensure_empty_robopark_paths()
    assert (opt / "existing-file").read_text() == "keep"


def test_empty_legacy_directories_are_still_not_adopted(tmp_path):
    from robopark_ota.host_install import HostInstallRuntime

    runtime = object.__new__(HostInstallRuntime)
    runtime.root = tmp_path
    (tmp_path / "opt/robopark").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="clean_install_requires_empty_host"):
        runtime._ensure_empty_robopark_paths()


def test_install_guard_failure_precedes_docker_or_release_writes(tmp_path, monkeypatch):
    from robopark_ota import host_install, storage

    runtime = object.__new__(host_install.HostInstallRuntime)
    runtime.root = tmp_path
    runtime._run = lambda *_args, **_kw: pytest.fail("must not run Docker")
    monkeypatch.setattr(
        storage,
        "require_storage",
        lambda *_args, **_kw: (_ for _ in ()).throw(
            RuntimeError("storage_mount_missing")
        ),
    )
    with pytest.raises(RuntimeError, match="storage_mount_missing"):
        runtime.ensure_empty_host()
    assert not (tmp_path / "opt").exists()


def test_remove_clears_owned_contents_but_preserves_managed_mount_root(
    tmp_path, monkeypatch
):
    from robopark_ota import remove, storage

    opt = tmp_path / "opt/robopark"
    opt.mkdir(parents=True)
    (opt / "release-file").write_text("owned")
    plan = remove.RemovalPlan.for_root(tmp_path)
    monkeypatch.setattr(
        storage,
        "require_storage",
        lambda *_args, **_kw: {"state": "ready", "mode": "emmc-nvme-data"},
    )
    monkeypatch.setattr(storage, "validate_removal_mounts", lambda *_args: None)

    class Docker:
        def remove_containers(self, _names):
            pass

        def remove_volumes(self, _names):
            pass

        def remove_networks(self, _names):
            pass

        def remove_images(self, _names):
            pass

    remove.remove_owned_installation(plan, Docker(), remove.DockerTargets.empty())
    assert opt.is_dir()
    assert list(opt.iterdir()) == []


def test_archive_bundles_independent_storage_bootstrap(tmp_path):
    from scripts.build_ota import ROOT, build_ota

    artifact = build_ota(ROOT, tmp_path, git_sha="f" * 40)
    with zipfile.ZipFile(artifact) as archive:
        assert "robopark_storage/storage_layout.py" in archive.namelist()
        assert "robopark_storage/storage_setup.py" in archive.namelist()
        assert "robopark_storage/storage_watchdog.py" in archive.namelist()
    result = subprocess.run(
        [sys.executable, "-I", str(artifact), "storage", "check"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "unmanaged"


def test_remove_refuses_missing_storage_before_docker_socket_activation(
    tmp_path, monkeypatch
):
    from robopark_ota import cli, storage

    monkeypatch.setattr(
        storage,
        "require_storage",
        lambda *_args, **_kw: (_ for _ in ()).throw(
            storage.StorageError("storage_mount_missing")
        ),
    )
    monkeypatch.setattr(
        cli.DockerCli,
        "discover_owned",
        lambda _self: pytest.fail("must not activate Docker"),
    )
    with pytest.raises(storage.StorageError, match="storage_mount_missing"):
        cli._remove(tmp_path)


def test_interactive_storage_failure_has_actionable_message(
    tmp_path, monkeypatch, capsys
):
    from robopark_ota import cli, storage

    monkeypatch.setattr(cli, "_bundle_path", lambda: tmp_path / "bundle.ota")
    monkeypatch.setattr(
        cli,
        "run_menu",
        lambda **_kw: (_ for _ in ()).throw(
            storage.StorageError("storage_daemon_running")
        ),
    )
    assert cli.run_interactive() == 1
    assert "Docker" in capsys.readouterr().err


def test_install_rejects_unsupported_os_before_storage_mutation(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from robopark_ota import cli, storage

    runtime = SimpleNamespace(
        verified=SimpleNamespace(manifest=SimpleNamespace(requirements=None))
    )
    monkeypatch.setattr(cli, "HostInstallRuntime", lambda *_args, **_kw: runtime)
    monkeypatch.setattr(cli, "clean_install_lock", lambda _root: nullcontext())
    monkeypatch.setattr(
        cli,
        "validate_host_platform",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("unsupported_os")),
    )
    monkeypatch.setattr(
        storage,
        "choose_install_storage",
        lambda _root: pytest.fail("must reject before storage mutation"),
    )
    with pytest.raises(RuntimeError, match="unsupported_os"):
        cli._clean_install(Path("/bundle.ota"), Path("/"))


def test_install_reconciles_ready_manifest_after_interrupted_timer_enable(
    tmp_path, monkeypatch
):
    from robopark_ota import storage

    layout = {
        "state": "ready",
        "mode": "nvme-root",
        "device": "/dev/nvme0n1p1",
        "uuid": "disk-uuid",
    }
    monkeypatch.setattr(storage, "load_layout", lambda _root: layout)
    calls = []
    monkeypatch.setattr(
        storage, "prepare_storage", lambda *args, **kw: calls.append((args, kw))
    )
    storage.choose_install_storage(tmp_path)
    assert calls == [
        (
            ("nvme-root", "/dev/nvme0n1p1"),
            {"confirmation": "PREPARE nvme-root disk-uuid", "root": tmp_path},
        )
    ]
