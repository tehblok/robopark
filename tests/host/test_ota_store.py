from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from robopark_host.ota_store import OtaPackageStore
from robopark_ota import OtaError


def make_ota(path: Path, *, version: str = "0.2.0-rc.7", compatible=("0.2.0-rc.6",)):
    payload = b"print('robopark')\n"
    manifest = {
        "app_version": version,
        "changes": ["test update"],
        "compatible_from": list(compatible),
        "files": [{
            "path": "__main__.py",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }],
        "format_version": 1,
        "git_sha": "a" * 40,
        "max_expanded_bytes": 1024,
        "migration_head": "0050_media_action_dependency",
        "required_free_bytes": 1,
        "requirements": {
            "architectures": ["aarch64", "x86_64"],
            "memory_profiles_mb": [8192, 32768, 65536],
            "python": ">=3.10",
            "systems": ["armbian", "ubuntu"],
        },
    }
    raw = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("__main__.py", payload)
        archive.writestr("manifest.json", raw)
    path.chmod(0o600)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_admit_verifies_uuid_digest_version_and_reuses_digest(host_paths):
    upload_id = uuid4()
    upload = host_paths.ops / "ota-uploads" / f"{upload_id}.ota"
    digest = make_ota(upload)
    store = OtaPackageStore(host_paths)

    first = store.admit(
        upload_id=upload_id,
        expected_sha256=digest,
        expected_version="0.2.0-rc.7",
        current_version="0.2.0-rc.6",
    )
    upload.unlink()
    second = store.admit(
        upload_id=upload_id,
        expected_sha256=digest,
        expected_version="0.2.0-rc.7",
        current_version="0.2.0-rc.6",
    )

    assert first.path == second.path
    assert first.sha256 == digest
    assert first.path.name == f"{digest}.ota"
    assert first.path.is_file()


def test_admit_rejects_wrong_digest_version_incompatible_and_downgrade(host_paths):
    upload_id = uuid4()
    upload = host_paths.ops / "ota-uploads" / f"{upload_id}.ota"
    digest = make_ota(upload)
    store = OtaPackageStore(host_paths)

    with pytest.raises(OtaError, match="ota_hash_mismatch"):
        store.admit(
            upload_id=upload_id,
            expected_sha256="0" * 64,
            expected_version="0.2.0-rc.7",
            current_version="0.2.0-rc.6",
        )
    with pytest.raises(OtaError, match="ota_version_mismatch"):
        store.admit(
            upload_id=upload_id,
            expected_sha256=digest,
            expected_version="0.2.0-rc.8",
            current_version="0.2.0-rc.6",
        )
    with pytest.raises(OtaError, match="ota_incompatible"):
        store.admit(
            upload_id=upload_id,
            expected_sha256=digest,
            expected_version="0.2.0-rc.7",
            current_version="0.2.0-rc.5",
        )
    newer_id = uuid4()
    newer_upload = host_paths.ops / "ota-uploads" / f"{newer_id}.ota"
    newer_digest = make_ota(newer_upload, version="0.1.9", compatible=())
    with pytest.raises(OtaError, match="ota_downgrade_forbidden"):
        store.admit(
            upload_id=newer_id,
            expected_sha256=newer_digest,
            expected_version="0.1.9",
            current_version="0.2.0",
        )


def test_admit_rejects_symlink_and_group_writable_upload(host_paths, tmp_path):
    upload_id = uuid4()
    upload = host_paths.ops / "ota-uploads" / f"{upload_id}.ota"
    external = tmp_path / "external.ota"
    digest = make_ota(external)
    upload.parent.mkdir(parents=True, mode=0o700)
    upload.symlink_to(external)
    store = OtaPackageStore(host_paths)

    with pytest.raises(OtaError, match="ota_unsafe_upload"):
        store.admit(
            upload_id=upload_id,
            expected_sha256=digest,
            expected_version="0.2.0-rc.7",
            current_version="0.2.0-rc.6",
        )

    upload.unlink()
    digest = make_ota(upload)
    os.chmod(upload, 0o660)
    with pytest.raises(OtaError, match="ota_unsafe_upload"):
        store.admit(
            upload_id=upload_id,
            expected_sha256=digest,
            expected_version="0.2.0-rc.7",
            current_version="0.2.0-rc.6",
        )


def test_admit_checks_free_space_and_cleanup_removes_only_expired_unclaimed(
    host_paths, monkeypatch
):
    upload_id = uuid4()
    upload = host_paths.ops / "ota-uploads" / f"{upload_id}.ota"
    digest = make_ota(upload)
    store = OtaPackageStore(host_paths, reserve_bytes=10**15)
    with pytest.raises(OtaError, match="ota_insufficient_space"):
        store.admit(
            upload_id=upload_id,
            expected_sha256=digest,
            expected_version="0.2.0-rc.7",
            current_version="0.2.0-rc.6",
        )

    stale = host_paths.ops / "ota-uploads" / f"{uuid4()}.part"
    recent = host_paths.ops / "ota-uploads" / f"{uuid4()}.part"
    stale.write_bytes(b"old")
    recent.write_bytes(b"new")
    os.utime(stale, (1, 1))
    removed = OtaPackageStore(host_paths).cleanup_expired(now=10_000, ttl_seconds=100)
    assert stale in removed
    assert not stale.exists()
    assert recent.exists()
