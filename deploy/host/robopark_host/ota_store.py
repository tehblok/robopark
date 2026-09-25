"""Root-owned, hash-only admission for locally uploaded OTA packages."""

from __future__ import annotations

import os
import re
import shutil
import stat
import time
from pathlib import Path
from uuid import UUID

from robopark_ota import OtaError, VerifiedOta, verify_ota
from robopark_ota.verify import DEFAULT_MAX_ARCHIVE_BYTES

from .release import version


class OtaPackageStore:
    def __init__(self, paths, *, reserve_bytes: int = 512 * 1024 * 1024) -> None:
        self.paths = paths
        self.reserve_bytes = reserve_bytes
        self.incoming = paths.ops / "ota-uploads"
        self.packages = paths.state / "ota-packages"

    @staticmethod
    def _uuid(value: UUID | str) -> UUID:
        try:
            parsed = UUID(str(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise OtaError("ota_unsafe_upload") from exc
        if str(parsed) != str(value):
            raise OtaError("ota_unsafe_upload")
        return parsed

    @staticmethod
    def _validate_file(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                mode = stat.S_IMODE(info.st_mode)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or mode & 0o022
                ):
                    raise OtaError("ota_unsafe_upload")
        except OtaError:
            raise
        except OSError as exc:
            raise OtaError("ota_unsafe_upload") from exc

    @staticmethod
    def _copy_upload(source: Path, target: Path, *, available_bytes: int) -> int:
        try:
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as read:
                info = os.fstat(read.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) & 0o022
                    or info.st_size > DEFAULT_MAX_ARCHIVE_BYTES
                ):
                    raise OtaError("ota_unsafe_upload")
                if info.st_size > available_bytes:
                    raise OtaError("ota_insufficient_space")
                with target.open("xb") as write:
                    os.chmod(target, 0o600)
                    copied = 0
                    while chunk := read.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > DEFAULT_MAX_ARCHIVE_BYTES:
                            raise OtaError("ota_unsafe_upload")
                        write.write(chunk)
                    write.flush()
                    os.fsync(write.fileno())
                if copied != info.st_size:
                    raise OtaError("ota_unsafe_upload")
                return copied
        except OtaError:
            raise
        except OSError as exc:
            raise OtaError("ota_unsafe_upload") from exc

    def _prepare_directories(self) -> None:
        for directory in (self.incoming, self.packages):
            if directory.is_symlink():
                raise OtaError("ota_unsafe_upload")
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)

    def _cached(self, digest: str) -> VerifiedOta | None:
        target = self.packages / f"{digest}.ota"
        if not target.exists():
            return None
        self._validate_file(target)
        return verify_ota(target, expected_sha256=digest)

    def admit(
        self,
        *,
        upload_id: UUID | str,
        expected_sha256: str,
        expected_version: str,
        current_version: str | None,
    ) -> VerifiedOta:
        identity = self._uuid(upload_id)
        if not isinstance(expected_sha256, str) or re.fullmatch(
            r"[a-f0-9]{64}", expected_sha256
        ) is None:
            raise OtaError("ota_hash_mismatch")
        self._prepare_directories()
        package = self._cached(expected_sha256)
        if package is None:
            source = self.incoming / f"{identity}.ota"
            temporary = self.packages / f".{expected_sha256}.{identity}.tmp"
            try:
                free = shutil.disk_usage(self.paths.var).free
            except OSError as exc:
                raise OtaError("ota_insufficient_space") from exc
            try:
                self._copy_upload(
                    source, temporary, available_bytes=max(0, free - self.reserve_bytes)
                )
                package = verify_ota(temporary, expected_sha256=expected_sha256)
                os.replace(temporary, self.packages / f"{expected_sha256}.ota")
                package = verify_ota(
                    self.packages / f"{expected_sha256}.ota",
                    expected_sha256=expected_sha256,
                )
            finally:
                temporary.unlink(missing_ok=True)

        if package.manifest.app_version != expected_version:
            raise OtaError("ota_version_mismatch")
        if current_version is not None:
            if version(package.manifest.app_version) < version(current_version):
                raise OtaError("ota_downgrade_forbidden")
            compatible = package.manifest.compatible_from
            if compatible and current_version not in compatible:
                raise OtaError("ota_incompatible")
        return package

    def cleanup_expired(
        self, *, now: float | None = None, ttl_seconds: int = 24 * 60 * 60
    ) -> tuple[Path, ...]:
        now = time.time() if now is None else now
        if not self.incoming.exists() or self.incoming.is_symlink():
            return ()
        removed = []
        for path in self.incoming.iterdir():
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix == ".part"
                and path.stat().st_mtime < now - ttl_seconds
            ):
                path.unlink()
                removed.append(path)
        return tuple(removed)
