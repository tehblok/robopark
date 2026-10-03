"""Read-only checks for the current OTA declarations.

The retired signed release-zip test suite targeted scripts that no longer
exist. Building an OTA is intentionally outside these checks.
"""

import importlib.util
import json
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def test_declared_head_and_supported_sources_are_real_revisions():
    metadata = json.loads((ROOT / "deploy/release-metadata.json").read_text())
    policy = json.loads((ROOT / "deploy/migration-policy.json").read_text())
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    revisions = ScriptDirectory.from_config(config)

    assert (
        metadata["migration_head"]
        == policy["target_head"]
        == revisions.get_current_head()
    )
    assert metadata["migration_compatibility"]["reversible"] is policy["reversible"]
    sources = metadata["migration_compatibility"]["from_heads"]
    assert len(sources) == len(set(sources))
    assert set(sources) == set(policy["known_heads"])
    assert all(revisions.get_revision(source) is not None for source in sources)


def test_current_ota_declarations_have_no_legacy_packaging_dependency():
    metadata = json.loads((ROOT / "deploy/release-metadata.json").read_text())
    version = (ROOT / "VERSION").read_text().strip()
    assert version not in metadata["compatible_from_versions"]
    from scripts.robopark_version import ReleaseVersion
    sources = metadata["compatible_from_versions"]
    assert sources and len(sources) == len(set(sources))
    assert all(ReleaseVersion.parse(source).precedence < ReleaseVersion.parse(version).precedence for source in sources)
    assert metadata["requirements"]["systems"] == ["armbian", "ubuntu"]
    assert metadata["requirements"]["architectures"] == ["aarch64", "x86_64"]
    assert metadata["update_notes"].strip()
    assert (ROOT / "scripts/build_ota.py").is_file()
    assert (ROOT / "deploy/ota/robopark_ota/host_install.py").is_file()


def test_release_checker_accepts_the_current_declarations_without_packaging():
    path = ROOT / "scripts/check-release-migrations.py"
    spec = importlib.util.spec_from_file_location("release_migrations", path)
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert checker.check_release_migrations(ROOT) == []
