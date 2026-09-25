"""Bounded, root-owned single-slot command consumer shared by boot and .path."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from .bundle import create_diagnostic_bundle
from .doctor import run_doctor
from .operational_state import backup_state, public_version, update_state
from .release import UTC, ReleaseError, UpdateRequest, timestamp, unique_object
from .repair import DEFAULT_REPAIRS, run_repairs
from .state import atomic_write_json, exclusive_lock


class OperationKind(StrEnum):
    OTA_UPDATE = "ota-update"
    RELEASE_UPDATE = "release-update"
    REINSTALL = "reinstall"
    ROLLBACK = "rollback"
    PACKAGE_INSPECT = "package-inspect"
    PACKAGE_UPDATE = "package-update"
    SERVICE_RESTART = "service-restart"
    REBOOT = "reboot"
    BACKUP = "backup"
    BACKUP_VERIFY = "backup-verify"
    BACKUP_RESTORE = "backup-restore"
    CLEANUP_PREVIEW = "cleanup-preview"
    CLEANUP_EXECUTE = "cleanup-execute"
    DIAGNOSTICS = "diagnostics"
    USB_DISCOVER = "usb-discover"
    USB_FORMAT = "usb-format"
    USB_SELECT = "usb-select"


DESTRUCTIVE_CONFIRMATIONS = {
    OperationKind.OTA_UPDATE: "UPDATE ROBOPARK",
    OperationKind.RELEASE_UPDATE: "UPDATE ROBOPARK",
    OperationKind.REINSTALL: "REINSTALL ROBOPARK",
    OperationKind.ROLLBACK: "ROLLBACK ROBOPARK",
    OperationKind.REBOOT: "REBOOT ROBOPARK",
    OperationKind.BACKUP_RESTORE: "RESTORE ROBOPARK BACKUP",
    OperationKind.CLEANUP_EXECUTE: "CLEAN ROBOPARK",
}
_DYNAMIC_CONFIRMATION_KINDS = {
    OperationKind.PACKAGE_UPDATE,
    OperationKind.SERVICE_RESTART,
    OperationKind.USB_FORMAT,
}
ALLOWED_SERVICES = frozenset(
    {
        "robopark-api.service",
        "robopark-worker.service",
        "robopark-tuna.service",
        "docker.service",
    }
)
ALLOWED_PACKAGES = frozenset({"docker-ce", "docker-ce-cli", "containerd.io", "openssl"})
ALLOWED_CLEANUP_CATEGORIES = frozenset({"diagnostics", "logs", "backups", "releases"})


@dataclass(frozen=True, slots=True)
class BlockDevice:
    uuid: str
    path: str
    removable: bool
    mounted: bool = False
    system_device: bool = False
    root_device: bool = False
    data_device: bool = False
    mount_point: str | None = None

    def __post_init__(self):
        if (
            str(UUID(self.uuid)) != self.uuid
            or not re.fullmatch(r"/dev/[A-Za-z0-9._+-]+", self.path)
            or Path(self.path).name in {".", ".."}
            or self.mount_point is not None
            and (
                not self.mounted
                or not self.mount_point.startswith("/")
                or ".." in Path(self.mount_point).parts
                or len(self.mount_point) > 4096
            )
        ):
            raise ValueError("invalid_block_device")


@dataclass(frozen=True, slots=True)
class TypedOperation:
    operation_id: str
    kind: OperationKind
    actor_user_id: int
    created_at: str
    payload: dict
    request: dict


class TypedHostEffects:
    """Explicit host-effect interface. Production adapters may implement only this allowlist."""

    supported_kinds = frozenset()

    def reconcile(self, operation):
        """Reconcile a durable dispatch checkpoint without replaying its effect."""

        del operation
        return {
            "state": "failed",
            "detail": {},
            "error": "manual_recovery_required",
        }

    def release_update(self, operation_id, release_id):
        raise ReleaseError("operation_unavailable")

    def ota_update(self, operation_id, upload_id, sha256, version):
        raise ReleaseError("operation_unavailable")

    def reinstall(self, operation_id):
        raise ReleaseError("operation_unavailable")

    def rollback(self, operation_id, release):
        raise ReleaseError("operation_unavailable")

    def package_inspect(self, operation_id, package):
        raise ReleaseError("operation_unavailable")

    def package_update(self, operation_id, package):
        raise ReleaseError("operation_unavailable")

    def service_restart(self, operation_id, service):
        raise ReleaseError("operation_unavailable")

    def reboot(self, operation_id):
        raise ReleaseError("operation_unavailable")

    def backup(self, operation_id, device_uuid):
        raise ReleaseError("operation_unavailable")

    def backup_verify(self, operation_id, backup_id):
        raise ReleaseError("operation_unavailable")

    def backup_restore(self, operation_id, backup_id):
        raise ReleaseError("operation_unavailable")

    def cleanup_preview(self, operation_id, categories):
        raise ReleaseError("operation_unavailable")

    def cleanup_execute(self, operation_id, plan_id):
        raise ReleaseError("operation_unavailable")

    def diagnostics(self, operation_id):
        raise ReleaseError("operation_unavailable")

    def usb_discover(self, operation_id):
        raise ReleaseError("operation_unavailable")

    def usb_format(self, operation_id, device):
        raise ReleaseError("operation_unavailable")

    def usb_select(self, operation_id, device):
        raise ReleaseError("operation_unavailable")


class SystemTypedHostEffects(TypedHostEffects):
    """Typed adapter over an equally closed, explicitly injected action surface.

    Neither layer accepts an executable, argv, shell text, environment, or output path.
    """

    def __init__(self, paths, system=None, *, runner=None, http=None):
        del paths, runner, http
        if system is None:
            raise ValueError("typed_system_adapter_required")
        self.system = system

    @property
    def supported_kinds(self):
        return getattr(self.system, "supported_kinds", frozenset())

    def reconcile(self, operation):
        return self.system.reconcile(operation)

    def release_update(self, operation_id, release_id):
        return self.system.release_update(operation_id, release_id)

    def ota_update(self, operation_id, upload_id, sha256, version):
        return self.system.ota_update(operation_id, upload_id, sha256, version)

    def reinstall(self, operation_id):
        return self.system.reinstall(operation_id)

    def rollback(self, operation_id, release):
        return self.system.rollback(operation_id, release)

    def package_inspect(self, operation_id, package):
        return self.system.package_inspect(operation_id, package)

    def package_update(self, operation_id, package):
        return self.system.package_update(operation_id, package)

    def service_restart(self, operation_id, service):
        return self.system.service_restart(operation_id, service)

    def reboot(self, operation_id):
        return self.system.reboot(operation_id)

    def backup(self, operation_id, device_uuid):
        return self.system.backup(operation_id, device_uuid)

    def backup_verify(self, operation_id, backup_id):
        return self.system.backup_verify(operation_id, backup_id)

    def backup_restore(self, operation_id, backup_id):
        return self.system.backup_restore(operation_id, backup_id)

    def cleanup_preview(self, operation_id, categories):
        return self.system.cleanup_preview(operation_id, categories)

    def cleanup_execute(self, operation_id, plan_id):
        return self.system.cleanup_execute(operation_id, plan_id)

    def diagnostics(self, operation_id):
        return self.system.diagnostics(operation_id)

    def usb_discover(self, operation_id):
        return self.system.usb_discover(operation_id)

    def usb_format(self, operation_id, device):
        return self.system.usb_format(operation_id, device)

    def usb_select(self, operation_id, device):
        return self.system.usb_select(operation_id, device)


_TYPED_FIELDS = {
    OperationKind.OTA_UPDATE: {"upload_id", "sha256", "version"},
    OperationKind.RELEASE_UPDATE: {"release_id"},
    OperationKind.REINSTALL: set(),
    OperationKind.ROLLBACK: {"release"},
    OperationKind.PACKAGE_INSPECT: {"package"},
    OperationKind.PACKAGE_UPDATE: {"package"},
    OperationKind.SERVICE_RESTART: {"service"},
    OperationKind.REBOOT: set(),
    OperationKind.BACKUP: {"device_uuid"},
    OperationKind.BACKUP_VERIFY: {"backup_id"},
    OperationKind.BACKUP_RESTORE: {"backup_id"},
    OperationKind.CLEANUP_PREVIEW: {"categories"},
    OperationKind.CLEANUP_EXECUTE: {"plan_id"},
    OperationKind.DIAGNOSTICS: set(),
    OperationKind.USB_DISCOVER: set(),
    OperationKind.USB_FORMAT: {"device_uuid", "confirmation_repeat"},
    OperationKind.USB_SELECT: {"device_uuid"},
}


def _canonical_uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError()
    return value


def _validate_authorization(value, operation, *, fresh=True):
    expected = {
        "operation_id",
        "operation_kind",
        "actor_user_id",
        "consumed",
        "validated_at",
    }
    try:
        stamp = timestamp(value["validated_at"])
        if (
            not isinstance(value, dict)
            or set(value) != expected
            or value["operation_id"] != operation["job_id"]
            or value["operation_kind"] != operation["kind"]
            or value["actor_user_id"] != operation["actor_user_id"]
            or value["consumed"] is not True
            or stamp.utcoffset().total_seconds() != 0
            or fresh
            and not -30 <= (datetime.now(UTC) - stamp).total_seconds() <= 300
        ):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise ReleaseError("authorization_required") from exc


def _required_confirmation(kind, value):
    if kind is OperationKind.PACKAGE_UPDATE:
        return f"UPDATE PACKAGE {value.get('package', '')}"
    if kind is OperationKind.SERVICE_RESTART:
        return f"RESTART SERVICE {value.get('service', '')}"
    if kind is OperationKind.USB_FORMAT:
        return f"FORMAT USB {value.get('device_uuid', '')}"
    safe = {
        OperationKind.PACKAGE_INSPECT,
        OperationKind.BACKUP_VERIFY,
        OperationKind.CLEANUP_PREVIEW,
        OperationKind.DIAGNOSTICS,
        OperationKind.USB_DISCOVER,
        OperationKind.USB_SELECT,
    }
    return (
        DESTRUCTIVE_CONFIRMATIONS.get(kind)
        or (f"ЗАПУСТИТЬ {kind.value.upper()}" if kind in safe else None)
    )


def validate_typed_operation(value, *, fresh=True, authorization_fresh=True):
    """Parse the closed operation union; unknown fields are always rejected."""

    try:
        if not isinstance(value, dict):
            raise ValueError()
        kind = OperationKind(value.get("kind"))
        required = set(_TYPED_FIELDS[kind])
        required.update({"confirmation", "authorization"})
        if "authorization" not in value:
            raise ReleaseError("authorization_required")
        if "confirmation" not in value:
            raise ReleaseError("confirmation_required")
        common = {"job_id", "kind", "actor_user_id", "created_at", "capability_revision"}
        if not isinstance(value.get("capability_revision"), str) or not re.fullmatch(
            r"[a-f0-9]{64}", value["capability_revision"]
        ):
            raise ValueError()
        if set(value) != common | required:
            raise ValueError()
        operation_id = _canonical_uuid(value["job_id"])
        if type(value["actor_user_id"]) is not int or not 0 < value["actor_user_id"] < 2**63:
            raise ValueError()
        stamp = timestamp(value["created_at"])
        if stamp.utcoffset().total_seconds() != 0 or fresh and not -300 <= (
            datetime.now(UTC) - stamp
        ).total_seconds() <= 86400:
            raise ValueError()
        for key in ("device_uuid", "backup_id", "plan_id", "upload_id"):
            if key in value:
                _canonical_uuid(value[key])
        if "sha256" in value and (
            not isinstance(value["sha256"], str)
            or re.fullmatch(r"[a-f0-9]{64}", value["sha256"]) is None
        ):
            raise ValueError()
        if "version" in value and (
            not isinstance(value["version"], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,150}", value["version"]) is None
        ):
            raise ValueError()
        if "release_id" in value and (
            type(value["release_id"]) is not int or not 0 < value["release_id"] < 2**63
        ):
            raise ValueError()
        if "release" in value and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._+-]{0,150}", value["release"]
        ):
            raise ValueError()
        if "package" in value and value["package"] not in ALLOWED_PACKAGES:
            raise ValueError()
        if "service" in value and value["service"] not in ALLOWED_SERVICES:
            raise ValueError()
        if "categories" in value and (
            not isinstance(value["categories"], list)
            or not value["categories"]
            or len(value["categories"]) != len(set(value["categories"]))
            or any(item not in ALLOWED_CLEANUP_CATEGORIES for item in value["categories"])
        ):
            raise ValueError()
        if "authorization" in value:
            _validate_authorization(
                value["authorization"], value, fresh=authorization_fresh
            )
        phrase = _required_confirmation(kind, value)
        if phrase is not None and value["confirmation"] != phrase:
            raise ReleaseError("confirmation_required")
        if kind is OperationKind.USB_FORMAT and value["confirmation_repeat"] != phrase:
            raise ReleaseError("confirmation_required")
        payload = {key: value[key] for key in required if key not in {"authorization", "confirmation"}}
        return TypedOperation(
            operation_id=operation_id,
            kind=kind,
            actor_user_id=value["actor_user_id"],
            created_at=value["created_at"],
            payload=payload,
            request=dict(value),
        )
    except ReleaseError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise ReleaseError("invalid_command") from exc


def select_removable_device(devices, identity, *, destructive=False):
    """Resolve exactly one discovered device by filesystem UUID, never by raw path."""

    _canonical_uuid(identity)
    matches = [device for device in devices if device.uuid == identity]
    if len(matches) != 1:
        raise ReleaseError("unsafe_usb_device")
    device = matches[0]
    if (
        not device.removable
        or device.system_device
        or device.root_device
        or device.data_device
        or destructive
        and device.mounted
    ):
        raise ReleaseError("unsafe_usb_device")
    return device


def _perform_typed(effect, operation, devices):
    identity = operation.operation_id
    value = operation.payload
    kind = operation.kind
    if kind is OperationKind.OTA_UPDATE:
        return effect.ota_update(
            identity, value["upload_id"], value["sha256"], value["version"]
        )
    if kind is OperationKind.RELEASE_UPDATE:
        return effect.release_update(identity, value["release_id"])
    if kind is OperationKind.REINSTALL:
        return effect.reinstall(identity)
    if kind is OperationKind.ROLLBACK:
        return effect.rollback(identity, value["release"])
    if kind is OperationKind.PACKAGE_INSPECT:
        return effect.package_inspect(identity, value["package"])
    if kind is OperationKind.PACKAGE_UPDATE:
        return effect.package_update(identity, value["package"])
    if kind is OperationKind.SERVICE_RESTART:
        return effect.service_restart(identity, value["service"])
    if kind is OperationKind.REBOOT:
        return effect.reboot(identity)
    if kind is OperationKind.BACKUP:
        return effect.backup(identity, value["device_uuid"])
    if kind is OperationKind.BACKUP_VERIFY:
        return effect.backup_verify(identity, value["backup_id"])
    if kind is OperationKind.BACKUP_RESTORE:
        return effect.backup_restore(identity, value["backup_id"])
    if kind is OperationKind.CLEANUP_PREVIEW:
        return effect.cleanup_preview(identity, tuple(value["categories"]))
    if kind is OperationKind.CLEANUP_EXECUTE:
        return effect.cleanup_execute(identity, value["plan_id"])
    if kind is OperationKind.DIAGNOSTICS:
        return effect.diagnostics(identity)
    if kind is OperationKind.USB_DISCOVER:
        return effect.usb_discover(identity)
    device = select_removable_device(
        devices, value["device_uuid"], destructive=kind is OperationKind.USB_FORMAT
    )
    if kind is OperationKind.USB_FORMAT:
        return effect.usb_format(identity, device)
    if kind is OperationKind.USB_SELECT:
        return effect.usb_select(identity, device)
    raise ReleaseError("invalid_command")


def execute_typed_operation(
    paths, request, effects, *, devices=(), authorization_fresh=True
):
    """Execute one idempotent typed operation and atomically retain its result."""

    operation = validate_typed_operation(
        request, authorization_fresh=authorization_fresh
    )
    if operation.kind in {OperationKind.USB_FORMAT, OperationKind.USB_SELECT}:
        # Device safety is request validation, not a host-effect failure. Resolve it
        # before publishing a dispatch checkpoint so an invalid device cannot strand
        # a durable operation.
        select_removable_device(
            devices,
            operation.payload["device_uuid"],
            destructive=operation.kind is OperationKind.USB_FORMAT,
        )
    with exclusive_lock(paths.ops / "typed-operation.lock"):
        return _execute_typed_operation_locked(paths, operation, effects, tuple(devices))


def _execute_typed_operation_locked(paths, operation, effects, devices):
    from .operation_capabilities import (
        current_capability_revision,
        operation_capabilities,
    )
    from .state import read_operation_progress, write_operation_progress

    receipt = paths.state / "typed-operation-receipts" / f"{operation.operation_id}.json"
    if receipt.is_file():
        saved = _read(receipt, limit=65536)
        if saved.get("request") != operation.request:
            raise ReleaseError("duplicate_operation_id")
        result = saved.get("result")
        if (
            not isinstance(result, dict)
            or result.get("operation_id") != operation.operation_id
            or result.get("kind") != operation.kind.value
            or result.get("state") not in {"succeeded", "failed"}
        ):
            raise ReleaseError("invalid_command_receipt")
        write_operation_progress(paths, operation.operation_id, result["state"], 100)
        return result
    intent = paths.state / "typed-operation-intents" / f"{operation.operation_id}.json"
    if intent.exists() or intent.is_symlink():
        saved_intent = _read(intent, limit=65536)
        if saved_intent != {"schema": 1, "request": operation.request}:
            if saved_intent.get("request") != operation.request:
                raise ReleaseError("duplicate_operation_id")
            raise ReleaseError("invalid_operation_intent")
    else:
        atomic_write_json(intent, {"schema": 1, "request": operation.request})
    checkpoint = paths.state / "typed-operation-dispatch" / f"{operation.operation_id}.json"
    dispatched = False
    if checkpoint.exists() or checkpoint.is_symlink():
        saved = _read(checkpoint, limit=65536)
        if saved != {"schema": 1, "request": operation.request, "state": "dispatched"}:
            if saved.get("request") != operation.request:
                raise ReleaseError("duplicate_operation_id")
            raise ReleaseError("invalid_dispatch_checkpoint")
        dispatched = True
    progress = read_operation_progress(paths, operation.operation_id)
    if progress is None:
        write_operation_progress(paths, operation.operation_id, "accepted", 0)
        progress = read_operation_progress(paths, operation.operation_id)
    if progress["phase"] == "accepted":
        write_operation_progress(paths, operation.operation_id, "executing", 50)
    elif progress["phase"] != "executing":
        raise ReleaseError("invalid_operation_progress")

    def terminal(state, detail, error):
        result = {
            "job_id": operation.operation_id,
            "operation_id": operation.operation_id,
            "kind": operation.kind.value,
            "actor_user_id": operation.actor_user_id,
            "state": state,
            "detail": detail if isinstance(detail, dict) else {},
            "error": error,
        }
        atomic_write_json(receipt, {"request": operation.request, "result": result})
        write_operation_progress(paths, operation.operation_id, state, 100)
        return result

    available = operation_capabilities(effects)[operation.kind.value]["available"]
    revision = operation.request["capability_revision"]
    if not available:
        return terminal("failed", {}, "manual_recovery_required" if dispatched else "capability_unavailable")
    if revision != current_capability_revision(paths, effects):
        return terminal("failed", {}, "manual_recovery_required" if dispatched else "capabilities_changed")

    if dispatched:
        try:
            reconciliation = effects.reconcile(operation)
        except Exception:
            reconciliation = None
        if not isinstance(reconciliation, dict) or reconciliation.get("state") not in {
            "succeeded",
            "failed",
        }:
            return terminal("failed", {}, "manual_recovery_required")
        state = reconciliation["state"]
        error = reconciliation.get("error")
        if state == "failed" and not isinstance(error, str):
            error = "manual_recovery_required"
        return terminal(state, reconciliation.get("detail", {}), error)

    atomic_write_json(
        checkpoint,
        {"schema": 1, "request": operation.request, "state": "dispatched"},
    )
    try:
        detail = _perform_typed(effects, operation, devices)
        return terminal("succeeded", detail, None)
    except ReleaseError as exc:
        return terminal("failed", {}, str(exc) or "host_operation_failed")
    except Exception:
        return terminal("failed", {}, "host_operation_failed")


_BACKUP_MAGIC = b"RPBK1\n"


@dataclass(frozen=True, slots=True)
class BackupArchiveLimits:
    """Hard bounds applied before and while reading authenticated ZIP payloads."""

    max_members: int = 20_000
    max_member_bytes: int = 2 * 1024**3
    max_total_bytes: int = 16 * 1024**3
    max_compression_ratio: int = 200
    max_metadata_bytes: int = 4 * 1024**2
    max_central_directory_bytes: int = 64 * 1024**2
    max_archive_bytes: int = 4 * 1024**3

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in (
            self.max_members,
            self.max_member_bytes,
            self.max_total_bytes,
            self.max_compression_ratio,
            self.max_metadata_bytes,
            self.max_central_directory_bytes,
            self.max_archive_bytes,
        )):
            raise ValueError("invalid_backup_archive_limits")


DEFAULT_BACKUP_ARCHIVE_LIMITS = BackupArchiveLimits()
_ZIP_EOCD = b"PK\x05\x06"
_ZIP_EOCD_BYTES = 22
_ZIP_MAX_COMMENT_BYTES = 65535
_ZIP_CENTRAL_HEADER = b"PK\x01\x02"
_ZIP_CENTRAL_HEADER_BYTES = 46
_ZIP64_LOCATOR = b"PK\x06\x07"


def _sha256_path(path):
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def create_encrypted_backup(source, artifact, *, recovery_key, app_version, schema_version):
    """Create an AES-256-GCM backup whose key is never persisted with host data."""

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if not isinstance(recovery_key, bytes) or len(recovery_key) != 32:
        raise ReleaseError("recovery_key_required")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", app_version):
        raise ReleaseError("backup_version_invalid")
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,100}", schema_version):
        raise ReleaseError("backup_schema_invalid")
    source = Path(source)
    artifact = Path(artifact)
    if source.is_symlink() or not source.is_dir():
        raise ReleaseError("unsafe_backup_source")
    if artifact.resolve(strict=False).is_relative_to(source.resolve()):
        raise ReleaseError("unsafe_backup_destination")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    if artifact.exists() or artifact.is_symlink():
        raise ReleaseError("backup_exists")
    descriptor, archive_name = tempfile.mkstemp(dir=artifact.parent, prefix=".backup-payload-")
    os.close(descriptor)
    archive_path = Path(archive_name)
    descriptor, encrypted_name = tempfile.mkstemp(
        dir=artifact.parent, prefix=f".{artifact.name}.encrypted-"
    )
    os.close(descriptor)
    encrypted_path = Path(encrypted_name)
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_symlink():
                    raise ReleaseError("unsafe_backup_source")
                if path.is_file():
                    archive.write(path, path.relative_to(source).as_posix())
        payload_sha256 = _sha256_path(archive_path)
        manifest = {
            "format": 1,
            "app_version": app_version,
            "schema_version": schema_version,
            "payload_sha256": payload_sha256,
        }
        header = _BACKUP_MAGIC + json.dumps(
            manifest, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode() + b"\n"
        nonce = os.urandom(12)
        encryptor = Cipher(algorithms.AES(recovery_key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(header)
        with archive_path.open("rb") as source_stream, encrypted_path.open("wb") as output:
            output.write(header)
            output.write(nonce)
            while chunk := source_stream.read(1024 * 1024):
                output.write(encryptor.update(chunk))
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
            output.flush()
            os.fsync(output.fileno())
        os.replace(encrypted_path, artifact)
        directory = os.open(artifact.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {
            "verified": False,
            "sha256": _sha256_path(artifact),
            "app_version": app_version,
            "schema_version": schema_version,
            "payload_sha256": payload_sha256,
        }
    finally:
        archive_path.unlink(missing_ok=True)
        encrypted_path.unlink(missing_ok=True)


def verify_encrypted_backup(
    artifact, *, recovery_key, archive_limits=DEFAULT_BACKUP_ARCHIVE_LIMITS
):
    """Read back, authenticate and inspect a backup before issuing a verified receipt."""

    artifact = Path(artifact)
    if not isinstance(recovery_key, bytes) or len(recovery_key) != 32:
        raise ReleaseError("recovery_key_required")
    try:
        if artifact.stat().st_size > archive_limits.max_archive_bytes + 8192:
            raise ReleaseError("backup_archive_limit")
        with artifact.open("rb") as source:
            if source.readline() != _BACKUP_MAGIC:
                raise ReleaseError("backup_manifest_invalid")
            manifest_line = source.readline(4097)
            if len(manifest_line) > 4096 or not manifest_line.endswith(b"\n"):
                raise ReleaseError("backup_manifest_invalid")
            manifest = json.loads(manifest_line, object_pairs_hook=unique_object)
    except ReleaseError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise ReleaseError("backup_manifest_invalid") from exc
    receipt = {
        "verified": True,
        "sha256": _sha256_path(artifact),
        "app_version": manifest.get("app_version"),
        "schema_version": manifest.get("schema_version"),
        "payload_sha256": manifest.get("payload_sha256"),
    }
    restore_encrypted_backup(
        artifact,
        None,
        recovery_key=recovery_key,
        verified=receipt,
        archive_limits=archive_limits,
        _verify_only=True,
    )
    return receipt


def _preflight_zip_directory(plain, limits):
    """Bound EOCD and central-directory metadata before ZipFile allocates entries."""

    try:
        size = plain.stat().st_size
        tail_size = min(size, _ZIP_EOCD_BYTES + _ZIP_MAX_COMMENT_BYTES)
        with plain.open("rb") as stream:
            stream.seek(size - tail_size)
            tail = stream.read(tail_size)
        relative = tail.rfind(_ZIP_EOCD)
        if relative < 0 or len(tail) - relative < _ZIP_EOCD_BYTES:
            raise ReleaseError("backup_manifest_invalid")
        (
            signature,
            disk,
            central_disk,
            disk_entries,
            total_entries,
            central_bytes,
            central_offset,
            comment_bytes,
        ) = struct.unpack_from("<4s4H2LH", tail, relative)
        eocd_offset = size - tail_size + relative
        if (
            signature != _ZIP_EOCD
            or comment_bytes != len(tail) - relative - _ZIP_EOCD_BYTES
            or disk != 0
            or central_disk != 0
            or disk_entries != total_entries
        ):
            raise ReleaseError("backup_manifest_invalid")
        if (
            total_entries == 0xFFFF
            or central_bytes == 0xFFFFFFFF
            or central_offset == 0xFFFFFFFF
        ):
            raise ReleaseError("backup_archive_limit")
        if (
            total_entries > limits.max_members
            or central_bytes > limits.max_central_directory_bytes
            or central_bytes > limits.max_metadata_bytes
        ):
            raise ReleaseError("backup_archive_limit")
        if central_offset > eocd_offset or central_offset + central_bytes != eocd_offset:
            raise ReleaseError("backup_manifest_invalid")
        if eocd_offset >= 20:
            with plain.open("rb") as stream:
                stream.seek(eocd_offset - 20)
                if stream.read(4) == _ZIP64_LOCATOR:
                    raise ReleaseError("backup_archive_limit")
        actual_entries = 0
        cursor = central_offset
        central_end = central_offset + central_bytes
        with plain.open("rb") as stream:
            while cursor < central_end:
                if central_end - cursor < _ZIP_CENTRAL_HEADER_BYTES:
                    raise ReleaseError("backup_manifest_invalid")
                stream.seek(cursor)
                header = stream.read(_ZIP_CENTRAL_HEADER_BYTES)
                fields = struct.unpack("<4s6H3L5H2L", header)
                if fields[0] != _ZIP_CENTRAL_HEADER or fields[13] != 0:
                    raise ReleaseError("backup_manifest_invalid")
                compressed, uncompressed = fields[8], fields[9]
                name_bytes, extra_bytes, member_comment_bytes = fields[10:13]
                local_offset = fields[16]
                if (
                    compressed == 0xFFFFFFFF
                    or uncompressed == 0xFFFFFFFF
                    or local_offset == 0xFFFFFFFF
                ):
                    raise ReleaseError("backup_archive_limit")
                entry_bytes = (
                    _ZIP_CENTRAL_HEADER_BYTES
                    + name_bytes
                    + extra_bytes
                    + member_comment_bytes
                )
                if entry_bytes > central_end - cursor:
                    raise ReleaseError("backup_manifest_invalid")
                stream.seek(cursor + _ZIP_CENTRAL_HEADER_BYTES + name_bytes)
                extra = stream.read(extra_bytes)
                offset = 0
                while offset < len(extra):
                    if len(extra) - offset < 4:
                        raise ReleaseError("backup_manifest_invalid")
                    field_id, field_bytes = struct.unpack_from("<HH", extra, offset)
                    offset += 4
                    if field_bytes > len(extra) - offset:
                        raise ReleaseError("backup_manifest_invalid")
                    if field_id == 0x0001:
                        raise ReleaseError("backup_archive_limit")
                    offset += field_bytes
                actual_entries += 1
                if actual_entries > limits.max_members:
                    raise ReleaseError("backup_archive_limit")
                cursor += entry_bytes
        if actual_entries != total_entries or cursor != central_end:
            raise ReleaseError("backup_manifest_invalid")
        return total_entries, central_bytes
    except ReleaseError:
        raise
    except (OSError, ValueError, struct.error) as exc:
        raise ReleaseError("backup_manifest_invalid") from exc


def _inspect_backup_archive(plain, staging, limits):
    """Validate the complete central directory, then stream each bounded member."""

    try:
        declared_members, _ = _preflight_zip_directory(plain, limits)
        with zipfile.ZipFile(plain) as archive:
            members = archive.infolist()
            if len(members) != declared_members:
                raise ReleaseError("backup_manifest_invalid")
            seen = set()
            total_declared = 0
            metadata = len(archive.comment)
            for member in members:
                name = member.filename
                path = Path(name)
                mode = member.external_attr >> 16
                metadata += (
                    46
                    + len(name.encode("utf-8"))
                    + len(member.extra)
                    + len(member.comment)
                )
                ratio = member.file_size / max(1, member.compress_size)
                if (
                    not name
                    or "\\" in name
                    or "\x00" in name
                    or path.is_absolute()
                    or ".." in path.parts
                    or ":" in path.parts[0]
                    or member.is_dir()
                    or name in seen
                    or mode and not stat.S_ISREG(mode)
                ):
                    raise ReleaseError("backup_manifest_invalid")
                if (
                    member.file_size > limits.max_member_bytes
                    or total_declared + member.file_size > limits.max_total_bytes
                    or ratio > limits.max_compression_ratio
                    or metadata > limits.max_metadata_bytes
                ):
                    raise ReleaseError("backup_archive_limit")
                total_declared += member.file_size
                seen.add(name)
            total_read = 0
            for member in members:
                written = 0
                output = None
                if staging is not None:
                    destination = staging.joinpath(*Path(member.filename).parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    descriptor = os.open(
                        destination,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                    )
                    output = os.fdopen(descriptor, "wb")
                try:
                    with archive.open(member, "r") as source:
                        while chunk := source.read(1024 * 1024):
                            written += len(chunk)
                            total_read += len(chunk)
                            if (
                                written > member.file_size
                                or written > limits.max_member_bytes
                                or total_read > limits.max_total_bytes
                            ):
                                raise ReleaseError("backup_archive_limit")
                            if output is not None:
                                output.write(chunk)
                    if written != member.file_size:
                        raise ReleaseError("backup_integrity_failed")
                    if output is not None:
                        output.flush()
                        os.fsync(output.fileno())
                finally:
                    if output is not None:
                        output.close()
    except ReleaseError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ReleaseError("backup_manifest_invalid") from exc


def restore_encrypted_backup(
    artifact,
    target,
    *,
    recovery_key,
    verified,
    expected_app_version=None,
    expected_schema_version=None,
    archive_limits=DEFAULT_BACKUP_ARCHIVE_LIMITS,
    _verify_only=False,
):
    """Authenticate a local archive before extracting into an empty target.

    Local cryptographic receipts remain supported for offline staging. Production
    USB receipts require host selection/ownership checks and must not enter this
    path while the device-aware production restore capability is unavailable.
    """

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if not isinstance(recovery_key, bytes) or len(recovery_key) != 32:
        raise ReleaseError("recovery_key_required")
    artifact = Path(artifact)
    try:
        if artifact.stat().st_size > archive_limits.max_archive_bytes + 8192:
            raise ReleaseError("backup_archive_limit")
    except OSError as exc:
        raise ReleaseError("backup_integrity_failed") from exc
    if not isinstance(verified, dict) or verified.get("verified") is not True:
        raise ReleaseError("verified_backup_required")
    if "device_uuid" in verified or "backup_id" in verified:
        raise ReleaseError("device_bound_restore_unavailable")
    if verified.get("sha256") != _sha256_path(artifact):
        raise ReleaseError("backup_integrity_failed")
    descriptor, plain_name = tempfile.mkstemp(dir=artifact.parent, prefix=".restore-payload-")
    os.close(descriptor)
    plain = Path(plain_name)
    try:
        with artifact.open("rb") as source:
            if source.readline() != _BACKUP_MAGIC:
                raise ReleaseError("backup_manifest_invalid")
            manifest_line = source.readline(4097)
            if len(manifest_line) > 4096 or not manifest_line.endswith(b"\n"):
                raise ReleaseError("backup_manifest_invalid")
            try:
                manifest = json.loads(manifest_line, object_pairs_hook=unique_object)
            except (ValueError, UnicodeError) as exc:
                raise ReleaseError("backup_manifest_invalid") from exc
            if (
                not isinstance(manifest, dict)
                or set(manifest)
                != {"format", "app_version", "schema_version", "payload_sha256"}
                or manifest.get("format") != 1
                or not re.fullmatch(
                    r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?",
                    str(manifest.get("app_version", "")),
                )
                or not re.fullmatch(
                    r"[A-Za-z0-9._+-]{1,100}", str(manifest.get("schema_version", ""))
                )
                or not re.fullmatch(r"[a-f0-9]{64}", str(manifest.get("payload_sha256", "")))
            ):
                raise ReleaseError("backup_manifest_invalid")
            if verified.get("app_version") != manifest["app_version"]:
                raise ReleaseError("backup_version_incompatible")
            if verified.get("schema_version") != manifest["schema_version"]:
                raise ReleaseError("backup_schema_incompatible")
            if expected_app_version is not None and expected_app_version != manifest["app_version"]:
                raise ReleaseError("backup_version_incompatible")
            if (
                expected_schema_version is not None
                and expected_schema_version != manifest["schema_version"]
            ):
                raise ReleaseError("backup_schema_incompatible")
            if verified.get("payload_sha256") != manifest["payload_sha256"]:
                raise ReleaseError("backup_integrity_failed")
            nonce = source.read(12)
            ciphertext_start = source.tell()
            source.seek(0, os.SEEK_END)
            ciphertext_end = source.tell()
            ciphertext_bytes = ciphertext_end - ciphertext_start - 16
            if (
                len(nonce) != 12
                or ciphertext_bytes < 0
                or ciphertext_bytes > archive_limits.max_archive_bytes
            ):
                raise ReleaseError("backup_integrity_failed")
            source.seek(ciphertext_end - 16)
            tag = source.read(16)
            source.seek(ciphertext_start)
            decryptor = Cipher(algorithms.AES(recovery_key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(_BACKUP_MAGIC + manifest_line)
            remaining = ciphertext_bytes
            with plain.open("wb") as output:
                try:
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ReleaseError("backup_integrity_failed")
                        remaining -= len(chunk)
                        output.write(decryptor.update(chunk))
                        if output.tell() > archive_limits.max_archive_bytes:
                            raise ReleaseError("backup_archive_limit")
                    output.write(decryptor.finalize())
                except InvalidTag as exc:
                    raise ReleaseError("backup_integrity_failed") from exc
                output.flush()
                os.fsync(output.fileno())
        if _sha256_path(plain) != manifest["payload_sha256"]:
            raise ReleaseError("backup_integrity_failed")
        if _verify_only:
            _inspect_backup_archive(plain, None, archive_limits)
            return {**manifest, "verified": True}
        target = Path(target)
        if target.exists() or target.is_symlink():
            raise ReleaseError("restore_target_not_empty")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=f".{target.name}.restore-"))
        try:
            _inspect_backup_archive(plain, staging, archive_limits)
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return {**manifest, "verified": True}
    finally:
        plain.unlink(missing_ok=True)


def _read(path, limit=4096):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError()
            value = json.loads(stream.read(limit + 1), object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise ReleaseError("invalid_command") from exc


def discover_usb_devices(paths):
    """Read the kernel UUID/removable/mount projections without invoking a command."""

    device_root = paths.root / "dev"
    uuid_root = device_root / "disk/by-uuid"
    mountinfo = paths.root / "proc/self/mountinfo"
    mounts = {}
    try:
        if mountinfo.is_file() and not mountinfo.is_symlink():
            raw = mountinfo.read_bytes()
            if len(raw) > 1024 * 1024:
                raise ReleaseError("usb_discovery_failed")
            for line in raw.decode("utf-8").splitlines():
                fields = line.split()
                separator = fields.index("-")
                source = fields[separator + 2]
                point = fields[4].replace("\\040", " ").replace("\\134", "\\")
                if re.fullmatch(r"/dev/[A-Za-z0-9._+-]+", source):
                    mounts.setdefault(source, []).append(point)
        if not uuid_root.exists():
            return ()
        if uuid_root.resolve().parent != (device_root / "disk").resolve() or not uuid_root.is_dir():
            raise ReleaseError("usb_discovery_failed")
        entries = list(uuid_root.iterdir())
        if len(entries) > 256:
            raise ReleaseError("usb_discovery_failed")
        devices = []
        for entry in entries:
            try:
                identity = _canonical_uuid(entry.name.lower())
                if not entry.is_symlink():
                    raise ValueError()
                target = entry.resolve(strict=True)
                target_info = target.stat()
                if target.parent != device_root.resolve() or not (
                    stat.S_ISBLK(target_info.st_mode)
                    or paths.root != Path("/")
                    and stat.S_ISREG(target_info.st_mode)
                ):
                    raise ValueError()
                logical_path = "/dev/" + target.name
                removable = (
                    paths.root / "sys/class/block" / target.name / "removable"
                ).read_text().strip() == "1"
                points = mounts.get(logical_path, [])
                root_device = "/" in points
                data_relative = str(paths.var.relative_to(paths.root))
                data_device = any(
                    point == "/"
                    or data_relative == point.lstrip("/")
                    or data_relative.startswith(point.lstrip("/") + "/")
                    for point in points
                )
                devices.append(
                    BlockDevice(
                        identity,
                        logical_path,
                        removable=removable,
                        mounted=bool(points),
                        root_device=root_device,
                        data_device=data_device,
                        mount_point=points[0] if len(points) == 1 else None,
                    )
                )
            except (OSError, ValueError, UnicodeError):
                continue
        return tuple(devices)
    except ReleaseError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise ReleaseError("usb_discovery_failed") from exc


class SafeProductionTypedHostEffects(TypedHostEffects):
    """Fail-closed production adapter for policy-safe read/file-only operations."""

    supported_kinds = frozenset(
        {
            OperationKind.PACKAGE_INSPECT,
            OperationKind.BACKUP_VERIFY,
            OperationKind.CLEANUP_PREVIEW,
            OperationKind.DIAGNOSTICS,
            OperationKind.USB_DISCOVER,
            OperationKind.USB_SELECT,
        }
    )

    def __init__(
        self, paths, *, runner=None, http=None, device_provider=None, ota_effects=None
    ):
        self.paths = paths
        self.runner = runner
        self.http = http
        self.ota_effects = ota_effects
        self.device_provider = device_provider or (lambda: discover_usb_devices(paths))
        if ota_effects is not None:
            self.supported_kinds = self.supported_kinds | {OperationKind.OTA_UPDATE}
        if runner is None or http is None:
            self.supported_kinds = self.supported_kinds - {OperationKind.DIAGNOSTICS}
        self.unavailable_reasons = {}
        try:
            self._external_key()
        except ReleaseError:
            self.supported_kinds = self.supported_kinds - {OperationKind.BACKUP_VERIFY}
            self.unavailable_reasons[OperationKind.BACKUP_VERIFY] = "context_unavailable"

    @classmethod
    def unsupported_kinds(cls):
        return frozenset(set(OperationKind) - set(cls.supported_kinds))

    @staticmethod
    def _unavailable():
        raise ReleaseError("capability_unavailable")

    def release_update(self, operation_id, release_id):
        del operation_id, release_id
        return self._unavailable()

    def ota_update(self, operation_id, upload_id, sha256, version):
        if self.ota_effects is None:
            return self._unavailable()
        return self.ota_effects.ota_update(operation_id, upload_id, sha256, version)

    def reinstall(self, operation_id):
        del operation_id
        return self._unavailable()

    def rollback(self, operation_id, release):
        del operation_id, release
        return self._unavailable()

    def package_update(self, operation_id, package):
        del operation_id, package
        return self._unavailable()

    def service_restart(self, operation_id, service):
        del operation_id, service
        return self._unavailable()

    def reboot(self, operation_id):
        del operation_id
        return self._unavailable()

    def backup(self, operation_id, device_uuid):
        del operation_id, device_uuid
        return self._unavailable()

    def backup_restore(self, operation_id, backup_id):
        del operation_id, backup_id
        return self._unavailable()

    def cleanup_execute(self, operation_id, plan_id):
        del operation_id, plan_id
        return self._unavailable()

    def usb_format(self, operation_id, device):
        del operation_id, device
        return self._unavailable()

    def reconcile(self, operation):
        try:
            if operation.kind is OperationKind.PACKAGE_INSPECT:
                detail = self.package_inspect(
                    operation.operation_id, operation.payload["package"]
                )
            elif operation.kind is OperationKind.CLEANUP_PREVIEW:
                detail = self.cleanup_preview(
                    operation.operation_id, tuple(operation.payload["categories"])
                )
            elif operation.kind is OperationKind.USB_DISCOVER:
                detail = self.usb_discover(operation.operation_id)
            elif operation.kind is OperationKind.BACKUP_VERIFY:
                self.verified_backup_receipt(operation.payload["backup_id"])
                detail = {
                    "backup_id": operation.payload["backup_id"],
                    "verified": True,
                }
            elif operation.kind is OperationKind.DIAGNOSTICS:
                detail = self.diagnostics(operation.operation_id)
            elif operation.kind is OperationKind.USB_SELECT:
                selected = self._read_private_json(None, "selected-usb.json")
                if selected != {
                    "schema": 1,
                    "device_uuid": operation.payload["device_uuid"],
                }:
                    raise ReleaseError("usb_not_selected")
                detail = selected
            else:
                return super().reconcile(operation)
            return {"state": "succeeded", "detail": detail, "error": None}
        except ReleaseError:
            return {
                "state": "failed",
                "detail": {},
                "error": "manual_recovery_required",
            }

    def _external_key(self):
        target = self.paths.root / "run/robopark/recovery.key"
        try:
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                key = stream.read(33)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o077
                or len(key) != 32
            ):
                raise ValueError()
            return key
        except (OSError, ValueError) as exc:
            raise ReleaseError("recovery_key_required") from exc

    @staticmethod
    def _same_inode(left, right):
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)

    def _validate_bindings(self, bindings):
        try:
            for parent, name, child, directory in bindings:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                opened = os.fstat(child)
                if (
                    not self._same_inode(current, opened)
                    or directory
                    and not stat.S_ISDIR(current.st_mode)
                    or not directory
                    and not stat.S_ISREG(current.st_mode)
                    or not directory
                    and (
                        opened.st_nlink != 1
                        or opened.st_uid not in {0, os.geteuid()}
                        or stat.S_IMODE(opened.st_mode) & 0o077
                    )
                ):
                    raise ReleaseError("unsafe_backup_path")
        except ReleaseError:
            raise
        except OSError as exc:
            raise ReleaseError("unsafe_backup_path") from exc

    def _open_backup_candidate(self, device, backup_id):
        if (
            not device.removable
            or not device.mounted
            or device.mount_point is None
            or device.system_device
            or device.root_device
            or device.data_device
        ):
            return None
        point = Path(device.mount_point)
        parts = point.parts[1:]
        if (
            not point.is_absolute()
            or not parts
            or parts[0] not in {"media", "mnt", "run"}
            or parts[0] == "run" and (len(parts) < 2 or parts[1] != "media")
            or any(not re.fullmatch(r"[A-Za-z0-9._+-]+", part) for part in parts)
        ):
            raise ReleaseError("unsafe_backup_path")
        descriptors = []
        bindings = []
        try:
            current = os.open(
                self.paths.root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            descriptors.append(current)
            for part in parts:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                descriptors.append(child)
                bindings.append((current, part, child, True))
                current = child
            if self.paths.root == Path("/"):
                device_fd = os.open(
                    device.path,
                    os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                )
                try:
                    device_info = os.fstat(device_fd)
                    mount_info = os.fstat(current)
                    if (
                        not stat.S_ISBLK(device_info.st_mode)
                        or mount_info.st_dev != device_info.st_rdev
                    ):
                        raise ReleaseError("unsafe_backup_path")
                finally:
                    os.close(device_fd)
            backups = os.open(
                "robopark-backups",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            descriptors.append(backups)
            bindings.append((current, "robopark-backups", backups, True))
            name = f"backup-{backup_id}.rpb"
            artifact = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=backups,
            )
            descriptors.append(artifact)
            bindings.append((backups, name, artifact, False))
            info = os.fstat(artifact)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(info.st_mode) & 0o077
                or not 0 < info.st_size
                <= DEFAULT_BACKUP_ARCHIVE_LIMITS.max_archive_bytes + 8192
            ):
                raise ReleaseError("unsafe_backup_path")
            self._validate_bindings(bindings)
            return descriptors, bindings, artifact
        except FileNotFoundError:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            return None
        except ReleaseError:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            raise
        except OSError as exc:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            raise ReleaseError("unsafe_backup_path") from exc

    def _selected_device(self):
        selected = self._read_private_json(None, "selected-usb.json")
        if set(selected) != {"schema", "device_uuid"} or selected["schema"] != 1:
            raise ReleaseError("usb_not_selected")
        try:
            identity = _canonical_uuid(selected["device_uuid"])
        except (TypeError, ValueError) as exc:
            raise ReleaseError("usb_not_selected") from exc
        device = select_removable_device(tuple(self.device_provider()), identity)
        if not device.mounted or device.mount_point is None:
            raise ReleaseError("unsafe_usb_device")
        return device

    @contextmanager
    def _backup_artifact(self, backup_id, *, device_uuid):
        identity = _canonical_uuid(backup_id)
        device = self._selected_device()
        if device.uuid != _canonical_uuid(device_uuid):
            raise ReleaseError("backup_device_changed")
        candidate = self._open_backup_candidate(device, identity)
        if candidate is None:
            raise ReleaseError("backup_not_found")
        descriptors, bindings, artifact = candidate
        try:
            self._validate_bindings(bindings)
            yield artifact
            self._validate_bindings(bindings)
            if self._selected_device() != device:
                raise ReleaseError("backup_device_changed")
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def verified_backup_receipt(self, backup_id):
        """Revalidate a USB receipt against the selected device and pinned bytes.

        Legacy receipts without a device UUID cannot authorize production actions.
        Caller holds host.lock for the selection and action to remain serialized.
        """
        identity = _canonical_uuid(backup_id)
        receipt = self._read_private_json("backup-receipts", f"{identity}.json")
        if (
            set(receipt) != {
                "schema", "backup_id", "device_uuid", "verified", "sha256",
                "verified_at", "recovery_required",
            }
            or receipt["schema"] != 1
            or receipt["backup_id"] != identity
            or receipt["verified"] is not True
            or not re.fullmatch(r"[a-f0-9]{64}", str(receipt["sha256"]))
            or type(receipt["verified_at"]) not in {int, float}
            or type(receipt["recovery_required"]) is not bool
        ):
            raise ReleaseError("backup_not_verified")
        try:
            _canonical_uuid(receipt["device_uuid"])
        except (TypeError, ValueError) as exc:
            raise ReleaseError("backup_not_verified") from exc
        digest = hashlib.sha256()
        total = 0
        with self._backup_artifact(identity, device_uuid=receipt["device_uuid"]) as artifact:
            while chunk := os.read(artifact, 1024 * 1024):
                total += len(chunk)
                if total > DEFAULT_BACKUP_ARCHIVE_LIMITS.max_archive_bytes + 8192:
                    raise ReleaseError("backup_archive_limit")
                digest.update(chunk)
        if digest.hexdigest() != receipt["sha256"]:
            raise ReleaseError("backup_integrity_failed")
        return receipt

    def _state_directory(self, child=None, *, create=False):
        state = None
        directory = None
        try:
            state = os.open(
                self.paths.state,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            if child is None:
                return state, None
            if create:
                try:
                    os.mkdir(child, 0o700, dir_fd=state)
                except FileExistsError:
                    pass
            directory = os.open(
                child,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=state,
            )
            info = os.stat(child, dir_fd=state, follow_symlinks=False)
            if not self._same_inode(info, os.fstat(directory)):
                raise ReleaseError("unsafe_backup_receipt")
            return directory, (state, child)
        except ReleaseError:
            if directory is not None:
                os.close(directory)
            if state is not None:
                os.close(state)
            raise
        except OSError as exc:
            if directory is not None:
                os.close(directory)
            if state is not None:
                os.close(state)
            raise ReleaseError("unsafe_backup_receipt") from exc

    def _close_state_directory(self, directory, binding):
        try:
            if binding is not None:
                state, child = binding
                try:
                    current = os.stat(child, dir_fd=state, follow_symlinks=False)
                    if not self._same_inode(current, os.fstat(directory)):
                        raise ReleaseError("unsafe_backup_receipt")
                except OSError as exc:
                    raise ReleaseError("unsafe_backup_receipt") from exc
        finally:
            os.close(directory)
            if binding is not None:
                os.close(binding[0])

    def _read_private_json(self, child, name):
        directory, binding = self._state_directory(child)
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid not in {0, os.geteuid()}
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > 65536
                ):
                    raise ReleaseError("unsafe_backup_receipt")
                value = json.loads(stream.read(65537), object_pairs_hook=unique_object)
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not self._same_inode(info, current):
                raise ReleaseError("unsafe_backup_receipt")
            if not isinstance(value, dict):
                raise ReleaseError("unsafe_backup_receipt")
            return value
        except ReleaseError:
            raise
        except (OSError, ValueError, UnicodeError) as exc:
            raise ReleaseError("unsafe_backup_receipt") from exc
        finally:
            self._close_state_directory(directory, binding)

    def _write_private_json(self, child, name, value):
        directory, binding = self._state_directory(child, create=child is not None)
        temporary = f".{name}.{os.urandom(8).hex()}"
        descriptor = None
        try:
            encoded = (
                json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode()
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(
                temporary,
                name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
            os.fsync(directory)
        except (OSError, ValueError) as exc:
            raise ReleaseError("unsafe_backup_receipt") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
            self._close_state_directory(directory, binding)

    def package_inspect(self, operation_id, package):
        del operation_id
        if package not in ALLOWED_PACKAGES:
            raise ReleaseError("package_not_allowed")
        target = self.paths.root / "var/lib/dpkg/status"
        if not target.exists():
            return {"package": package, "installed": False, "version": None}
        try:
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024**2:
                    raise ValueError()
                raw = stream.read(32 * 1024**2 + 1).decode("utf-8")
            for paragraph in raw.split("\n\n"):
                fields = {}
                for line in paragraph.splitlines():
                    if ": " in line:
                        key, value = line.split(": ", 1)
                        fields[key] = value
                if fields.get("Package") == package:
                    version = fields.get("Version")
                    if not isinstance(version, str) or not re.fullmatch(
                        r"[A-Za-z0-9.+:~_-]{1,200}", version
                    ):
                        raise ValueError()
                    return {
                        "package": package,
                        "installed": fields.get("Status") == "install ok installed",
                        "version": version,
                    }
            return {"package": package, "installed": False, "version": None}
        except (OSError, ValueError, UnicodeError) as exc:
            raise ReleaseError("package_inspection_failed") from exc

    def backup_verify(self, operation_id, backup_id):
        del operation_id
        backup_id = _canonical_uuid(backup_id)
        device = self._selected_device()
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.paths.state, prefix=".backup-verify-"
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        total = 0
        try:
            with self._backup_artifact(backup_id, device_uuid=device.uuid) as artifact, os.fdopen(
                descriptor, "wb"
            ) as output:
                descriptor = None
                os.lseek(artifact, 0, os.SEEK_SET)
                while chunk := os.read(artifact, 1024 * 1024):
                    total += len(chunk)
                    if total > DEFAULT_BACKUP_ARCHIVE_LIMITS.max_archive_bytes + 8192:
                        raise ReleaseError("backup_archive_limit")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            detail = verify_encrypted_backup(
                temporary, recovery_key=self._external_key()
            )
            if detail["sha256"] != digest.hexdigest():
                raise ReleaseError("backup_integrity_failed")
            if self._selected_device() != device:
                raise ReleaseError("backup_device_changed")
        finally:
            if descriptor is not None:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
        self._write_private_json(
            "backup-receipts",
            f"{backup_id}.json",
            {
                "schema": 1,
                "backup_id": backup_id,
                "device_uuid": device.uuid,
                "verified": True,
                "sha256": detail["sha256"],
                "verified_at": datetime.now(UTC).timestamp(),
                "recovery_required": False,
            },
        )
        return {"backup_id": backup_id, "verified": True}

    def cleanup_preview(self, operation_id, categories):
        from .retention import StorageBudget, preview_system_cleanup_plan

        del operation_id
        return preview_system_cleanup_plan(
            self.paths,
            categories,
            StorageBudget.for_path(self.paths.var),
        )

    def diagnostics(self, operation_id):
        del operation_id
        if self.runner is None or self.http is None:
            raise ReleaseError("capability_unavailable")
        report = run_doctor(self.paths, self.runner, self.http)
        publish_health(self.paths, report)
        return {"completed": True}

    def usb_discover(self, operation_id):
        del operation_id
        return {
            "devices": [
                {
                    "device_uuid": device.uuid,
                    "removable": device.removable,
                    "mounted": device.mounted,
                }
                for device in self.device_provider()
                if device.removable
                and not device.system_device
                and not device.root_device
                and not device.data_device
            ]
        }

    def usb_select(self, operation_id, device):
        del operation_id
        selected = select_removable_device(self.device_provider(), device.uuid)
        if selected.path != device.path:
            raise ReleaseError("unsafe_usb_device")
        value = {"schema": 1, "device_uuid": selected.uuid}
        self._write_private_json(None, "selected-usb.json", value)
        return value


def _validate(value, *, fresh=True, trusted_typed=False):
    try:
        kind = OperationKind(value.get("kind"))
    except (TypeError, ValueError):
        kind = None
    if kind is not None and (kind is not OperationKind.DIAGNOSTICS or "authorization" in value):
        return validate_typed_operation(
            value, fresh=fresh, authorization_fresh=not trusted_typed
        ).request
    if value.get("kind") == "update":
        try:
            stamp = timestamp(value.get("created_at"))
            if stamp.utcoffset().total_seconds() != 0:
                raise ValueError()
            # Preserve the exact request. Expired root claims must still reach
            # recovery; the worker never receives a refreshed approval.
            candidate = value if fresh else {**value, "created_at": datetime.now(UTC).isoformat()}
            UpdateRequest.from_dict(candidate)
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            raise ReleaseError("invalid_command") from exc
        return value
    try:
        expected = {"job_id", "kind", "actor_user_id", "created_at"}
        if value.get("kind") == "restore":
            expected.update({"artifact", "sha256"})
            if value.get("artifact") != "restore-" + str(value.get("job_id")) + ".zip":
                raise ValueError()
            if not isinstance(value.get("sha256"), str) or not re.fullmatch(
                r"[a-f0-9]{64}", value["sha256"]
            ):
                raise ValueError()
        if value.get("kind") == "github-update":
            expected.add("release_id")
            if type(value.get("release_id")) is not int or not 0 < value["release_id"] < 2**63:
                raise ValueError()
        if set(value) != expected:
            raise ValueError()
        if str(UUID(value["job_id"])) != value["job_id"] or value["kind"] not in {
            "diagnostics",
            "repair",
            "github-update",
            "restore",
        }:
            raise ValueError()
        if type(value["actor_user_id"]) is not int or not 0 < value["actor_user_id"] < 2**63:
            raise ValueError()
        stamp = timestamp(value["created_at"])
        if (
            stamp.utcoffset().total_seconds() != 0
            or fresh
            and not -300 <= (datetime.now(UTC) - stamp).total_seconds() <= 86400
        ):
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        raise ReleaseError("invalid_command") from exc


def _public(paths):
    root = paths.ops / "public"
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    root.chmod(0o755)
    return root


def publish_health(paths, report):
    """Only canonical diagnostic fields and bounded release identifiers leave root."""
    try:
        manifest = json.loads((paths.current / "manifest.json").read_text())
    except (OSError, ValueError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}

    def identifier(key, pattern):
        value = manifest.get(key)
        return value if isinstance(value, str) and re.fullmatch(pattern, value) else None

    value = {
        "version": public_version(manifest.get("app_version")),
        "git_sha": identifier("git_sha", r"[a-fA-F0-9]{40}"),
        "generated_at": report.created_at,
        "overall": "degraded" if any(c.status != "ok" for c in report.checks) else "ok",
        "checks": report.as_dict()["checks"],
        "update": {"state": "unknown"},
        "last_backup": {"status": "unknown"},
    }
    value["last_backup"] = backup_state(paths)
    value["update"] = update_state(paths)
    atomic_write_json(_public(paths) / "system-health.json", value, mode=0o644)


def _claim_public(paths, request, active):
    atomic_write_json(
        _public(paths) / "command-claim.json",
        {
            "job_id": request["job_id"],
            "kind": request["kind"],
            "actor_user_id": request["actor_user_id"],
            "active": active,
        },
        mode=0o644,
    )


def _finish(paths, request, result):
    public = _public(paths)
    atomic_write_json(public / "command-result.json", result, mode=0o644)
    receipts = paths.state / "command-receipts"
    atomic_write_json(
        receipts / (request["job_id"] + ".json"), {"request": request, "result": result}
    )
    _claim_public(paths, request, False)
    (paths.state / "command-request.json").unlink(missing_ok=True)


def _allow_attempt(paths, request):
    counter = paths.state / "command-attempts.json"
    invalid_restore_counter = False
    try:
        saved = _read(counter)
        invalid_restore_counter = request["kind"] == "restore" and (
            set(saved) != {"job_id", "attempts"}
            or not isinstance(saved["job_id"], str)
            or type(saved["attempts"]) is not int
            or not 0 <= saved["attempts"] <= 3
        )
    except ReleaseError:
        saved = {}
        invalid_restore_counter = request["kind"] == "restore" and (
            counter.exists() or counter.is_symlink()
        )
    attempts = saved.get("attempts", 0) if saved.get("job_id") == request["job_id"] else 0
    if invalid_restore_counter or type(attempts) is not int or attempts < 0:
        attempts = 3
    if attempts >= 3:
        # No more automatic retries for this command. Keep interrupted updates
        # in maintenance until the root operator explicitly recovers them.
        if request["kind"] in {"update", "restore"}:
            from .updater import _maintenance, _publish_status

            _maintenance(paths, True)
            _publish_status(
                paths,
                {
                    "state": "maintenance",
                    "job_id": request["job_id"],
                    "error": "manual_recovery_required",
                },
            )
        else:
            _finish(
                paths,
                request,
                {
                    "job_id": request["job_id"],
                    "kind": request["kind"],
                    "actor_user_id": request["actor_user_id"],
                    "state": "failed",
                    "artifact": None,
                    "before": [],
                    "after": [],
                    "performed": [],
                    "failed": [],
                    "error": "command_interrupted",
                },
            )
        return False
    atomic_write_json(counter, {"job_id": request["job_id"], "attempts": attempts + 1})
    return True


def _superseded_by_successful_update(paths, request):
    try:
        journal = _read(paths.state / "updater-journal.json", limit=65536)
        candidate = journal["candidate"]
        if not isinstance(candidate, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._+-]{0,150}", candidate
        ) or not paths.current.is_symlink():
            return False
        # Recovery rewrites a succeeded journal, but does not replace current.
        installed_after_request = datetime.fromtimestamp(
            paths.current.lstat().st_mtime, UTC
        ) > timestamp(request["created_at"])
        installed_candidate = paths.current.resolve(strict=True) == paths.releases / candidate
        result = _read(paths.ops / "public/rebuild.result")
        successor = str(UUID(journal["job_id"]))
    except (ReleaseError, KeyError, ValueError, TypeError, OSError, OverflowError):
        return False
    return (
        successor != request["job_id"]
        and installed_candidate
        and installed_after_request
        and journal.get("phase") == "succeeded"
        and result.get("job_id") == successor
        and result.get("ok") is True
        and result.get("error") is None
    )


def consume_commands(
    paths,
    runner,
    http,
    *,
    update_runner=None,
    github_http=None,
    typed_effects=None,
    typed_devices=None,
):
    """All privileged work is serialized; API never chooses argv or output paths."""
    if paths.root == Path("/") and os.geteuid() != 0:
        return 1
    from .operation_capabilities import publish_operation_capabilities

    effects = typed_effects if typed_effects is not None else TypedHostEffects()
    # Separate outer lock prevents two launchers while worker owns host.lock.
    with exclusive_lock(paths.ops / "command-consumer.lock"):
        publish_operation_capabilities(paths, effects)
        pending = paths.state / "command-request.json"
        inbox = paths.ops / "inbox/approved.json"
        with exclusive_lock(paths.host_lock):
            resumed = pending.exists()
            if not resumed:
                if not inbox.exists() and not inbox.is_symlink():
                    return 0
                try:
                    request = _validate(_read(inbox), fresh=False)
                except ReleaseError:
                    inbox.unlink(missing_ok=True)
                    return 1
                # Persist a private copy before removing the untrusted slot.
                atomic_write_json(pending, request)
                _claim_public(paths, request, True)
                inbox.unlink(missing_ok=True)
            else:
                try:
                    request = _validate(_read(pending), fresh=False, trusted_typed=True)
                except ReleaseError:
                    return 1
            if resumed and inbox.exists():
                try:
                    if _read(inbox) == request:
                        inbox.unlink()
                except ReleaseError:
                    inbox.unlink(missing_ok=True)
            fresh = (
                -300
                <= (datetime.now(UTC) - timestamp(request["created_at"])).total_seconds()
                <= 86400
            )
            from .retention import command_retired

            if command_retired(paths, request["job_id"]):
                if request["kind"] in {"update", "github-update"}:
                    atomic_write_json(
                        _public(paths) / "rebuild.result",
                        {
                            "job_id": request["job_id"],
                            "ok": False,
                            "error": "command_retired",
                        },
                        mode=0o644,
                    )
                else:
                    atomic_write_json(
                        _public(paths) / "command-result.json",
                        {
                            "job_id": request["job_id"],
                            "kind": request["kind"],
                            "actor_user_id": request["actor_user_id"],
                            "state": "failed",
                            "artifact": None,
                            "before": [],
                            "after": [],
                            "performed": [],
                            "failed": [],
                            "error": "command_retired",
                        },
                        mode=0o644,
                    )
                pending.unlink(missing_ok=True)
                _claim_public(paths, request, False)
                return 1
            receipt = paths.state / "command-receipts" / (request["job_id"] + ".json")
            if receipt.is_file():
                saved = _read(receipt, limit=65536)
                if saved.get("request") != request:
                    pending.unlink(missing_ok=True)
                    _claim_public(paths, request, False)
                    return 1
                if request["kind"] in {"update", "github-update"}:
                    atomic_write_json(
                        _public(paths) / "rebuild.result", saved["result"], mode=0o644
                    )
                    _claim_public(paths, request, False)
                    pending.unlink(missing_ok=True)
                else:
                    _finish(paths, request, saved["result"])
                return 0
            try:
                typed_kind = OperationKind(request["kind"])
            except (KeyError, ValueError):
                typed_kind = None
            if typed_kind is not None and (
                typed_kind is not OperationKind.DIAGNOSTICS or "authorization" in request
            ):
                devices = typed_devices() if callable(typed_devices) else ()
                result = execute_typed_operation(
                    paths,
                    request,
                    effects,
                    devices=devices,
                    authorization_fresh=not resumed,
                )
                _finish(paths, request, result)
                return int(result["state"] != "succeeded")
            if (
                resumed
                and request["kind"] == "update"
                and _superseded_by_successful_update(paths, request)
            ):
                result = {
                    "job_id": request["job_id"],
                    "ok": False,
                    "error": "request_superseded",
                }
                atomic_write_json(_public(paths) / "rebuild.result", result, mode=0o644)
                atomic_write_json(receipt, {"request": request, "result": result})
                _claim_public(paths, request, False)
                pending.unlink(missing_ok=True)
                return 1
            if request["kind"] == "github-update":
                from .github_releases import (
                    GithubHttp,
                    current_available,
                    download_approved_release,
                )
                from .updater import publish_result

                try:
                    if resumed or not fresh:
                        directory = paths.state / "github-artifacts"
                        for suffix in (".zip.partial", ".zip.sig.partial"):
                            (
                                directory
                                / ("github-release-" + str(request["release_id"]) + suffix)
                            ).unlink(missing_ok=True)
                        raise ReleaseError("github_approval_expired")
                    release = current_available(paths, request["release_id"])
                    artifact = download_approved_release(
                        release, paths, github_http or GithubHttp()
                    )
                    request = {key: value for key, value in request.items() if key != "release_id"}
                    request.update(kind="update", artifact=artifact.name)
                    atomic_write_json(artifact.with_suffix(".zip.approval.json"), request)
                    atomic_write_json(pending, request)
                    _claim_public(paths, request, True)
                except (OSError, ValueError, TypeError, KeyError, RecursionError):
                    result = {
                        "job_id": request["job_id"],
                        "ok": False,
                        "error": "github_download_failed",
                    }
                    publish_result(paths, result)
                    atomic_write_json(receipt, {"request": request, "result": result})
                    _claim_public(paths, request, False)
                    pending.unlink(missing_ok=True)
                    return 1
            if not _allow_attempt(paths, request):
                return 0
            if request["kind"] == "restore":
                from .restore import run_restore
                from .updater import SystemRunner, _maintenance

                if not fresh and not resumed:
                    result = {
                        **{key: request[key] for key in ("job_id", "kind", "actor_user_id")},
                        "state": "failed",
                        "error": "request_expired",
                    }
                else:
                    try:
                        result = run_restore(paths, request, update_runner or SystemRunner())
                    except Exception:
                        _maintenance(paths, True)
                        return 1
                if result["state"] == "maintenance":
                    atomic_write_json(_public(paths) / "command-result.json", result, mode=0o644)
                    return 1
                _finish(paths, request, result)
                return int(result["state"] != "succeeded")
            if request["kind"] != "update":
                result = {
                    "job_id": request["job_id"],
                    "kind": request["kind"],
                    "actor_user_id": request["actor_user_id"],
                    "state": "failed",
                    "artifact": None,
                    "before": [],
                    "after": [],
                    "performed": [],
                    "failed": [],
                    "error": "command_interrupted" if resumed else "request_expired",
                }
                try:
                    if not resumed and fresh:
                        if request["kind"] == "diagnostics":
                            from .retention import require_capacity

                            require_capacity(paths, 16 * 1024**2)
                        before = run_doctor(paths, runner, http)
                        result["before"] = before.as_dict()["checks"]
                        if request["kind"] == "diagnostics":
                            directory = _public(paths) / "artifacts"
                            directory.mkdir(mode=0o755, exist_ok=True)
                            directory.chmod(0o755)
                            artifact = create_diagnostic_bundle(
                                paths,
                                before,
                                runner,
                                directory / (request["job_id"] + ".zip"),
                            )
                            artifact.chmod(0o644)
                            result["artifact"] = artifact.name
                            after = before
                        else:
                            repairs = run_repairs(before, DEFAULT_REPAIRS, runner)
                            after = run_doctor(paths, runner, http)
                            result.update(performed=repairs.performed, failed=repairs.failed)
                        result["after"] = after.as_dict()["checks"]
                        publish_health(paths, after)
                        result.update(
                            state="failed" if result["failed"] else "succeeded",
                            error="repair_failed" if result["failed"] else None,
                        )
                except Exception:
                    result.update(state="failed", error="host_operation_failed")
                _finish(paths, request, result)
                return int(result["state"] != "succeeded")
        # Worker/recovery acquire host.lock themselves. Keep outer consumer lock.
        from .launcher import launch_update
        from .updater import SystemRunner, recover_interrupted_update

        if update_runner is None:
            update_runner = SystemRunner()
            update_runner.failure_log = paths.root / "var/log/robopark/ota-update.log"
        if resumed or not fresh:
            recover_interrupted_update(paths, update_runner)
        try:
            finished = _read(paths.ops / "public/rebuild.result").get("job_id") == request["job_id"]
        except ReleaseError:
            finished = False
        worker_request = paths.state / "update-worker-request.json"
        code = 0
        if not finished and not fresh:
            from .updater import publish_result

            publish_result(
                paths,
                {"job_id": request["job_id"], "ok": False, "error": "request_expired"},
            )
            finished = True
            code = 1
        if not finished:
            atomic_write_json(worker_request, request)
            code = launch_update(paths, worker_request, update_runner)
        worker_request.unlink(missing_ok=True)
        with exclusive_lock(paths.host_lock):
            try:
                result = _read(paths.ops / "public/rebuild.result")
            except ReleaseError:
                return 1
            if result.get("job_id") == request["job_id"]:
                atomic_write_json(
                    paths.state / "command-receipts" / (request["job_id"] + ".json"),
                    {"request": request, "result": result},
                )
                _claim_public(paths, request, False)
                pending.unlink(missing_ok=True)
        return code
