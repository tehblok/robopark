"""Durable hash-only OTA coordinator independent of legacy release signing."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import UUID

from robopark_ota import OtaError, VerifiedOta

from .ota_store import OtaPackageStore
from .release import ReleaseError
from .state import atomic_write_json, exclusive_lock

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,150}$")
_OTA_ERROR_CODE = re.compile(r"^ota_[a-z0-9_]{1,96}$")
_PHASES = {
    "accepted",
    "verified",
    "snapshot_done",
    "staged",
    "migration_started",
    "migration_done",
    "cutover_started",
    "health_checked",
    "published",
    "rolled_back",
    "failed",
}
_ROLLBACK_PHASES = {
    "migration_started",
    "migration_done",
    "cutover_started",
    "health_checked",
}
_QUIESCED_PHASES = {"staged", "snapshot_done"}


class OtaFinalizationPending(RuntimeError):
    """Durable terminal OTA work still needs a safe finalization retry."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync_directory_no_follow(path: Path) -> None:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise RuntimeError("ota_unsafe_staging")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise RuntimeError("ota_unsafe_staging") from exc


def _sync_staged_tree(staging: Path) -> None:
    try:
        if not stat.S_ISDIR(staging.lstat().st_mode):
            raise RuntimeError("ota_unsafe_staging")
        directories = [staging]
        for path in staging.rglob("*"):
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                directories.append(path)
            elif not stat.S_ISREG(mode):
                raise RuntimeError("ota_unsafe_staging")
    except OSError as exc:
        raise RuntimeError("ota_unsafe_staging") from exc
    for directory in sorted(
        directories, key=lambda path: len(path.relative_to(staging).parts), reverse=True
    ):
        _sync_directory_no_follow(directory)


@dataclass(frozen=True, slots=True)
class OtaUpdateRequest:
    operation_id: UUID
    upload_id: UUID
    sha256: str
    version: str

    @classmethod
    def from_dict(cls, value: dict) -> OtaUpdateRequest:
        try:
            if not isinstance(value, dict) or set(value) != {
                "operation_id", "upload_id", "sha256", "version"
            }:
                raise ValueError()
            operation_id = UUID(str(value["operation_id"]))
            upload_id = UUID(str(value["upload_id"]))
            if (
                str(operation_id) != str(value["operation_id"])
                or str(upload_id) != str(value["upload_id"])
                or not isinstance(value["sha256"], str)
                or _SHA256.fullmatch(value["sha256"]) is None
                or not isinstance(value["version"], str)
                or _VERSION.fullmatch(value["version"]) is None
            ):
                raise ValueError()
            return cls(operation_id, upload_id, value["sha256"], value["version"])
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError("invalid_ota_update_request") from exc

    def as_dict(self) -> dict:
        return {
            "operation_id": str(self.operation_id),
            "upload_id": str(self.upload_id),
            "sha256": self.sha256,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class OtaUpdateReceipt:
    operation_id: str
    version: str
    sha256: str
    phase: str
    started_at: str
    finished_at: str
    error: str | None

    @classmethod
    def from_dict(cls, value: dict) -> OtaUpdateReceipt:
        return cls(**value)

    def as_dict(self) -> dict:
        return {
            "operation_id": self.operation_id,
            "version": self.version,
            "sha256": self.sha256,
            "phase": self.phase,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


class OtaUpdateRuntime(Protocol):
    def current_version(self) -> str | None: ...
    def snapshot(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def abort_snapshot(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def stage(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def migrate(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def cutover(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def health_check(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def publish(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def resume(self, request: OtaUpdateRequest) -> None: ...
    def rollback(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def cleanup(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...


class OtaUpdateEngine:
    def __init__(self, paths, runtime: OtaUpdateRuntime, store=None) -> None:
        self.paths = paths
        self.runtime = runtime
        self.store = store or OtaPackageStore(paths)
        self.journal_path = paths.state / "ota-update-journal.json"
        self.receipts = paths.state / "ota-update-receipts"
        self.lock_path = paths.lock_dir / "ota-update.lock"

    def _read_json(self, path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError) as exc:
            raise RuntimeError("ota_state_invalid") from exc
        if not isinstance(value, dict):
            raise RuntimeError("ota_state_invalid")  # noqa: TRY004 - stable OTA error contract
        return value

    def _read_journal(self) -> dict | None:
        value = self._read_json(self.journal_path)
        if value is None:
            return None
        try:
            if (
                set(value) != {"schema", "request", "phase", "started_at", "updated_at", "error"}
                or type(value["schema"]) is not int
                or value["schema"] != 1
                or value["phase"] not in _PHASES
                or value["error"] is not None
                and (not isinstance(value["error"], str) or len(value["error"]) > 128)
            ):
                raise ValueError()
            OtaUpdateRequest.from_dict(value["request"])
            for key in ("started_at", "updated_at"):
                stamp = value[key]
                if not isinstance(stamp, str) or len(stamp) > 40:
                    raise ValueError()
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise RuntimeError("ota_state_invalid") from exc
        return value

    def _write_journal(self, request: OtaUpdateRequest, phase: str, **extra) -> dict:
        from .storage_compatibility import require_storage_operations

        require_storage_operations(self.paths, check_space=False)
        if phase not in _PHASES:
            raise ValueError("invalid_ota_phase")
        old = self._read_journal()
        started_at = (
            old["started_at"] if old and old["request"] == request.as_dict() else _now()
        )
        value = {
            "schema": 1,
            "request": request.as_dict(),
            "phase": phase,
            "started_at": started_at,
            "updated_at": _now(),
            "error": extra.get("error"),
        }
        atomic_write_json(self.journal_path, value)
        public = self.paths.ops / "public"
        public.mkdir(parents=True, exist_ok=True, mode=0o755)
        atomic_write_json(
            public / "ota-status.json",
            {
                "schema": 1,
                "operation_id": str(request.operation_id),
                "version": request.version,
                "sha256": request.sha256,
                "phase": phase,
                "error": value["error"],
            },
            mode=0o644,
        )
        return value

    def _receipt_path(self, request: OtaUpdateRequest) -> Path:
        return self.receipts / f"{request.operation_id}.json"

    def _existing_receipt(self, request: OtaUpdateRequest) -> OtaUpdateReceipt | None:
        value = self._read_json(self._receipt_path(request))
        if value is None:
            return None
        try:
            if set(value) != {
                "operation_id", "version", "sha256", "phase",
                "started_at", "finished_at", "error",
            }:
                raise ValueError()
            receipt = OtaUpdateReceipt.from_dict(value)
            if (
                not isinstance(receipt.operation_id, str)
                or str(UUID(receipt.operation_id)) != receipt.operation_id
                or receipt.operation_id != str(request.operation_id)
                or not isinstance(receipt.version, str)
                or _VERSION.fullmatch(receipt.version) is None
                or not isinstance(receipt.sha256, str)
                or _SHA256.fullmatch(receipt.sha256) is None
                or receipt.phase not in {"published", "rolled_back", "failed"}
                or (receipt.phase == "published") != (receipt.error is None)
                or receipt.error is not None
                and (not isinstance(receipt.error, str) or len(receipt.error) > 128)
            ):
                raise ValueError()
            for stamp in (receipt.started_at, receipt.finished_at):
                if not isinstance(stamp, str) or len(stamp) > 40:
                    raise ValueError()
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise RuntimeError("ota_state_invalid") from exc
        if receipt.sha256 != request.sha256 or receipt.version != request.version:
            raise RuntimeError("ota_operation_conflict")
        return receipt

    def _finish(self, request, journal, phase, error) -> OtaUpdateReceipt:
        receipt = OtaUpdateReceipt(
            operation_id=str(request.operation_id),
            version=request.version,
            sha256=request.sha256,
            phase=phase,
            started_at=journal["started_at"],
            finished_at=_now(),
            error=error,
        )
        try:
            self._write_journal(request, phase, error=error)
        except OSError as exc:
            raise OtaFinalizationPending("ota_finalization_pending") from exc
        if phase in {"published", "rolled_back"}:
            try:
                self.runtime.resume(request)
            except (OSError, RuntimeError, ValueError) as exc:
                raise OtaFinalizationPending("ota_finalization_pending") from exc
        try:
            atomic_write_json(self._receipt_path(request), receipt.as_dict())
        except OSError as exc:
            raise OtaFinalizationPending("ota_finalization_pending") from exc
        if error != "ota_rollback_failed":
            with suppress(OtaError, OSError):
                self.store.discard_terminal(request.sha256)
        return receipt

    @staticmethod
    def _error_code(step: str) -> str:
        return f"ota_{step}_failed"

    def _cleanup(self, request, package) -> None:
        try:
            self.runtime.cleanup(request, package)
        except (OSError, RuntimeError, ValueError):
            with suppress(OSError):
                atomic_write_json(
                    self.paths.state
                    / "ota-cleanup-warnings"
                    / f"{request.operation_id}.json",
                    {
                        "operation_id": str(request.operation_id),
                        "warning": "ota_cleanup_failed",
                    },
                )

    def _resume_terminal_cleanup(
        self, request: OtaUpdateRequest, receipt: OtaUpdateReceipt
    ) -> None:
        try:
            journal = self._read_journal()
        except RuntimeError:
            return
        if (
            journal is not None
            and journal["request"] == request.as_dict()
            and journal["phase"] == receipt.phase
            and journal["error"] == receipt.error
            and journal["phase"] in {"published", "rolled_back", "failed"}
            and journal["error"] != "ota_rollback_failed"
        ):
            self._cleanup(request, None)

    def apply(self, request: OtaUpdateRequest) -> OtaUpdateReceipt:
        from .storage_compatibility import require_storage_operations

        require_storage_operations(self.paths, check_space=False)
        request = OtaUpdateRequest.from_dict(request.as_dict())
        with exclusive_lock(self.lock_path):
            receipt = self._existing_receipt(request)
            if receipt is not None:
                self._resume_terminal_cleanup(request, receipt)
                return receipt
            old = self._read_journal()
            if (
                old
                and old["phase"] in {"published", "rolled_back", "failed"}
                and old["request"] == request.as_dict()
            ):
                receipt = self._finish(request, old, old["phase"], old["error"])
                if old["error"] != "ota_rollback_failed":
                    self._cleanup(request, None)
                return receipt
            if old and old.get("error") == "ota_rollback_failed":
                raise RuntimeError("ota_rollback_failed")
            if old and old.get("phase") not in {"published", "rolled_back", "failed"}:
                if old.get("request") != request.as_dict():
                    same_id = old.get("request", {}).get("operation_id") == str(
                        request.operation_id
                    )
                    raise RuntimeError(
                        "ota_operation_conflict" if same_id else "ota_update_in_progress"
                    )
                return self._recover_locked(old)
            require_storage_operations(self.paths)
            journal = self._write_journal(request, "accepted")
            try:
                package = self.store.admit(
                    upload_id=request.upload_id,
                    expected_sha256=request.sha256,
                    expected_version=request.version,
                    current_version=self.runtime.current_version(),
                )
                journal = self._write_journal(request, "verified")
            except (OtaError, OSError, RuntimeError) as exc:
                code = str(exc) if isinstance(exc, OtaError) else ""
                error = code if _OTA_ERROR_CODE.fullmatch(code) else "ota_admission_failed"
                return self._finish(request, journal, "failed", error)

            steps = (
                ("stage", "staged"),
                ("snapshot", "snapshot_done"),
                ("migrate", "migration_done"),
                ("cutover", "cutover_started"),
                ("health_check", "health_checked"),
                ("publish", "published"),
            )
            for name, phase in steps:
                if name == "migrate":
                    journal = self._write_journal(request, "migration_started")
                try:
                    getattr(self.runtime, name)(request, package)
                    journal = self._write_journal(request, phase)
                except (OSError, RuntimeError, ValueError):
                    rolled_back = journal["phase"] in _ROLLBACK_PHASES
                    quiesced = journal["phase"] in _QUIESCED_PHASES
                    if rolled_back or quiesced:
                        try:
                            if rolled_back:
                                self.runtime.rollback(request, package)
                            else:
                                self.runtime.abort_snapshot(request, package)
                        except (OSError, RuntimeError, ValueError):
                            return self._finish(
                                request, journal, "failed", "ota_rollback_failed"
                            )
                    receipt = self._finish(
                        request,
                        journal,
                        "rolled_back" if rolled_back or quiesced else "failed",
                        self._error_code(name),
                    )
                    self._cleanup(request, package)
                    return receipt
            receipt = self._finish(request, journal, "published", None)
            self._cleanup(request, package)
            return receipt

    def _recover_locked(self, journal: dict) -> OtaUpdateReceipt:
        request = OtaUpdateRequest.from_dict(journal["request"])
        phase = journal["phase"]
        if phase in _ROLLBACK_PHASES | _QUIESCED_PHASES:
            try:
                package = self.store.cached_for_recovery(
                    expected_sha256=request.sha256,
                    expected_version=request.version,
                )
            except OtaError as exc:
                if str(exc) != "ota_cache_missing":
                    raise
                package = self.store.admit(
                    upload_id=request.upload_id,
                    expected_sha256=request.sha256,
                    expected_version=request.version,
                    current_version=None,
                )
        else:
            package = self.store.admit(
                upload_id=request.upload_id,
                expected_sha256=request.sha256,
                expected_version=request.version,
                current_version=None,
            )
        try:
            if phase in _ROLLBACK_PHASES:
                self.runtime.rollback(request, package)
                result = "rolled_back"
            elif phase in _QUIESCED_PHASES:
                self.runtime.abort_snapshot(request, package)
                result = "rolled_back"
            else:
                result = "failed"
        except (OSError, RuntimeError, ValueError):
            return self._finish(request, journal, "failed", "ota_rollback_failed")
        receipt = self._finish(request, journal, result, "ota_interrupted")
        self._cleanup(request, package)
        return receipt

    def recover(self) -> OtaUpdateReceipt | None:
        from .storage_compatibility import require_storage_operations

        require_storage_operations(self.paths, check_space=False)
        with exclusive_lock(self.lock_path):
            journal = self._read_journal()
            if journal is None:
                return None
            request = OtaUpdateRequest.from_dict(journal["request"])
            existing = self._existing_receipt(request)
            if existing is not None:
                self._resume_terminal_cleanup(request, existing)
                return None
            if journal["phase"] in {"published", "rolled_back", "failed"}:
                receipt = self._finish(
                    request, journal, journal["phase"], journal["error"]
                )
                if journal["error"] != "ota_rollback_failed":
                    self._cleanup(request, None)
                return receipt
            return self._recover_locked(journal)


class SystemOtaUpdateRuntime:
    """Concrete Docker/systemd runtime; uploaded code is never imported."""

    def __init__(self, paths, runner) -> None:
        self.paths = paths
        self.runner = runner
        self.tuna_state: str | None = None

    def _require_storage(self, *, check_space: bool = True) -> None:
        from .storage_compatibility import require_storage_operations

        require_storage_operations(self.paths, check_space=check_space)

    def _identity(self, request: OtaUpdateRequest) -> str:
        return str(request.operation_id)

    def _candidate_name(self, request: OtaUpdateRequest) -> str:
        return f"{request.version}-{request.operation_id}"

    def _metadata_path(self, request: OtaUpdateRequest) -> Path:
        return self.paths.state / "ota-runtime" / f"{request.operation_id}.json"

    def _staged_candidate_receipt_path(self, request: OtaUpdateRequest) -> Path:
        return (
            self.paths.state
            / "ota-staged-candidates"
            / f"{request.operation_id}.json"
        )

    def _record_staged_candidate(self, request: OtaUpdateRequest) -> None:
        receipt = self._staged_candidate_receipt_path(request)
        if receipt.parent.is_symlink() or (
            receipt.parent.exists() and not receipt.parent.is_dir()
        ):
            raise RuntimeError("ota_staging_state_invalid")
        atomic_write_json(
            receipt,
            {
                "schema": 1,
                "operation_id": self._identity(request),
                "candidate": self._candidate_name(request),
            },
        )
        _sync_directory_no_follow(receipt.parent.parent)

    def _staged_candidate(self, request: OtaUpdateRequest) -> Path | None:
        from .release import unique_object

        receipt = self._staged_candidate_receipt_path(request)
        if receipt.parent.is_symlink() or (
            receipt.parent.exists() and not receipt.parent.is_dir()
        ):
            raise RuntimeError("ota_staging_state_invalid")
        try:
            descriptor = os.open(
                receipt, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RuntimeError("ota_staging_state_invalid") from exc
        try:
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > 1024
                ):
                    raise ValueError()
                value = json.loads(
                    stream.read(1025), object_pairs_hook=unique_object
                )
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise RuntimeError("ota_staging_state_invalid") from exc
        if value != {
            "schema": 1,
            "operation_id": self._identity(request),
            "candidate": self._candidate_name(request),
        }:
            raise RuntimeError("ota_staging_state_invalid")
        return self.paths.releases / value["candidate"]

    def _metadata(self, request: OtaUpdateRequest) -> dict:
        from .operational_state import read_object

        value = read_object(self._metadata_path(request))
        fields = {"schema", "operation_id", "candidate", "current", "previous", "compose"}
        names = (value.get("current"), value.get("previous"))
        compose = value.get("compose")
        if (
            set(value) not in (fields, fields | {"release_status"})
            or value.get("schema") != 1
            or value.get("operation_id") != self._identity(request)
            or value.get("candidate") != self._candidate_name(request)
            or any(
                name is not None and (
                    not isinstance(name, str)
                    or name in {".", ".."}
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,200}", name) is None
                )
                for name in names
            )
            or names[0] is None
            or not isinstance(compose, str)
            or len(Path(compose).parts) != 2
            or Path(compose).parts[0] != "compose"
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.json", Path(compose).name) is None
            or "release_status" in value and value["release_status"] is not None
            and not isinstance(value["release_status"], dict)
        ):
            raise RuntimeError("ota_runtime_state_invalid")
        return value

    def current_version(self) -> str | None:
        if not self.paths.current.is_symlink():
            if self.paths.current.exists():
                raise OtaError("ota_unsafe_current")
            return None
        try:
            target = self.paths.current.resolve(strict=True)
            if target.parent != self.paths.releases.resolve() or target.is_symlink():
                raise OtaError("ota_unsafe_current")
            manifest = json.loads((target / "manifest.json").read_text())
            value = manifest.get("app_version") if isinstance(manifest, dict) else None
            if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
                raise OtaError("ota_current_version_unknown")
            return value
        except (OSError, ValueError, UnicodeError) as exc:
            raise OtaError("ota_current_version_unknown") from exc

    def manual_rollback(self, release: str) -> dict:
        """Restore the snapshot belonging to the currently installed OTA."""

        try:
            current = self.paths.current.resolve(strict=True)
            previous = self.paths.previous.resolve(strict=True)
            releases = self.paths.releases.resolve(strict=True)
            if (
                current.parent != releases
                or previous.parent != releases
                or previous.name != release
                or len(current.name) < 38
                or current.name[-37] != "-"
            ):
                raise ValueError()
            update_id = UUID(current.name[-36:])
            version = current.name[:-37]
            request = OtaUpdateRequest(update_id, update_id, "0" * 64, version)
            metadata = self._metadata(request)
            if metadata["current"] != release or metadata["candidate"] != current.name:
                raise ValueError()
        except (OSError, ValueError, RuntimeError) as exc:
            raise ReleaseError("rollback_release_changed") from exc
        self.rollback(request, None)
        self.resume(request)
        return {"release": release, "rolled_back": True}

    def snapshot(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        self._require_storage()
        del package
        from .rollback import snapshot
        from .storage_compatibility import require_storage_release
        from .terminal_install import quiesce_terminal
        from .updater import _maintenance, compose

        current = self.paths.current.resolve(strict=True)
        if current.parent != self.paths.releases.resolve() or current.is_symlink():
            raise RuntimeError("ota_unsafe_current")
        require_storage_release(self.paths, current)
        quiesce_terminal(self.paths, self.runner, reason="ota")
        compose_link = self.paths.state / "current-compose.json"
        compose_target = compose_link.resolve(strict=True)
        if not compose_link.is_symlink() or not compose_target.is_relative_to(
            self.paths.state.resolve()
        ):
            raise RuntimeError("ota_unsafe_compose")
        previous = None
        if self.paths.previous.is_symlink():
            resolved_previous = self.paths.previous.resolve(strict=True)
            if resolved_previous.parent == self.paths.releases.resolve():
                previous = resolved_previous.name
        release_status_path = self.paths.ops / "public/release-status.json"
        try:
            release_status = json.loads(release_status_path.read_text())
        except FileNotFoundError:
            release_status = None
        except (OSError, ValueError, UnicodeError) as exc:
            raise RuntimeError("ota_runtime_state_invalid") from exc
        if release_status is not None and not isinstance(release_status, dict):
            raise RuntimeError("ota_runtime_state_invalid")
        metadata = {
            "schema": 1,
            "operation_id": self._identity(request),
            "candidate": self._candidate_name(request),
            "current": current.name,
            "previous": previous,
            "compose": compose_target.relative_to(self.paths.state.resolve()).as_posix(),
            "release_status": release_status,
        }
        atomic_write_json(self._metadata_path(request), metadata)
        _maintenance(self.paths, True)
        self.runner.run(["systemctl", "stop", "robopark.service"], timeout=120)
        journal = {"job_id": self._identity(request)}
        snapshot(self.paths, journal, runner=None)
        rollback = self.paths.ops / "rollbacks" / self._identity(request)
        target = f"/host-rollbacks/{request.operation_id}/database.dump"
        self.runner.run(
            compose("robopark", compose_link)
            + [
                "exec", "-T", "db", "pg_dump", "--format=custom", f"--file={target}",
                "--username=robopark", "--dbname=robopark",
            ],
            timeout=300,
        )
        self.runner.run(
            compose("robopark", compose_link)
            + ["exec", "-T", "db", "pg_restore", "--list", target],
            timeout=60,
        )
        dump = rollback / "database.dump"
        if not dump.is_file() or dump.stat().st_size <= 0:
            raise RuntimeError("ota_snapshot_failed")

    def abort_snapshot(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        self._require_storage(check_space=False)
        del request, package
        from .runtime import bot_enabled

        self.runner.run(["systemctl", "restart", "robopark.service"], timeout=900)
        if not self.runner.wait_ready(
            project="robopark", config=self.paths.state / "current-compose.json", timeout=180,
            bot_required=bot_enabled(self.paths),
        ):
            raise RuntimeError("ota_rollback_failed")

    @staticmethod
    def _executable(name: str, info: zipfile.ZipInfo) -> bool:
        mode = info.external_attr >> 16
        return bool(mode & stat.S_IXUSR) or name.endswith(".sh") or name in {
            "deploy/host/robopark", "deploy/ops-agent.sh", "deploy/compose-production.sh"
        }

    def stage(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        self._require_storage()
        from .image_retention import record as record_images
        from .image_retention import require_record_capacity
        from .image_retention import reserve as reserve_images
        from .owned_builder import ensure_owned_builder
        from .rollback import sync_directory
        from .runtime import build_service_config, build_service_names, pin_images
        from .updater import _render_configs, compose

        identity = self._identity(request)
        staging = self.paths.releases / f".staging-{identity}"
        candidate = self.paths.releases / self._candidate_name(request)
        if staging.exists() or staging.is_symlink() or candidate.exists() or candidate.is_symlink():
            raise RuntimeError("ota_candidate_exists")
        staging.mkdir(parents=True, mode=0o755)
        try:
            with zipfile.ZipFile(package.path) as archive:
                for info in archive.infolist():
                    if not info.filename.startswith("release/"):
                        continue
                    name = info.filename.removeprefix("release/")
                    if not name:
                        continue
                    destination = staging.joinpath(*name.split("/"))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                        os.fchmod(
                            output.fileno(),
                            0o755 if self._executable(name, info) else 0o644,
                        )
                        output.flush()
                        os.fsync(output.fileno())
            with (staging / "manifest.json").open("x") as manifest:
                manifest.write(
                    json.dumps(
                        {
                            "format_version": package.manifest.format_version,
                            "app_version": package.manifest.app_version,
                            "git_sha": package.manifest.git_sha,
                            "migration_head": package.manifest.migration_head,
                            "package_sha256": package.sha256,
                        },
                        sort_keys=True,
                    )
                )
                os.fchmod(manifest.fileno(), 0o644)
                manifest.flush()
                os.fsync(manifest.fileno())
            from .storage_compatibility import require_storage_release

            require_storage_release(self.paths, staging)
            journal = {
                "job_id": identity,
                "candidate": self._candidate_name(request),
            }
            _, smoke_config = _render_configs(self.paths, journal, self.runner, staging)
            prefix = compose(f"robopark-candidate-{identity}", smoke_config)
            self.runner.run(prefix + ["config", "--quiet"], timeout=60)
            require_record_capacity(self.paths)
            reserve_images(self.paths, candidate, identity)
            builder = ensure_owned_builder(self.paths, self.runner)
            production_path = self.paths.state / "compose" / f"{identity}-production.json"
            production = json.loads(production_path.read_text())
            # External images have no build step. Resolve the candidate DB tag before
            # pin_images, including when the installed release used an older tag.
            if "db" in production["services"]:
                self.runner.run(
                    compose(f"robopark-candidate-{identity}", production_path) + ["pull", "db"],
                    timeout=600,
                )
            for service in build_service_names(production):
                build_prefix = compose(
                    f"robopark-candidate-{identity}",
                    build_service_config(service, smoke_config, production_path),
                )
                self.runner.run(
                    build_prefix + ["build", "--builder", builder, "--pull", service],
                    timeout=1800,
                )
            pin_images(
                production,
                lambda argv: self.runner.run(argv, timeout=30, capture=True),
            )
            atomic_write_json(production_path, production)
            record_images(self.paths, candidate, identity, production)
            self.runner.run(prefix + ["up", "-d", "--no-build"], timeout=180)
            if not self.runner.wait_ready(
                project=f"robopark-candidate-{identity}", config=smoke_config, timeout=180
            ):
                raise RuntimeError("ota_smoke_failed")
            self.runner.run(prefix + ["down", "--volumes", "--remove-orphans"], timeout=120)
            _sync_staged_tree(staging)
            require_storage_release(self.paths, staging)
            self._record_staged_candidate(request)
            os.replace(staging, candidate)
            sync_directory(self.paths.releases)
        except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
            self._stop_candidate(request)
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def migrate(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        self._require_storage()
        from .updater import _verify_database_head, compose

        identity = self._identity(request)
        config = self.paths.state / "compose" / f"{identity}-production.json"
        from .ai_install import migration_compose

        config = migration_compose(self.paths, config, identity)
        self.runner.run(
            compose("robopark", config)
            + ["run", "--rm", "--no-deps", "--entrypoint", "python", "api", "-m", "alembic", "upgrade", "head"],
            timeout=900,
        )
        _verify_database_head(self.paths, self.runner, package.manifest.migration_head)

    def cutover(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        self._require_storage()
        del package
        from .commands import ensure_backup_recovery_key
        from .rollback import atomic_symlink
        from .terminal_install import prepare_terminal_installation
        from .updater import _activate_system_files

        metadata = self._metadata(request)
        current = self.paths.releases / metadata["current"]
        candidate = self.paths.releases / metadata["candidate"]
        from .storage_compatibility import require_storage_release

        require_storage_release(self.paths, candidate)
        config = self.paths.state / "compose" / f"{request.operation_id}-production.json"
        ensure_backup_recovery_key(self.paths)
        atomic_symlink(current, self.paths.previous)
        atomic_symlink(candidate, self.paths.current)
        atomic_symlink(config, self.paths.state / "current-compose.json")
        _activate_system_files(self.paths, candidate)
        atomic_symlink(candidate / "deploy/host", self.paths.opt / "host-tools")
        prepare_terminal_installation(self.paths, candidate, self.runner)
        from .ai_install import reconcile_ai_installation
        reconcile_ai_installation(self.paths, candidate, self.runner, auto_install=False)
        self.runner.run(["systemctl", "daemon-reload"], timeout=60)
        self.runner.run(["systemctl", "restart", "robopark.service"], timeout=900)

    def health_check(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        self._require_storage()
        del request, package
        from .runtime import bot_enabled

        if not self.runner.wait_ready(
            project="robopark", config=self.paths.state / "current-compose.json", timeout=180,
            bot_required=bot_enabled(self.paths),
        ):
            raise RuntimeError("ota_healthcheck_failed")
        from .terminal_install import reconcile_terminal_installation

        reconcile_terminal_installation(self.paths, self.paths.current.resolve(), self.runner)
        from .ai_install import reconcile_ai_installation
        reconcile_ai_installation(
            self.paths, self.paths.current.resolve(), self.runner, auto_install=False
        )

    def publish(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        self._require_storage()
        from .image_retention import maintenance as cleanup_images
        from .updater import _retention

        metadata = self._metadata(request)
        public = self.paths.ops / "public"
        public.mkdir(parents=True, exist_ok=True, mode=0o755)
        atomic_write_json(
            public / "release-status.json",
            {
                "version": package.manifest.app_version,
                "build_id": package.sha256[:16],
                "git_sha": package.manifest.git_sha,
                "channel": "manual",
                "support_class": None,
                "released_at": None,
                "supported_until": None,
                "database_head": package.manifest.migration_head,
                "installer_version": "hash-only-v1",
                "bridges": [],
            },
            mode=0o644,
        )
        for release in (metadata["current"], metadata["candidate"]):
            atomic_write_json(
                self.paths.state / "successful-releases" / f"{release}.json",
                {"successful": True},
            )
        # Retention is post-publication housekeeping. A blocked or partially
        # completed cleanup must never roll back an already healthy cutover.
        with suppress(OSError, RuntimeError, ValueError):
            _retention(
                self.paths,
                {
                    "job_id": self._identity(request),
                    "previous_config": metadata["compose"],
                },
            )
        try:
            cleanup_images(self.paths, self.runner, enforce_builder_budget=True)
        except (OSError, RuntimeError, ValueError):
            # Image and BuildKit retention is post-publication housekeeping.
            # A Docker failure here must not reverse a healthy release.
            with suppress(OSError):
                atomic_write_json(
                    self.paths.state / "image-retention.json",
                    {"blocked": True, "pending": False, "deleted_tags": 0},
                )
            with suppress(OSError):
                atomic_write_json(
                    self.paths.state
                    / "ota-cleanup-warnings"
                    / f"{request.operation_id}.json",
                    {
                        "operation_id": str(request.operation_id),
                        "warning": "ota_image_cleanup_failed",
                    },
                )

    def resume(self, request: OtaUpdateRequest) -> None:
        self._require_storage(check_space=False)
        from robopark_ota.local_update import restart_enabled_tuna

        from .updater import _maintenance, _publish_status

        _maintenance(self.paths, False)
        self.tuna_state = restart_enabled_tuna(self.runner)
        status = {"state": "current_healthy", "error": None, "job_id": str(request.operation_id)}
        if self.tuna_state == "failed":
            status.update(publication="degraded", publication_error="ota_tuna_restart_failed")
        _publish_status(self.paths, status)

    def rollback(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        self._require_storage(check_space=False)
        del package
        from .rollback import (
            atomic_copy,
            atomic_symlink,
            restore_data,
            restore_units,
            sync_directory,
        )
        from .updater import (
            TMPFILES_SOURCE,
            TMPFILES_TARGET,
            _maintenance,
            _publish_release_lifecycle,
        )

        metadata = self._metadata(request)
        current = self.paths.releases / metadata["current"]
        from .storage_compatibility import require_storage_release

        require_storage_release(self.paths, current, check_space=False)
        from .terminal_install import quiesce_terminal

        quiesce_terminal(self.paths, self.runner, reason="rollback")
        journal = {"job_id": self._identity(request)}
        _maintenance(self.paths, True)
        self.runner.run(["systemctl", "stop", "robopark.service"], timeout=120)
        restore_data(self.paths, journal, self.runner)
        restore_units(self.paths, journal)
        from .storage_compatibility import refresh_storage_release_guard

        refresh_storage_release_guard(self.paths, current, check_space=False)
        tmpfiles_source = current / TMPFILES_SOURCE
        tmpfiles_target = self.paths.root / TMPFILES_TARGET
        if tmpfiles_source.is_symlink():
            raise ReleaseError("unsafe_tmpfiles")
        if tmpfiles_source.is_file():
            atomic_copy(tmpfiles_source, tmpfiles_target, 0o644)
        else:
            tmpfiles_target.unlink(missing_ok=True)
            if tmpfiles_target.parent.exists():
                sync_directory(tmpfiles_target.parent)
        atomic_symlink(current, self.paths.current)
        atomic_symlink(
            self.paths.state / metadata["compose"], self.paths.state / "current-compose.json"
        )
        from .rollback import restore_previous_link

        restore_previous_link(self.paths, metadata["previous"])
        release_status_path = self.paths.ops / "public/release-status.json"
        if "release_status" not in metadata:
            try:
                manifest = json.loads((current / "manifest.json").read_text())
            except (OSError, ValueError, UnicodeError) as exc:
                raise RuntimeError("ota_runtime_state_invalid") from exc
            if not isinstance(manifest, dict):
                raise RuntimeError("ota_runtime_state_invalid")
            _publish_release_lifecycle(self.paths, manifest)
        elif metadata["release_status"] is None:
            release_status_path.unlink(missing_ok=True)
            if release_status_path.parent.exists():
                sync_directory(release_status_path.parent)
        elif isinstance(metadata["release_status"], dict):
            atomic_write_json(release_status_path, metadata["release_status"], mode=0o644)
        else:
            raise RuntimeError("ota_runtime_state_invalid")
        atomic_symlink(current / "deploy/host", self.paths.opt / "host-tools")
        self.runner.run(["systemctl", "daemon-reload"], timeout=60)
        self.runner.run(["systemctl", "restart", "robopark.service"], timeout=900)
        from .runtime import bot_enabled

        if not self.runner.wait_ready(
            project="robopark", config=self.paths.state / "current-compose.json", timeout=180,
            bot_required=bot_enabled(self.paths),
        ):
            raise RuntimeError("ota_rollback_failed")

        from .terminal_install import reconcile_terminal_installation

        reconcile_terminal_installation(self.paths, current, self.runner)
        from .ai_install import reconcile_ai_installation
        reconcile_ai_installation(self.paths, current, self.runner, auto_install=False)

    def cleanup(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        self._require_storage(check_space=False)
        del package
        from .rollback import discard_rollback_artifacts, sync_directory

        identity = self._identity(request)
        if not self._stop_candidate(request):
            raise RuntimeError("ota_candidate_cleanup_failed")
        staging = self.paths.releases / f".staging-{identity}"
        if staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)
        staged_candidate = self._staged_candidate(request)
        metadata = self._read_metadata_optional(request)
        candidate = (
            self.paths.releases / metadata["candidate"]
            if metadata else staged_candidate
        )
        if candidate is not None:
            protected = set()
            releases = self.paths.releases.resolve()
            if candidate.parent.resolve() != releases or not releases.is_dir():
                raise RuntimeError("ota_unsafe_candidate")
            candidate = releases / candidate.name
            for link in (self.paths.current, self.paths.previous, self.paths.recovery):
                if not link.exists() and not link.is_symlink():
                    if link == self.paths.current:
                        raise RuntimeError("ota_unsafe_release_link")
                    continue
                if not link.is_symlink():
                    raise RuntimeError("ota_unsafe_release_link")
                try:
                    target = link.resolve(strict=True)
                except OSError as exc:
                    raise RuntimeError("ota_unsafe_release_link") from exc
                if target.parent != releases or not target.is_dir():
                    raise RuntimeError("ota_unsafe_release_link")
                protected.add(target)
            if candidate not in protected:
                if candidate.is_symlink() or (
                    candidate.exists() and not candidate.is_dir()
                ):
                    raise RuntimeError("ota_unsafe_candidate")
                if candidate.is_dir() and not candidate.is_symlink():
                    shutil.rmtree(candidate)
                discard_rollback_artifacts(self.paths, {"job_id": identity})
                success = (
                    self.paths.state
                    / "successful-releases"
                    / f"{candidate.name}.json"
                )
                if success.is_symlink() or (success.exists() and not success.is_file()):
                    raise RuntimeError("ota_unsafe_cleanup_receipt")
                if success.is_file():
                    success.unlink()
                    sync_directory(success.parent)
        if staged_candidate is not None:
            receipt = self._staged_candidate_receipt_path(request)
            receipt.unlink()
            sync_directory(receipt.parent)
        for suffix in ("production", "smoke", "migration"):
            config = self.paths.state / "compose" / f"{identity}-{suffix}.json"
            if config.exists() and not config.is_symlink():
                try:
                    if (self.paths.state / "current-compose.json").resolve(strict=True) == config:
                        continue
                except OSError:
                    pass
                config.unlink(missing_ok=True)
        work = self.paths.ops / "staging" / identity
        if work.is_symlink() or (work.exists() and not work.is_dir()):
            raise RuntimeError("ota_unsafe_staging")
        if work.is_dir():
            shutil.rmtree(work)

    def _stop_candidate(self, request: OtaUpdateRequest) -> bool:
        from .updater import compose

        identity = self._identity(request)
        smoke = self.paths.state / "compose" / f"{identity}-smoke.json"
        if not smoke.is_file() or smoke.is_symlink():
            return True
        command = compose(f"robopark-candidate-{identity}", smoke) + [
            "down",
            "--volumes",
            "--remove-orphans",
        ]
        cleanup = getattr(self.runner, "run_cleanup", None)
        try:
            if cleanup is not None:
                return cleanup(command, timeout=120) is not False
            self.runner.run(command, timeout=120)
            return True
        except (OSError, RuntimeError, ValueError):
            return False

    def _read_metadata_optional(self, request: OtaUpdateRequest) -> dict | None:
        try:
            return self._metadata(request)
        except RuntimeError:
            return None


class OtaProductionEffects:
    """Closed typed-operation adapter for the production OTA coordinator."""

    def __init__(self, paths, runner) -> None:
        self.engine = OtaUpdateEngine(paths, SystemOtaUpdateRuntime(paths, runner))

    def ota_update(self, operation_id, upload_id, sha256, version):
        receipt = self.engine.apply(
            OtaUpdateRequest.from_dict(
                {
                    "operation_id": operation_id,
                    "upload_id": upload_id,
                    "sha256": sha256,
                    "version": version,
                }
            )
        )
        if receipt.phase != "published" or receipt.error is not None:
            raise ReleaseError("ota_update_failed")
        return receipt.as_dict()

    def manual_rollback(self, operation_id, release):
        del operation_id
        return self.engine.runtime.manual_rollback(release)
