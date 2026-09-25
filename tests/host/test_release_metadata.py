"""The public packer, not fixture-only manifest construction, declares migrations."""

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest
from test_packaging import ROOT, run
from test_packaging import packaging as packaging_factory

sys.path.insert(0, str(ROOT / "scripts"))

from release_policy import SupportPolicy, validate_manifest_policy


def test_actual_alembic_head_matches_release_declarations():
    checker_path = ROOT / "scripts/check-release-migrations.py"
    assert checker_path.is_file(), "release migration checker is missing"
    spec = importlib.util.spec_from_file_location("release_migrations", checker_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.check_release_migrations(ROOT) == []
    metadata = json.loads((ROOT / "deploy/release-metadata.json").read_text())
    policy = json.loads((ROOT / "deploy/migration-policy.json").read_text())
    assert metadata["migration_head"] == policy["target_head"] == "0048_host_operation_status"
    assert metadata["migration_compatibility"]["from_heads"] == [
        "0036_audit_remediation_state",
        "0037_claim_workflow_visibility",
        "0038_inventory_photo_cleanup",
    ]


def test_release_migration_checker_rejects_declared_head_drift(tmp_path: Path):
    checker_path = ROOT / "scripts/check-release-migrations.py"
    assert checker_path.is_file(), "release migration checker is missing"
    spec = importlib.util.spec_from_file_location("release_migrations", checker_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    api = tmp_path / "apps/api"
    versions = api / "alembic/versions"
    versions.mkdir(parents=True)
    (api / "alembic.ini").write_text("[alembic]\nscript_location = alembic\n")
    (versions / "head.py").write_text("revision = 'head'\ndown_revision = None\n")
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "release-metadata.json").write_text(json.dumps({"migration_head": "old"}))
    (deploy / "migration-policy.json").write_text(json.dumps({"target_head": "head"}))

    findings = module.check_release_migrations(tmp_path)
    assert findings == ["actual=head metadata=old policy=head"]


def test_release_migration_checker_rejects_synchronized_non_rc6_head(tmp_path: Path):
    checker_path = ROOT / "scripts/check-release-migrations.py"
    spec = importlib.util.spec_from_file_location("release_migrations", checker_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    api = tmp_path / "apps/api"
    versions = api / "alembic/versions"
    versions.mkdir(parents=True)
    (api / "alembic.ini").write_text("[alembic]\nscript_location = alembic\n")
    (versions / "head.py").write_text(
        "revision = '0039_unplanned'\ndown_revision = None\n"
    )
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "release-metadata.json").write_text(
        json.dumps({"migration_head": "0039_unplanned"})
    )
    (deploy / "migration-policy.json").write_text(
        json.dumps({"target_head": "0039_unplanned"})
    )

    assert module.check_release_migrations(tmp_path) == [
        "actual=0039_unplanned metadata=0039_unplanned policy=0039_unplanned"
    ]


def test_release_migration_checker_rejects_multiple_heads(tmp_path: Path):
    checker_path = ROOT / "scripts/check-release-migrations.py"
    spec = importlib.util.spec_from_file_location("release_migrations", checker_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    api = tmp_path / "apps/api"
    versions = api / "alembic/versions"
    versions.mkdir(parents=True)
    (api / "alembic.ini").write_text("[alembic]\nscript_location = alembic\n")
    (versions / "first.py").write_text("revision = 'first'\ndown_revision = None\n")
    (versions / "second.py").write_text("revision = 'second'\ndown_revision = None\n")
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "release-metadata.json").write_text(json.dumps({"migration_head": "first"}))
    (deploy / "migration-policy.json").write_text(json.dumps({"target_head": "first"}))

    findings = module.check_release_migrations(tmp_path)
    assert len(findings) == 1
    assert "multiple heads" in findings[0].lower()


@pytest.fixture
def packaging(tmp_path):
    return packaging_factory.__wrapped__(tmp_path)


def test_support_policy_assigns_lts_and_standard_windows():
    policy = SupportPolicy.from_file(ROOT / "deploy/support-policy.json")
    assert policy.release("1.0.0").support_class == "lts"
    assert policy.release("1.0.0").support_months == 24
    assert policy.release("1.1.0").support_class == "standard"
    assert policy.release("1.1.0").support_months == 6


def test_manifest_v3_rejects_prerelease_on_stable_channel():
    manifest = {
        "format": 3,
        "app_version": "1.0.0-rc.1",
        "eligible_channels": ["stable"],
        "support_class": "candidate",
        "support_months": 0,
        "build_id": "a" * 20,
        "content_digest": "b" * 64,
        "upgrade_policy": {"mode": "graph"},
    }
    with pytest.raises(ValueError, match="invalid_release_policy"):
        validate_manifest_policy(manifest)


def test_public_packer_emits_support_aware_manifest_v3(packaging, tmp_path):
    metadata = {
        "migration_head": "new",
        "migration_compatibility": {"from_heads": ["old"], "reversible": True},
    }
    result, output = metadata_pack(packaging, tmp_path, metadata, version="1.0.0")
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format"] == 3
    assert manifest["eligible_channels"] == ["rc", "stable"]
    assert manifest["support_class"] == "lts"
    assert manifest["support_months"] == 24
    assert len(manifest["build_id"]) == 20
    assert len(manifest["content_digest"]) == 64


def metadata_pack(packaging, tmp_path, metadata, version="1.2.3"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source, private, _, _, env = packaging
    migrations = source / "apps/api/alembic/versions"
    migrations.mkdir(parents=True, exist_ok=True)
    (migrations / "initial.py").unlink(missing_ok=True)
    (migrations / "old.py").write_text("revision = 'old'\ndown_revision = None\n")
    (migrations / "new.py").write_text("revision = 'new'\ndown_revision = 'old'\n")
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(metadata))
    output = tmp_path / "release.zip"
    result = run(
        sys.executable,
        ROOT / "scripts/release_pack.py",
        "--root",
        source,
        "--output",
        output,
        "--version",
        version,
        "--git-sha",
        "a" * 40,
        "--migration-head",
        "new",
        "--metadata",
        path,
        "--signing-key",
        private,
        env=env,
    )
    return result, output


def test_release_rejects_unsupported_0133_migration_head():
    from robopark_host.release import ReleaseError, check_compatibility

    metadata = json.loads((ROOT / "deploy/release-metadata.json").read_text())
    files = {
        path: {}
        for path in (
            "deploy/Dockerfile.api-tests",
            "apps/api/Dockerfile",
            "apps/api/uv.lock",
            "apps/api/pyproject.toml",
            "apps/web/Dockerfile",
            "apps/web/package-lock.json",
            "apps/web/package.json",
            "scripts/verify.sh",
        )
    }
    candidate = {
        "files": files,
        "app_version": (ROOT / "VERSION").read_text().strip(),
        "min_installer_version": "1.0.0",
        "required_capabilities": [],
        **metadata,
    }
    current = {"app_version": "0.1.33", "migration_head": "0026_global_inventory_workflows"}

    with pytest.raises(ReleaseError, match="migration_incompatible"):
        check_compatibility(candidate, current)


def test_production_metadata_is_signed_and_matches_both_verifiers(packaging, tmp_path):
    from robopark_api.services.ops.archives import inspect_archive
    from robopark_host.release import verify_archive

    metadata = {
        "migration_head": "new",
        "migration_compatibility": {"from_heads": ["old"], "reversible": True},
        "update_notes": "Reviewed migration",
    }
    result, output = metadata_pack(packaging, tmp_path, metadata)
    assert result.returncode == 0, result.stderr
    raw = output.read_bytes()
    public = packaging[2].read_bytes()
    assert inspect_archive(raw, expected_kind="release", public_key=public).migration_head == "new"
    assert (
        verify_archive(raw, public).manifest["migration_compatibility"]
        == metadata["migration_compatibility"]
    )
    assert (
        run(
            sys.executable,
            ROOT / "scripts/verify-artifact.py",
            "--public-key",
            packaging[2],
            output,
        ).returncode
        == 0
    )
    with zipfile.ZipFile(output) as archive:
        assert json.loads(archive.read("manifest.json"))["update_notes"] == "Reviewed migration"


@pytest.mark.parametrize(
    ("current_version", "current_head"),
    [
        ("0.2.0-rc.5", "0036_audit_remediation_state"),
        ("0.2.0-rc.5", "0037_claim_workflow_visibility"),
        ("0.2.0-rc.5", "0038_inventory_photo_cleanup"),
    ],
)
def test_production_release_accepts_supported_upgrade(current_version, current_head):
    from robopark_host.release import check_compatibility

    metadata = json.loads((ROOT / "deploy/release-metadata.json").read_text())
    candidate = {
        **metadata,
        "app_version": (ROOT / "VERSION").read_text().strip(),
        "min_installer_version": "0",
        "required_capabilities": [],
        "files": {
            "deploy/Dockerfile.api-tests": {},
            "apps/api/Dockerfile": {},
            "apps/api/uv.lock": {},
            "apps/api/pyproject.toml": {},
            "apps/web/Dockerfile": {},
            "apps/web/package-lock.json": {},
            "apps/web/package.json": {},
            "scripts/verify.sh": {},
        },
    }
    current = {"app_version": current_version, "migration_head": current_head}

    check_compatibility(candidate, current)


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "migration_head": "lie",
            "migration_compatibility": {"from_heads": ["old"], "reversible": True},
        },
        {
            "migration_head": "new",
            "migration_compatibility": {"from_heads": ["old"], "reversible": "true"},
        },
        {
            "migration_head": "new",
            "migration_compatibility": {"from_heads": ["old", "old"], "reversible": True},
        },
        {"migration_head": "new", "migration_compatibility": {}},
        {
            "migration_head": "new",
            "migration_compatibility": {"from_heads": ["old"], "reversible": True},
            "arbitrary": "no",
        },
    ],
)
def test_invalid_metadata_never_publishes(packaging, tmp_path, metadata):
    result, output = metadata_pack(packaging, tmp_path, metadata)
    assert result.returncode != 0
    assert not output.exists()


def test_offline_rotation_proof_accepts_new_key_only_after_bridge(packaging, tmp_path):
    from test_key_rotation import new_key

    private, public = new_key()
    metadata = {
        "migration_head": "new",
        "migration_compatibility": {"from_heads": ["old"], "reversible": True},
        "signing_key_rotation": {"next_public_key": public.decode(), "activation_version": "1.2.3"},
    }
    result, bridge = metadata_pack(packaging, tmp_path / "bridge", metadata)
    assert result.returncode == 0, result.stderr
    packaging[1].write_bytes(private)
    del metadata["signing_key_rotation"]
    result, successor = metadata_pack(packaging, tmp_path / "next", metadata, version="2.0.0")
    assert result.returncode == 0, result.stderr
    command = [
        sys.executable,
        ROOT / "scripts/verify-artifact.py",
        "--public-key",
        packaging[2],
        "--trust-transition",
        bridge,
    ]
    assert run(*command, successor).returncode == 0
    result, premature = metadata_pack(packaging, tmp_path / "premature", metadata)
    assert result.returncode == 0
    assert run(*command, premature).returncode != 0
