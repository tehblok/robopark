"""Bounded, root-owned single-slot command consumer shared by boot and .path."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import struct
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

from .bundle import create_diagnostic_bundle
from .doctor import run_doctor
from .operational_state import backup_state, public_version, update_state
from .release import UTC, ReleaseError, timestamp, unique_object
from .repair import DEFAULT_REPAIRS, run_repairs
from .state import atomic_write_json, exclusive_lock


class OperationKind(str, Enum):
    __str__ = str.__str__

    OTA_UPDATE = "ota-update"
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
    DOCKER_IMAGE_PREVIEW = "docker-image-preview"
    DOCKER_IMAGE_EXECUTE = "docker-image-execute"
    BUILDER_CACHE_PREVIEW = "builder-cache-preview"
    BUILDER_CACHE_EXECUTE = "builder-cache-execute"
    DIAGNOSTICS = "diagnostics"
    USB_DISCOVER = "usb-discover"
    USB_FORMAT = "usb-format"
    USB_SELECT = "usb-select"


DESTRUCTIVE_CONFIRMATIONS = {
    OperationKind.OTA_UPDATE: "UPDATE ROBOPARK",
    OperationKind.ROLLBACK: "ROLLBACK ROBOPARK",
    OperationKind.REBOOT: "REBOOT ROBOPARK",
    OperationKind.BACKUP_RESTORE: "RESTORE ROBOPARK BACKUP",
    OperationKind.CLEANUP_EXECUTE: "CLEAN ROBOPARK",
    OperationKind.DOCKER_IMAGE_EXECUTE: "CLEAN ROBOPARK IMAGES",
    OperationKind.BUILDER_CACHE_EXECUTE: "CLEAN ROBOPARK BUILD CACHE",
}
_LOW_SPACE_OPERATION_KINDS = {
    OperationKind.CLEANUP_PREVIEW,
    OperationKind.CLEANUP_EXECUTE,
    OperationKind.DOCKER_IMAGE_PREVIEW,
    OperationKind.DOCKER_IMAGE_EXECUTE,
    OperationKind.BUILDER_CACHE_PREVIEW,
    OperationKind.BUILDER_CACHE_EXECUTE,
}
SAFE_CONFIRMATIONS = {OperationKind.BACKUP: "BACKUP ROBOPARK"}
_DYNAMIC_CONFIRMATION_KINDS = {
    OperationKind.PACKAGE_UPDATE,
    OperationKind.SERVICE_RESTART,
    OperationKind.USB_FORMAT,
}
ALLOWED_SERVICES = frozenset(
    {
        "robopark.service",
        "robopark-tuna.service",
        "docker.service",
    }
)
ALLOWED_PACKAGES = frozenset(
    {"docker-ce", "docker-ce-cli", "containerd.io", "openssl", "python3-cryptography"}
)
ALLOWED_CLEANUP_CATEGORIES = frozenset({
    "diagnostics", "logs", "ota_cache", "backups", "releases"
})


def _read_private_key(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        key = stream.read(33)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) != 0o600
        or len(key) != 32
    ):
        raise ValueError("invalid_recovery_key")
    return key


RECOVERY_KEY_REPLACE_CONFIRMATION = "REPLACE ROBOPARK RECOVERY KEY"


def _write_private_key_exclusive(path, key):
    path = Path(path)
    parent = path.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or parent.is_symlink():
        raise OSError("unsafe key directory")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with suppress(FileNotFoundError):
            path.unlink()
        raise
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    _read_private_key(path)
    return path


def export_backup_recovery_key(paths, destination):
    """Copy the root-private recovery key to one new explicit file."""

    try:
        key = _read_private_key(paths.etc / "backup-recovery.key")
        return _write_private_key_exclusive(destination, key)
    except (OSError, ValueError) as exc:
        raise ReleaseError("recovery_key_export_failed") from exc


def import_backup_recovery_key(paths, source, *, confirmation=""):
    """Install an external recovery key, preserving a replaced host key."""

    try:
        key = _read_private_key(source)
    except (OSError, ValueError) as exc:
        raise ReleaseError("invalid_recovery_key") from exc
    target = paths.etc / "backup-recovery.key"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.parent.is_symlink():
        raise ReleaseError("recovery_key_import_failed")
    try:
        current = _read_private_key(target)
    except FileNotFoundError:
        try:
            return _write_private_key_exclusive(target, key), None
        except (OSError, ValueError) as exc:
            raise ReleaseError("recovery_key_import_failed") from exc
    except (OSError, ValueError) as exc:
        raise ReleaseError("recovery_key_required") from exc
    if current == key:
        return target, None
    if confirmation != RECOVERY_KEY_REPLACE_CONFIRMATION:
        raise ReleaseError("recovery_key_replace_confirmation_required")
    previous = target.with_name(f"backup-recovery.key.previous-{uuid4()}")
    temporary = target.with_name(f".backup-recovery.key.import-{uuid4()}")
    try:
        _write_private_key_exclusive(previous, current)
        _write_private_key_exclusive(temporary, key)
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        _read_private_key(target)
        return target, previous
    except (OSError, ValueError) as exc:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise ReleaseError("recovery_key_import_failed") from exc


def ensure_backup_recovery_key(paths):
    """Create a persistent root-private key, preserving any legacy runtime key."""

    target = paths.etc / "backup-recovery.key"
    if target.exists() or target.is_symlink():
        try:
            _read_private_key(target)
            return target
        except (OSError, ValueError) as exc:
            raise ReleaseError("recovery_key_required") from exc
    runtime = paths.root / "run/robopark/recovery.key"
    try:
        key = _read_private_key(runtime)
    except FileNotFoundError:
        key = os.urandom(32)
    except (OSError, ValueError) as exc:
        raise ReleaseError("recovery_key_required") from exc
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.parent.is_symlink():
            raise OSError("unsafe key directory")
        return _write_private_key_exclusive(target, key)
    except FileExistsError:
        try:
            _read_private_key(target)
            return target
        except (OSError, ValueError) as exc:
            raise ReleaseError("recovery_key_required") from exc
    except (OSError, ValueError) as exc:
        raise ReleaseError("recovery_key_required") from exc


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

    def ota_update(self, operation_id, upload_id, sha256, version):
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

    def backup_restore(self, operation_id, backup_id, actor_user_id):
        del actor_user_id
        raise ReleaseError("operation_unavailable")

    def cleanup_preview(self, operation_id, categories):
        raise ReleaseError("operation_unavailable")

    def cleanup_execute(self, operation_id, plan_id):
        raise ReleaseError("operation_unavailable")

    def docker_image_preview(self, operation_id):
        raise ReleaseError("operation_unavailable")

    def docker_image_execute(self, operation_id, plan_id):
        raise ReleaseError("operation_unavailable")

    def builder_cache_preview(self, operation_id):
        raise ReleaseError("operation_unavailable")

    def builder_cache_execute(self, operation_id, plan_id):
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

    def ota_update(self, operation_id, upload_id, sha256, version):
        return self.system.ota_update(operation_id, upload_id, sha256, version)

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

    def backup_restore(self, operation_id, backup_id, actor_user_id):
        return self.system.backup_restore(operation_id, backup_id, actor_user_id)

    def cleanup_preview(self, operation_id, categories):
        return self.system.cleanup_preview(operation_id, categories)

    def cleanup_execute(self, operation_id, plan_id):
        return self.system.cleanup_execute(operation_id, plan_id)

    def docker_image_preview(self, operation_id):
        return self.system.docker_image_preview(operation_id)

    def docker_image_execute(self, operation_id, plan_id):
        return self.system.docker_image_execute(operation_id, plan_id)

    def builder_cache_preview(self, operation_id):
        return self.system.builder_cache_preview(operation_id)

    def builder_cache_execute(self, operation_id, plan_id):
        return self.system.builder_cache_execute(operation_id, plan_id)

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
    OperationKind.DOCKER_IMAGE_PREVIEW: set(),
    OperationKind.DOCKER_IMAGE_EXECUTE: {"plan_id"},
    OperationKind.BUILDER_CACHE_PREVIEW: set(),
    OperationKind.BUILDER_CACHE_EXECUTE: {"plan_id"},
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
        OperationKind.DOCKER_IMAGE_PREVIEW,
        OperationKind.BUILDER_CACHE_PREVIEW,
        OperationKind.DIAGNOSTICS,
        OperationKind.USB_DISCOVER,
        OperationKind.USB_SELECT,
    }
    return (
        DESTRUCTIVE_CONFIRMATIONS.get(kind)
        or SAFE_CONFIRMATIONS.get(kind)
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
        return effect.backup_restore(
            identity, value["backup_id"], operation.actor_user_id
        )
    if kind is OperationKind.CLEANUP_PREVIEW:
        return effect.cleanup_preview(identity, tuple(value["categories"]))
    if kind is OperationKind.CLEANUP_EXECUTE:
        return effect.cleanup_execute(identity, value["plan_id"])
    if kind is OperationKind.DOCKER_IMAGE_PREVIEW:
        return effect.docker_image_preview(identity)
    if kind is OperationKind.DOCKER_IMAGE_EXECUTE:
        return effect.docker_image_execute(identity, value["plan_id"])
    if kind is OperationKind.BUILDER_CACHE_PREVIEW:
        return effect.builder_cache_preview(identity)
    if kind is OperationKind.BUILDER_CACHE_EXECUTE:
        return effect.builder_cache_execute(identity, value["plan_id"])
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
    from .storage_compatibility import require_storage_operations

    check_space = _typed_operation_check_space(paths, operation)
    require_storage_operations(paths, check_space=check_space)
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
        return _execute_typed_operation_locked(
            paths, operation, effects, tuple(devices), check_space=check_space
        )


def _typed_operation_check_space(paths, operation) -> bool:
    """Keep fresh admission strict while allowing durable recovery to make progress."""

    if operation.kind in _LOW_SPACE_OPERATION_KINDS | {OperationKind.ROLLBACK}:
        return False
    if operation.kind not in {OperationKind.OTA_UPDATE, OperationKind.BACKUP_RESTORE}:
        return True
    checkpoint = (
        paths.state
        / "typed-operation-dispatch"
        / f"{operation.operation_id}.json"
    )
    if checkpoint.exists() or checkpoint.is_symlink():
        try:
            saved = _read(checkpoint, limit=65536)
        except (OSError, ReleaseError, ValueError):
            return True
        if saved == {
            "schema": 1,
            "request": operation.request,
            "state": "dispatched",
        }:
            return False
    if operation.kind is OperationKind.BACKUP_RESTORE:
        if _matching_restore_journal(
            paths, operation.operation_id, operation.actor_user_id
        ):
            return False
    return True


def _matching_restore_journal(paths, operation_id, actor_user_id) -> bool:
    from .restore import _load as load_restore_journal

    try:
        journal = load_restore_journal(paths)
    except ReleaseError:
        return False
    return bool(
        journal is not None
        and journal["request"].get("job_id") == operation_id
        and journal["request"].get("actor_user_id") == actor_user_id
    )


def _execute_typed_operation_locked(
    paths, operation, effects, devices, *, check_space=True
):
    from .builder_cleanup import BuilderCleanupPartialError
    from .image_retention import ImageCleanupPartialError
    from .operation_capabilities import (
        current_capability_revision,
        operation_capabilities,
    )
    from .ota_update import OtaFinalizationPending
    from .retention import CleanupPartialError
    from .state import read_operation_progress, write_operation_progress
    from .storage_layout import StorageError

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
        write_operation_progress(
            paths,
            operation.operation_id,
            result["state"],
            100,
            check_space=check_space,
        )
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
        write_operation_progress(
            paths, operation.operation_id, "accepted", 0, check_space=check_space
        )
        progress = read_operation_progress(paths, operation.operation_id)
    if progress["phase"] == "accepted":
        write_operation_progress(
            paths, operation.operation_id, "executing", 50, check_space=check_space
        )
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
        write_operation_progress(
            paths, operation.operation_id, state, 100, check_space=check_space
        )
        return result

    available = operation_capabilities(effects)[operation.kind.value]["available"]
    revision = operation.request["capability_revision"]
    recovering_effect = dispatched and operation.kind in {
        OperationKind.OTA_UPDATE,
        OperationKind.BACKUP_RESTORE,
    }
    if not available and not recovering_effect:
        return terminal("failed", {}, "manual_recovery_required" if dispatched else "capability_unavailable")
    if revision != current_capability_revision(paths, effects) and not recovering_effect:
        return terminal("failed", {}, "manual_recovery_required" if dispatched else "capabilities_changed")

    if dispatched:
        try:
            reconciliation = effects.reconcile(operation)
        except OtaFinalizationPending:
            raise
        except StorageError:
            # A transient mount/UUID/RO failure must keep recovery retryable.
            raise
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
    except OtaFinalizationPending:
        raise
    except StorageError:
        # Storage recovery is reconciled from the durable dispatch checkpoint.
        raise
    except CleanupPartialError as exc:
        return terminal("failed", {
            "deleted": exc.deleted,
            "deleted_count": len(exc.deleted),
            "uncertain_target": exc.uncertain_target,
        }, "cleanup_partial")
    except ImageCleanupPartialError as exc:
        return terminal("failed", {
            "deleted": exc.deleted,
            "deleted_count": len(exc.deleted),
            "uncertain_target": exc.uncertain_target,
        }, "image_cleanup_partial")
    except BuilderCleanupPartialError as exc:
        return terminal("failed", {
            "deleted": exc.deleted,
            "deleted_count": len(exc.deleted),
            "uncertain_target": exc.uncertain_target,
        }, "builder_cleanup_partial")
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

def _restorable_backup_limits():
    # The outer encrypted container holds one compressed snapshot ZIP. Its
    # member must fit the restore reader, even when outer compression is tiny.
    from .restore import MAX_ARCHIVE

    return replace(DEFAULT_BACKUP_ARCHIVE_LIMITS, max_member_bytes=MAX_ARCHIVE)


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
            OperationKind.ROLLBACK,
            OperationKind.BACKUP,
            OperationKind.BACKUP_VERIFY,
            OperationKind.BACKUP_RESTORE,
            OperationKind.PACKAGE_UPDATE,
            OperationKind.SERVICE_RESTART,
            OperationKind.REBOOT,
            OperationKind.CLEANUP_PREVIEW,
            OperationKind.CLEANUP_EXECUTE,
            OperationKind.DOCKER_IMAGE_PREVIEW,
            OperationKind.DOCKER_IMAGE_EXECUTE,
            OperationKind.BUILDER_CACHE_PREVIEW,
            OperationKind.BUILDER_CACHE_EXECUTE,
            OperationKind.DIAGNOSTICS,
            OperationKind.USB_DISCOVER,
            OperationKind.USB_FORMAT,
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
        supported = self.supported_kinds
        if ota_effects is not None:
            supported = supported | {OperationKind.OTA_UPDATE}
        else:
            supported = supported - {OperationKind.ROLLBACK}
        if runner is None or http is None:
            supported = supported - {OperationKind.DIAGNOSTICS}
        if runner is None:
            supported = supported - {
                OperationKind.PACKAGE_UPDATE,
                OperationKind.SERVICE_RESTART,
                OperationKind.REBOOT,
                OperationKind.USB_FORMAT,
                OperationKind.DOCKER_IMAGE_PREVIEW, OperationKind.DOCKER_IMAGE_EXECUTE,
                OperationKind.BUILDER_CACHE_PREVIEW, OperationKind.BUILDER_CACHE_EXECUTE,
            }
        self._structural_kinds = frozenset(supported)
        self.supported_kinds = self._structural_kinds
        self.unavailable_reasons = {}
        self.refresh_capabilities()

    def refresh_capabilities(self):
        """Recompute choices whose safe context can change while the process lives."""

        supported = self._structural_kinds
        reasons = {}
        if OperationKind.ROLLBACK in supported and self._rollback_release() is None:
            supported = supported - {OperationKind.ROLLBACK}
            reasons[OperationKind.ROLLBACK] = "context_unavailable"
        backup_kinds = {
            OperationKind.BACKUP,
            OperationKind.BACKUP_VERIFY,
            OperationKind.BACKUP_RESTORE,
        }
        try:
            self._external_key()
            self._selected_device()
        except ReleaseError:
            supported = supported - backup_kinds
            reasons.update(dict.fromkeys(backup_kinds, "context_unavailable"))
        if importlib.util.find_spec("cryptography") is None:
            supported = supported - backup_kinds
            reasons.update(dict.fromkeys(backup_kinds, "dependency_unavailable"))
        self.supported_kinds = frozenset(supported)
        self.unavailable_reasons = reasons

    @classmethod
    def unsupported_kinds(cls):
        return frozenset(set(OperationKind) - set(cls.supported_kinds))

    @staticmethod
    def _unavailable():
        raise ReleaseError("capability_unavailable")

    def ota_update(self, operation_id, upload_id, sha256, version):
        if self.ota_effects is None:
            return self._unavailable()
        return self.ota_effects.ota_update(operation_id, upload_id, sha256, version)

    def rollback(self, operation_id, release):
        if self.ota_effects is None:
            return self._unavailable()
        expected = self._rollback_release()
        if expected is None or release != expected:
            raise ReleaseError("rollback_release_changed")
        rollback = getattr(self.ota_effects, "manual_rollback", None)
        if rollback is None:
            return self._unavailable()
        return rollback(operation_id, release)

    def package_update(self, operation_id, package):
        operation_id = _canonical_uuid(operation_id)
        before = self.package_inspect("inspect-before", package)
        if not before["installed"] and package != "python3-cryptography":
            raise ReleaseError("package_not_installed")
        self._run_transient(
            operation_id, "apt-update", ["/usr/bin/apt-get", "update"], timeout=900
        )
        self._run_transient(
            operation_id,
            "apt-upgrade",
            [
                "/usr/bin/apt-get", "install", "-y", "--no-remove",
                *(["--only-upgrade"] if before["installed"] else []),
                "--no-install-recommends", package,
            ],
            timeout=1800,
        )
        after = self.package_inspect("inspect-after", package)
        if not after["installed"]:
            raise ReleaseError("package_update_failed")
        if package in {"docker-ce", "docker-ce-cli", "containerd.io"}:
            self.service_restart(operation_id, "docker.service")
        return {"package": package, "updated": True, "version": after["version"]}

    def service_restart(self, operation_id, service):
        del operation_id
        if service not in ALLOWED_SERVICES:
            raise ReleaseError("service_not_allowed")
        self._run_checked(["systemctl", "restart", service], timeout=900)
        self._run_checked(
            ["systemctl", "is-active", "--quiet", service], timeout=30
        )
        if service == "docker.service":
            self._run_checked(
                ["systemctl", "restart", "robopark.service"], timeout=900
            )
            self._run_checked(
                ["systemctl", "is-active", "--quiet", "robopark.service"],
                timeout=30,
            )
        if service in {"docker.service", "robopark.service"}:
            self._require_application_ready()
            enabled = self._run(
                ["systemctl", "is-enabled", "--quiet", "robopark-tuna.service"],
                timeout=30,
            )
            if enabled.returncode == 0:
                self._run_checked(
                    ["systemctl", "restart", "robopark-tuna.service"], timeout=120
                )
                self._run_checked(
                    ["systemctl", "is-active", "--quiet", "robopark-tuna.service"],
                    timeout=30,
                )
        return {"service": service, "restarted": True}

    def reboot(self, operation_id):
        from .operation_capabilities import _boot_id

        operation_id = _canonical_uuid(operation_id)
        boot_id = _boot_id(self.paths)
        if boot_id is None:
            raise ReleaseError("manual_recovery_required")
        receipt = {"schema": 1, "kind": "reboot", "boot_id": boot_id}
        self._write_private_json(
            "action-receipts",
            f"{operation_id}.json",
            {**receipt, "state": "scheduling"},
        )
        self._run_checked(
            [
                "systemd-run", "--unit", f"robopark-reboot-{operation_id}",
                "--on-active=5s", "/usr/bin/systemctl", "reboot", "--no-wall",
            ],
            timeout=30,
        )
        self._write_private_json(
            "action-receipts",
            f"{operation_id}.json",
            {**receipt, "state": "scheduled"},
        )
        return {"reboot_scheduled": True}

    def backup(self, operation_id, device_uuid):
        from .scheduled_backup import create_scheduled_backup

        backup_id = _canonical_uuid(operation_id)
        device = self._selected_device()
        if device.uuid != _canonical_uuid(device_uuid) or device.mount_point is None:
            raise ReleaseError("backup_device_changed")
        point = self.paths.root / device.mount_point.lstrip("/")
        destination = point / "robopark-backups"
        try:
            if point.is_symlink() or not point.is_dir():
                raise OSError()
            destination.mkdir(mode=0o700, exist_ok=True)
            if destination.is_symlink() or not destination.is_dir():
                raise OSError()
            from .restore import MAX_ARCHIVE

            local = Path(create_scheduled_backup(self.paths, parent_operation_id=backup_id))
            info = local.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_ARCHIVE:
                raise OSError()
            manifest = json.loads((self.paths.current / "manifest.json").read_text())
            app_version = manifest["app_version"]
            schema_version = manifest["migration_head"]
            if not isinstance(app_version, str) or not isinstance(schema_version, str):
                raise ValueError()
            staging = Path(tempfile.mkdtemp(prefix="backup-source-", dir=self.paths.state))
            try:
                shutil.copyfile(local, staging / "snapshot.zip")
                artifact = destination / f"backup-{backup_id}.rpb"
                create_encrypted_backup(
                    staging,
                    artifact,
                    recovery_key=self._external_key(),
                    app_version=app_version,
                    schema_version=schema_version,
                )
                artifact.chmod(0o600)
            finally:
                shutil.rmtree(staging)
        except ReleaseError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ReleaseError("backup_failed") from exc
        verified = self.backup_verify("verify-" + backup_id, backup_id)
        try:
            expected_parent = self.paths.root / "var/backups/robopark"
            local_info = local.lstat()
            if (
                local.parent == expected_parent
                and re.fullmatch(r"robopark-[0-9]{8}T[0-9]{12}\.zip", local.name)
                and stat.S_ISREG(local_info.st_mode)
                and local_info.st_nlink == 1
            ):
                local.unlink()
                directory = os.open(expected_parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except OSError as exc:
            raise ReleaseError("backup_source_cleanup_failed") from exc
        return {
            "backup_id": backup_id,
            "device_uuid": device.uuid,
            "bytes": artifact.stat().st_size,
            "verified": verified["verified"] is True,
        }

    def backup_restore(self, operation_id, backup_id, actor_user_id):
        from .restore import _load as load_restore_journal, run_restore
        from .updater import SystemRunner

        operation_id = _canonical_uuid(operation_id)
        backup_id = _canonical_uuid(backup_id)
        if type(actor_user_id) is not int or not 0 < actor_user_id < 2**63:
            raise ReleaseError("invalid_command")
        journal = load_restore_journal(self.paths)
        if journal is not None and journal["request"].get("job_id") == operation_id:
            request = journal["request"]
            if request.get("actor_user_id") != actor_user_id:
                raise ReleaseError("manual_recovery_required")
            result = run_restore(self.paths, request, SystemRunner())
            if result.get("state") != "succeeded":
                raise ReleaseError(result.get("error") or "backup_restore_failed")
            return {"backup_id": backup_id, "restored": True}
        receipt = self.verified_backup_receipt(backup_id)
        descriptor, encrypted_name = tempfile.mkstemp(
            dir=self.paths.state, prefix=".backup-restore-"
        )
        os.close(descriptor)
        encrypted = Path(encrypted_name)
        workspace = Path(tempfile.mkdtemp(prefix="backup-restore-", dir=self.paths.state))
        staging = workspace / "payload"
        try:
            with self._backup_artifact(
                backup_id, device_uuid=receipt["device_uuid"]
            ) as source, encrypted.open("wb") as output:
                os.lseek(source, 0, os.SEEK_SET)
                while chunk := os.read(source, 1024 * 1024):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            verified = verify_encrypted_backup(
                encrypted, recovery_key=self._external_key(),
                archive_limits=_restorable_backup_limits(),
            )
            restore_encrypted_backup(
                encrypted,
                staging,
                recovery_key=self._external_key(),
                verified=verified, archive_limits=_restorable_backup_limits(),
            )
            snapshot = staging / "snapshot.zip"
            if snapshot.is_symlink() or not snapshot.is_file() or len(list(staging.iterdir())) != 1:
                raise ReleaseError("backup_manifest_invalid")
            artifact_name = f"restore-{operation_id}.zip"
            artifact = self.paths.ops / "artifacts" / artifact_name
            artifact.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if artifact.exists() or artifact.is_symlink():
                raise ReleaseError("restore_artifact_exists")
            shutil.copyfile(snapshot, artifact)
            artifact.chmod(0o600)
            request = {
                "kind": "restore",
                "job_id": operation_id,
                "artifact": artifact_name,
                "sha256": _sha256_path(artifact),
                "actor_user_id": actor_user_id,
                "created_at": datetime.now(UTC).isoformat(),
            }
            result = run_restore(self.paths, request, SystemRunner())
            if result.get("state") != "succeeded":
                raise ReleaseError(result.get("error") or "backup_restore_failed")
            return {"backup_id": backup_id, "restored": True}
        finally:
            encrypted.unlink(missing_ok=True)
            shutil.rmtree(workspace, ignore_errors=True)

    def cleanup_execute(self, operation_id, plan_id):
        from .retention import (
            CleanupPartialError,
            StorageBudget,
            execute_system_cleanup_plan,
            preview_system_cleanup_plan,
        )

        del operation_id
        try:
            receipt = self._read_private_json(None, "cleanup-preview.json")
            if (
                set(receipt) != {"schema", "created_at", "categories", "budget", "plan", "consumed"}
                or receipt["schema"] != 1
                or receipt["consumed"] is not False
                or type(receipt["created_at"]) not in {int, float}
                or not 0 <= datetime.now(UTC).timestamp() - receipt["created_at"] <= 600
                or not isinstance(receipt["categories"], list)
                or not receipt["categories"]
                or len(receipt["categories"]) != len(set(receipt["categories"]))
                or any(item not in ALLOWED_CLEANUP_CATEGORIES for item in receipt["categories"])
                or not isinstance(receipt["plan"], dict)
                or receipt["plan"].get("plan_id") != plan_id
                or not isinstance(receipt["budget"], dict)
                or set(receipt["budget"]) != {"partition_bytes", "free_bytes"}
                or any(type(value) is not int or value < 0 for value in receipt["budget"].values())
            ):
                raise ReleaseError("cleanup_plan_changed")
            budget = StorageBudget(**receipt["budget"])
            expected = preview_system_cleanup_plan(
                self.paths, receipt["categories"], budget, now=receipt["created_at"]
            )
            if expected != receipt["plan"] or expected["blocked"]:
                raise ReleaseError("cleanup_plan_changed")
            self._write_private_json(None, "cleanup-preview.json", {**receipt, "consumed": True})
            result = execute_system_cleanup_plan(
                self.paths, receipt["categories"], budget, receipt["plan"],
                now=receipt["created_at"],
            )
            return {
                "plan_id": plan_id,
                "deleted": result["deleted"],
                "deleted_count": result["deleted_count"],
            }
        except CleanupPartialError:
            raise
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise ReleaseError("cleanup_plan_changed") from exc

    def docker_image_preview(self, operation_id):
        from .image_retention import preview_owned_images

        if self.runner is None:
            raise ReleaseError("capability_unavailable")
        report = preview_owned_images(self.paths, self.runner)
        plan_id = operation_id if not report["blocked"] and report["planned"] else None
        if plan_id is not None:
            self._write_private_json(None, "docker-image-preview.json", {
                "schema": 1,
                "plan_id": plan_id,
                "created_at": datetime.now(UTC).timestamp(),
                "plan": report,
                "consumed": False,
            })
        return {**report, "plan_id": plan_id}

    def docker_image_execute(self, operation_id, plan_id):
        from .image_retention import (
            ImageCleanupPartialError,
            preview_owned_images,
            remove_previewed_images,
        )

        del operation_id
        if self.runner is None:
            raise ReleaseError("capability_unavailable")
        try:
            receipt = self._read_private_json(None, "docker-image-preview.json")
            if (
                set(receipt) != {"schema", "plan_id", "created_at", "plan", "consumed"}
                or type(receipt["schema"]) is not int
                or receipt["schema"] != 1
                or receipt["plan_id"] != plan_id
                or type(receipt["created_at"]) not in {int, float}
                or not 0 <= datetime.now(UTC).timestamp() - receipt["created_at"] <= 600
                or receipt["consumed"] is not False
                or not isinstance(receipt["plan"], dict)
            ):
                raise ReleaseError("image_plan_changed")
            current = preview_owned_images(self.paths, self.runner)
            if current["blocked"] or not current["planned"] or current != receipt["plan"]:
                raise ReleaseError("image_plan_changed")
            self._write_private_json(None, "docker-image-preview.json", {**receipt, "consumed": True})
            return remove_previewed_images(self.runner, current["planned"])
        except ImageCleanupPartialError:
            raise
        except ReleaseError as exc:
            raise ReleaseError("image_plan_changed") from exc
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ReleaseError("image_plan_changed") from exc

    def builder_cache_preview(self, operation_id):
        from .builder_cleanup import preview_owned_builder_cache

        if self.runner is None:
            raise ReleaseError("capability_unavailable")
        report = preview_owned_builder_cache(self.paths, self.runner)
        plan_id = operation_id if not report["blocked"] and report["planned"] else None
        if plan_id is not None:
            self._write_private_json(None, "builder-cache-preview.json", {
                "schema": 1,
                "plan_id": plan_id,
                "created_at": datetime.now(UTC).timestamp(),
                "plan": report,
                "consumed": False,
            })
        return {**report, "plan_id": plan_id}

    def builder_cache_execute(self, operation_id, plan_id):
        from .builder_cleanup import (
            BuilderCleanupPartialError,
            execute_owned_builder_cache_plan,
        )

        del operation_id
        if self.runner is None:
            raise ReleaseError("capability_unavailable")
        try:
            receipt = self._read_private_json(None, "builder-cache-preview.json")
            if (
                set(receipt) != {"schema", "plan_id", "created_at", "plan", "consumed"}
                or type(receipt["schema"]) is not int
                or receipt["schema"] != 1
                or receipt["plan_id"] != plan_id
                or type(receipt["created_at"]) not in {int, float}
                or not 0 <= datetime.now(UTC).timestamp() - receipt["created_at"] <= 600
                or receipt["consumed"] is not False
                or not isinstance(receipt["plan"], dict)
            ):
                raise ReleaseError("builder_plan_changed")
            self._write_private_json(None, "builder-cache-preview.json", {**receipt, "consumed": True})
            return execute_owned_builder_cache_plan(self.paths, self.runner, receipt["plan"])
        except BuilderCleanupPartialError:
            raise
        except (ReleaseError, OSError, ValueError, TypeError, KeyError) as exc:
            raise ReleaseError("builder_plan_changed") from exc

    def usb_format(self, operation_id, device):
        operation_id = _canonical_uuid(operation_id)
        current = select_removable_device(
            tuple(self.device_provider()), device.uuid, destructive=True
        )
        if current.path != device.path:
            raise ReleaseError("unsafe_usb_device")
        target = self.paths.root / current.path.lstrip("/")
        try:
            descriptor = os.open(
                target,
                os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW,
            )
        except OSError as exc:
            raise ReleaseError("unsafe_usb_device") from exc
        try:
            opened = os.fstat(descriptor)
            if not (
                stat.S_ISBLK(opened.st_mode)
                or self.paths.root != Path("/") and stat.S_ISREG(opened.st_mode)
            ):
                raise ReleaseError("unsafe_usb_device")
            pinned = f"/proc/{os.getpid()}/fd/{descriptor}"
            self._run_transient(
                operation_id,
                "wipefs",
                ["/usr/sbin/wipefs", "--all", pinned],
                timeout=120,
            )
            if not self._same_inode(opened, os.fstat(descriptor)):
                raise ReleaseError("unsafe_usb_device")
            self._run_transient(
                operation_id,
                "mkfs",
                [
                    "/usr/sbin/mkfs.ext4", "-F", "-L", "ROBOPARK", "-U",
                    current.uuid, pinned,
                ],
                timeout=900,
            )
            if not self._same_inode(opened, os.fstat(descriptor)):
                raise ReleaseError("unsafe_usb_device")
        finally:
            os.close(descriptor)
        return {"device_uuid": current.uuid, "formatted": True}

    def _run(self, argv, *, timeout):
        if self.runner is None:
            raise ReleaseError("capability_unavailable")
        try:
            return self.runner(argv, timeout=timeout, max_output=65536)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ReleaseError("host_operation_failed") from exc

    def _run_checked(self, argv, *, timeout):
        result = self._run(argv, timeout=timeout)
        if getattr(result, "returncode", 1) != 0:
            raise ReleaseError("host_operation_failed")
        return result

    def _run_transient(self, operation_id, suffix, argv, *, timeout):
        if not re.fullmatch(r"[a-z0-9-]{1,32}", suffix):
            raise ReleaseError("host_operation_failed")
        return self._run_checked(
            [
                "systemd-run",
                "--wait",
                "--collect",
                "--pipe",
                "--quiet",
                "--service-type=exec",
                "--setenv=DEBIAN_FRONTEND=noninteractive",
                "--setenv=PATH=/usr/sbin:/usr/bin:/sbin:/bin",
                "--unit",
                f"robopark-operation-{operation_id}-{suffix}",
                *argv,
            ],
            timeout=timeout,
        )

    def _require_application_ready(self):
        if self.http is None:
            raise ReleaseError("host_operation_failed")
        try:
            response = self.http.get(
                "http://127.0.0.1:8080/api/health/ready", timeout=10
            )
        except Exception as exc:
            raise ReleaseError("host_operation_failed") from exc
        if getattr(response, "status", None) != 200:
            raise ReleaseError("host_operation_failed")

    def reconcile(self, operation):
        try:
            if operation.kind is OperationKind.OTA_UPDATE:
                detail = self.ota_update(
                    operation.operation_id,
                    operation.payload["upload_id"],
                    operation.payload["sha256"],
                    operation.payload["version"],
                )
            elif operation.kind is OperationKind.PACKAGE_INSPECT:
                detail = self.package_inspect(
                    operation.operation_id, operation.payload["package"]
                )
            elif operation.kind is OperationKind.PACKAGE_UPDATE:
                detail = self.package_update(
                    operation.operation_id, operation.payload["package"]
                )
            elif operation.kind is OperationKind.SERVICE_RESTART:
                detail = self.service_restart(
                    operation.operation_id, operation.payload["service"]
                )
            elif operation.kind is OperationKind.REBOOT:
                from .operation_capabilities import _boot_id

                receipt = self._read_private_json(
                    "action-receipts", f"{operation.operation_id}.json"
                )
                if receipt.get("schema") != 1 or receipt.get("kind") != "reboot":
                    raise ReleaseError("manual_recovery_required")
                if receipt.get("state") == "scheduled":
                    detail = {"reboot_scheduled": True}
                elif receipt.get("state") == "scheduling":
                    boot_id = _boot_id(self.paths)
                    original_boot = receipt.get("boot_id")
                    if boot_id is None or not isinstance(original_boot, str):
                        raise ReleaseError("manual_recovery_required")
                    _canonical_uuid(original_boot)
                    if original_boot != boot_id:
                        self._write_private_json(
                            "action-receipts", f"{operation.operation_id}.json",
                            {**receipt, "state": "scheduled"},
                        )
                        return {"state": "succeeded", "detail": {"reboot_scheduled": True}, "error": None}
                    unit = f"robopark-reboot-{operation.operation_id}.service"
                    result = self._run(
                        ["systemctl", "show", unit, "--property=LoadState", "--value"],
                        timeout=30,
                    )
                    if result.returncode != 0 or result.stdout.strip() not in {"not-found", "loaded"}:
                        raise ReleaseError("manual_recovery_required")
                    if result.stdout.strip() == "not-found":
                        detail = self.reboot(operation.operation_id)
                    else:
                        self._write_private_json(
                            "action-receipts",
                            f"{operation.operation_id}.json",
                            {**receipt, "state": "scheduled"},
                        )
                        detail = {"reboot_scheduled": True}
                else:
                    raise ReleaseError("manual_recovery_required")
            elif operation.kind is OperationKind.ROLLBACK:
                detail = self.rollback(
                    operation.operation_id, operation.payload["release"]
                )
            elif operation.kind is OperationKind.BACKUP:
                backup_id = operation.operation_id
                try:
                    receipt = self.verified_backup_receipt(backup_id)
                    with self._backup_artifact(
                        backup_id, device_uuid=receipt["device_uuid"]
                    ) as artifact:
                        size = os.fstat(artifact).st_size
                    detail = {
                        "backup_id": backup_id,
                        "device_uuid": receipt["device_uuid"],
                        "bytes": size,
                        "verified": True,
                    }
                except ReleaseError:
                    detail = self.backup(
                        operation.operation_id, operation.payload["device_uuid"]
                    )
            elif operation.kind is OperationKind.CLEANUP_PREVIEW:
                detail = self.cleanup_preview(
                    operation.operation_id, tuple(operation.payload["categories"])
                )
            elif operation.kind is OperationKind.DOCKER_IMAGE_PREVIEW:
                detail = self.docker_image_preview(operation.operation_id)
            elif operation.kind is OperationKind.BUILDER_CACHE_PREVIEW:
                detail = self.builder_cache_preview(operation.operation_id)
            elif operation.kind is OperationKind.USB_DISCOVER:
                detail = self.usb_discover(operation.operation_id)
            elif operation.kind is OperationKind.BACKUP_VERIFY:
                self.verified_backup_receipt(operation.payload["backup_id"])
                detail = {
                    "backup_id": operation.payload["backup_id"],
                    "verified": True,
                }
            elif operation.kind is OperationKind.BACKUP_RESTORE:
                detail = self.backup_restore(
                    operation.operation_id,
                    operation.payload["backup_id"],
                    operation.actor_user_id,
                )
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
            elif operation.kind is OperationKind.USB_FORMAT:
                device = select_removable_device(
                    tuple(self.device_provider()),
                    operation.payload["device_uuid"],
                    destructive=True,
                )
                detail = self.usb_format(operation.operation_id, device)
            else:
                return super().reconcile(operation)
            return {"state": "succeeded", "detail": detail, "error": None}
        except ReleaseError as exc:
            return {
                "state": "failed",
                "detail": {},
                "error": "ota_update_failed"
                if operation.kind is OperationKind.OTA_UPDATE
                and str(exc) == "ota_update_failed"
                else "manual_recovery_required",
            }

    def _external_key(self):
        for target in (
            self.paths.etc / "backup-recovery.key",
            self.paths.root / "run/robopark/recovery.key",
        ):
            try:
                descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    key = stream.read(33)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid not in {0, os.geteuid()}
                    or stat.S_IMODE(info.st_mode) & 0o077
                    or len(key) != 32
                ):
                    raise ValueError()
                return key
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                raise ReleaseError("recovery_key_required") from exc
        raise ReleaseError("recovery_key_required")

    def _rollback_release(self):
        try:
            releases = self.paths.releases.resolve(strict=True)
            if not self.paths.previous.is_symlink() or not self.paths.current.is_symlink():
                return None
            target = self.paths.previous.resolve(strict=True)
            current = self.paths.current.resolve(strict=True)
            if target.parent != releases or target.is_symlink() or not target.is_dir():
                return None
            if (
                current.parent != releases
                or current.is_symlink()
                or not current.is_dir()
                or len(current.name) < 38
                or current.name[-37] != "-"
            ):
                return None
            update_id = str(UUID(current.name[-36:]))
            metadata = _read(
                self.paths.state / "ota-runtime" / f"{update_id}.json",
                limit=65536,
            )
            rollback = self.paths.ops / "rollbacks" / update_id
            if (
                metadata.get("schema") != 1
                or metadata.get("operation_id") != update_id
                or metadata.get("candidate") != current.name
                or metadata.get("current") != target.name
                or rollback.is_symlink()
                or not rollback.is_dir()
            ):
                return None
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,150}", target.name):
                return None
            return target.name
        except (OSError, ValueError, ReleaseError):
            return None

    def operation_context(self):
        devices = []
        for device in tuple(self.device_provider()):
            if (
                device.removable
                and not device.system_device
                and not device.root_device
                and not device.data_device
            ):
                devices.append({
                    "device_uuid": device.uuid,
                    "removable": True,
                    "mounted": device.mounted,
                })
                if len(devices) == 8:
                    break
        selected = None
        try:
            selected = self._selected_device()
        except ReleaseError:
            pass
        backups = []
        if selected is not None and selected.mount_point is not None:
            directory = self.paths.root / selected.mount_point.lstrip("/") / "robopark-backups"
            try:
                for path in sorted(directory.iterdir())[:256]:
                    match = re.fullmatch(r"backup-([a-f0-9-]{36})\.rpb", path.name)
                    if match is None or path.is_symlink() or not path.is_file():
                        continue
                    identity = _canonical_uuid(match.group(1))
                    info = path.stat()
                    if info.st_nlink != 1 or info.st_mode & 0o077:
                        continue
                    verified = False
                    created_at = datetime.fromtimestamp(info.st_mtime, UTC).isoformat()
                    try:
                        receipt = self.verified_backup_receipt(identity)
                        verified = receipt["verified"] is True
                    except ReleaseError:
                        pass
                    backups.append({
                        "backup_id": identity,
                        "bytes": info.st_size,
                        "verified": verified,
                        "created_at": created_at,
                    })
                    if len(backups) == 32:
                        break
            except OSError:
                pass
        return {
            "selected_device_uuid": selected.uuid if selected is not None else None,
            "rollback_release": self._rollback_release(),
            "packages": sorted(ALLOWED_PACKAGES),
            "services": sorted(ALLOWED_SERVICES),
            "devices": devices,
            "backups": backups,
        }

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
                temporary, recovery_key=self._external_key(),
                archive_limits=_restorable_backup_limits(),
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
        budget = StorageBudget.for_path(self.paths.var)
        now = datetime.now(UTC).timestamp()
        plan = preview_system_cleanup_plan(self.paths, categories, budget, now=now)
        receipt = {
            "schema": 1,
            "created_at": now,
            "categories": list(categories),
            "budget": {"partition_bytes": budget.partition_bytes, "free_bytes": budget.free_bytes},
            "plan": plan,
            "consumed": False,
        }
        if len(json.dumps(receipt, allow_nan=False).encode()) > 60000:
            raise ReleaseError("cleanup_preview_too_large")
        self._write_private_json(None, "cleanup-preview.json", receipt)
        return plan

    def diagnostics(self, operation_id):
        from .retention import require_capacity

        operation_id = _canonical_uuid(operation_id)
        if self.runner is None or self.http is None:
            raise ReleaseError("capability_unavailable")
        directory = _public(self.paths) / "artifacts"
        directory.mkdir(mode=0o755, exist_ok=True)
        directory.chmod(0o755)
        artifact = directory / f"{operation_id}.zip"
        if artifact.exists() or artifact.is_symlink():
            try:
                info = artifact.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid not in {0, os.geteuid()}
                    or stat.S_IMODE(info.st_mode) != 0o644
                    or info.st_size <= 0
                ):
                    raise OSError()
                with zipfile.ZipFile(artifact) as archive:
                    if archive.testzip() is not None:
                        raise OSError()
                return {"artifact": artifact.name, "completed": True}
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                raise ReleaseError("diagnostics_artifact_invalid") from exc
        require_capacity(self.paths, 16 * 1024**2)
        report = run_doctor(self.paths, self.runner, self.http)
        publish_health(self.paths, report)
        artifact = create_diagnostic_bundle(
            self.paths, report, self.runner, artifact
        )
        artifact.chmod(0o644)
        return {"artifact": artifact.name, "completed": True}

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
        if set(value) != expected:
            raise ValueError()
        if str(UUID(value["job_id"])) != value["job_id"] or value["kind"] not in {
            "diagnostics",
            "repair",
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
    from .storage_compatibility import require_storage_operations

    require_storage_operations(paths, check_space=False)
    public = _public(paths)
    atomic_write_json(public / "command-result.json", result, mode=0o644)
    receipts = paths.state / "command-receipts"
    atomic_write_json(
        receipts / (request["job_id"] + ".json"), {"request": request, "result": result}
    )
    _claim_public(paths, request, False)
    (paths.state / "command-request.json").unlink(missing_ok=True)


def _storage_space_result(request, typed_kind):
    if typed_kind is not None and (
        typed_kind is not OperationKind.DIAGNOSTICS or "authorization" in request
    ):
        return {
            "job_id": request["job_id"],
            "operation_id": request["job_id"],
            "kind": typed_kind.value,
            "actor_user_id": request["actor_user_id"],
            "state": "failed",
            "detail": {},
            "error": "storage_space_low",
        }
    return {
        "job_id": request["job_id"],
        "kind": request["kind"],
        "actor_user_id": request["actor_user_id"],
        "state": "failed",
        "artifact": None,
        "before": [],
        "after": [],
        "performed": [],
        "failed": [],
        "error": "storage_space_low",
    }


def _allow_attempt(paths, request):
    from .storage_compatibility import require_storage_operations

    try:
        kind = OperationKind(request["kind"])
    except (KeyError, ValueError):
        kind = None
    check_space = kind not in _LOW_SPACE_OPERATION_KINDS | {OperationKind.ROLLBACK}
    if request.get("kind") == "restore" and _matching_restore_journal(
        paths, request.get("job_id"), request.get("actor_user_id")
    ):
        check_space = False
    require_storage_operations(paths, check_space=check_space)
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
        # Interrupted restore stays in maintenance for manual recovery.
        if request["kind"] == "restore":
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


def consume_commands(
    paths,
    runner,
    http,
    *,
    update_runner=None,
    typed_effects=None,
    typed_devices=None,
):
    """All privileged work is serialized; API never chooses argv or output paths."""
    if paths.root == Path("/") and os.geteuid() != 0:
        return 1
    from .storage_compatibility import require_storage_operations
    from .storage_layout import StorageError

    require_storage_operations(paths, check_space=False)
    from .operation_capabilities import (
        publish_operation_capabilities,
        publish_operation_context,
    )

    effects = typed_effects if typed_effects is not None else TypedHostEffects()
    # Separate outer lock prevents two launchers while worker owns host.lock.
    with exclusive_lock(paths.ops / "command-consumer.lock"):
        publish_operation_capabilities(paths, effects)
        publish_operation_context(paths, effects)
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
                try:
                    typed_kind = OperationKind(request["kind"])
                except (KeyError, ValueError):
                    typed_kind = None
                try:
                    if typed_kind is not None and (
                        typed_kind is not OperationKind.DIAGNOSTICS
                        or "authorization" in request
                    ):
                        operation = validate_typed_operation(
                            request, authorization_fresh=True
                        )
                        check_space = _typed_operation_check_space(paths, operation)
                    else:
                        check_space = True
                    require_storage_operations(paths, check_space=check_space)
                except ReleaseError:
                    inbox.unlink(missing_ok=True)
                    return 1
                except StorageError as error:
                    if error.code != "storage_space_low":
                        raise
                    result = _storage_space_result(request, typed_kind)
                    _finish(paths, request, result)
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
                try:
                    result = execute_typed_operation(
                        paths,
                        request,
                        effects,
                        devices=devices,
                        authorization_fresh=not resumed,
                    )
                except StorageError as error:
                    if error.code != "storage_space_low":
                        raise
                    operation = validate_typed_operation(
                        request, authorization_fresh=not resumed
                    )
                    if not _typed_operation_check_space(paths, operation):
                        # A durable dispatch or restore journal may represent a
                        # partially applied effect. Preserve its pending claim.
                        raise
                    result = _storage_space_result(request, typed_kind)
                _finish(paths, request, result)
                # Refresh user-visible choices after the terminal receipt exists.
                # Projection failures must never turn a completed host action into a
                # retryable operation (especially format, restore, or reboot).
                try:
                    publish_operation_capabilities(paths, effects)
                    publish_operation_context(paths, effects)
                except Exception:
                    pass
                return int(result["state"] != "succeeded")
            try:
                allowed = _allow_attempt(paths, request)
            except StorageError as error:
                if error.code != "storage_space_low":
                    raise
                _finish(paths, request, _storage_space_result(request, typed_kind))
                return 1
            if not allowed:
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
