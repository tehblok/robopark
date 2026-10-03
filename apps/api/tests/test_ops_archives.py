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
    env.write_text("APP_MODE=test\n", encoding="utf-8")

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


def test_snapshot_builder_rejects_large_input_before_reading_it(tmp_path, monkeypatch):
    from robopark_api.services.ops import archives

    root = tmp_path / "tree"
    root.mkdir()
    source = root / "large.bin"
    source.write_bytes(b"x" * 17)
    monkeypatch.setattr(archives, "MAX_UNCOMPRESSED_BYTES", 16)
    original_read = Path.read_bytes

    def guarded_read(path):
        if path == source:
            raise AssertionError("source was loaded into memory")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    with pytest.raises(ArchiveError, match="archive_too_large"):
        build_archive(kind=KIND_SNAPSHOT, source_root=root, app_version="0.1.0")


@pytest.mark.parametrize("with_comment", [False, True])
def test_snapshot_builder_and_inspector_use_bounded_reads(tmp_path, monkeypatch, with_comment):
    root = tmp_path / "tree"
    root.mkdir()
    source = root / "sample.bin"
    # A compressible tiny archive cannot reveal accidental whole-archive reads.
    payload = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(8192))
    source.write_bytes(payload)
    original_read = Path.read_bytes

    def guarded_read(path):
        if path == source:
            raise AssertionError("source was loaded into memory")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    archive = build_archive(kind=KIND_SNAPSHOT, source_root=root, app_version="0.1.0")
    if with_comment:
        commented = io.BytesIO(archive)
        with zipfile.ZipFile(commented, "a") as writer:
            writer.comment = b"bounded ZIP directory search"
        archive = commented.getvalue()
    # ZIP's EOCD record is 22 bytes with an optional 65535-byte comment.
    tail_limit = 65535 + 22
    assert len(archive) > tail_limit

    class BoundedReader(io.BytesIO):
        def read(self, size=-1):
            # Python 3.12 zipfile seeks to the bounded tail then calls read().
            remaining = len(self.getbuffer()) - self.tell()
            if size < 0 and remaining > tail_limit:
                raise AssertionError("archive was loaded into memory")
            if size > 1024 * 1024:
                raise AssertionError("archive read exceeded the chunk budget")
            return super().read(size)

    # Negative control: the guard must reject the regression it protects against.
    with pytest.raises(AssertionError, match="archive was loaded into memory"):
        BoundedReader(archive).read()
    meta = inspect_archive(BoundedReader(archive), expected_kind=KIND_SNAPSHOT)
    assert meta.files["sample.bin"] == _sha256(payload)


def test_streamed_snapshot_inspection_rejects_changed_member(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "sample.txt").write_text("original")
    archive = build_archive(kind=KIND_SNAPSHOT, source_root=root, app_version="0.1.0")
    damaged = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(damaged, "w") as out:
        for name in source.namelist():
            out.writestr(name, b"changed" if name == "sample.txt" else source.read(name))

    with pytest.raises(ArchiveError, match="checksum_mismatch"):
        inspect_archive(damaged, expected_kind=KIND_SNAPSHOT)


def test_nonseekable_snapshot_stream_may_return_short_chunks(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "sample.txt").write_text("original")
    archive = build_archive(kind=KIND_SNAPSHOT, source_root=root, app_version="0.1.0")

    class ShortReader:
        def __init__(self, payload):
            self.payload = io.BytesIO(payload)

        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            return self.payload.read(min(size, 7))

    meta = inspect_archive(ShortReader(archive), expected_kind=KIND_SNAPSHOT)
    assert meta.files["sample.txt"] == _sha256(b"original")


def test_snapshot_manifest_is_bounded_before_json_decoding(monkeypatch):
    from robopark_api.services.ops import archives

    monkeypatch.setattr(archives, "MAX_MANIFEST_BYTES", 80)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("manifest.json", b" " * 81)

    with pytest.raises(ArchiveError, match="invalid_manifest"):
        inspect_archive(archive, expected_kind=KIND_SNAPSHOT)


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


def test_legacy_release_builder_rejects_before_reading_source(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    root.mkdir()
    source = root / "large.bin"
    source.write_bytes(b"legacy payload")
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == source:
            raise AssertionError("retired release source was opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(ArchiveError, match="legacy_release_update_removed"):
        build_archive(kind=KIND_RELEASE, source_root=root, app_version="0.1.0")


def test_legacy_release_inspector_rejects_release_manifest():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("manifest.json", json.dumps({"kind": KIND_RELEASE, "format": 3}))
    with pytest.raises(ArchiveError, match="legacy_release_update_removed"):
        inspect_archive(archive, expected_kind=KIND_RELEASE)


def test_zip_slip_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "kind": KIND_SNAPSHOT,
                    "format": FORMAT_VERSION,
                    "app_version": "1",
                    "files": {"../etc/passwd": _sha256(b"x")},
                }
            ),
        )
        zf.writestr("../etc/passwd", b"x")
    with pytest.raises(ArchiveError, match="unsafe_path"):
        inspect_archive(buf.getvalue(), expected_kind=KIND_SNAPSHOT)


def test_unpack_snapshot_writes_only_under_dest(tmp_path: Path):
    payload = tmp_path / "tree"
    (payload / "data").mkdir(parents=True)
    (payload / "data" / "ok.txt").write_text("snapshot\n", encoding="utf-8")
    archive = build_archive(kind=KIND_SNAPSHOT, source_root=payload, app_version="0.1.0")
    dest = tmp_path / "out"
    unpack_archive(archive, dest, expected_kind=KIND_SNAPSHOT)
    assert (dest / "data" / "ok.txt").read_text(encoding="utf-8") == "snapshot\n"
    assert not (tmp_path / "etc").exists()


def test_unpack_snapshot_streams_large_member_after_validation(tmp_path, monkeypatch):
    payload = tmp_path / "tree"
    (payload / "data").mkdir(parents=True)
    expected = bytes(range(256)) * 100
    (payload / "data" / "blob.bin").write_bytes(expected)
    archive = build_archive(kind=KIND_SNAPSHOT, source_root=payload, app_version="0.1.0")
    original_read = zipfile.ZipFile.read

    def guarded_read(file, name, *args, **kwargs):
        if name == "data/blob.bin":
            raise AssertionError("snapshot member was loaded into memory")
        return original_read(file, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "read", guarded_read)
    dest = tmp_path / "out"
    unpack_archive(archive, dest, expected_kind=KIND_SNAPSHOT)
    assert (dest / "data" / "blob.bin").read_bytes() == expected
