import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from migration_graph import MigrationPolicy, plan_upgrade


def test_old_release_requires_declared_bridge():
    policy = MigrationPolicy.from_file(ROOT / "deploy/migration-policy.json")
    target = {"app_version": "0.2.0-rc.1", "migration_head": "0036_audit_remediation_state"}
    plan = plan_upgrade("0.1.18", "0022_tracker_collaboration", target, policy)
    assert plan.releases == ("0.1.45", "0.2.0-rc.1")
    assert plan.recovery == "snapshot"


def test_recent_release_can_update_directly():
    policy = MigrationPolicy.from_file(ROOT / "deploy/migration-policy.json")
    target = {"app_version": "0.2.0-rc.1", "migration_head": "0036_audit_remediation_state"}
    plan = plan_upgrade("0.1.45", "0036_audit_remediation_state", target, policy)
    assert plan.releases == ("0.2.0-rc.1",)


def test_unknown_schema_is_rejected_before_mutation():
    policy = MigrationPolicy.from_file(ROOT / "deploy/migration-policy.json")
    with pytest.raises(ValueError, match="migration_incompatible"):
        plan_upgrade(
            "0.1.9",
            "unknown",
            {"app_version": "0.2.0-rc.1", "migration_head": "0036_audit_remediation_state"},
            policy,
        )
