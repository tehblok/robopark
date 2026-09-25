"""Durable hash-only OTA coordinator independent of legacy release signing."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import UUID

from robopark_ota import OtaError, VerifiedOta

from .ota_store import OtaPackageStore
from .state import atomic_write_json, exclusive_lock

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,150}$")
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    def stage(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def migrate(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def cutover(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def health_check(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
    def publish(self, request: OtaUpdateRequest, package: VerifiedOta) -> None: ...
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
        return value if isinstance(value, dict) else None

    def _write_journal(self, request: OtaUpdateRequest, phase: str, **extra) -> dict:
        if phase not in _PHASES:
            raise ValueError("invalid_ota_phase")
        old = self._read_json(self.journal_path)
        started_at = old.get("started_at", _now()) if old else _now()
        value = {
            "schema": 1,
            "request": request.as_dict(),
            "phase": phase,
            "started_at": started_at,
            "updated_at": _now(),
            "error": extra.get("error"),
        }
        atomic_write_json(self.journal_path, value)
        return value

    def _receipt_path(self, request: OtaUpdateRequest) -> Path:
        return self.receipts / f"{request.operation_id}.json"

    def _existing_receipt(self, request: OtaUpdateRequest) -> OtaUpdateReceipt | None:
        value = self._read_json(self._receipt_path(request))
        if value is None:
            return None
        receipt = OtaUpdateReceipt.from_dict(value)
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
        atomic_write_json(self._receipt_path(request), receipt.as_dict())
        self._write_journal(request, phase, error=error)
        return receipt

    @staticmethod
    def _error_code(step: str) -> str:
        return f"ota_{step}_failed"

    def apply(self, request: OtaUpdateRequest) -> OtaUpdateReceipt:
        request = OtaUpdateRequest.from_dict(request.as_dict())
        with exclusive_lock(self.lock_path):
            receipt = self._existing_receipt(request)
            if receipt is not None:
                return receipt
            old = self._read_json(self.journal_path)
            if old and old.get("phase") not in {"published", "rolled_back", "failed"}:
                if old.get("request") != request.as_dict():
                    same_id = old.get("request", {}).get("operation_id") == str(
                        request.operation_id
                    )
                    raise RuntimeError(
                        "ota_operation_conflict" if same_id else "ota_update_in_progress"
                    )
                return self._recover_locked(old)
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
                return self._finish(request, journal, "failed", str(exc))

            steps = (
                ("snapshot", "snapshot_done"),
                ("stage", "staged"),
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
                    try:
                        if journal["phase"] not in {"accepted", "verified"}:
                            self.runtime.rollback(request, package)
                        self.runtime.cleanup(request, package)
                        return self._finish(
                            request,
                            journal,
                            "failed"
                            if journal["phase"] in {"accepted", "verified"}
                            else "rolled_back",
                            self._error_code(name),
                        )
                    except (OSError, RuntimeError, ValueError):
                        return self._finish(
                            request, journal, "failed", "ota_rollback_failed"
                        )
            self.runtime.cleanup(request, package)
            return self._finish(request, journal, "published", None)

    def _recover_locked(self, journal: dict) -> OtaUpdateReceipt:
        request = OtaUpdateRequest.from_dict(journal["request"])
        package = self.store.admit(
            upload_id=request.upload_id,
            expected_sha256=request.sha256,
            expected_version=request.version,
            current_version=None,
        )
        phase = journal["phase"]
        try:
            if phase not in {"accepted", "verified"}:
                self.runtime.rollback(request, package)
                result = "rolled_back"
            else:
                result = "failed"
            self.runtime.cleanup(request, package)
            return self._finish(request, journal, result, "ota_interrupted")
        except (OSError, RuntimeError, ValueError):
            return self._finish(request, journal, "failed", "ota_rollback_failed")

    def recover(self) -> OtaUpdateReceipt | None:
        with exclusive_lock(self.lock_path):
            journal = self._read_json(self.journal_path)
            if journal is None or journal.get("phase") in {
                "published", "rolled_back", "failed"
            }:
                return None
            return self._recover_locked(journal)


class SystemOtaUpdateRuntime:
    """Concrete Docker/systemd runtime; uploaded code is never imported."""

    def __init__(self, paths, runner) -> None:
        self.paths = paths
        self.runner = runner

    def _identity(self, request: OtaUpdateRequest) -> str:
        return str(request.operation_id)

    def _candidate_name(self, request: OtaUpdateRequest) -> str:
        return f"{request.version}-{request.operation_id}"

    def _metadata_path(self, request: OtaUpdateRequest) -> Path:
        return self.paths.state / "ota-runtime" / f"{request.operation_id}.json"

    def _metadata(self, request: OtaUpdateRequest) -> dict:
        try:
            value = json.loads(self._metadata_path(request).read_text())
        except (OSError, ValueError, UnicodeError) as exc:
            raise RuntimeError("ota_runtime_state_invalid") from exc
        if not isinstance(value, dict) or value.get("operation_id") != self._identity(request):
            raise RuntimeError("ota_runtime_state_invalid")
        return value

    def current_version(self) -> str | None:
        try:
            target = self.paths.current.resolve(strict=True)
            if target.parent != self.paths.releases.resolve() or target.is_symlink():
                raise OSError()
            manifest = json.loads((target / "manifest.json").read_text())
            value = manifest.get("app_version")
            return value if isinstance(value, str) and _VERSION.fullmatch(value) else None
        except (OSError, ValueError, UnicodeError):
            return None

    def snapshot(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        del package
        from .rollback import snapshot
        from .updater import compose

        current = self.paths.current.resolve(strict=True)
        if current.parent != self.paths.releases.resolve() or current.is_symlink():
            raise RuntimeError("ota_unsafe_current")
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
        metadata = {
            "schema": 1,
            "operation_id": self._identity(request),
            "candidate": self._candidate_name(request),
            "current": current.name,
            "previous": previous,
            "compose": compose_target.relative_to(self.paths.state.resolve()).as_posix(),
        }
        atomic_write_json(self._metadata_path(request), metadata)
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

    @staticmethod
    def _executable(name: str, info: zipfile.ZipInfo) -> bool:
        mode = info.external_attr >> 16
        return bool(mode & stat.S_IXUSR) or name.endswith(".sh") or name in {
            "deploy/host/robopark", "deploy/ops-agent.sh", "deploy/compose-production.sh"
        }

    def stage(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        from .runtime import pin_images
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
                        output.flush()
                        os.fsync(output.fileno())
                    destination.chmod(0o755 if self._executable(name, info) else 0o644)
            (staging / "manifest.json").write_text(
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
            journal = {
                "job_id": identity,
                "candidate": self._candidate_name(request),
            }
            _, smoke_config = _render_configs(self.paths, journal, self.runner, staging)
            prefix = compose(f"robopark-candidate-{identity}", smoke_config)
            self.runner.run(prefix + ["config", "--quiet"], timeout=60)
            self.runner.run(prefix + ["build", "--pull", "api"], timeout=1800)
            self.runner.run(prefix + ["build", "--pull", "web"], timeout=1800)
            production_path = self.paths.state / "compose" / f"{identity}-production.json"
            production = json.loads(production_path.read_text())
            pin_images(
                production,
                lambda argv: self.runner.run(argv, timeout=30, capture=True),
            )
            atomic_write_json(production_path, production)
            self.runner.run(prefix + ["up", "-d", "--no-build"], timeout=180)
            if not self.runner.wait_ready(
                project=f"robopark-candidate-{identity}", config=smoke_config, timeout=180
            ):
                raise RuntimeError("ota_smoke_failed")
            self.runner.run(prefix + ["down", "--volumes", "--remove-orphans"], timeout=120)
            os.replace(staging, candidate)
        except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def migrate(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        from .updater import _maintenance, _verify_database_head, compose

        identity = self._identity(request)
        config = self.paths.state / "compose" / f"{identity}-production.json"
        _maintenance(self.paths, True)
        self.runner.run(["systemctl", "stop", "robopark.service"], timeout=120)
        self.runner.run(
            compose("robopark", config)
            + ["run", "--rm", "--no-deps", "--entrypoint", "python", "api", "-m", "alembic", "upgrade", "head"],
            timeout=900,
        )
        _verify_database_head(self.paths, self.runner, package.manifest.migration_head)

    def cutover(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        del package
        from .rollback import atomic_symlink
        from .updater import _activate_system_files

        metadata = self._metadata(request)
        current = self.paths.releases / metadata["current"]
        candidate = self.paths.releases / metadata["candidate"]
        config = self.paths.state / "compose" / f"{request.operation_id}-production.json"
        atomic_symlink(current, self.paths.previous)
        atomic_symlink(candidate, self.paths.current)
        atomic_symlink(config, self.paths.state / "current-compose.json")
        _activate_system_files(self.paths, candidate)
        atomic_symlink(candidate / "deploy/host", self.paths.opt / "host-tools")
        self.runner.run(["systemctl", "daemon-reload"], timeout=60)
        self.runner.run(["systemctl", "restart", "robopark.service"], timeout=900)

    def health_check(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        del request, package
        if not self.runner.wait_ready(
            project="robopark", config=self.paths.state / "current-compose.json", timeout=180
        ):
            raise RuntimeError("ota_healthcheck_failed")

    def publish(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        from .updater import _maintenance, _publish_status

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
        atomic_write_json(
            self.paths.state / "successful-ota" / f"{package.sha256}.json",
            {
                "version": package.manifest.app_version,
                "sha256": package.sha256,
                "operation_id": str(request.operation_id),
            },
        )
        _maintenance(self.paths, False)
        _publish_status(
            self.paths,
            {"state": "current_healthy", "error": None, "job_id": str(request.operation_id)},
        )

    def rollback(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        del package
        from .rollback import atomic_symlink, restore_data, restore_units
        from .updater import _maintenance

        metadata = self._metadata(request)
        journal = {"job_id": self._identity(request)}
        _maintenance(self.paths, True)
        self.runner.run(["systemctl", "stop", "robopark.service"], timeout=120)
        restore_data(self.paths, journal, self.runner)
        restore_units(self.paths, journal)
        current = self.paths.releases / metadata["current"]
        atomic_symlink(current, self.paths.current)
        atomic_symlink(
            self.paths.state / metadata["compose"], self.paths.state / "current-compose.json"
        )
        if metadata["previous"]:
            atomic_symlink(self.paths.releases / metadata["previous"], self.paths.previous)
        else:
            self.paths.previous.unlink(missing_ok=True)
        atomic_symlink(current / "deploy/host", self.paths.opt / "host-tools")
        self.runner.run(["systemctl", "daemon-reload"], timeout=60)
        self.runner.run(["systemctl", "restart", "robopark.service"], timeout=900)
        if not self.runner.wait_ready(
            project="robopark", config=self.paths.state / "current-compose.json", timeout=180
        ):
            raise RuntimeError("ota_rollback_failed")
        _maintenance(self.paths, False)

    def cleanup(self, request: OtaUpdateRequest, package: VerifiedOta) -> None:
        del package
        identity = self._identity(request)
        staging = self.paths.releases / f".staging-{identity}"
        if staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)
        metadata = self._read_metadata_optional(request)
        if metadata:
            candidate = self.paths.releases / metadata["candidate"]
            try:
                active = self.paths.current.resolve(strict=True)
            except OSError:
                active = None
            if candidate.is_dir() and not candidate.is_symlink() and candidate != active:
                shutil.rmtree(candidate)
        for suffix in ("production", "smoke"):
            config = self.paths.state / "compose" / f"{identity}-{suffix}.json"
            if config.exists() and not config.is_symlink():
                try:
                    if (self.paths.state / "current-compose.json").resolve(strict=True) == config:
                        continue
                except OSError:
                    pass
                config.unlink(missing_ok=True)

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
        return receipt.as_dict()
