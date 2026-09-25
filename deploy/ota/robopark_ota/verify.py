from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .model import OtaError, OtaFile, OtaManifest, VerifiedOta

DEFAULT_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 768 * 1024 * 1024
DEFAULT_MAX_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_MAX_MEMBERS = 20_000
DEFAULT_MAX_COMPRESSION_RATIO = 250
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,150}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_GIT_SHA = re.compile(r"^[a-f0-9]{40}$")
_MIGRATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,150}$")
_MANIFEST_KEYS = {
    "format_version",
    "app_version",
    "git_sha",
    "migration_head",
    "compatible_from",
    "required_free_bytes",
    "max_expanded_bytes",
    "changes",
    "files",
}
_FILE_KEYS = {"path", "size", "sha256"}


def _fail(code: str) -> None:
    raise OtaError(code)


def validate_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        _fail("ota_invalid_container")
    if name.startswith("/") or re.match(r"^[A-Za-z]:/", name):
        _fail("ota_invalid_container")
    path = PurePosixPath(name)
    parts = path.parts
    if (
        name.endswith("/")
        or "//" in name
        or not parts
        or any(part in {"", ".", ".."} for part in parts)
        or path.as_posix() != name
    ):
        _fail("ota_invalid_container")
    return name


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_int(value: object, *, maximum: int) -> int:
    if not _is_int(value) or not 0 < value <= maximum:
        _fail("ota_manifest_invalid")
    return value


def _text(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail("ota_manifest_invalid")
    return value


def _parse_manifest(raw: bytes) -> OtaManifest:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("ota_manifest_invalid")
    if not isinstance(document, dict) or set(document) != _MANIFEST_KEYS:
        _fail("ota_manifest_invalid")
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if canonical != raw:
        _fail("ota_manifest_invalid")
    if document["format_version"] != 1:
        _fail("ota_manifest_invalid")

    compatible = document["compatible_from"]
    changes = document["changes"]
    file_rows = document["files"]
    if (
        not isinstance(compatible, list)
        or len(compatible) > 256
        or any(not isinstance(item, str) or _VERSION.fullmatch(item) is None for item in compatible)
        or len(compatible) != len(set(compatible))
        or not isinstance(changes, list)
        or not 0 < len(changes) <= 100
        or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in changes)
        or not isinstance(file_rows, list)
        or not file_rows
    ):
        _fail("ota_manifest_invalid")

    files: list[OtaFile] = []
    seen: set[str] = set()
    for row in file_rows:
        if not isinstance(row, dict) or set(row) != _FILE_KEYS:
            _fail("ota_manifest_invalid")
        try:
            path = validate_member_name(row["path"])
        except OtaError:
            _fail("ota_manifest_invalid")
        if path == "manifest.json" or path in seen:
            _fail("ota_manifest_invalid")
        seen.add(path)
        size = row["size"]
        if not _is_int(size) or size < 0 or size > DEFAULT_MAX_FILE_BYTES:
            _fail("ota_manifest_invalid")
        digest = row["sha256"]
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            _fail("ota_manifest_invalid")
        files.append(OtaFile(path=path, size=size, sha256=digest))

    if "__main__.py" not in seen or tuple(sorted(seen)) != tuple(item.path for item in files):
        _fail("ota_manifest_invalid")

    return OtaManifest(
        format_version=1,
        app_version=_text(document["app_version"], _VERSION),
        git_sha=_text(document["git_sha"], _GIT_SHA),
        migration_head=_text(document["migration_head"], _MIGRATION),
        compatible_from=tuple(compatible),
        required_free_bytes=_positive_int(document["required_free_bytes"], maximum=2**63 - 1),
        max_expanded_bytes=_positive_int(
            document["max_expanded_bytes"], maximum=DEFAULT_MAX_EXPANDED_BYTES
        ),
        changes=tuple(item.strip() for item in changes),
        files=tuple(files),
    )


def _stream_digest(stream: BinaryIO, *, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = stream.read(CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            _fail("ota_invalid_container")
        digest.update(chunk)
    return total, digest.hexdigest()


def _whole_file(path: Path, *, max_archive_bytes: int) -> tuple[int, str]:
    try:
        with path.open("rb") as source:
            return _stream_digest(source, limit=max_archive_bytes)
    except (OSError, ValueError) as exc:
        raise OtaError("ota_invalid_container") from exc


def _regular_member(info: zipfile.ZipInfo) -> bool:
    if info.flag_bits & 0x1:
        return False
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    return file_type == 0 or file_type == stat.S_IFREG


def verify_ota(
    path: Path,
    *,
    expected_sha256: str | None = None,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
) -> VerifiedOta:
    path = Path(path)
    size, whole_digest = _whole_file(path, max_archive_bytes=max_archive_bytes)
    if expected_sha256 is not None and (
        _SHA256.fullmatch(expected_sha256) is None or whole_digest != expected_sha256
    ):
        _fail("ota_hash_mismatch")

    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = archive.infolist()
            if not members or len(members) > max_members:
                _fail("ota_invalid_container")
            names = [validate_member_name(info.filename) for info in members]
            if len(names) != len(set(names)) or names.count("manifest.json") != 1:
                _fail("ota_invalid_container")
            for info in members:
                if not _regular_member(info) or info.file_size > max_file_bytes:
                    _fail("ota_invalid_container")
                if info.file_size and (
                    info.compress_size <= 0
                    or info.file_size > info.compress_size * max_compression_ratio
                ):
                    _fail("ota_invalid_container")

            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                _fail("ota_manifest_invalid")
            with archive.open(manifest_info, "r") as source:
                raw_manifest = source.read(MAX_MANIFEST_BYTES + 1)
            if len(raw_manifest) != manifest_info.file_size:
                _fail("ota_manifest_invalid")
            manifest = _parse_manifest(raw_manifest)

            payload_infos = {
                info.filename: info for info in members if info.filename != "manifest.json"
            }
            declared = {item.path: item for item in manifest.files}
            if set(payload_infos) != set(declared):
                _fail("ota_manifest_invalid")
            expanded = sum(info.file_size for info in payload_infos.values())
            if expanded > manifest.max_expanded_bytes:
                _fail("ota_manifest_invalid")

            for name, expected in declared.items():
                info = payload_infos[name]
                if info.file_size != expected.size:
                    _fail("ota_hash_mismatch")
                with archive.open(info, "r") as source:
                    actual_size, digest = _stream_digest(source, limit=max_file_bytes)
                if actual_size != expected.size or digest != expected.sha256:
                    _fail("ota_hash_mismatch")
    except OtaError:
        raise
    except (OSError, KeyError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        raise OtaError("ota_invalid_container") from exc

    return VerifiedOta(path=path, sha256=whole_digest, size=size, manifest=manifest)
