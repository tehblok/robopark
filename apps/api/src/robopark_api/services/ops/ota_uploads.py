"""Bounded resumable OTA uploads; the privileged host verifies packages again."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import stat
import time
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_CHUNK_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 20_000
MAX_FILE_BYTES = 768 * 1024 * 1024
MAX_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250
VERIFY_CHUNK_BYTES = 1024 * 1024
MIN_HOST_FREE_BYTES = 6 * 1024**3


class OtaUploadError(ValueError):
    pass


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class OtaUploadRecord:
    upload_id: UUID
    filename: str
    size: int
    sha256: str
    offset: int
    expires_at: float
    state: str
    chunk_size: int
    version: str | None = None
    changes: tuple[str, ...] = ()
    compatible_from: tuple[str, ...] = ()
    required_free_bytes: int | None = None
    already_present: bool = False


def _safe_member(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise OtaUploadError("ota_invalid_container")
    path = PurePosixPath(name)
    if (
        name.startswith("/")
        or path.as_posix() != name
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise OtaUploadError("ota_invalid_container")
    return name


def _stream_member_digest(source, *, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while chunk := source.read(VERIFY_CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise OtaUploadError("ota_invalid_container")
        digest.update(chunk)
    return total, digest.hexdigest()


def _inspect_package(path: Path) -> tuple[str, tuple[str, ...], tuple[str, ...], int]:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [_safe_member(item.filename) for item in infos]
            if (
                not infos
                or len(infos) > MAX_MEMBERS
                or len(names) != len(set(names))
                or names.count("manifest.json") != 1
            ):
                raise OtaUploadError("ota_invalid_container")
            for info in infos:
                mode = info.external_attr >> 16
                if (
                    info.flag_bits & 1
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                    or info.file_size > MAX_FILE_BYTES
                    or (
                        info.file_size
                        and (
                            info.compress_size <= 0
                            or info.file_size > info.compress_size * MAX_COMPRESSION_RATIO
                        )
                    )
                ):
                    raise OtaUploadError("ota_invalid_container")
            info = archive.getinfo("manifest.json")
            if info.file_size > MAX_MANIFEST_BYTES:
                raise OtaUploadError("ota_manifest_invalid")
            raw = archive.read(info)
            manifest = json.loads(raw.decode("utf-8"))
            if (
                json.dumps(
                    manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
                != raw
            ):
                raise OtaUploadError("ota_manifest_invalid")
            expected = {
                "format_version",
                "app_version",
                "git_sha",
                "migration_head",
                "compatible_from",
                "required_free_bytes",
                "max_expanded_bytes",
                "changes",
                "requirements",
                "files",
            }
            if not isinstance(manifest, dict) or set(manifest) != expected:
                raise OtaUploadError("ota_manifest_invalid")
            version = manifest["app_version"]
            changes = manifest["changes"]
            compatible = manifest["compatible_from"]
            files = manifest["files"]
            max_expanded = manifest["max_expanded_bytes"]
            required_free = manifest["required_free_bytes"]
            if (
                manifest["format_version"] != 1
                or not isinstance(version, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,150}", version) is None
                or not isinstance(changes, list)
                or not isinstance(compatible, list)
                or not isinstance(files, list)
                or type(max_expanded) is not int
                or not 0 < max_expanded <= MAX_EXPANDED_BYTES
                or type(required_free) is not int
                or not 0 < required_free <= 2**63 - 1
            ):
                raise OtaUploadError("ota_manifest_invalid")
            declared = {}
            for row in files:
                if not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}:
                    raise OtaUploadError("ota_manifest_invalid")
                member = _safe_member(row["path"])
                if (
                    member in declared
                    or type(row["size"]) is not int
                    or not 0 <= row["size"] <= MAX_FILE_BYTES
                    or not isinstance(row["sha256"], str)
                    or re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) is None
                ):
                    raise OtaUploadError("ota_manifest_invalid")
                declared[member] = row
            if set(declared) != set(names) - {"manifest.json"} or "__main__.py" not in declared:
                raise OtaUploadError("ota_manifest_invalid")
            if sum(archive.getinfo(member).file_size for member in declared) > max_expanded:
                raise OtaUploadError("ota_manifest_invalid")
            for member, row in declared.items():
                member_info = archive.getinfo(member)
                if member_info.file_size != row["size"]:
                    raise OtaUploadError("ota_hash_mismatch")
                with archive.open(member_info, "r") as source:
                    actual_size, digest = _stream_member_digest(
                        source,
                        limit=MAX_FILE_BYTES,
                    )
                if actual_size != row["size"] or digest != row["sha256"]:
                    raise OtaUploadError("ota_hash_mismatch")
            return (
                version,
                tuple(str(item)[:500] for item in changes[:100]),
                tuple(compatible[:256]),
                required_free,
            )
    except OtaUploadError:
        raise
    except (OSError, ValueError, UnicodeError, KeyError, zipfile.BadZipFile) as exc:
        raise OtaUploadError("ota_invalid_container") from exc


class OtaUploadStore:
    def __init__(
        self,
        state_root: Path,
        host_root: Path,
        *,
        max_bytes: int = MAX_UPLOAD_BYTES,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        max_active_per_actor: int = 2,
        max_active_global: int = 8,
        ttl_seconds: int = 24 * 60 * 60,
        reserve_bytes: int = 512 * 1024 * 1024,
        now=time.time,
    ) -> None:
        self.state_root = Path(state_root)
        self.host_root = Path(host_root)
        self.max_bytes = max_bytes
        self.chunk_bytes = chunk_bytes
        self.max_active_per_actor = max_active_per_actor
        self.max_active_global = max_active_global
        self.ttl_seconds = ttl_seconds
        self.reserve_bytes = reserve_bytes
        self.now = now
        for directory in (self.state_root, self.host_root):
            if directory.is_symlink():
                raise OtaUploadError("ota_upload_storage_unsafe")
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def _locked(self, name: str):
        del name
        lock = self.state_root / ".uploads.lock"
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _meta(self, identity: UUID) -> Path:
        return self.state_root / f"{identity}.json"

    def _part(self, identity: UUID) -> Path:
        return self.state_root / f"{identity}.part"

    def _host(self, identity: UUID) -> Path:
        return self.host_root / f"{identity}.ota"

    def _write(self, identity: UUID, value: dict) -> None:
        target = self._meta(identity)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            _fsync_directory(self.state_root)
        finally:
            temporary.unlink(missing_ok=True)

    def _read(self, identity: UUID | str) -> dict:
        try:
            parsed = UUID(str(identity))
            if str(parsed) != str(identity):
                raise ValueError()
            descriptor = os.open(self._meta(parsed), os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
                    raise ValueError()
                value = json.load(stream)
            if not isinstance(value, dict) or value.get("upload_id") != str(parsed):
                raise ValueError()
            expires_at = value.get("expires_at")
            if type(expires_at) not in (int, float) or not math.isfinite(expires_at):
                raise ValueError()
            if value.get("state") not in {"uploading", "verified"}:
                raise ValueError()
            return value
        except FileNotFoundError as exc:
            raise OtaUploadError("ota_upload_not_found") from exc
        except (OSError, ValueError, UnicodeError) as exc:
            raise OtaUploadError("ota_upload_state_invalid") from exc

    def _record(self, value: dict) -> OtaUploadRecord:
        required_free = value.get("required_free_bytes")
        if required_free is not None and (
            type(required_free) is not int or not 0 < required_free <= 2**63 - 1
        ):
            raise OtaUploadError("ota_upload_state_invalid")
        return OtaUploadRecord(
            upload_id=UUID(value["upload_id"]),
            filename=value["filename"],
            size=value["size"],
            sha256=value["sha256"],
            offset=value["offset"],
            expires_at=value["expires_at"],
            state=value["state"],
            chunk_size=self.chunk_bytes,
            version=value.get("version"),
            changes=tuple(value.get("changes", ())),
            compatible_from=tuple(value.get("compatible_from", ())),
            required_free_bytes=required_free,
            already_present=bool(value.get("already_present", False)),
        )

    def _free_floor(self, total: int) -> int:
        return max(self.reserve_bytes, MIN_HOST_FREE_BYTES, int(total * 0.15))

    def list_owned(self, *, actor_id: int) -> list[OtaUploadRecord]:
        with self._locked("global"):
            records = []
            for index, meta in enumerate(self.state_root.glob("*.json")):
                if index >= 512:
                    raise OtaUploadError("ota_upload_state_invalid")
                try:
                    row = self._read(meta.stem)
                    if (
                        row.get("actor_id") != actor_id
                        or row.get("operation_id")
                        or row["expires_at"] < self.now()
                    ):
                        continue
                    identity = UUID(row["upload_id"])
                    path = (
                        self._host(identity) if row["state"] == "verified" else self._part(identity)
                    )
                    if path.is_symlink() or not path.is_file():
                        continue
                    records.append(self._record(row))
                except (OtaUploadError, KeyError, TypeError, ValueError):
                    continue
            return sorted(records, key=lambda row: (row.expires_at, str(row.upload_id)))

    def create(self, *, actor_id: int, filename: str, size: int, sha256: str) -> OtaUploadRecord:
        with self._locked("global"):
            return self._create(actor_id=actor_id, filename=filename, size=size, sha256=sha256)

    def _create(self, *, actor_id: int, filename: str, size: int, sha256: str) -> OtaUploadRecord:
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".ota")
        ):
            raise OtaUploadError("ota_filename_invalid")
        if type(size) is not int or not 0 < size <= self.max_bytes:
            raise OtaUploadError("ota_package_too_large")
        if not isinstance(sha256, str) or re.fullmatch(r"[a-f0-9]{64}", sha256) is None:
            raise OtaUploadError("ota_hash_invalid")
        self._cleanup_expired()
        active = []
        for meta in self.state_root.glob("*.json"):
            try:
                row = self._read(meta.stem)
            except OtaUploadError:
                continue
            if row["state"] == "uploading" or (
                row["state"] == "verified" and self._host(UUID(row["upload_id"])).is_file()
            ):
                active.append(row)
            if (
                row.get("actor_id") == actor_id
                and row.get("filename") == filename
                and row.get("size") == size
                and row.get("sha256") == sha256
                and row.get("state") in {"uploading", "verified"}
            ):
                if row["state"] == "verified" and not self._host(UUID(row["upload_id"])).is_file():
                    continue
                result = dict(row)
                result["already_present"] = row["state"] == "verified"
                return self._record(result)
        if (
            len(active) >= self.max_active_global
            or sum(row["actor_id"] == actor_id for row in active) >= self.max_active_per_actor
        ):
            raise OtaUploadError("ota_upload_quota")
        state_usage = shutil.disk_usage(self.state_root)
        host_usage = shutil.disk_usage(self.host_root)
        same_device = self.state_root.stat().st_dev == self.host_root.stat().st_dev
        uploading = [row for row in active if row["state"] == "uploading"]
        if any(
            type(row.get("size")) is not int
            or type(row.get("offset")) is not int
            or not 0 <= row["offset"] <= row["size"] <= self.max_bytes
            for row in uploading
        ):
            raise OtaUploadError("ota_upload_state_invalid")
        remaining_parts = sum(row["size"] - row["offset"] for row in uploading)
        required_state = remaining_parts + size + self._free_floor(state_usage.total)
        if same_device:
            # All parts may grow before finalization. One verified copy can
            # coexist with its part, even when another upload is still active.
            required_state += max((size, *(row["size"] for row in uploading)))
            required_host = required_state
        else:
            # On a distinct host filesystem every active part can eventually
            # become a verified file without freeing space on that filesystem.
            required_host = sum(row["size"] for row in uploading) + size
            required_host += self._free_floor(host_usage.total)
        if state_usage.free < required_state or host_usage.free < required_host:
            raise OtaUploadError("ota_insufficient_space")
        identity = uuid4()
        value = {
            "schema": 1,
            "upload_id": str(identity),
            "actor_id": actor_id,
            "filename": filename,
            "size": size,
            "sha256": sha256,
            "offset": 0,
            "expires_at": self.now() + self.ttl_seconds,
            "state": "uploading",
        }
        descriptor = os.open(self._part(identity), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self._write(identity, value)
        return self._record(value)

    def status(self, identity: UUID | str, *, actor_id: int) -> OtaUploadRecord:
        value = self._read(identity)
        if value["actor_id"] != actor_id:
            raise OtaUploadError("ota_upload_forbidden")
        pinned = (
            value.get("state") == "verified"
            and value.get("operation_id")
            and self._host(UUID(value["upload_id"])).is_file()
        )
        if value["expires_at"] < self.now() and not pinned:
            raise OtaUploadError("ota_upload_expired")
        return self._record(value)

    def append(self, identity: UUID | str, *, actor_id: int, offset: int, chunk: bytes) -> int:
        with self._locked(str(identity)):
            return self._append(identity, actor_id=actor_id, offset=offset, chunk=chunk)

    def _append(self, identity: UUID | str, *, actor_id: int, offset: int, chunk: bytes) -> int:
        record = self.status(identity, actor_id=actor_id)
        if record.state != "uploading":
            raise OtaUploadError("ota_upload_finalized")
        if not chunk or len(chunk) > self.chunk_bytes:
            raise OtaUploadError("ota_chunk_too_large")
        if offset + len(chunk) > record.size:
            raise OtaUploadError("ota_package_too_large")
        part = self._part(record.upload_id)
        descriptor = os.open(part, os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r+b") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size != record.offset
            ):
                raise OtaUploadError("ota_upload_state_invalid")
            if offset < record.offset and offset + len(chunk) <= record.offset:
                stream.seek(offset)
                if stream.read(len(chunk)) == chunk:
                    return record.offset
            if offset != record.offset:
                raise OtaUploadError("ota_offset_mismatch")
            stream.seek(offset)
            stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        value = self._read(record.upload_id)
        value["offset"] = offset + len(chunk)
        self._write(record.upload_id, value)
        return value["offset"]

    def finalize(self, identity: UUID | str, *, actor_id: int) -> OtaUploadRecord:
        with self._locked(str(identity)):
            return self._finalize(identity, actor_id=actor_id)

    def pin_for_operation(
        self,
        identity: UUID | str,
        *,
        actor_id: int,
        operation_id: UUID,
        reassignable: Callable[[UUID], bool] | None = None,
    ) -> None:
        with self._locked(str(identity)):
            record = self.status(identity, actor_id=actor_id)
            if record.state != "verified" or not self._host(record.upload_id).is_file():
                raise OtaUploadError("ota_upload_not_verified")
            value = self._read(record.upload_id)
            previous = value.get("operation_id")
            if previous and previous != str(operation_id):
                try:
                    previous_id = UUID(previous)
                except (TypeError, ValueError) as exc:
                    raise OtaUploadError("ota_upload_in_use") from exc
                if reassignable is None or not reassignable(previous_id):
                    raise OtaUploadError("ota_upload_in_use")
            value["operation_id"] = str(operation_id)
            self._write(record.upload_id, value)

    def check_install_capacity(self, identity: UUID | str, *, actor_id: int) -> OtaUploadRecord:
        """Fast API preflight; root verifies the package and disk again before snapshot."""
        with self._locked(str(identity)):
            record = self.status(identity, actor_id=actor_id)
            if record.state != "verified" or not self._host(record.upload_id).is_file():
                raise OtaUploadError("ota_upload_not_verified")
            try:
                usage = shutil.disk_usage(self.host_root)
            except OSError as exc:
                raise OtaUploadError("ota_insufficient_space") from exc
            # Legacy verified records have no stored requirement. Root admission
            # still verifies the signed manifest and rejects insufficient space.
            # Root first writes a second durable copy into ota-packages, then
            # checks the installation budget. Reserve that transient copy here
            # so the API does not accept an operation root must reject.
            required = record.size + max(
                self._free_floor(usage.total), record.required_free_bytes or 0
            )
            if usage.free < required:
                raise OtaUploadError("ota_insufficient_space")
            return record

    def _finalize(self, identity: UUID | str, *, actor_id: int) -> OtaUploadRecord:
        record = self.status(identity, actor_id=actor_id)
        if record.state == "verified":
            self._part(record.upload_id).unlink(missing_ok=True)
            _fsync_directory(self.state_root)
            return record
        if record.offset != record.size:
            raise OtaUploadError("ota_upload_incomplete")
        part = self._part(record.upload_id)
        digest = hashlib.sha256()
        with part.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != record.sha256:
            raise OtaUploadError("ota_hash_mismatch")
        version, changes, compatible, required_free = _inspect_package(part)
        host_usage = shutil.disk_usage(self.host_root)
        if host_usage.free < record.size + self._free_floor(host_usage.total):
            raise OtaUploadError("ota_insufficient_space")
        target = self._host(record.upload_id)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with part.open("rb") as source, temporary.open("xb") as output:
                os.chmod(temporary, 0o600)
                shutil.copyfileobj(source, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            _fsync_directory(self.host_root)
        finally:
            temporary.unlink(missing_ok=True)
        value = self._read(record.upload_id)
        value.update(
            state="verified",
            version=version,
            changes=list(changes),
            compatible_from=list(compatible),
            required_free_bytes=required_free,
        )
        self._write(record.upload_id, value)
        part.unlink(missing_ok=True)
        _fsync_directory(self.state_root)
        return self._record(value)

    def delete(self, identity: UUID | str, *, actor_id: int) -> None:
        with self._locked(str(identity)):
            record = self.status(identity, actor_id=actor_id)
            value = self._read(record.upload_id)
            if value.get("operation_id") and self._host(record.upload_id).is_file():
                raise OtaUploadError("ota_upload_in_use")
            self._part(record.upload_id).unlink(missing_ok=True)
            self._host(record.upload_id).unlink(missing_ok=True)
            _fsync_directory(self.host_root)
            self._meta(record.upload_id).unlink(missing_ok=True)
            _fsync_directory(self.state_root)

    def cleanup_expired(self, *, is_terminal: Callable[[UUID], bool] | None = None) -> int:
        with self._locked("global"):
            return self._cleanup_expired(is_terminal=is_terminal)

    def pinned_operation_ids(self) -> set[str]:
        """Protect terminal receipts until their verified upload has been reclaimed."""
        with self._locked("global"):
            metadata = tuple(self.state_root.glob("*.json"))
            if len(metadata) > 512:
                raise OtaUploadError("ota_upload_state_invalid")
            pinned: set[str] = set()
            for path in metadata:
                value = self._read(path.stem)
                if (
                    value["state"] != "verified"
                    or not self._host(UUID(value["upload_id"])).is_file()
                ):
                    continue
                operation_id = value.get("operation_id")
                if operation_id is None:
                    continue
                try:
                    identity = UUID(operation_id)
                except (TypeError, ValueError) as exc:
                    raise OtaUploadError("ota_upload_state_invalid") from exc
                if str(identity) != operation_id:
                    raise OtaUploadError("ota_upload_state_invalid")
                pinned.add(operation_id)
            return pinned

    def _cleanup_expired(self, *, is_terminal: Callable[[UUID], bool] | None = None) -> int:
        removed = 0
        for meta in tuple(self.state_root.glob("*.json")):
            try:
                value = self._read(meta.stem)
            except OtaUploadError:
                continue
            if value.get("expires_at", self.now() + 1) >= self.now():
                continue
            identity = UUID(value["upload_id"])
            if (
                value.get("state") == "verified"
                and value.get("operation_id")
                and self._host(identity).is_file()
            ):
                try:
                    operation_id = UUID(value["operation_id"])
                except (TypeError, ValueError):
                    continue
                if is_terminal is None or not is_terminal(operation_id):
                    continue
            self._part(identity).unlink(missing_ok=True)
            self._host(identity).unlink(missing_ok=True)
            # Persist the host unlink before the metadata/DB receipt can vanish.
            _fsync_directory(self.host_root)
            meta.unlink(missing_ok=True)
            _fsync_directory(self.state_root)
            removed += 1
        # _write() can leave this exact temporary file after SIGKILL.
        if self.state_root.is_symlink():
            raise OtaUploadError("ota_upload_storage_unsafe")
        metadata_temps_removed = False
        for path in self.state_root.iterdir():
            if (
                re.fullmatch(
                    r"\.[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.json\.[0-9a-f]{32}\.tmp",
                    path.name,
                )
                is None
            ):
                continue
            info = path.lstat()
            if (
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_uid == os.geteuid()
                and info.st_mtime < self.now() - self.ttl_seconds
            ):
                path.unlink()
                removed += 1
                metadata_temps_removed = True
        if metadata_temps_removed:
            _fsync_directory(self.state_root)
        # A process killed while finalize() copies into host_root bypasses its
        # finally block. Only its exact temporary naming scheme is reclaimable.
        if self.host_root.is_symlink():
            raise OtaUploadError("ota_upload_storage_unsafe")
        temporary_removed = False
        for path in self.host_root.iterdir():
            if (
                re.fullmatch(
                    r"\.[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.ota\.[0-9a-f]{32}\.tmp",
                    path.name,
                )
                is None
            ):
                continue
            info = path.lstat()
            if (
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_uid == os.geteuid()
                and info.st_mtime < self.now() - self.ttl_seconds
            ):
                path.unlink()
                removed += 1
                temporary_removed = True
        if temporary_removed:
            _fsync_directory(self.host_root)
        return removed
