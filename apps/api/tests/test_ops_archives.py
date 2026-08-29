"""Release/snapshot ZIP contract: kind, checksums, zip-slip, size caps."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

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


def test_checksum_mismatch_is_rejected(tmp_path: Path):
    payload = tmp_path / "tree"
    payload.mkdir()
    (payload / "a.txt").write_text("ok", encoding="utf-8")
    lying = io.BytesIO()
    with zipfile.ZipFile(lying, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "kind": KIND_RELEASE,
                    "format": FORMAT_VERSION,
                    "app_version": "1",
                    "files": {"a.txt": "0" * 64},
                }
            ),
        )
        zf.writestr("a.txt", "ok")
    with pytest.raises(ArchiveError, match="checksum_mismatch"):
        inspect_archive(lying.getvalue(), expected_kind=KIND_RELEASE)


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


def test_unpack_writes_only_under_dest(tmp_path: Path):
    payload = tmp_path / "tree"
    (payload / "apps" / "api").mkdir(parents=True)
    (payload / "apps" / "api" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    archive = build_archive(kind=KIND_RELEASE, source_root=payload, app_version="1")
    dest = tmp_path / "out"
    unpack_archive(archive, dest, expected_kind=KIND_RELEASE)
    assert (dest / "apps" / "api" / "ok.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (tmp_path / "etc").exists()
