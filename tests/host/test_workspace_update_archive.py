from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
from robopark_ota import verify_ota

from scripts import build_workspace_install as install
from scripts.build_ota import BuildError
from scripts.robopark_version import ReleaseVersion

ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_FUNCTIONS = "\ndef upgrade() -> None:\n    pass\n\ndef downgrade() -> None:\n    pass\n"


def _copy_buildable_repository(destination: Path) -> Path:
    paths = set(install.workspace_source_files(ROOT))
    paths.update({
        Path("deploy/install-archive/INSTALL.sh"),
        Path("deploy/install-archive/UPDATE.sh"),
        Path("docs/runbooks/local-update.md"),
        Path("docs/runbooks/system-operations.md"),
        Path("docs/runbooks/usb-clean-install.md"),
    })
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True)
    subprocess.run(["git", "add", "--all"], cwd=destination, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Robopark Tests",
            "-c", "user.email=tests@robopark.invalid",
            "commit", "-qm", "fixture",
        ],
        cwd=destination,
        check=True,
    )
    return destination


def test_workspace_packages_latest_offline_runtime():
    selected = {p.as_posix() for p in install.workspace_source_files(ROOT)}
    assert {"apps/web/src/shared/auth/offlineIdentity.ts", "apps/web/src/lib/useMinuteClock.ts"} <= selected
    assert not any(p.startswith("apps/web/src/domains/map/") for p in selected)


def test_local_update_archive_preserves_exact_compatibility_and_checksums(tmp_path):
    from scripts import build_workspace_update as update

    prior = install.build_workspace_install_archive(ROOT, tmp_path / "prior")
    target = update.build_workspace_update_archive(ROOT, tmp_path / "update", [prior])
    with tarfile.open(prior) as t:
        previous = json.load(t.extractfile("SOURCE-SNAPSHOT.json"))["version"]
    with tarfile.open(target) as t:
        data = {m.name: t.extractfile(m).read() for m in t.getmembers()}
    ota_name = next(n for n in data if n.endswith(".ota"))
    assert set(data) == {"UPDATE.sh", "README-RU.md", "SYSTEM-OPERATIONS-RU.md", "SOURCE-SNAPSHOT.json", "SHA256SUMS", ota_name}
    for line in data["SHA256SUMS"].decode().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256(data[name]).hexdigest() == digest
    ota_path = tmp_path / ota_name
    ota_path.write_bytes(data[ota_name])
    verified = verify_ota(ota_path)
    assert verified.manifest.compatible_from == (previous,)
    snapshot = json.loads(data["SOURCE-SNAPSHOT.json"])
    release_notes = json.loads((ROOT / "deploy/release-metadata.json").read_text())[
        "update_notes"
    ].strip()
    assert release_notes in " ".join(verified.manifest.changes[:-1])
    assert snapshot["source_sha256"] in verified.manifest.changes[-1]
    assert all(len(item) <= 500 for item in verified.manifest.changes)
    parsed_previous = ReleaseVersion.parse(previous)
    next_rc = int(parsed_previous.prerelease.split('.')[1]) + 1
    core = f"{parsed_previous.major}.{parsed_previous.minor}.{parsed_previous.patch}"
    assert verified.manifest.app_version.startswith(f"{core}-rc.{next_rc}.dev")
    assert "чистой установки" not in verified.manifest.changes[0]
    assert b'update' in data["UPDATE.sh"]
    with zipfile.ZipFile(io.BytesIO(data[ota_name])) as z:
        assert "release/apps/web/src/shared/auth/offlineIdentity.ts" in z.namelist()
        assert not any("/tests/" in n or "/node_modules/" in n or "/.env" in n for n in z.namelist())
    assert update.build_workspace_update_archive(ROOT, tmp_path / "again", [prior]).read_bytes() == target.read_bytes()
    following = update.build_workspace_update_archive(ROOT, tmp_path / "following", [target])
    with tarfile.open(following) as t:
        next_snapshot = json.load(t.extractfile("SOURCE-SNAPSHOT.json"))
    assert next_snapshot["version"].startswith(f"{core}-rc.{next_rc + 1}.dev")
    assert next_snapshot["compatible_from"] == [verified.manifest.app_version]


def test_update_archive_rejects_changed_prior_migrations(tmp_path):
    from scripts import build_workspace_update as update

    prior = install.build_workspace_install_archive(ROOT, tmp_path / "prior")
    files = install.workspace_source_files(ROOT)
    # Compare a real, verified prior package with a different candidate migration.
    candidate = tmp_path / "candidate"
    for p in files:
        destination = candidate / p
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / p).read_bytes())
    path = candidate / "apps/api/alembic/versions/0054_park_coordinates.py"
    path.write_text(path.read_text() + "\n# changed schema\n")
    with pytest.raises(BuildError, match="update_migrations_differ"):
        update.compatible_versions(candidate, [prior])


def test_update_requires_verified_prior_archive(tmp_path):
    from scripts import build_workspace_update as update

    with pytest.raises(BuildError, match="update_previous_archive_required"):
        update.compatible_versions(ROOT, [])


@pytest.mark.parametrize(
    "invalid",
    [None, "undeclared", "changed_old", "orphan", "wrong_parent", "duplicate_revision"],
)
def test_update_accepts_only_declared_append_only_migration_chain(tmp_path, invalid):
    from scripts import build_workspace_update as update

    prior = install.build_workspace_install_archive(ROOT, tmp_path / "prior")
    candidate = tmp_path / "candidate"
    migration_dir = candidate / "apps/api/alembic/versions"
    migration_dir.mkdir(parents=True)
    for original in (ROOT / "apps/api/alembic/versions").glob("*.py"):
        (migration_dir / original.name).write_bytes(original.read_bytes())
    metadata = json.loads((ROOT / "deploy/release-metadata.json").read_text())
    previous_head = metadata["migration_head"]
    target_revision = "0001" if invalid == "duplicate_revision" else "0056_test_additive"
    metadata["migration_head"] = target_revision
    metadata["migration_compatibility"]["from_heads"] = [] if invalid == "undeclared" else [previous_head]
    (candidate / "deploy").mkdir()
    (candidate / "deploy/release-metadata.json").write_text(json.dumps(metadata))
    parent = "unknown_parent" if invalid == "wrong_parent" else previous_head
    (migration_dir / "0056_test_additive.py").write_text(
        f"revision = {target_revision!r}\ndown_revision = {parent!r}\n"
        "branch_labels = None\ndepends_on = None\n"
        + _MIGRATION_FUNCTIONS
    )
    if invalid == "changed_old":
        original = next(migration_dir.glob("0001*.py"))
        original.write_text(original.read_text() + "\n# changed released migration\n")
    if invalid == "orphan":
        (migration_dir / "0057_orphan.py").write_text(
            f"revision = '0057_orphan'\ndown_revision = {previous_head!r}\nbranch_labels = None\ndepends_on = None\n"
            + _MIGRATION_FUNCTIONS
        )
    if invalid:
        with pytest.raises(BuildError, match="update_migrations_differ"):
            update.compatible_versions(candidate, [prior])
    else:
        with tarfile.open(prior) as package:
            previous = json.load(package.extractfile("SOURCE-SNAPSHOT.json"))["version"]
        assert update.compatible_versions(candidate, [prior]) == [previous]


def test_append_only_migration_rejects_any_released_revision_collision(tmp_path):
    from scripts import build_workspace_update as update

    migration = tmp_path / "0056_duplicate.py"
    migration.write_text(
        "revision = '0001'\n"
        "down_revision = '0055_media_upload_park'\n"
        "branch_labels = None\n"
        "depends_on = None\n"
        + _MIGRATION_FUNCTIONS
    )

    assert not update._append_only_migrations(
        tmp_path,
        {migration.name},
        "0055_media_upload_park",
        "0001",
        previous_revisions={"0001", "0055_media_upload_park"},
    )


@pytest.mark.parametrize(
    "hidden_assignment",
    [
        "if True:\n    down_revision = '0001'\n",
        "if True:\n    branch_labels = ('hidden',)\n",
        "if True:\n    depends_on = object()\n",
        "globals()['down_revision'] = '0001'\n",
        "exec(\"down_revision = '0001'\")\n",
        "@exec(\"down_revision = '0001'\")\ndef upgrade():\n    pass\n",
        "def upgrade(value=exec(\"down_revision = '0001'\")):\n    pass\n",
        "def upgrade() -> exec(\"down_revision = '0001'\"):\n    pass\n",
        "from malicious_helper import *\n",
    ],
)
def test_append_only_migration_rejects_nested_metadata_reassignment(
    tmp_path, hidden_assignment,
):
    from scripts import build_workspace_update as update

    migration = tmp_path / "0056_hidden.py"
    migration.write_text(
        "revision = '0056_hidden'\n"
        "down_revision = '0055_media_upload_park'\n"
        "branch_labels = None\n"
        "depends_on = None\n"
        f"{hidden_assignment}"
        + ("\ndef downgrade():\n    pass\n" if "def upgrade(" in hidden_assignment else _MIGRATION_FUNCTIONS)
    )

    assert not update._append_only_migrations(
        tmp_path,
        {migration.name},
        "0055_media_upload_park",
        "0056_hidden",
        previous_revisions={"0055_media_upload_park"},
    )


def test_append_only_migration_rejects_nonliteral_metadata(tmp_path):
    from scripts import build_workspace_update as update

    migration = tmp_path / "0056_nonliteral.py"
    migration.write_text(
        "revision = '0056_nonliteral'\n"
        "down_revision = '0055_media_upload_park'\n"
        "branch_labels = None\n"
        "depends_on = object()\n"
        + _MIGRATION_FUNCTIONS
    )

    assert not update._append_only_migrations(
        tmp_path,
        {migration.name},
        "0055_media_upload_park",
        "0056_nonliteral",
        previous_revisions={"0055_media_upload_park"},
    )


@pytest.mark.parametrize("functions", [
    "", "def upgrade():\n    pass\n", "def downgrade():\n    pass\n",
    _MIGRATION_FUNCTIONS + "\ndef upgrade():\n    pass\n",
    _MIGRATION_FUNCTIONS + "\ndef downgrade():\n    pass\n",
])
def test_append_only_migration_requires_one_upgrade_and_downgrade(tmp_path, functions):
    from scripts import build_workspace_update as update

    migration = tmp_path / "0056_functions.py"
    migration.write_text(
        "revision = '0056_functions'\ndown_revision = '0055_media_upload_park'\n"
        "branch_labels = None\ndepends_on = None\n" + functions
    )
    assert not update._append_only_migrations(
        tmp_path, {migration.name}, "0055_media_upload_park", "0056_functions",
        previous_revisions={"0055_media_upload_park"},
    )


def test_update_rejects_migration_mutated_after_compatibility_check(tmp_path, monkeypatch):
    from scripts import build_workspace_update as update

    candidate = _copy_buildable_repository(tmp_path / "candidate")
    prior = install.build_workspace_install_archive(candidate, tmp_path / "prior")
    original_compatibility = update._compatibility
    migration = candidate / "apps/api/alembic/versions/0001_create_users_sessions.py"

    def mutate_after_check(repository, archives):
        result = original_compatibility(repository, archives)
        migration.write_text(migration.read_text() + "\n# changed after validation\n")
        return result

    monkeypatch.setattr(update, "_compatibility", mutate_after_check)

    with pytest.raises(BuildError, match="workspace_changed_during_build"):
        update.build_workspace_update_archive(candidate, tmp_path / "update", [prior])


def test_stable_release_upgrades_snapshot_and_remains_updatable(tmp_path):
    from scripts import build_workspace_update as update

    prior = install.build_workspace_install_archive(ROOT, tmp_path / "prior")
    with tarfile.open(prior) as package:
        previous = ReleaseVersion.parse(json.load(package.extractfile("SOURCE-SNAPSHOT.json"))["version"])
    stable_version = f"{previous.major}.{previous.minor}.{previous.patch}"
    following_version = f"{previous.major}.{previous.minor}.{previous.patch + 1}"
    stable = update.build_workspace_update_archive(
        ROOT, tmp_path / "stable", [prior], target_version=stable_version
    )
    assert update.compatible_versions(ROOT, [stable]) == [stable_version]
    following = update.build_workspace_update_archive(
        ROOT, tmp_path / "following", [stable], target_version=following_version
    )
    with tarfile.open(following) as package:
        metadata = json.load(package.extractfile("SOURCE-SNAPSHOT.json"))
    assert metadata["version"] == following_version
    assert metadata["compatible_from"] == [stable_version]
    automatic = update.build_workspace_update_archive(ROOT, tmp_path / "automatic", [stable])
    with tarfile.open(automatic) as package:
        metadata = json.load(package.extractfile("SOURCE-SNAPSHOT.json"))
    assert metadata["version"].startswith(f"{following_version}-rc.1.dev")
    for invalid in (stable_version, "0.0.1", f"{stable_version}-rc.99", "bad-version"):
        with pytest.raises(BuildError):
            update.build_workspace_update_archive(
                ROOT, tmp_path / "rejected", [stable], target_version=invalid
            )
