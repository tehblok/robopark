"""Compatibility policy for the clean-install base and its future successors."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from migration_graph import MigrationPolicy, plan_upgrade


def test_current_release_declares_foundation_and_same_head_upgrade():
    policy = MigrationPolicy.from_file(ROOT / "deploy/migration-policy.json")
    metadata = json.loads((ROOT / "deploy/release-metadata.json").read_text())
    compatibility = json.loads((ROOT / "docs/releases/compatibility.json").read_text())

    assert policy.target_head == metadata["migration_head"] == "0056_host_terminal"
    assert policy.known_heads == frozenset({"0055_media_upload_park", "0056_host_terminal"})
    assert metadata["migration_compatibility"]["from_heads"] == ["0055_media_upload_park", "0056_host_terminal"]
    assert metadata["compatible_from_versions"]
    assert all(version.startswith("0.2.0-rc.") for version in metadata["compatible_from_versions"])
    assert compatibility["known_heads"] == ["0055_media_upload_park", "0056_host_terminal"]
    for version in metadata["compatible_from_versions"]:
        plan = plan_upgrade(version, "0055_media_upload_park", {
            "app_version": compatibility["target_version"],
            "migration_head": policy.target_head,
        }, policy)
        assert plan.releases == (compatibility["target_version"],)
        assert plan.recovery == "snapshot"


@pytest.mark.parametrize(
    ("current_version", "current_head"),
    [
        ("0.2.0-rc.7", "0050_media_action_dependency"),
        ("0.2.0-rc.7", "unknown_schema"),
        ("0.2.0-rc.8", "0050_media_action_dependency"),
    ],
)
def test_current_base_rejects_unknown_schema_upgrade(current_version, current_head):
    policy = MigrationPolicy.from_file(ROOT / "deploy/migration-policy.json")
    target = {"app_version": "0.2.0-rc.8", "migration_head": policy.target_head}

    with pytest.raises(ValueError, match="migration_incompatible"):
        plan_upgrade(current_version, current_head, target, policy)


def test_future_release_can_explicitly_accept_this_base(tmp_path):
    path = tmp_path / "migration-policy.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "target_head": "0055_next_base_migration",
                "known_heads": ["0054_park_coordinates"],
                "bridge_before": "0.2.0-rc.8",
                "bridge_version": "0.2.0-rc.8",
                "reversible": False,
                "recovery": "snapshot",
            }
        )
    )
    policy = MigrationPolicy.from_file(path)
    target = {"app_version": "0.2.0-rc.9", "migration_head": policy.target_head}

    plan = plan_upgrade("0.2.0-rc.8", "0054_park_coordinates", target, policy)
    assert plan.releases == ("0.2.0-rc.9",)
    assert plan.recovery == "snapshot"


def test_rc21_same_head_has_an_explicit_rc22_upgrade_path():
    policy = MigrationPolicy.from_file(ROOT / "deploy/migration-policy.json")
    metadata = json.loads((ROOT / "deploy/release-metadata.json").read_text())
    version = "0.2.0-rc.21.dev13181400260723547202"
    assert version in metadata["compatible_from_versions"]
    plan = plan_upgrade(version, "0056_host_terminal", {"app_version": "0.2.0-rc.22", "migration_head": "0056_host_terminal"}, policy)
    assert plan.releases == ("0.2.0-rc.22",)
    assert plan.recovery == "snapshot"
