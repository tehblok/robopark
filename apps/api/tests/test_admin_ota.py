from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from conftest import login_as
from robopark_api.models import HostOperationStatus
from robopark_api.services.ops import operation_registry, ota_uploads
from robopark_api.services.ops.ota_uploads import OtaUploadError, OtaUploadStore


@pytest.fixture(autouse=True)
def healthy_upload_disk(monkeypatch):
    # Fixtures describe a healthy host; explicit pressure tests override this.
    monkeypatch.setattr(
        ota_uploads.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=100 * 1024**3, free=80 * 1024**3),
    )


def ota_bytes(
    version="0.2.0-rc.7",
    *,
    payload: bytes = b"print('ota')\n",
    compression: int = zipfile.ZIP_STORED,
    required_free_bytes: int = 1,
) -> bytes:
    manifest = {
        "app_version": version,
        "changes": ["test"],
        "compatible_from": ["0.2.0-rc.6"],
        "files": [
            {
                "path": "__main__.py",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
        "format_version": 1,
        "git_sha": "a" * 40,
        "max_expanded_bytes": max(1024, len(payload)),
        "migration_head": "0050_media_action_dependency",
        "required_free_bytes": required_free_bytes,
        "requirements": {
            "python": ">=3.10",
            "systems": ["armbian", "ubuntu"],
            "architectures": ["aarch64", "x86_64"],
            "memory_profiles_mb": [8192, 32768, 65536],
        },
    }
    from io import BytesIO

    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression) as archive:
        archive.writestr("__main__.py", payload)
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
        )
    return output.getvalue()


def test_store_rejects_zip_bomb_before_staging(tmp_path):
    content = ota_bytes(
        payload=b"0" * (1024 * 1024),
        compression=zipfile.ZIP_DEFLATED,
    )
    digest = hashlib.sha256(content).hexdigest()
    store = OtaUploadStore(
        tmp_path / "state",
        tmp_path / "host",
        chunk_bytes=len(content),
    )
    upload = store.create(
        actor_id=7,
        filename="compressed.ota",
        size=len(content),
        sha256=digest,
    )
    store.append(upload.upload_id, actor_id=7, offset=0, chunk=content)

    with pytest.raises(OtaUploadError, match="^ota_invalid_container$"):
        store.finalize(upload.upload_id, actor_id=7)

    assert not (tmp_path / "host" / f"{upload.upload_id}.ota").exists()


def test_store_resumes_exact_offsets_deduplicates_chunks_and_finalizes(tmp_path):
    content = ota_bytes()
    digest = hashlib.sha256(content).hexdigest()
    store = OtaUploadStore(tmp_path / "state", tmp_path / "host", chunk_bytes=64)
    created = store.create(actor_id=7, filename="release.ota", size=len(content), sha256=digest)

    first = content[:64]
    assert store.append(created.upload_id, actor_id=7, offset=0, chunk=first) == 64
    assert store.append(created.upload_id, actor_id=7, offset=0, chunk=first) == 64
    with pytest.raises(OtaUploadError, match="ota_offset_mismatch"):
        store.append(created.upload_id, actor_id=7, offset=0, chunk=b"different")
    offset = 64
    while offset < len(content):
        chunk = content[offset : offset + 64]
        offset = store.append(created.upload_id, actor_id=7, offset=offset, chunk=chunk)
    result = store.finalize(created.upload_id, actor_id=7)
    assert result.state == "verified"
    assert result.sha256 == digest
    assert result.version == "0.2.0-rc.7"
    assert (tmp_path / "host" / f"{created.upload_id}.ota").read_bytes() == content
    assert store.finalize(created.upload_id, actor_id=7) == result
    reused = store.create(actor_id=7, filename="release.ota", size=len(content), sha256=digest)
    assert reused.upload_id == created.upload_id
    assert reused.already_present is True


def test_verified_upload_rejects_manifest_space_before_operation_reservation(tmp_path):
    content = ota_bytes(required_free_bytes=2**63 - 1)
    store = OtaUploadStore(tmp_path / "state", tmp_path / "host", chunk_bytes=len(content))
    upload = store.create(
        actor_id=7,
        filename="large-budget.ota",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    store.append(upload.upload_id, actor_id=7, offset=0, chunk=content)
    verified = store.finalize(upload.upload_id, actor_id=7)

    assert verified.required_free_bytes == 2**63 - 1
    with pytest.raises(OtaUploadError, match="ota_insufficient_space"):
        store.check_install_capacity(upload.upload_id, actor_id=7)
    assert (tmp_path / "host" / f"{upload.upload_id}.ota").is_file()


def test_store_bounds_verified_candidates_and_allows_two_sequential_ota(tmp_path):
    store = OtaUploadStore(
        tmp_path / "state",
        tmp_path / "host",
        chunk_bytes=1024,
        max_active_per_actor=2,
    )

    finalized = []
    for index in range(2):
        content = ota_bytes(version=f"0.2.0-rc.{index}")
        digest = hashlib.sha256(content).hexdigest()
        upload = store.create(
            actor_id=7,
            filename=f"release-{index}.ota",
            size=len(content),
            sha256=digest,
        )
        store.append(upload.upload_id, actor_id=7, offset=0, chunk=content)
        finalized.append(store.finalize(upload.upload_id, actor_id=7))

    third = ota_bytes(version="0.2.0-rc.2")
    with pytest.raises(OtaUploadError, match="ota_upload_quota"):
        store.create(
            actor_id=7,
            filename="release-2.ota",
            size=len(third),
            sha256=hashlib.sha256(third).hexdigest(),
        )
    assert [record.state for record in finalized] == ["verified", "verified"]
    assert not list((tmp_path / "state").glob("*.part"))
    assert len(list((tmp_path / "host").glob("*.ota"))) == 2
    store.delete(finalized[0].upload_id, actor_id=7)
    resumed = store.create(
        actor_id=7,
        filename="release-2.ota",
        size=len(third),
        sha256=hashlib.sha256(third).hexdigest(),
    )
    assert resumed.state == "uploading"


def test_list_uploads_only_exposes_owned_unpinned_existing_files(tmp_path):
    clock = {"now": 1000.0}
    store = OtaUploadStore(
        tmp_path / "state", tmp_path / "host", now=lambda: clock["now"], max_active_per_actor=8
    )
    content = ota_bytes()
    records = []
    for actor, name in [
        (7, "old.ota"),
        (8, "foreign.ota"),
        (7, "pinned.ota"),
        (7, "missing.ota"),
        (7, "partial.ota"),
    ]:
        record = store.create(
            actor_id=actor,
            filename=name,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        records.append(record)
        if name != "partial.ota":
            store.append(record.upload_id, actor_id=actor, offset=0, chunk=content)
            store.finalize(record.upload_id, actor_id=actor)
    store.pin_for_operation(records[2].upload_id, actor_id=7, operation_id=uuid4())
    store._host(records[3].upload_id).unlink()
    (store.state_root / f"{uuid4()}.json").write_text("invalid")

    listed = store.list_owned(actor_id=7)
    assert {row.filename for row in listed} == {"old.ota", "partial.ota"}
    old = next(row for row in listed if row.filename == "old.ota")
    assert old.version == "0.2.0-rc.7"
    assert old.compatible_from == ("0.2.0-rc.6",)
    store.delete(old.upload_id, actor_id=7)
    assert [row.filename for row in store.list_owned(actor_id=7)] == ["partial.ota"]
    clock["now"] += 86401
    assert store.list_owned(actor_id=7) == []
    assert store._host(records[2].upload_id).is_file()


def test_store_reserves_host_floor_and_temporary_copy_before_upload(tmp_path, monkeypatch):
    gib = 1024**3
    store = OtaUploadStore(tmp_path / "state", tmp_path / "host")
    available = {"free": 9 * gib}
    monkeypatch.setattr(
        ota_uploads.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=20 * gib, free=available["free"]),
    )
    with pytest.raises(OtaUploadError, match="ota_insufficient_space"):
        store.create(actor_id=7, filename="large.ota", size=2 * gib, sha256="a" * 64)
    available["free"] = 11 * gib
    assert (
        store.create(actor_id=7, filename="large.ota", size=2 * gib, sha256="a" * 64).state
        == "uploading"
    )


def test_store_reserves_remaining_bytes_of_other_active_uploads(tmp_path, monkeypatch):
    gib = 1024**3
    store = OtaUploadStore(tmp_path / "state", tmp_path / "host")
    monkeypatch.setattr(
        ota_uploads.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=20 * gib, free=11 * gib),
    )

    first = store.create(actor_id=7, filename="first.ota", size=2 * gib, sha256="a" * 64)
    assert first.state == "uploading"
    with pytest.raises(OtaUploadError, match="ota_insufficient_space"):
        store.create(actor_id=8, filename="second.ota", size=2 * gib, sha256="b" * 64)


def test_expired_verified_upload_stays_available_for_unfinished_host_operation(tmp_path):
    clock = {"now": 1000.0}
    store = OtaUploadStore(
        tmp_path / "state",
        tmp_path / "host",
        chunk_bytes=1024,
        ttl_seconds=10,
        now=lambda: clock["now"],
    )
    records = []
    for index in range(2):
        content = ota_bytes(version=f"0.2.0-rc.{index}")
        upload = store.create(
            actor_id=7,
            filename=f"release-{index}.ota",
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        store.append(upload.upload_id, actor_id=7, offset=0, chunk=content)
        records.append(store.finalize(upload.upload_id, actor_id=7))
    operation_id = uuid4()
    store.pin_for_operation(records[0].upload_id, actor_id=7, operation_id=operation_id)
    next_operation_id = uuid4()
    with pytest.raises(OtaUploadError, match="ota_upload_in_use"):
        store.pin_for_operation(
            records[0].upload_id,
            actor_id=7,
            operation_id=next_operation_id,
        )
    assert store._read(records[0].upload_id)["operation_id"] == str(operation_id)

    clock["now"] = 1011.0
    assert store.cleanup_expired() == 1
    assert store._host(records[0].upload_id).is_file()
    assert store.status(records[0].upload_id, actor_id=7).state == "verified"
    assert not store._host(records[1].upload_id).exists()
    with pytest.raises(OtaUploadError, match="ota_upload_in_use"):
        store.delete(records[0].upload_id, actor_id=7)
    assert store.cleanup_expired(is_terminal=lambda identity: identity == operation_id) == 1
    assert not store._host(records[0].upload_id).exists()
    assert not store._meta(records[0].upload_id).exists()


def test_verified_upload_can_be_reassigned_after_terminal_operation(tmp_path):
    store = OtaUploadStore(tmp_path / "state", tmp_path / "host", chunk_bytes=1024)
    content = ota_bytes()
    upload = store.create(
        actor_id=7,
        filename="release.ota",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    store.append(upload.upload_id, actor_id=7, offset=0, chunk=content)
    store.finalize(upload.upload_id, actor_id=7)
    previous = uuid4()
    current = uuid4()
    store.pin_for_operation(upload.upload_id, actor_id=7, operation_id=previous)
    store.pin_for_operation(
        upload.upload_id,
        actor_id=7,
        operation_id=current,
        reassignable=lambda identity: identity == previous,
    )
    assert store._read(upload.upload_id)["operation_id"] == str(current)


def test_expired_pinned_upload_metadata_reclaimed_after_host_admission(tmp_path):
    clock = {"now": 1000.0}
    store = OtaUploadStore(
        tmp_path / "state",
        tmp_path / "host",
        chunk_bytes=1024,
        ttl_seconds=10,
        now=lambda: clock["now"],
    )
    content = ota_bytes()
    upload = store.create(
        actor_id=7,
        filename="release.ota",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    store.append(upload.upload_id, actor_id=7, offset=0, chunk=content)
    store.finalize(upload.upload_id, actor_id=7)
    store.pin_for_operation(upload.upload_id, actor_id=7, operation_id=uuid4())
    clock["now"] = 1011.0
    store._host(upload.upload_id).unlink()  # Host admission consumed its own durable copy.
    assert store.cleanup_expired() == 1
    assert not store._meta(upload.upload_id).exists()


def test_terminal_operation_receipt_is_retained_until_pinned_upload_is_reclaimed(
    tmp_path, monkeypatch, db_session, seed_royal
):
    settings = SimpleNamespace(
        ops_dir=str(tmp_path / "ops"),
        ops_host_root=str(tmp_path / "host"),
        ops_max_upload_bytes=ota_uploads.MAX_UPLOAD_BYTES,
    )
    from robopark_api import config

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    store = OtaUploadStore(tmp_path / "ops" / "ota-uploads", tmp_path / "host" / "ota-uploads")
    content = ota_bytes()
    upload = store.create(
        actor_id=seed_royal.id,
        filename="release.ota",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    store.append(upload.upload_id, actor_id=seed_royal.id, offset=0, chunk=content)
    store.finalize(upload.upload_id, actor_id=seed_royal.id)
    operation_id = uuid4()
    operation_registry.reserve(
        db_session,
        operation_id=str(operation_id),
        actor_user_id=seed_royal.id,
        kind="ota-update",
    )
    receipt = operation_registry.mark_rejected(
        db_session, operation_id=str(operation_id), error="ota_admission_failed"
    )
    receipt.terminal_at = datetime.now(UTC) - timedelta(days=8)
    db_session.commit()
    store.pin_for_operation(upload.upload_id, actor_id=seed_royal.id, operation_id=operation_id)

    assert operation_registry.prune(db_session) == 0
    assert db_session.get(HostOperationStatus, str(operation_id)) is not None
    metadata = store._read(upload.upload_id)
    metadata["expires_at"] = 0
    store._write(upload.upload_id, metadata)
    assert store.cleanup_expired(is_terminal=lambda identity: identity == operation_id) == 1
    assert operation_registry.prune(db_session) == 1


def test_expired_interrupted_finalize_copy_is_reclaimed_without_touching_unknown_files(tmp_path):
    clock = {"now": 1000.0}
    store = OtaUploadStore(
        tmp_path / "state",
        tmp_path / "host",
        ttl_seconds=10,
        now=lambda: clock["now"],
    )
    stale = store.host_root / f".{uuid4()}.ota.{uuid4().hex}.tmp"
    unrelated = store.host_root / ".unknown.ota.deadbeef.tmp"
    fresh = store.host_root / f".{uuid4()}.ota.{uuid4().hex}.tmp"
    for path in (stale, unrelated, fresh):
        path.write_bytes(b"interrupted-copy")
    os.utime(stale, (980, 980))
    os.utime(unrelated, (980, 980))

    assert store.cleanup_expired() == 1
    assert not stale.exists()
    assert unrelated.is_file()
    assert fresh.is_file()


def test_expired_upload_cleanup_persists_deleted_files_before_receipt_prune(tmp_path, monkeypatch):
    clock = {"now": 1000.0}
    store = OtaUploadStore(
        tmp_path / "state", tmp_path / "host", ttl_seconds=10, now=lambda: clock["now"]
    )
    content = ota_bytes()
    upload = store.create(
        actor_id=7,
        filename="release.ota",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    store.append(upload.upload_id, actor_id=7, offset=0, chunk=content)
    store.finalize(upload.upload_id, actor_id=7)
    operation_id = uuid4()
    store.pin_for_operation(upload.upload_id, actor_id=7, operation_id=operation_id)
    clock["now"] = 1011.0
    synced = []
    real_fsync = ota_uploads._fsync_directory
    monkeypatch.setattr(
        ota_uploads, "_fsync_directory", lambda path: (synced.append(path), real_fsync(path))
    )

    assert store.cleanup_expired(is_terminal=lambda identity: identity == operation_id) == 1
    assert store.state_root in synced
    assert store.host_root in synced


def test_expired_interrupted_metadata_write_is_reclaimed(tmp_path):
    clock = {"now": 1000.0}
    store = OtaUploadStore(
        tmp_path / "state", tmp_path / "host", ttl_seconds=10, now=lambda: clock["now"]
    )
    stale = store.state_root / f".{uuid4()}.json.{uuid4().hex}.tmp"
    unrelated = store.state_root / ".unknown.json.deadbeef.tmp"
    stale.write_text("interrupted", encoding="utf-8")
    unrelated.write_text("unknown", encoding="utf-8")
    os.utime(stale, (980, 980))
    os.utime(unrelated, (980, 980))

    assert store.cleanup_expired() == 1
    assert not stale.exists()
    assert unrelated.is_file()


def test_malformed_upload_expiry_does_not_block_other_expired_uploads(tmp_path):
    clock = {"now": 1000.0}
    store = OtaUploadStore(
        tmp_path / "state",
        tmp_path / "host",
        ttl_seconds=10,
        now=lambda: clock["now"],
    )
    malformed = store.create(actor_id=7, filename="malformed.ota", size=1, sha256="a" * 64)
    expired = store.create(actor_id=7, filename="expired.ota", size=1, sha256="b" * 64)
    metadata = store._read(malformed.upload_id)
    metadata["expires_at"] = "invalid"
    store._write(malformed.upload_id, metadata)

    clock["now"] = 1011.0
    assert store.cleanup_expired() == 1
    assert store._meta(malformed.upload_id).is_file()
    assert not store._meta(expired.upload_id).exists()
    assert (
        store.create(actor_id=7, filename="new.ota", size=1, sha256="c" * 64).state == "uploading"
    )


def test_malformed_pinned_upload_state_never_removes_unfinished_host_package(tmp_path):
    clock = {"now": 1000.0}
    content = ota_bytes()
    store = OtaUploadStore(
        tmp_path / "state",
        tmp_path / "host",
        chunk_bytes=len(content),
        ttl_seconds=10,
        now=lambda: clock["now"],
    )
    pinned = store.create(
        actor_id=7,
        filename="pinned.ota",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    store.append(pinned.upload_id, actor_id=7, offset=0, chunk=content)
    store.finalize(pinned.upload_id, actor_id=7)
    store.pin_for_operation(pinned.upload_id, actor_id=7, operation_id=uuid4())
    ordinary = store.create(actor_id=7, filename="ordinary.ota", size=1, sha256="a" * 64)
    metadata = store._read(pinned.upload_id)
    metadata["state"] = "unknown"
    store._write(pinned.upload_id, metadata)

    clock["now"] = 1011.0
    assert store.cleanup_expired(is_terminal=lambda _identity: False) == 1
    assert store._meta(pinned.upload_id).is_file()
    assert store._host(pinned.upload_id).is_file()
    assert not store._meta(ordinary.upload_id).exists()


def test_store_enforces_owner_limits_expiry_and_hash(tmp_path):
    content = ota_bytes()
    digest = hashlib.sha256(content).hexdigest()
    clock = {"now": 1000.0}
    store = OtaUploadStore(
        tmp_path / "state",
        tmp_path / "host",
        chunk_bytes=1024,
        max_active_per_actor=1,
        max_active_global=2,
        ttl_seconds=10,
        now=lambda: clock["now"],
    )
    upload = store.create(actor_id=7, filename="one.ota", size=len(content), sha256=digest)
    with pytest.raises(OtaUploadError, match="ota_upload_quota"):
        store.create(actor_id=7, filename="two.ota", size=len(content), sha256=digest)
    with pytest.raises(OtaUploadError, match="ota_upload_forbidden"):
        store.status(upload.upload_id, actor_id=8)
    store.append(upload.upload_id, actor_id=7, offset=0, chunk=content)
    metadata = store._read(upload.upload_id)
    metadata["sha256"] = "0" * 64
    store._write(upload.upload_id, metadata)
    with pytest.raises(OtaUploadError, match="ota_hash_mismatch"):
        store.finalize(upload.upload_id, actor_id=7)
    clock["now"] = 1011.0
    assert store.cleanup_expired() == 1
    with pytest.raises(OtaUploadError, match="ota_upload_not_found"):
        store.status(upload.upload_id, actor_id=7)


def test_store_rejects_bad_name_size_digest_and_oversized_chunk(tmp_path):
    store = OtaUploadStore(tmp_path / "state", tmp_path / "host", max_bytes=100, chunk_bytes=8)
    for filename, size, digest, code in (
        ("bad.zip", 10, "a" * 64, "ota_filename_invalid"),
        ("good.ota", 101, "a" * 64, "ota_package_too_large"),
        ("good.ota", 10, "bad", "ota_hash_invalid"),
    ):
        with pytest.raises(OtaUploadError, match=code):
            store.create(actor_id=1, filename=filename, size=size, sha256=digest)
    upload = store.create(actor_id=1, filename="good.ota", size=10, sha256="a" * 64)
    with pytest.raises(OtaUploadError, match="ota_chunk_too_large"):
        store.append(upload.upload_id, actor_id=1, offset=0, chunk=b"123456789")


def test_http_upload_is_royal_only_resumable_and_finalizes(
    client, seed_royal, seed_mechanic, test_settings, tmp_path
):
    host = tmp_path / "host"
    for name in ("inbox", "artifacts", "public", "ota-uploads"):
        (host / name).mkdir(parents=True)
    object.__setattr__(test_settings, "ops_host_root", str(host))
    content = ota_bytes()
    digest = hashlib.sha256(content).hexdigest()

    login_as(client, "mech1", "secret")
    assert client.get("/admin/ops/ota/uploads").status_code == 403
    denied = client.post(
        "/admin/ops/ota/uploads",
        json={"filename": "release.ota", "size": len(content), "sha256": digest},
    )
    assert denied.status_code == 403

    login_as(client, "royal", "secret")
    created = client.post(
        "/admin/ops/ota/uploads",
        json={"filename": "release.ota", "size": len(content), "sha256": digest},
    )
    assert created.status_code == 201
    upload_id = created.json()["upload_id"]
    offset = 0
    while offset < len(content):
        chunk = content[offset : offset + created.json()["chunk_size"]]
        patched = client.patch(
            f"/admin/ops/ota/uploads/{upload_id}",
            content=chunk,
            headers={
                "Upload-Offset": str(offset),
                "Content-Type": "application/offset+octet-stream",
            },
        )
        assert patched.status_code == 200
        offset = patched.json()["offset"]
    headed = client.head(f"/admin/ops/ota/uploads/{upload_id}")
    assert headed.headers["Upload-Offset"] == str(len(content))
    finalized = client.post(f"/admin/ops/ota/uploads/{upload_id}/finalize")
    assert finalized.status_code == 200
    assert finalized.json()["version"] == "0.2.0-rc.7"
    listed = client.get("/admin/ops/ota/uploads")
    assert listed.status_code == 200
    assert [row["upload_id"] for row in listed.json()["items"]] == [upload_id]


def test_ota_http_rejects_disk_budget_before_reserving_operation(
    client, seed_royal, db_session, test_settings, tmp_path, monkeypatch
):
    host = tmp_path / "host"
    for name in ("inbox", "artifacts", "public", "ota-uploads"):
        (host / name).mkdir(parents=True)
    object.__setattr__(test_settings, "ops_host_root", str(host))
    login_as(client, "royal", "secret")
    content = ota_bytes(required_free_bytes=1)
    digest = hashlib.sha256(content).hexdigest()
    created = client.post(
        "/admin/ops/ota/uploads",
        json={"filename": "release.ota", "size": len(content), "sha256": digest},
    )
    assert created.status_code == 201
    upload_id = created.json()["upload_id"]
    patched = client.patch(
        f"/admin/ops/ota/uploads/{upload_id}",
        content=content,
        headers={"Upload-Offset": "0", "Content-Type": "application/offset+octet-stream"},
    )
    assert patched.status_code == 200
    assert client.post(f"/admin/ops/ota/uploads/{upload_id}/finalize").status_code == 200

    gib = 1024**3
    monkeypatch.setattr(
        ota_uploads.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=20 * gib, free=6 * gib + len(content) - 1),
    )

    operation_id = str(uuid4())
    response = client.post(
        "/admin/ops/operations",
        json={
            "operation_id": operation_id,
            "kind": "ota-update",
            "upload_id": upload_id,
            "sha256": digest,
            "version": "0.2.0-rc.7",
            "capability_revision": "b" * 64,
            "confirmation": "ЗАПУСТИТЬ OTA-UPDATE",
        },
    )

    assert response.status_code == 507
    assert response.json()["detail"] == "ota_insufficient_space"
    assert db_session.get(HostOperationStatus, operation_id) is None
    assert (host / "ota-uploads" / f"{upload_id}.ota").is_file()


def test_ota_operation_replay_returns_durable_receipt_after_host_consumes_upload(
    client, seed_royal, db_session, test_settings, tmp_path
):
    host = tmp_path / "host"
    for name in ("inbox", "artifacts", "public", "ota-uploads"):
        (host / name).mkdir(parents=True)
    object.__setattr__(test_settings, "ops_host_root", str(host))
    login_as(client, "royal", "secret")
    operation_id = str(uuid4())
    payload = {
        "operation_id": operation_id,
        "kind": "ota-update",
        "upload_id": str(uuid4()),
        "sha256": "a" * 64,
        "version": "0.2.0-rc.9",
        "capability_revision": "b" * 64,
        "confirmation": "ЗАПУСТИТЬ OTA-UPDATE",
    }
    operation_registry.reserve(
        db_session,
        operation_id=operation_id,
        actor_user_id=seed_royal.id,
        kind="ota-update",
        request_digest=operation_registry.request_digest(payload),
    )
    operation_registry.mark_accepted(db_session, operation_id=operation_id)

    response = client.post("/admin/ops/operations", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == operation_id
    assert response.json()["receipt_state"] == "accepted"
