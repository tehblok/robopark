from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import tomllib
from robopark_ota import verify_ota

from scripts.build_ota import BuildError
from scripts.build_workspace_install import (
    build_workspace_install_archive,
    snapshot_version,
    workspace_source_files,
)

ROOT = Path(__file__).resolve().parents[2]


def test_workspace_snapshot_includes_new_runtime_modules_without_local_data(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    paths = {
        "VERSION": "0.2.0-rc.8\n",
        "README.md": "Robopark\n",
        "apps/api/src/app.py": "value = 1\n",
        "apps/web/src/domains/map/MapPage.tsx": "export const newScreen = true\n",
        "apps/web/src/design-system/data/EntityRow.tsx": "export const row = true\n",
        "deploy/host/robopark_host/storage_inventory.py": "value = 2\n",
        "apps/api/data/private.db": "do not package",
        "apps/api/src/credentials.json": "do not package",
        "apps/api/tests/test_private.py": "do not package",
        "deploy/tuna.env": "do not package",
    }
    for name, content in paths.items():
        file = repository / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
    subprocess.run(
        ["git", "-C", str(repository), "add", "VERSION", "README.md", "apps/api/src/app.py", "apps/web/src/design-system/data/EntityRow.tsx", "deploy/tuna.env"],
        check=True,
    )

    selected = {path.as_posix() for path in workspace_source_files(repository)}

    assert selected == {
        "VERSION", "README.md", "apps/api/src/app.py",
        "apps/web/src/design-system/data/EntityRow.tsx",
        "apps/web/src/domains/map/MapPage.tsx", "deploy/host/robopark_host/storage_inventory.py",
    }


def test_unreviewed_new_runtime_file_blocks_snapshot(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    unexpected = repository / "apps/api/src/rogue.py"
    unexpected.parent.mkdir(parents=True)
    unexpected.write_text("print('unreviewed')\n")

    with pytest.raises(BuildError, match="unreviewed_untracked_source"):
        workspace_source_files(repository)


def test_workspace_snapshot_includes_optional_bot_and_primary_tracker_bridge():
    selected = {path.as_posix() for path in workspace_source_files(ROOT)}

    assert {
        "apps/web/src/design-system/data/EntityRow.tsx",
        "apps/web/src/design-system/data/MetricCard.tsx",
        "apps/web/scripts/install-dependencies.sh",
        "apps/bot/Dockerfile",
        "apps/bot/requirements.txt",
        "apps/bot/entrypoint.py",
        "apps/bot/legacy/app/tracker_api.py",
        "apps/api/src/robopark_api/routers/internal_bot.py",
        "apps/api/src/robopark_api/routers/admin_bot.py",
        "apps/api/src/robopark_api/routers/admin_bot_config.py",
        "apps/api/src/robopark_api/services/bot_shared_settings.py",
        "apps/web/src/domains/system/BotSettingsPanel.tsx",
        "apps/web/src/domains/system/BotConfigPanel.tsx",
        "apps/web/src/domains/system/botConfigApi.ts",
        "deploy/host/robopark-bot-runtime",
        "deploy/systemd/robopark-bot.service",
        "deploy/systemd/robopark-bot.path",
    } <= selected
    assert not any(path.startswith("apps/bot/legacy/data/") for path in selected)
    assert "apps/web/scripts/install-dependencies.test.mjs" not in selected
    assert not any(path.endswith(".test.mjs") for path in selected)
    assert not any(path.startswith("apps/web/e2e-production/") for path in selected)


def test_snapshot_version_is_newer_than_stable_or_rc_base():
    assert snapshot_version("0.2.0", "f" * 64).startswith("0.2.1-rc.1.dev")
    assert snapshot_version("0.2.0-rc.8+build.1", "f" * 64).startswith("0.2.0-rc.9.dev")
    assert snapshot_version("0.2.0-rc.21.dev18446744073709551615", "0" * 64) == "0.2.0-rc.22.dev0"


def test_workspace_archive_contains_current_untracked_runtime_and_no_tests(tmp_path: Path):
    archive = build_workspace_install_archive(ROOT, tmp_path / "out")

    with tarfile.open(archive, "r:gz") as package:
        names = package.getnames()
        assert set(names) == {"INSTALL.sh", "README-RU.md", "SOURCE-SNAPSHOT.json", "SHA256SUMS", next(name for name in names if name.endswith(".ota"))}
        ota_name = next(name for name in names if name.endswith(".ota"))
        ota_data = package.extractfile(ota_name).read()
        source = json.load(package.extractfile("SOURCE-SNAPSHOT.json"))
        install_data = package.extractfile("INSTALL.sh").read()
        readme_data = package.extractfile("README-RU.md").read()
        checksum = package.extractfile("SHA256SUMS").read().decode()
    embedded = tmp_path / ota_name
    embedded.write_bytes(ota_data)
    verified = verify_ota(embedded)

    with zipfile.ZipFile(io.BytesIO(ota_data)) as ota:
        release_paths = set(ota.namelist())
        api_version = tomllib.loads(ota.read("release/apps/api/pyproject.toml").decode())["project"]["version"]
        api_lock = tomllib.loads(ota.read("release/apps/api/uv.lock").decode())
        api_lock_version = next(item["version"] for item in api_lock["package"] if item["name"] == "robopark-api")
        web_version = json.loads(ota.read("release/apps/web/package.json"))["version"]
        web_lock = json.loads(ota.read("release/apps/web/package-lock.json"))
        context = ota.read("release/apps/api/src/robopark_api/services/ops/context.py").decode()
    assert "release/apps/api/src/robopark_api/services/sla_clock.py" in release_paths
    assert "release/apps/web/src/shared/auth/offlineIdentity.ts" in release_paths
    assert not any(p.startswith("release/apps/web/src/domains/map/") for p in release_paths)
    assert "release/apps/web/src/design-system/data/EntityRow.tsx" in release_paths
    assert "release/apps/web/src/design-system/data/MetricCard.tsx" in release_paths
    assert not any("/tests/" in path or "/e2e/" in path for path in release_paths)
    assert not any("/data/robopark.db" in path or ".pnpm-store" in path for path in release_paths)
    assert verified.manifest.app_version == source["version"]
    assert api_version == api_lock_version == web_version == source["version"]
    assert web_lock["version"] == web_lock["packages"][""]["version"] == source["version"]
    assert f'APP_VERSION = "{source["version"]}"' in context
    assert source["kind"] == "uncommitted-worktree"
    assert source["source_sha256"] in verified.manifest.changes[-1]
    install_hashes = {
        "INSTALL.sh": hashlib.sha256(install_data).hexdigest(),
        "README-RU.md": hashlib.sha256(readme_data).hexdigest(),
    }
    identity = hashlib.sha256(
        b"robopark-install-archive-v1\0"
        + bytes.fromhex(source["source_sha256"])
        + bytes.fromhex(source["base_git_sha"])
        + bytes.fromhex(install_hashes["INSTALL.sh"])
        + bytes.fromhex(install_hashes["README-RU.md"])
    ).hexdigest()
    assert source["install_file_sha256"] == install_hashes
    assert source["archive_identity_sha256"] == identity
    assert source["version"] == snapshot_version((ROOT / "VERSION").read_text().strip(), identity)
    assert ota_name in checksum
    assert "INSTALL.sh" in checksum
    assert "README-RU.md" in checksum


def test_parallel_archives_do_not_share_temporary_output(tmp_path: Path):
    output = tmp_path / "out"
    with ThreadPoolExecutor(max_workers=4) as pool:
        archives = list(pool.map(lambda _: build_workspace_install_archive(ROOT, output), range(4)))
    assert len(set(archives)) == 1
    assert archives[0].is_file()


def test_archive_is_reproducible_across_output_directories(tmp_path: Path):
    first = build_workspace_install_archive(ROOT, tmp_path / "one")
    second = build_workspace_install_archive(ROOT, tmp_path / "two")
    assert first.read_bytes() == second.read_bytes()


def test_workspace_archive_identity_changes_with_base_git_sha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts import build_workspace_install as builder

    monkeypatch.setattr(builder, "_base_sha", lambda _repository: "1" * 40)
    first = build_workspace_install_archive(ROOT, tmp_path / "out")
    first_data = first.read_bytes()
    monkeypatch.setattr(builder, "_base_sha", lambda _repository: "2" * 40)
    second = build_workspace_install_archive(ROOT, tmp_path / "out")

    assert first != second
    assert first.read_bytes() == first_data
    assert second.read_bytes() != first_data


def test_workspace_archive_never_overwrites_different_bytes_under_same_name(tmp_path: Path):
    output = tmp_path / "out"
    archive = build_workspace_install_archive(ROOT, output)
    archive.write_bytes(b"existing different artifact")

    with pytest.raises(BuildError, match="archive_identity_collision"):
        build_workspace_install_archive(ROOT, output)
    assert archive.read_bytes() == b"existing different artifact"


def test_installer_change_during_build_blocks_archive_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    installer = (ROOT / "deploy/install-archive/INSTALL.sh").resolve()
    original_read_bytes = Path.read_bytes
    reads = 0

    def changing_read_bytes(path: Path) -> bytes:
        nonlocal reads
        data = original_read_bytes(path)
        if path.resolve() == installer:
            reads += 1
            if reads > 1:
                return data + b"\n# changed during build\n"
        return data

    monkeypatch.setattr(Path, "read_bytes", changing_read_bytes)

    with pytest.raises(BuildError, match="workspace_changed_during_build"):
        build_workspace_install_archive(ROOT, tmp_path / "out")
