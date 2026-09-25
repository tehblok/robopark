from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from conftest import login_as
from robopark_api.services.ops.ota_uploads import OtaUploadError, OtaUploadStore


def ota_bytes(
    version="0.2.0-rc.7",
    *,
    payload: bytes = b"print('ota')\n",
    compression: int = zipfile.ZIP_STORED,
) -> bytes:
    manifest = {
        "app_version": version,
        "changes": ["test"],
        "compatible_from": ["0.2.0-rc.6"],
        "files": [{"path": "__main__.py", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}],
        "format_version": 1,
        "git_sha": "a" * 40,
        "max_expanded_bytes": max(1024, len(payload)),
        "migration_head": "0050_media_action_dependency",
        "required_free_bytes": 1,
        "requirements": {
            "python": ">=3.10", "systems": ["armbian", "ubuntu"],
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


def test_store_enforces_owner_limits_expiry_and_hash(tmp_path):
    content = ota_bytes()
    digest = hashlib.sha256(content).hexdigest()
    clock = {"now": 1000.0}
    store = OtaUploadStore(
        tmp_path / "state", tmp_path / "host", chunk_bytes=1024,
        max_active_per_actor=1, max_active_global=2, ttl_seconds=10,
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
