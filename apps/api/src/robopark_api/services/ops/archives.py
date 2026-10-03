"""Typed ZIP archives for local snapshots.

A Robopark archive is a ZIP with ``manifest.json`` at the root:

* ``kind`` — ``snapshot`` (data+config)
* ``format`` — integer schema version
* ``app_version`` — string from the packing side
* ``files`` — relative path → sha256 hex of uncompressed bytes

Members other than the manifest must appear in ``files``. Paths are POSIX,
relative, and must not escape the unpack root.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import stat
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

FORMAT_VERSION = 1
KIND_SNAPSHOT = "snapshot"
# Older callers still pass this value; those release ZIPs are explicitly retired.
KIND_RELEASE = "release"
MANIFEST_NAME = "manifest.json"

# Large SQLite files need headroom; zip bombs do not.
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_COUNT = 20_000
MAX_COMPRESSION_RATIO = 200
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024


class ArchiveError(ValueError):
    """Invalid or unsafe archive. ``args[0]`` is a stable detail token."""


@dataclass(frozen=True)
class ArchiveMeta:
    kind: str
    format: int
    app_version: str
    files: dict[str, str]
    git_sha: str | None = None
    migration_head: str | None = None


def _normalize_member(name: str) -> str:
    if not isinstance(name, str) or not name or name != name.strip() or "\\" in name:
        raise ArchiveError("unsafe_path")
    if name == MANIFEST_NAME:
        return name
    if name.startswith("/") or "\x00" in name or name.endswith("/"):
        raise ArchiveError("unsafe_path")
    parts = name.split("/")
    if any(part in {"", ".", ".."} or part != part.strip() for part in parts):
        raise ArchiveError("unsafe_path")
    return name


def _iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ArchiveError("unsafe_path")
        if path.is_file():
            yield path


def build_archive(
    *,
    kind: str,
    source_root: Path,
    app_version: str,
    extra: dict | None = None,
) -> bytes:
    buffer = io.BytesIO()
    write_archive(
        kind=kind, source_root=source_root, app_version=app_version, destination=buffer, extra=extra
    )
    return buffer.getvalue()


def write_archive(
    *,
    kind: str,
    source_root: Path,
    app_version: str,
    destination: BinaryIO,
    extra: dict | None = None,
) -> None:
    """Write the same bounded snapshot format without retaining the ZIP in RAM."""
    if kind == KIND_RELEASE:
        raise ArchiveError("legacy_release_update_removed")
    if kind != KIND_SNAPSHOT:
        raise ArchiveError("unexpected_kind")
    source_root = source_root.resolve()
    files: dict[str, str] = {}
    uncompressed = 0
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in _iter_files(source_root):
            rel = path.relative_to(source_root).as_posix()
            if rel == MANIFEST_NAME:
                raise ArchiveError("unsafe_path")
            if len(files) >= MAX_MEMBER_COUNT - 1:
                raise ArchiveError("too_many_files")
            size = path.stat().st_size
            if uncompressed + size > MAX_UNCOMPRESSED_BYTES:
                raise ArchiveError("archive_too_large")
            digest = hashlib.sha256()
            written = 0
            with path.open("rb") as source, zf.open(rel, "w") as target:
                for chunk in iter(lambda: source.read(COPY_CHUNK_BYTES), b""):
                    written += len(chunk)
                    if uncompressed + written > MAX_UNCOMPRESSED_BYTES:
                        raise ArchiveError("archive_too_large")
                    digest.update(chunk)
                    target.write(chunk)
            uncompressed += written
            files[rel] = digest.hexdigest()
            if destination.tell() > MAX_ARCHIVE_BYTES:
                raise ArchiveError("archive_too_large")
        manifest = {
            "kind": kind,
            "format": FORMAT_VERSION,
            "app_version": app_version,
            "files": files,
        }
        if extra:
            manifest["extra"] = extra
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    if destination.tell() > MAX_ARCHIVE_BYTES:
        raise ArchiveError("archive_too_large")


def inspect_archive(
    data: bytes | BinaryIO,
    *,
    expected_kind: str | None = None,
) -> ArchiveMeta:
    if isinstance(data, bytes):
        if len(data) > MAX_ARCHIVE_BYTES:
            raise ArchiveError("archive_too_large")
        source: BinaryIO = io.BytesIO(data)
    else:
        try:
            position = data.tell()
            data.seek(0, io.SEEK_END)
            length = data.tell()
            data.seek(position)
        except (AttributeError, OSError, ValueError):
            source = io.BytesIO()
            while chunk := data.read(min(COPY_CHUNK_BYTES, MAX_ARCHIVE_BYTES - source.tell() + 1)):
                source.write(chunk)
                if source.tell() > MAX_ARCHIVE_BYTES:
                    raise ArchiveError("archive_too_large") from None
            source.seek(0)
        else:
            if length > MAX_ARCHIVE_BYTES:
                raise ArchiveError("archive_too_large")
            source = data
    try:
        zf = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("invalid_zip") from exc
    with zf:
        return _inspect_open(zf, expected_kind=expected_kind)


def _inspect_open(zf: zipfile.ZipFile, *, expected_kind: str | None) -> ArchiveMeta:
    names = zf.namelist()
    if len(names) > MAX_MEMBER_COUNT:
        raise ArchiveError("too_many_files")
    if len(names) != len(set(names)):
        raise ArchiveError("duplicate_member")
    uncompressed = 0
    for info in zf.infolist():
        mode = (info.external_attr >> 16) & 0o170000
        if mode not in {0, stat.S_IFREG}:
            raise ArchiveError("unsafe_path")
        _normalize_member(info.filename)
        uncompressed += max(info.file_size, 0)
        if uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise ArchiveError("archive_too_large")
        if (
            info.compress_size
            and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO
        ):
            raise ArchiveError("archive_too_large")
    try:
        with zf.open(MANIFEST_NAME) as manifest_file:
            manifest_raw = manifest_file.read(MAX_MANIFEST_BYTES + 1)
    except KeyError as exc:
        raise ArchiveError("missing_manifest") from exc
    if len(manifest_raw) > MAX_MANIFEST_BYTES:
        raise ArchiveError("invalid_manifest")
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("invalid_manifest") from exc
    if not isinstance(manifest, dict):
        raise ArchiveError("invalid_manifest")
    kind = manifest.get("kind")
    fmt = manifest.get("format")
    if kind not in {KIND_SNAPSHOT, KIND_RELEASE}:
        raise ArchiveError("unexpected_kind")
    if expected_kind is not None and kind != expected_kind:
        raise ArchiveError("unexpected_kind")
    if kind == KIND_RELEASE:
        raise ArchiveError("legacy_release_update_removed")
    app_version = manifest.get("app_version")
    files = manifest.get("files")
    if fmt != FORMAT_VERSION:
        raise ArchiveError("unsupported_format")
    if not isinstance(app_version, str) or not app_version or not isinstance(files, dict):
        raise ArchiveError("invalid_manifest")
    listed = {}
    for name, digest in files.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ArchiveError("invalid_manifest")
        normalized = _normalize_member(name)
        if normalized in listed:
            raise ArchiveError("invalid_manifest")
        listed[normalized] = digest
    members = [_normalize_member(name) for name in names]
    member_set = set(members)
    if MANIFEST_NAME not in member_set:
        raise ArchiveError("missing_manifest")
    payload_members = member_set - {MANIFEST_NAME}
    listed_set = set(listed)
    if payload_members != listed_set:
        raise ArchiveError("manifest_files_mismatch")

    for rel, expected in listed.items():
        digest = hashlib.sha256()
        with zf.open(rel) as member:
            for chunk in iter(lambda: member.read(COPY_CHUNK_BYTES), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ArchiveError("checksum_mismatch")
    return ArchiveMeta(
        kind=kind,
        format=fmt,
        app_version=manifest["app_version"],
        files=listed,
    )


def unpack_archive(
    data: bytes,
    dest: Path,
    *,
    expected_kind: str,
) -> ArchiveMeta:
    meta = inspect_archive(data, expected_kind=expected_kind)
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if name.endswith("/") or name == MANIFEST_NAME:
                continue
            rel = _normalize_member(name)
            target = (dest / rel).resolve()
            if not target.is_relative_to(dest):
                raise ArchiveError("unsafe_path")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=COPY_CHUNK_BYTES)
    return meta
