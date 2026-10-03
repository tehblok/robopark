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
from .retention import StorageBudget
from .rollback import sync_directory

_OTA_JOURNAL_PHASES = {
    "accepted", "verified", "snapshot_done", "staged", "migration_started",
    "migration_done", "cutover_started", "health_checked", "published",
    "rolled_back", "failed",
}


class OtaPackageStore:
    def __init__(self, paths, *, reserve_bytes: int = 512 * 1024 * 1024) -> None:
        self.paths = paths
        self.reserve_bytes = reserve_bytes
        self.incoming = paths.ops / "ota-uploads"
        self.packages = paths.state / "ota-packages"

    def _validate_roots(self) -> None:
        for root in (
            self.paths.var, self.paths.ops, self.paths.state,
            self.incoming, self.packages,
        ):
            if root.is_symlink():
                raise OtaError("ota_unsafe_cache")

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
        self._validate_roots()
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

    def cached_for_recovery(
        self, *, expected_sha256: str, expected_version: str
    ) -> VerifiedOta:
        """Reopen a durable candidate without reapplying the installation space floor.

        A rollback can be the only way to restore service after the disk fills.
        The file and manifest are still verified before any recovery action.
        """
        if re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None:
            raise OtaError("ota_hash_mismatch")
        self._validate_roots()
        if self.packages.is_symlink():
            raise OtaError("ota_unsafe_cache")
        package = self._cached(expected_sha256)
        if package is None:
            raise OtaError("ota_cache_missing")
        if package.manifest.app_version != expected_version:
            raise OtaError("ota_version_mismatch")
        return package

    def _check_admission(
        self,
        package: VerifiedOta,
        *,
        expected_version: str,
        current_version: str | None,
    ) -> None:
        if package.manifest.app_version != expected_version:
            raise OtaError("ota_version_mismatch")
        if current_version is not None:
            if version(package.manifest.app_version) < version(current_version):
                raise OtaError("ota_downgrade_forbidden")
            if current_version not in package.manifest.compatible_from:
                raise OtaError("ota_incompatible")
        # Keep the snapshot reserve after the temporary copy exists, before it
        # becomes a durable candidate that a failed admission could strand.
        try:
            usage = shutil.disk_usage(self.paths.var)
        except OSError as exc:
            raise OtaError("ota_insufficient_space") from exc
        budget = StorageBudget(usage.total, usage.free)
        if budget.free_bytes < max(
            budget.floor_bytes, package.manifest.required_free_bytes
        ):
            raise OtaError("ota_insufficient_space")

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
                self._check_admission(
                    package,
                    expected_version=expected_version,
                    current_version=current_version,
                )
                os.replace(temporary, self.packages / f"{expected_sha256}.ota")
                sync_directory(self.packages)
                package = verify_ota(
                    self.packages / f"{expected_sha256}.ota",
                    expected_sha256=expected_sha256,
                )
            finally:
                temporary.unlink(missing_ok=True)
        else:
            self._check_admission(
                package,
                expected_version=expected_version,
                current_version=current_version,
            )
        source = self.incoming / f"{identity}.ota"
        if source.exists() or source.is_symlink():
            self._validate_file(source)
            verify_ota(source, expected_sha256=expected_sha256)
            source.unlink()
            sync_directory(self.incoming)
        return package

    def discard_terminal(self, digest: str) -> None:
        """Release the exact verified package after a durable terminal receipt."""
        self._validate_roots()
        if re.fullmatch(r"[a-f0-9]{64}", digest) is None:
            raise OtaError("ota_hash_mismatch")
        target = self.packages / f"{digest}.ota"
        if target.exists() or target.is_symlink():
            self._validate_file(target)
            target.unlink()
            sync_directory(self.packages)

    def terminal_cache_candidates(self) -> tuple[Path, ...]:
        """List exact cached packages with terminal receipts, protecting active OTA."""
        from .operational_state import read_object

        self._validate_roots()
        receipts = self.paths.state / "ota-update-receipts"
        journal_path = self.paths.state / "ota-update-journal.json"
        if self.packages.is_symlink() or receipts.is_symlink():
            raise OtaError("ota_unsafe_cache")
        if not self.packages.is_dir() or not receipts.is_dir():
            return ()
        journal = read_object(journal_path)
        if journal_path.is_symlink() or (journal_path.exists() and not journal):
            raise OtaError("ota_state_invalid")
        if journal and (
            journal.get("phase") not in _OTA_JOURNAL_PHASES
            or not isinstance(journal.get("request"), dict)
            or not isinstance(journal["request"].get("sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", journal["request"]["sha256"]) is None
        ):
            raise OtaError("ota_state_invalid")
        active_request = journal.get("request")
        active_digest = active_request.get("sha256") if isinstance(active_request, dict) else None
        protect_active = (
            journal.get("phase") not in {"published", "rolled_back", "failed"}
            or journal.get("error") == "ota_rollback_failed"
        )
        eligible_count = 0
        try:
            with os.scandir(self.packages) as entries:
                for entry in entries:
                    if re.fullmatch(r"[a-f0-9]{64}\.ota", entry.name) is None:
                        continue
                    if protect_active and entry.name == f"{active_digest}.ota":
                        continue
                    eligible_count += 1
                    if eligible_count > 512:
                        break
        except OSError as exc:
            raise OtaError("ota_cache_scan_failed") from exc
        if eligible_count == 0:
            return ()
        candidates: dict[str, Path] = {}
        try:
            # Receipts are permanent replay evidence. Scan them incrementally;
            # a fixed receipt or cache-entry ceiling would disable cleanup forever.
            # Return a bounded batch of exact, receipted files per invocation.
            with os.scandir(receipts) as entries:
                for entry in entries:
                    if not re.fullmatch(r"[a-f0-9-]{36}\.json", entry.name):
                        continue
                    receipt_path = Path(entry.path)
                    value = read_object(receipt_path)
                    digest = value.get("sha256")
                    if not (
                        value.get("operation_id") == receipt_path.stem
                        and value.get("phase") in {"published", "rolled_back", "failed"}
                        and value.get("error") != "ota_rollback_failed"
                        and isinstance(digest, str)
                        and re.fullmatch(r"[a-f0-9]{64}", digest)
                    ):
                        continue
                    if protect_active and digest == active_digest:
                        continue
                    target = self.packages / f"{digest}.ota"
                    if target.exists() or target.is_symlink():
                        candidates[digest] = target
                        if len(candidates) == min(eligible_count, 512):
                            break
        except OSError as exc:
            raise OtaError("ota_cache_scan_failed") from exc
        return tuple(candidates[digest] for digest in sorted(candidates))

    def cleanup_terminal_packages(self) -> tuple[Path, ...]:
        """Reclaim legacy caches only with matching durable terminal receipts."""
        removed = []
        for path in self.terminal_cache_candidates():
            self._validate_file(path)
            path.unlink()
            removed.append(path)
        if removed:
            sync_directory(self.packages)
        return tuple(removed)

    def cleanup_abandoned_copies(
        self, *, now: float | None = None, ttl_seconds: int = 24 * 60 * 60
    ) -> tuple[Path, ...]:
        """Reclaim exact, expired admission copies left by a killed host process.

        The scheduled caller holds host.lock, which also serializes OTA admission.
        Keep the active journal's copy when this is called independently.
        """
        from .operational_state import read_object

        self._validate_roots()
        now = time.time() if now is None else now
        if self.packages.is_symlink():
            raise OtaError("ota_unsafe_cache")
        if not self.packages.exists():
            return ()
        if not self.packages.is_dir():
            raise OtaError("ota_unsafe_cache")
        journal_path = self.paths.state / "ota-update-journal.json"
        journal = read_object(journal_path)
        if journal_path.is_symlink() or (journal_path.exists() and not journal):
            raise OtaError("ota_state_invalid")
        active = None
        if journal:
            request = journal.get("request")
            phase = journal.get("phase")
            if not isinstance(request, dict) or phase not in _OTA_JOURNAL_PHASES:
                raise OtaError("ota_state_invalid")
            if phase not in {"published", "rolled_back", "failed"} or journal.get("error") == "ota_rollback_failed":
                digest = request.get("sha256")
                upload_id = request.get("upload_id")
                if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                    raise OtaError("ota_state_invalid")
                active = (digest, str(self._uuid(upload_id)))

        removed = []
        for path in self.packages.iterdir():
            match = re.fullmatch(r"\.([a-f0-9]{64})\.([a-f0-9-]{36})\.tmp", path.name)
            if match is None:
                continue
            try:
                identity = str(self._uuid(match.group(2)))
                info = path.lstat()
            except (OtaError, OSError):
                continue
            if (
                active == (match.group(1), identity)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or info.st_mtime >= now - ttl_seconds
            ):
                continue
            path.unlink()
            removed.append(path)
        if removed:
            sync_directory(self.packages)
        return tuple(removed)

    def cleanup_expired(
        self, *, now: float | None = None, ttl_seconds: int = 24 * 60 * 60
    ) -> tuple[Path, ...]:
        """Retire stale upload files while preserving an interrupted OTA's input."""
        from .operational_state import read_object

        self._validate_roots()
        now = time.time() if now is None else now
        if not self.incoming.exists() or self.incoming.is_symlink():
            return ()
        journal_path = self.paths.state / "ota-update-journal.json"
        journal = read_object(journal_path)
        if journal_path.is_symlink() or (journal_path.exists() and not journal):
            raise OtaError("ota_state_invalid")
        active_upload = None
        if journal:
            phase = journal.get("phase")
            request = journal.get("request")
            if phase not in _OTA_JOURNAL_PHASES or not isinstance(request, dict):
                raise OtaError("ota_state_invalid")
            if phase not in {"published", "rolled_back", "failed"} or journal.get("error") == "ota_rollback_failed":
                active_upload = str(self._uuid(request.get("upload_id")))
        removed = []
        api_metadata = self.paths.var / "api-ops" / "ota-uploads"
        for path in self.incoming.iterdir():
            if path.suffix not in {".part", ".ota"}:
                continue
            try:
                self._uuid(path.stem)
                info = path.lstat()
            except (OtaError, OSError):
                continue
            if (
                path.stem != active_upload
                # The API owns verified uploads until its terminal receipt
                # allows cleanup. The host sees only orphaned inputs here.
                and not (api_metadata / f"{path.stem}.json").exists()
                and not (api_metadata / f"{path.stem}.json").is_symlink()
                and stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_uid == os.geteuid()
                and info.st_mtime < now - ttl_seconds
            ):
                path.unlink()
                removed.append(path)
        if removed:
            sync_directory(self.incoming)
        return tuple(removed)
