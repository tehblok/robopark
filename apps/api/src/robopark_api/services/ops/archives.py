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
import stat
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

# Keep Python 3.10 compatibility for snapshot creation.
UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017
FORMAT_VERSION = 1
RELEASE_FORMAT_VERSION = 3
KIND_SNAPSHOT = "snapshot"
KIND_RELEASE = "release"
MANIFEST_NAME = "manifest.json"

_RELEASE_METADATA_DEFAULTS = {
    "min_installer_version": "0",
    "migration_compatibility": {},
    "required_capabilities": [],
    "update_notes": "",
}
_RELEASE_MANIFEST_KEYS = {
    "kind",
    "format",
    "app_version",
    "git_sha",
    "migration_head",
    "min_installer_version",
    "migration_compatibility",
    "required_capabilities",
    "created_at",
    "update_notes",
    "files",
}
_RELEASE_MANIFEST_V3_KEYS = (_RELEASE_MANIFEST_KEYS - {"created_at"}) | {
    "built_at",
    "eligible_channels",
    "support_class",
    "support_months",
    "build_id",
    "content_digest",
    "upgrade_policy",
}

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
    git_sha: str | None = None
    migration_head: str | None = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _release_manifest(
    *, app_version: str, files: dict[str, dict[str, int | str]], release_meta: dict | None
) -> dict:
    if not isinstance(release_meta, dict):
        raise ArchiveError("invalid_manifest")
    if set(release_meta) - (
        {
            "git_sha",
            "migration_head",
            "built_at",
            "eligible_channels",
            "support_class",
            "support_months",
            "build_id",
            "content_digest",
            "upgrade_policy",
            "signing_key_rotation",
        }
        | set(_RELEASE_METADATA_DEFAULTS)
    ):
        raise ArchiveError("invalid_manifest")
    metadata = {
        **_RELEASE_METADATA_DEFAULTS,
        "built_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        **release_meta,
    }
    prerelease = "-" in app_version
    lts = app_version.split("-", 1)[0].rsplit(".", 1)[0] == "1.0"
    descriptors = [
        {"path": name, "size": value["size"], "sha256": value["sha256"]}
        for name, value in sorted(files.items())
    ]
    canonical = json.dumps(descriptors, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    metadata.setdefault("eligible_channels", ["rc"] if prerelease else ["rc", "stable"])
    metadata.setdefault(
        "support_class", "candidate" if prerelease else "lts" if lts else "standard"
    )
    metadata.setdefault("support_months", 0 if prerelease else 24 if lts else 6)
    identity = (
        f"{app_version}\0{metadata.get('git_sha', '')}\0{metadata.get('migration_head', '')}"
    ).encode()
    metadata.setdefault("build_id", hashlib.sha256(identity).hexdigest()[:20])
    metadata.setdefault("content_digest", hashlib.sha256(canonical.encode()).hexdigest())
    metadata.setdefault("upgrade_policy", {"mode": "graph"})
    manifest = {
        "kind": KIND_RELEASE,
        "format": RELEASE_FORMAT_VERSION,
        "app_version": app_version,
        "files": files,
        **metadata,
    }
    _validate_release_manifest(manifest)
    return manifest


def _validate_release_manifest(manifest: object) -> tuple[dict[str, str], str, str]:
    if not isinstance(manifest, dict):
        raise ArchiveError("invalid_manifest")
    fmt = manifest.get("format")
    expected = _RELEASE_MANIFEST_KEYS if fmt == 2 else _RELEASE_MANIFEST_V3_KEYS
    if set(manifest) not in (expected, expected | {"signing_key_rotation"}):
        raise ArchiveError("invalid_manifest")
    if manifest.get("kind") != KIND_RELEASE or fmt not in {2, RELEASE_FORMAT_VERSION}:
        raise ArchiveError("unsupported_format")
    raise ArchiveError("legacy_release_update_removed")
    app_version = manifest.get("app_version")
    git_sha = manifest.get("git_sha")
    migration_head = manifest.get("migration_head")
    if not isinstance(app_version, str) or not app_version:
        raise ArchiveError("invalid_manifest")
    if (
        not isinstance(git_sha, str)
        or len(git_sha) != 40
        or any(c not in "0123456789abcdef" for c in git_sha.lower())
    ):
        raise ArchiveError("invalid_manifest")
    if not isinstance(migration_head, str) or not migration_head:
        raise ArchiveError("invalid_manifest")
    if not isinstance(manifest.get("min_installer_version"), str):
        raise ArchiveError("invalid_manifest")
    if not isinstance(manifest.get("migration_compatibility"), dict):
        raise ArchiveError("invalid_manifest")
    if not isinstance(manifest.get("required_capabilities"), list) or not all(
        isinstance(value, str) and value for value in manifest["required_capabilities"]
    ):
        raise ArchiveError("invalid_manifest")
    timestamp_key = "created_at" if fmt == 2 else "built_at"
    if not isinstance(manifest.get(timestamp_key), str) or not manifest[timestamp_key]:
        raise ArchiveError("invalid_manifest")
    if not isinstance(manifest.get("update_notes"), str):
        raise ArchiveError("invalid_manifest")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ArchiveError("invalid_manifest")
    listed: dict[str, str] = {}
    for name, descriptor in files.items():
        if not isinstance(name, str) or not isinstance(descriptor, dict):
            raise ArchiveError("invalid_manifest")
        if set(descriptor) != {"sha256", "size"}:
            raise ArchiveError("invalid_manifest")
        digest = descriptor.get("sha256")
        size = descriptor.get("size")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest.lower())
        ):
            raise ArchiveError("invalid_manifest")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ArchiveError("invalid_manifest")
        normalized = _normalize_member(name)
        if normalized == MANIFEST_NAME or normalized in listed:
            raise ArchiveError("invalid_manifest")
        listed[normalized] = digest
    if fmt == 3:
        descriptors = [
            {"path": name, "size": value["size"], "sha256": value["sha256"]}
            for name, value in sorted(files.items())
        ]
        canonical = json.dumps(
            descriptors, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if sha256_bytes(canonical.encode()) != manifest["content_digest"]:
            raise ArchiveError("invalid_manifest")
    return listed, git_sha, migration_head


def build_archive(
    *,
    kind: str,
    source_root: Path,
    app_version: str,
    extra: dict | None = None,
    release_meta: dict | None = None,
    signing_key: bytes | None = None,
) -> bytes:
    if kind not in {KIND_SNAPSHOT, KIND_RELEASE}:
        raise ArchiveError("unexpected_kind")
    source_root = source_root.resolve()
    files: dict[str, str] = {}
    release_files: dict[str, dict[str, int | str]] = {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in _iter_files(source_root):
            rel = path.relative_to(source_root).as_posix()
            if rel == MANIFEST_NAME:
                raise ArchiveError("unsafe_path")
            data = path.read_bytes()
            files[rel] = sha256_bytes(data)
            release_files[rel] = {"sha256": files[rel], "size": len(data)}
            zf.writestr(rel, data)
        if kind == KIND_RELEASE:
            raise ArchiveError("legacy_release_update_removed")
        else:
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
    public_key: bytes | None = None,
) -> ArchiveMeta:
    raw = data if isinstance(data, bytes) else data.read()
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("archive_too_large")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ArchiveError("invalid_zip") from exc
    with zf:
        return _inspect_open(zf, expected_kind=expected_kind, public_key=public_key)


def _inspect_open(
    zf: zipfile.ZipFile, *, expected_kind: str | None, public_key: bytes | None
) -> ArchiveMeta:
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
        manifest_raw = zf.read(MANIFEST_NAME)
    except KeyError as exc:
        raise ArchiveError("missing_manifest") from exc
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
    git_sha: str | None = None
    migration_head: str | None = None
    if kind == KIND_RELEASE:
        raise ArchiveError("legacy_release_update_removed")
    else:
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
        actual_data = zf.read(rel)
        if kind == KIND_RELEASE and len(actual_data) != manifest["files"][rel]["size"]:
            raise ArchiveError("checksum_mismatch")
        actual = sha256_bytes(actual_data)
        if actual != expected:
            raise ArchiveError("checksum_mismatch")
    return ArchiveMeta(
        kind=kind,
        format=fmt,
        app_version=manifest["app_version"],
        files=listed,
        git_sha=git_sha,
        migration_head=migration_head,
    )


def unpack_archive(
    data: bytes,
    dest: Path,
    *,
    expected_kind: str,
    public_key: bytes | None = None,
) -> ArchiveMeta:
    meta = inspect_archive(data, expected_kind=expected_kind, public_key=public_key)
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
