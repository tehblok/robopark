from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
from robopark_ota import verify_ota

from scripts import build_agx_release as builder
from scripts.build_ota import BuildError
from scripts.build_workspace_install import _INSTALL_FILES, workspace_source_files

ROOT = Path(__file__).resolve().parents[2]


def _commit(repository: Path) -> None:
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "add", "--all"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Robopark Test",
            "-c",
            "user.email=robopark-test@example.invalid",
            "commit",
            "-qm",
            "stable fixture",
        ],
        cwd=repository,
        check=True,
    )


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _repository(tmp_path: Path, *, version: str = "1.2.3") -> Path:
    repository = tmp_path / "repository"
    repository.mkdir(parents=True)
    (repository / "VERSION").write_text(version + "\n", encoding="utf-8")
    (repository / "README.md").write_text("Robopark stable\n", encoding="utf-8")
    (repository / "apps/api/pyproject.toml").parent.mkdir(parents=True)
    (repository / "apps/api/pyproject.toml").write_text(
        f'[project]\nname = "fixture"\nversion = "{version}"\n', encoding="utf-8"
    )

    required = [
        Path("deploy/release-metadata.json"),
        Path("deploy/ota/__main__.py"),
        Path("deploy/ota/README.installed.md"),
        Path("deploy/host/robopark_host/storage_layout.py"),
        Path("deploy/host/robopark_host/storage_setup.py"),
        Path("deploy/host/robopark_host/storage_watchdog.py"),
    ]
    required.extend(
        path.relative_to(ROOT)
        for path in sorted((ROOT / "deploy/ota/robopark_ota").glob("*.py"))
    )
    required.extend(relative for _name, relative in _INSTALL_FILES)
    for relative in dict.fromkeys(required):
        _copy(ROOT / relative, repository / relative)

    private_files = {
        "deploy/support/robopark-install-preset.enc": b"personal preset",
        "deploy/support/robopark_support_ed25519.age": b"private key",
        "apps/api/src/credentials.json": b'{"token":"secret"}',
        "deploy/tuna.env": b"TUNA_TOKEN=secret\n",
    }
    for name, data in private_files.items():
        target = repository / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _commit(repository)
    return repository


def test_public_stable_release_preserves_commit_and_excludes_private_data(tmp_path):
    repository = _repository(tmp_path)
    output = tmp_path / "output"

    result = builder.build_agx_release(repository, output)
    verified = verify_ota(result.ota)
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert result.version == "1.2.3"
    assert result.git_sha == git_sha == verified.manifest.git_sha
    assert verified.manifest.app_version == "1.2.3"
    assert result.ota.name == "robopark-1.2.3.ota"
    assert result.install_archive.name == "robopark-1.2.3-install.tar.gz"

    with zipfile.ZipFile(result.ota) as ota:
        names = set(ota.namelist())
        assert ota.read("release/VERSION") == b"1.2.3\n"
        assert b'version = "1.2.3"' in ota.read("release/apps/api/pyproject.toml")
    forbidden = {
        "release/deploy/support/robopark-install-preset.enc",
        "release/deploy/support/robopark_support_ed25519.age",
        "release/apps/api/src/credentials.json",
        "release/deploy/tuna.env",
    }
    assert names.isdisjoint(forbidden)

    with tarfile.open(result.install_archive, "r:gz") as package:
        archive_names = set(package.getnames())
        source = json.load(package.extractfile("SOURCE.json"))
        embedded_ota = package.extractfile(result.ota.name).read()
        checksums = package.extractfile("SHA256SUMS").read().decode()
        install_hashes = {
            name: hashlib.sha256(package.extractfile(name).read()).hexdigest()
            for name, _relative in _INSTALL_FILES
        }
    assert "nvidia-bootfw-guard.sh" in archive_names
    assert "SOURCE-SNAPSHOT.json" not in archive_names
    assert embedded_ota == result.ota.read_bytes()
    assert source == {
        "schema": 1,
        "git_sha": git_sha,
        "version": "1.2.3",
        "source_sha256": result.source_sha256,
        "install_file_sha256": install_hashes,
    }
    assert "workspace" not in json.dumps(source).lower()
    assert hashlib.sha256(embedded_ota).hexdigest() in checksums
    assert result.source_sha256 == builder._source_digest(
        repository, workspace_source_files(repository)
    )


def test_release_is_reproducible_and_never_overwrites_different_public_assets(
    tmp_path,
):
    repository = _repository(tmp_path)
    first = builder.build_agx_release(repository, tmp_path / "one")
    second = builder.build_agx_release(repository, tmp_path / "two")
    assert first.ota.read_bytes() == second.ota.read_bytes()
    assert first.install_archive.read_bytes() == second.install_archive.read_bytes()

    first.ota.write_bytes(b"different public asset")
    with pytest.raises(BuildError, match="public_asset_collision"):
        builder.build_agx_release(repository, first.ota.parent)
    assert first.ota.read_bytes() == b"different public asset"


def test_release_rejects_dirty_tracked_tree_and_prerelease(tmp_path):
    repository = _repository(tmp_path)
    (repository / "README.md").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(BuildError, match="tracked_tree_is_dirty"):
        builder.build_agx_release(repository, tmp_path / "dirty-output")

    repository = _repository(tmp_path / "prerelease", version="1.2.3-rc.1")
    with pytest.raises(BuildError, match="stable_release_required"):
        builder.build_agx_release(repository, tmp_path / "rc-output")


def test_release_rejects_source_change_during_build(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    original = builder.build_ota

    def changing_build_ota(*args, **kwargs):
        result = original(*args, **kwargs)
        (repository / "README.md").write_text("changed during build\n", encoding="utf-8")
        return result

    monkeypatch.setattr(builder, "build_ota", changing_build_ota)
    with pytest.raises(BuildError, match="source_changed_during_build"):
        builder.build_agx_release(repository, tmp_path / "output")
    assert not (tmp_path / "output").exists()
