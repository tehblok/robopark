import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from migration_graph import MigrationPolicy, plan_upgrade


def test_old_release_requires_declared_bridge():
    policy = MigrationPolicy.from_file(ROOT / "deploy/migration-policy.json")
    target = {
        "app_version": "0.2.0-rc.6",
        "migration_head": "0050_media_action_dependency",
    }
    plan = plan_upgrade("0.1.18", "0036_audit_remediation_state", target, policy)
    assert plan.releases == ("0.1.45", "0.2.0-rc.6")
    assert plan.recovery == "snapshot"


def test_recent_release_can_update_directly():
    policy = MigrationPolicy.from_file(ROOT / "deploy/migration-policy.json")
    target = {
        "app_version": "0.2.0-rc.6",
        "migration_head": "0050_media_action_dependency",
    }
    plan = plan_upgrade("0.2.0-rc.5", "0036_audit_remediation_state", target, policy)
    assert plan.releases == ("0.2.0-rc.6",)


def test_rc6_accepts_only_three_source_heads():
    policy = MigrationPolicy.from_file(ROOT / "deploy/migration-policy.json")
    assert policy.known_heads == {
        "0036_audit_remediation_state",
        "0037_claim_workflow_visibility",
        "0038_inventory_photo_cleanup",
    }
    target = {"app_version": "0.2.0-rc.6", "migration_head": "0050_media_action_dependency"}
    for head in policy.known_heads:
        assert plan_upgrade("0.2.0-rc.5", head, target, policy).releases == ("0.2.0-rc.6",)
    with pytest.raises(ValueError, match="migration_incompatible"):
        plan_upgrade("0.2.0-rc.5", "0035_schedules_and_push", target, policy)


def test_unknown_schema_is_rejected_before_mutation():
    policy = MigrationPolicy.from_file(ROOT / "deploy/migration-policy.json")
    with pytest.raises(ValueError, match="migration_incompatible"):
        plan_upgrade(
            "0.1.9",
            "unknown",
            {
                "app_version": "0.2.0-rc.6",
                "migration_head": "0050_media_action_dependency",
            },
            policy,
        )


def test_host_admission_requires_declared_bridge_before_mutation():
    from robopark_host.release import ReleaseError, check_compatibility

    candidate = {
        "app_version": "0.2.0-rc.1",
        "migration_head": "0036_audit_remediation_state",
        "migration_compatibility": {
            "from_heads": ["0022_tracker_collaboration"],
            "reversible": True,
        },
        "min_installer_version": "0",
        "required_capabilities": [],
        "upgrade_policy": {
            "mode": "graph",
            "bridge_before": "0.1.45",
            "bridge_version": "0.1.45",
            "reversible": False,
            "recovery": "snapshot",
        },
        "files": {
            name: {}
            for name in (
                "deploy/Dockerfile.api-tests",
                "apps/api/Dockerfile",
                "apps/api/uv.lock",
                "apps/api/pyproject.toml",
                "apps/web/Dockerfile",
                "apps/web/package-lock.json",
                "apps/web/package.json",
                "scripts/verify.sh",
            )
        },
    }
    with pytest.raises(ReleaseError, match="bridge_required"):
        check_compatibility(
            candidate,
            {
                "app_version": "0.1.18",
                "migration_head": "0022_tracker_collaboration",
            },
        )
    check_compatibility(
        candidate,
        {
            "app_version": "0.1.45",
            "migration_head": "0022_tracker_collaboration",
        },
    )
