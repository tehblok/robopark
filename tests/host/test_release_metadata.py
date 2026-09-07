"""The public packer, not fixture-only manifest construction, declares migrations."""

import json
import sys
import zipfile

import pytest
from test_packaging import ROOT, run
from test_packaging import packaging as packaging_factory


@pytest.fixture
def packaging(tmp_path):
    return packaging_factory.__wrapped__(tmp_path)


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
