"""Typed ZIP archives for snapshots and releases.

A Robopark archive is a ZIP with ``manifest.json`` at the root:

* ``kind`` — ``snapshot`` (data+config) or ``release`` (application tree)
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
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

FORMAT_VERSION = 1
KIND_SNAPSHOT = "snapshot"
KIND_RELEASE = "release"
MANIFEST_NAME = "manifest.json"

# Caps apply to both kinds. Large SQLite files need headroom; zip bombs do not.
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_COUNT = 20_000
MAX_COMPRESSION_RATIO = 200


class ArchiveError(ValueError):
    """Invalid or unsafe archive. ``args[0]`` is a stable detail token."""


@dataclass(frozen=True)
class ArchiveMeta:
    kind: str
    format: int
    app_version: str
    files: dict[str, str]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_member(name: str) -> str:
    raw = name.replace("\\", "/").strip()
    if not raw or raw.endswith("/"):
        raise ArchiveError("unsafe_path")
    if raw == MANIFEST_NAME:
        return raw
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ArchiveError("unsafe_path")
    return path.as_posix()


def _iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def build_archive(
    *,
    kind: str,
    source_root: Path,
    app_version: str,
    extra: dict | None = None,
) -> bytes:
    if kind not in {KIND_SNAPSHOT, KIND_RELEASE}:
        raise ArchiveError("unexpected_kind")
    source_root = source_root.resolve()
    files: dict[str, str] = {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in _iter_files(source_root):
            rel = path.relative_to(source_root).as_posix()
            if rel == MANIFEST_NAME:
                raise ArchiveError("unsafe_path")
            data = path.read_bytes()
            files[rel] = sha256_bytes(data)
            zf.writestr(rel, data)
        manifest = {
            "kind": kind,
            "format": FORMAT_VERSION,
            "app_version": app_version,
            "files": files,
        }
        if extra:
            manifest["extra"] = extra
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    payload = buffer.getvalue()
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("archive_too_large")
    return payload


def inspect_archive(
    data: bytes | BinaryIO,
    *,
    expected_kind: str | None = None,
) -> ArchiveMeta:
    raw = data if isinstance(data, bytes) else data.read()
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("archive_too_large")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ArchiveError("invalid_zip") from exc
    with zf:
        return _inspect_open(zf, expected_kind=expected_kind)


def _inspect_open(zf: zipfile.ZipFile, *, expected_kind: str | None) -> ArchiveMeta:
    names = zf.namelist()
    if len(names) > MAX_MEMBER_COUNT:
        raise ArchiveError("too_many_files")
    uncompressed = 0
    for info in zf.infolist():
        uncompressed += max(info.file_size, 0)
        if uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise ArchiveError("archive_too_large")
        if (
            info.compress_size
            and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO
        ):
            raise ArchiveError("archive_too_large")
    try:
        manifest_raw = zf.read(MANIFEST_NAME)
    except KeyError as exc:
        raise ArchiveError("missing_manifest") from exc
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("invalid_manifest") from exc
    kind = manifest.get("kind")
    fmt = manifest.get("format")
    app_version = manifest.get("app_version")
    files = manifest.get("files")
    if kind not in {KIND_SNAPSHOT, KIND_RELEASE}:
        raise ArchiveError("unexpected_kind")
    if expected_kind is not None and kind != expected_kind:
        raise ArchiveError("unexpected_kind")
    if fmt != FORMAT_VERSION:
        raise ArchiveError("unsupported_format")
    if not isinstance(app_version, str) or not app_version:
        raise ArchiveError("invalid_manifest")
    if not isinstance(files, dict):
        raise ArchiveError("invalid_manifest")

    listed = {_normalize_member(str(name)): digest for name, digest in files.items()}
    members = []
    for name in names:
        if name.endswith("/"):
            continue
        members.append(_normalize_member(name))
    member_set = set(members)
    if MANIFEST_NAME not in member_set:
        raise ArchiveError("missing_manifest")
    payload_members = member_set - {MANIFEST_NAME}
    listed_set = set(listed)
    if payload_members != listed_set:
        raise ArchiveError("manifest_files_mismatch")

    for rel, expected in listed.items():
        actual = sha256_bytes(zf.read(rel))
        if actual != expected:
            raise ArchiveError("checksum_mismatch")
    return ArchiveMeta(kind=kind, format=fmt, app_version=app_version, files=listed)


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
            target.write_bytes(zf.read(name))
    return meta
