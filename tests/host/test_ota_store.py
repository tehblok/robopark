from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from robopark_host.ota_store import OtaPackageStore
from robopark_ota import OtaError


def make_ota(
    path: Path, *, version: str = "0.2.0-rc.7",
    compatible=("0.2.0-rc.6",), required_free_bytes: int = 1,
):
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
        "required_free_bytes": required_free_bytes,
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
    assert not upload.exists()
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


def test_admit_drops_verified_input_only_after_durable_cache_exists(host_paths):
    upload_id = uuid4()
    upload = host_paths.ops / "ota-uploads" / f"{upload_id}.ota"
    digest = make_ota(upload)

    package = OtaPackageStore(host_paths).admit(
        upload_id=upload_id,
        expected_sha256=digest,
        expected_version="0.2.0-rc.7",
        current_version="0.2.0-rc.6",
    )

    assert package.path.is_file()
    assert not upload.exists()


def test_clean_install_only_package_rejects_existing_version(host_paths):
    upload_id = uuid4()
    upload = host_paths.ops / "ota-uploads" / f"{upload_id}.ota"
    digest = make_ota(upload, compatible=())

    with pytest.raises(OtaError, match="ota_incompatible"):
        OtaPackageStore(host_paths).admit(
            upload_id=upload_id,
            expected_sha256=digest,
            expected_version="0.2.0-rc.7",
            current_version="0.2.0-rc.6",
        )

    assert upload.is_file()
    assert not (host_paths.state / "ota-packages" / f"{digest}.ota").exists()


@pytest.mark.parametrize(
    ("total_gib", "free_gib"), [(20, 5), (100, 10)]
)
def test_admit_keeps_host_reserve_before_snapshot(
    host_paths, monkeypatch, total_gib, free_gib
):
    upload_id = uuid4()
    upload = host_paths.ops / "ota-uploads" / f"{upload_id}.ota"
    digest = make_ota(upload, required_free_bytes=1)
    gib = 1024**3
    monkeypatch.setattr(
        "robopark_host.ota_store.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=total_gib * gib, free=free_gib * gib),
    )

    with pytest.raises(OtaError, match="ota_insufficient_space"):
        OtaPackageStore(host_paths).admit(
            upload_id=upload_id, expected_sha256=digest,
            expected_version="0.2.0-rc.7", current_version="0.2.0-rc.6",
        )

    assert upload.is_file()
    assert not (host_paths.state / "ota-packages" / f"{digest}.ota").exists()


def test_retention_drops_only_receipted_terminal_cache_after_active_operation_finishes(
    host_paths,
):
    from robopark_host.state import atomic_write_json

    upload_id = uuid4()
    digest = make_ota(host_paths.ops / "ota-uploads" / f"{upload_id}.ota")
    store = OtaPackageStore(host_paths)
    store.admit(
        upload_id=upload_id,
        expected_sha256=digest,
        expected_version="0.2.0-rc.7",
        current_version="0.2.0-rc.6",
    )
    cache = host_paths.state / "ota-packages" / f"{digest}.ota"
    unknown = host_paths.state / "ota-packages" / ("a" * 64 + ".ota")
    unknown.write_bytes(b"unowned")
    operation_id = uuid4()
    atomic_write_json(
        host_paths.state / "ota-update-receipts" / f"{operation_id}.json",
        {"operation_id": str(operation_id), "sha256": digest, "phase": "published"},
    )
    atomic_write_json(
        host_paths.state / "ota-update-journal.json",
        {"request": {"sha256": digest}, "phase": "staged"},
    )

    assert store.cleanup_terminal_packages() == ()
    assert cache.exists()
    atomic_write_json(
        host_paths.state / "ota-update-journal.json",
        {"request": {"sha256": digest}, "phase": "published"},
    )
    assert store.cleanup_terminal_packages() == (cache,)
    assert not cache.exists()
    assert unknown.exists()


def test_terminal_cache_cleanup_still_works_after_many_update_receipts(host_paths):
    from robopark_host.state import atomic_write_json

    store = OtaPackageStore(host_paths)
    upload_id = uuid4()
    digest = make_ota(store.incoming / f"{upload_id}.ota")
    package = store.admit(
        upload_id=upload_id,
        expected_sha256=digest,
        expected_version="0.2.0-rc.7",
        current_version="0.2.0-rc.6",
    )
    receipts = host_paths.state / "ota-update-receipts"
    for _ in range(512):
        operation_id = uuid4()
        atomic_write_json(
            receipts / f"{operation_id}.json",
            {"operation_id": str(operation_id), "sha256": "a" * 64, "phase": "published"},
        )
    operation_id = uuid4()
    atomic_write_json(
        receipts / f"{operation_id}.json",
        {"operation_id": str(operation_id), "sha256": digest, "phase": "published"},
    )

    assert store.cleanup_terminal_packages() == (package.path,)
    assert not package.path.exists()
    assert len(list(receipts.iterdir())) == 513


def test_terminal_cache_cleanup_batches_more_than_512_receipted_packages(host_paths):
    from robopark_host.state import atomic_write_json

    store = OtaPackageStore(host_paths)
    store.packages.mkdir(parents=True, exist_ok=True)
    receipts = host_paths.state / "ota-update-receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    for index in range(513):
        digest = f"{index:064x}"
        (store.packages / f"{digest}.ota").write_bytes(b"cached")
        operation_id = uuid4()
        atomic_write_json(
            receipts / f"{operation_id}.json",
            {"operation_id": str(operation_id), "sha256": digest, "phase": "published"},
        )

    first = store.cleanup_terminal_packages()
    assert len(first) == 512
    assert len(list(store.packages.iterdir())) == 1
    second = store.cleanup_terminal_packages()
    assert len(second) == 1
    assert list(store.packages.iterdir()) == []


def test_terminal_cache_cleanup_skips_receipt_io_when_no_cached_packages(
    host_paths, monkeypatch,
):
    store = OtaPackageStore(host_paths)
    store.packages.mkdir(parents=True, exist_ok=True)
    receipts = host_paths.state / "ota-update-receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    original = os.scandir

    def scandir(path):
        if path == receipts:
            raise AssertionError("receipt directory must not be scanned")
        return original(path)

    monkeypatch.setattr(os, "scandir", scandir)
    assert store.cleanup_terminal_packages() == ()


def test_abandoned_ota_copies_expire_without_touching_active_or_foreign_files(
    host_paths, tmp_path,
):
    from robopark_host.state import atomic_write_json

    store = OtaPackageStore(host_paths)
    store.packages.mkdir(parents=True, exist_ok=True)
    stale = store.packages / f".{('a' * 64)}.{uuid4()}.tmp"
    active_id = uuid4()
    active = store.packages / f".{('b' * 64)}.{active_id}.tmp"
    recent = store.packages / f".{('c' * 64)}.{uuid4()}.tmp"
    foreign = store.packages / ".other.tmp"
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    linked = store.packages / f".{('d' * 64)}.{uuid4()}.tmp"
    for path in (stale, active, recent, foreign):
        path.write_bytes(b"data")
    linked.symlink_to(outside)
    for path in (stale, active, foreign):
        os.utime(path, (1, 1))
    os.utime(recent, (9999, 9999))
    atomic_write_json(
        host_paths.state / "ota-update-journal.json",
        {"phase": "accepted", "request": {"sha256": "b" * 64, "upload_id": str(active_id)}},
    )

    assert store.cleanup_abandoned_copies(now=10_000, ttl_seconds=100) == (stale,)
    assert not stale.exists()
    assert active.exists() and recent.exists() and foreign.exists()
    assert linked.is_symlink() and outside.read_bytes() == b"keep"


def test_abandoned_copy_cleanup_blocks_unknown_ota_journal_phase(host_paths):
    from robopark_host.state import atomic_write_json

    store = OtaPackageStore(host_paths)
    store.packages.mkdir(parents=True, exist_ok=True)
    stale = store.packages / f".{'a' * 64}.{uuid4()}.tmp"
    stale.write_bytes(b"keep until journal is repaired")
    os.utime(stale, (1, 1))
    atomic_write_json(
        host_paths.state / "ota-update-journal.json",
        {"phase": "unknown", "request": {"sha256": "b" * 64, "upload_id": str(uuid4())}},
    )

    with pytest.raises(OtaError, match="ota_state_invalid"):
        store.cleanup_abandoned_copies(now=10_000, ttl_seconds=100)
    assert stale.is_file()


def test_abandoned_copy_cleanup_rejects_symlinked_state_parent(host_paths, tmp_path):
    store = OtaPackageStore(host_paths)
    host_paths.ops.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside-state"
    (outside / "ota-packages").mkdir(parents=True)
    target = outside / "ota-packages" / f".{'a' * 64}.{uuid4()}.tmp"
    target.write_bytes(b"not Robopark state")
    os.utime(target, (1, 1))
    host_paths.state.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OtaError, match="ota_unsafe_cache"):
        store.cleanup_abandoned_copies(now=10_000, ttl_seconds=100)
    assert target.is_file()


@pytest.mark.parametrize(
    "action", ["admit", "discard_terminal", "terminal_cache_candidates", "cleanup_expired"]
)
def test_ota_store_actions_reject_symlinked_state_parent(host_paths, tmp_path, action):
    host_paths.ops.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside-state"
    outside.mkdir()
    host_paths.state.symlink_to(outside, target_is_directory=True)
    store = OtaPackageStore(host_paths)

    with pytest.raises(OtaError, match="ota_unsafe_cache"):
        if action == "admit":
            store.admit(
                upload_id=uuid4(), expected_sha256="a" * 64,
                expected_version="0.2.0-rc.7", current_version=None,
            )
        elif action == "discard_terminal":
            store.discard_terminal("a" * 64)
        else:
            getattr(store, action)()


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
    assert not (host_paths.state / "ota-packages" / f"{digest}.ota").exists()
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
    foreign = host_paths.ops / "ota-uploads" / "other-service.part"
    stale.write_bytes(b"old")
    recent.write_bytes(b"new")
    foreign.write_bytes(b"keep")
    os.utime(stale, (1, 1))
    os.utime(foreign, (1, 1))
    removed = OtaPackageStore(host_paths).cleanup_expired(now=10_000, ttl_seconds=100)
    assert stale in removed
    assert not stale.exists()
    assert recent.exists()
    assert foreign.exists()


def test_expired_uploaded_ota_is_removed_only_when_no_active_resume_needs_it(host_paths):
    from robopark_host.state import atomic_write_json

    incoming = host_paths.ops / "ota-uploads"
    incoming.mkdir(parents=True, exist_ok=True)
    stale = incoming / f"{uuid4()}.ota"
    active_id = uuid4()
    active = incoming / f"{active_id}.ota"
    foreign = incoming / "other-service.ota"
    for path in (stale, active, foreign):
        path.write_bytes(b"upload")
        os.utime(path, (1, 1))
    atomic_write_json(
        host_paths.state / "ota-update-journal.json",
        {"phase": "staged", "request": {"upload_id": str(active_id)}},
    )

    removed = OtaPackageStore(host_paths).cleanup_expired(now=10_000, ttl_seconds=100)

    assert removed == (stale,)
    assert not stale.exists()
    assert active.exists()
    assert foreign.exists()


def test_host_cleanup_keeps_expired_upload_still_owned_by_api(host_paths):
    incoming = host_paths.ops / "ota-uploads"
    incoming.mkdir(parents=True, exist_ok=True)
    upload_id = uuid4()
    upload = incoming / f"{upload_id}.ota"
    upload.write_bytes(b"verified upload awaiting host start")
    os.utime(upload, (1, 1))
    metadata = host_paths.var / "api-ops" / "ota-uploads" / f"{upload_id}.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text('{"state":"verified"}', encoding="utf-8")

    assert OtaPackageStore(host_paths).cleanup_expired(now=10_000, ttl_seconds=100) == ()
    assert upload.is_file()


def test_expired_upload_cleanup_blocks_on_broken_journal_symlink(host_paths):
    incoming = host_paths.ops / "ota-uploads"
    incoming.mkdir(parents=True, exist_ok=True)
    stale = incoming / f"{uuid4()}.ota"
    stale.write_bytes(b"upload")
    os.utime(stale, (1, 1))
    journal = host_paths.state / "ota-update-journal.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.symlink_to("missing-journal")

    with pytest.raises(OtaError, match="ota_state_invalid"):
        OtaPackageStore(host_paths).cleanup_expired(now=10_000, ttl_seconds=100)
    assert stale.exists()
