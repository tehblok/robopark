"""Release/snapshot ZIP contract: kind, checksums, zip-slip, size caps."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from robopark_api.services.ops.archives import (
    FORMAT_VERSION,
    KIND_RELEASE,
    KIND_SNAPSHOT,
    ArchiveError,
    build_archive,
    inspect_archive,
    unpack_archive,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def release_keys() -> tuple[bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


def _signed_release(root: Path, keys: tuple[bytes, bytes], *, version: str = "1") -> bytes:
    return build_archive(
        kind=KIND_RELEASE,
        source_root=root,
        app_version=version,
        release_meta={"git_sha": "a" * 40, "migration_head": "0017_driver_work_reports"},
        signing_key=keys[0],
    )


def test_build_and_inspect_snapshot_roundtrip(tmp_path: Path):
    payload = tmp_path / "tree"
    (payload / "data").mkdir(parents=True)
    db = payload / "data" / "robopark.db"
    db.write_bytes(b"sqlite-bytes")
    env = payload / "config" / "host.env"
    env.parent.mkdir()
    env.write_text("SECRET_KEY=test\n", encoding="utf-8")

    archive = build_archive(
        kind=KIND_SNAPSHOT,
        source_root=payload,
        app_version="0.1.0",
    )
    meta = inspect_archive(archive)
    assert meta.kind == KIND_SNAPSHOT
    assert meta.format == FORMAT_VERSION
    assert meta.app_version == "0.1.0"
    assert meta.files["data/robopark.db"] == _sha256(b"sqlite-bytes")


def test_release_rejects_snapshot_kind():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        manifest = {
            "kind": KIND_SNAPSHOT,
            "format": FORMAT_VERSION,
            "app_version": "0.1.0",
            "files": {},
        }
        zf.writestr("manifest.json", json.dumps(manifest))
    buf.seek(0)
    with pytest.raises(ArchiveError, match="unexpected_kind"):
        inspect_archive(buf.getvalue(), expected_kind=KIND_RELEASE)


def test_checksum_mismatch_is_rejected(tmp_path: Path, release_keys: tuple[bytes, bytes]):
    payload = tmp_path / "tree"
    payload.mkdir()
    (payload / "a.txt").write_text("ok", encoding="utf-8")
    source = zipfile.ZipFile(io.BytesIO(_signed_release(payload, release_keys)))
    lying = io.BytesIO()
    with source, zipfile.ZipFile(lying, "w") as zf:
        for info in source.infolist():
            zf.writestr(
                info.filename,
                b"changed" if info.filename == "a.txt" else source.read(info.filename),
            )
    with pytest.raises(ArchiveError, match="checksum_mismatch"):
        inspect_archive(lying.getvalue(), expected_kind=KIND_RELEASE, public_key=release_keys[1])


def test_zip_slip_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "kind": KIND_RELEASE,
                    "format": FORMAT_VERSION,
                    "app_version": "1",
                    "files": {"../etc/passwd": _sha256(b"x")},
                }
            ),
        )
        zf.writestr("../etc/passwd", b"x")
    with pytest.raises(ArchiveError, match="unsafe_path"):
        inspect_archive(buf.getvalue(), expected_kind=KIND_RELEASE)


def test_unpack_writes_only_under_dest(tmp_path: Path, release_keys: tuple[bytes, bytes]):
    payload = tmp_path / "tree"
    (payload / "apps" / "api").mkdir(parents=True)
    (payload / "apps" / "api" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    archive = _signed_release(payload, release_keys)
    dest = tmp_path / "out"
    unpack_archive(archive, dest, expected_kind=KIND_RELEASE, public_key=release_keys[1])
    assert (dest / "apps" / "api" / "ok.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (tmp_path / "etc").exists()
