from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from robopark_ota import verify_ota
from scripts.build_ota import (
    BuildError,
    build_ota,
    ensure_clean_tracked_tree,
    include_source_path,
    validate_output_directory,
)

ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def test_two_builds_are_byte_identical(tmp_path: Path):
    first = build_ota(ROOT, tmp_path / "one", git_sha="a" * 40)
    second = build_ota(ROOT, tmp_path / "two", git_sha="a" * 40)

    assert first.read_bytes() == second.read_bytes()
    assert _sha256(first) == _sha256(second)


def test_built_ota_is_self_executable_and_independently_verifiable(tmp_path: Path):
    artifact = build_ota(ROOT, tmp_path, git_sha="b" * 40)

    completed = subprocess.run(
        [sys.executable, str(artifact), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    verified = verify_ota(artifact)

    assert completed.returncode == 0, completed.stderr
    assert "Чистая установка" in completed.stdout
    assert verified.sha256 == _sha256(artifact)
    assert verified.manifest.app_version == (ROOT / "VERSION").read_text().strip()


def test_manifest_inventory_exactly_matches_regular_members(tmp_path: Path):
    artifact = build_ota(ROOT, tmp_path, git_sha="c" * 40)

    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))

    assert names == sorted(names)
    assert {row["path"] for row in manifest["files"]} == set(names) - {"manifest.json"}
    assert manifest["requirements"] == {
        "architectures": ["aarch64", "x86_64"],
        "memory_profiles_mb": [8192, 32768, 65536],
        "python": ">=3.10",
        "systems": ["armbian", "ubuntu"],
    }


def test_manifest_is_readable_by_installed_browser_without_decompression(tmp_path: Path):
    artifact = build_ota(ROOT, tmp_path, git_sha="c" * 40)

    with zipfile.ZipFile(artifact) as archive:
        manifest = archive.getinfo("manifest.json")
        assert manifest.compress_type == zipfile.ZIP_STORED
        assert manifest.compress_size == manifest.file_size
        assert all(
            item.compress_type == zipfile.ZIP_DEFLATED
            for item in archive.infolist() if item.filename != "manifest.json"
        )


@pytest.mark.parametrize(
    "forbidden",
    [
        ".git/",
        ".pnpm-store/",
        "output/",
        "/e2e/",
        "/e2e-production/",
        "__pycache__/",
        ".pyc",
        ".log",
        ".sig",
        ".pem",
        "release-public-key",
        "robopark-release-",
    ],
)
def test_artifact_excludes_runtime_cache_keys_and_old_artifacts(tmp_path: Path, forbidden: str):
    artifact = build_ota(ROOT, tmp_path, git_sha="d" * 40)
    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()
    assert not any(forbidden in name for name in names)


def test_artifact_excludes_runtime_environment_files_but_keeps_examples(tmp_path: Path):
    artifact = build_ota(ROOT, tmp_path, git_sha="e" * 40)
    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()

    assert not any(Path(name).name == ".env" for name in names)
    assert "release/deploy/host.env.example" in names


def test_source_filter_distinguishes_secret_env_from_public_example():
    assert include_source_path(Path("deploy/.env")) is False
    assert include_source_path(Path("deploy/host.env.example")) is True


@pytest.mark.parametrize(
    "relative",
    [
        "apps/web/e2e/app-shell.spec.ts",
        "apps/web/e2e/operational/interface-visual-acceptance.spec.ts-snapshots/classic-work-light-1440.png",
        "apps/web/e2e-production/pwa-production.spec.ts",
    ],
)
def test_ota_source_filter_omits_browser_acceptance_files(relative: str):
    assert include_source_path(Path(relative)) is False


@pytest.mark.parametrize(
    "relative",
    [
        "apps/web/src/pwa/offlineDb.ts",
        "apps/web/src/domains/assistant/AssistantPage.tsx",
        "apps/web/public/manifest.webmanifest",
        "apps/api/knowledge/repair-v1/seed.jsonl",
    ],
)
def test_ota_source_filter_retains_runtime_and_knowledge(relative: str):
    assert include_source_path(Path(relative)) is True


def test_output_directory_must_be_absolute_and_outside_repository(tmp_path: Path):
    with pytest.raises(BuildError, match="output_directory_must_be_absolute"):
        validate_output_directory(ROOT, Path("relative"))
    with pytest.raises(BuildError, match="output_directory_inside_repository"):
        validate_output_directory(ROOT, ROOT / "output")

    outside = validate_output_directory(ROOT, tmp_path)
    assert outside == tmp_path.resolve()


def test_cleanliness_check_ignores_untracked_but_rejects_modified_tracked(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "tracked.txt").write_text("one")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=OTA Test",
            "-c",
            "user.email=ota@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    (repository / "untracked.cache").write_text("ignored by release cleanliness")
    ensure_clean_tracked_tree(repository)

    (repository / "tracked.txt").write_text("two")
    with pytest.raises(BuildError, match="tracked_tree_is_dirty"):
        ensure_clean_tracked_tree(repository)
