#!/usr/bin/env python3
"""One-way, fail-closed transition of the former production Khadas to TEST."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

EXPECTED_VERSION = "0.2.0-rc.44.dev7114536505405942335"
EXPECTED_OTA_NAME = f"robopark-{EXPECTED_VERSION}.ota"
EXPECTED_OTA_SHA256 = "4e5bc4a27f9f0ac0e6e246786511cb54515a4cb4a204909e2145e8eadfff6d9a"
EXPECTED_OTA_SIZE = 5_965_251
TEST_ORIGIN = "https://roboparktest.ru.tuna.am"
PINNED_UPDATE_ROOT_B64 = "KgfdWbJw01IJlIG4vFzNcTY7EiwWKSJhQakpzgFH58M="
RESUME_REQUIRED = 3

_TARGET_FIELDS = {
    "version", "ota_name", "ota_sha256", "ota_size", "origin",
    "subdomain", "region", "instance_id", "profile_sha256",
}
_BLOCKED_UNITS = (
    "robopark-bot.service", "robopark-bot.path",
    "robopark-commands.service", "robopark-commands.path",
    "robopark-updater.service", "robopark-doctor.service", "robopark-doctor.timer",
    "robopark-watchdog.service", "robopark-watchdog.timer",
    "robopark-backup.service", "robopark-backup.timer",
    "robopark-support-tuna.service", "robopark-support-sshd.service",
)
_RESTORED_UNITS = (
    "robopark-commands.service", "robopark-commands.path",
    "robopark-updater.service", "robopark-doctor.service", "robopark-doctor.timer",
    "robopark-watchdog.service", "robopark-watchdog.timer",
    "robopark-backup.service", "robopark-backup.timer",
)
_STANDARD_ENABLED_UNITS = (
    "robopark.service", "robopark-commands.path", "robopark-doctor.timer",
    "robopark-watchdog.timer", "robopark-backup.timer",
)
_STANDARD_ENABLED_INACTIVE_UNITS = ("robopark-updater.service",)
_PENDING_OPERATION_PATHS = {
    "inbox-approved.json": "var/lib/robopark/ops/inbox/approved.json",
    "command-request.json": "var/lib/robopark/ops/state/command-request.json",
    "update-worker-request.json": "var/lib/robopark/ops/state/update-worker-request.json",
}
_API_JOB_PATH = "var/lib/robopark/api-ops/job.json"
_LEGACY_API_JOB_PATH = "var/lib/robopark/data/ops/job.json"
_API_JOB_PATHS = (_API_JOB_PATH, _LEGACY_API_JOB_PATH)
_COMMAND_CLAIM_PATH = "var/lib/robopark/ops/public/command-claim.json"
_OTA_JOURNAL_PHASES = {
    "accepted", "verified", "snapshot_done", "staged", "migration_started",
    "migration_done", "cutover_started", "health_checked", "published",
    "rolled_back", "failed",
}
_OTA_RECOVERY_CODE = """\
import sys
from pathlib import Path
root = Path(sys.argv[1])
host_tools = (root / "opt/robopark/host-tools").resolve(strict=True)
host_tools.relative_to((root / "opt/robopark/releases").resolve(strict=True))
sys.path.insert(0, str(host_tools))
from robopark_host.ota_update import OtaUpdateEngine, SystemOtaUpdateRuntime
from robopark_host.paths import HostPaths
from robopark_host.state import exclusive_lock
from robopark_host.updater import SystemRunner
paths = HostPaths.from_root(root)
runner = SystemRunner()
runner.failure_log = paths.root / "var/log/robopark/ota-update.log"
with exclusive_lock(paths.host_lock):
    receipt = OtaUpdateEngine(paths, SystemOtaUpdateRuntime(paths, runner)).recover()
if receipt is not None and receipt.error == "ota_rollback_failed":
    raise RuntimeError("ota_rollback_failed")
"""


class SetupError(RuntimeError):
    """Safe, non-secret transition error."""


@dataclass(frozen=True)
class VerifiedBundle:
    target: dict[str, object]
    ota: bytes
    profile: bytes


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_target(document: Mapping[str, object]) -> dict[str, object]:
    """Validate the immutable reviewed destination; accepts no production variant."""
    if not isinstance(document, Mapping) or set(document) != _TARGET_FIELDS:
        raise SetupError("target_schema_invalid")
    if (
        document["version"] != EXPECTED_VERSION
        or document["ota_name"] != EXPECTED_OTA_NAME
        or document["ota_sha256"] != EXPECTED_OTA_SHA256
        or document["ota_size"] != EXPECTED_OTA_SIZE
    ):
        raise SetupError("target_ota_invalid")
    if (
        document["origin"] != TEST_ORIGIN
        or document["subdomain"] != "roboparktest"
        or document["region"] != "ru"
    ):
        raise SetupError("target_origin_invalid")
    try:
        instance = UUID(str(document["instance_id"]))
    except (ValueError, TypeError, AttributeError) as error:
        raise SetupError("target_instance_invalid") from error
    if str(instance) != document["instance_id"] or not instance.int:
        raise SetupError("target_instance_invalid")
    profile_digest = document["profile_sha256"]
    if (
        not isinstance(profile_digest, str)
        or len(profile_digest) != 64
        or any(character not in "0123456789abcdef" for character in profile_digest)
    ):
        raise SetupError("target_profile_invalid")
    parts = urlsplit(str(document["origin"]))
    if parts.scheme != "https" or parts.netloc != "roboparktest.ru.tuna.am" or parts.path:
        raise SetupError("target_origin_invalid")
    return dict(document)


def _read_member(archive: zipfile.ZipFile, name: str, maximum: int) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise SetupError("bundle_member_missing") from error
    if info.is_dir() or not 0 < info.file_size <= maximum:
        raise SetupError("bundle_member_invalid")
    with archive.open(info) as source:
        content = source.read(maximum + 1)
    if len(content) != info.file_size:
        raise SetupError("bundle_member_invalid")
    return content


def _verify_profile(profile: bytes, update_root: bytes, target: Mapping[str, object]) -> None:
    try:
        root_document = json.loads(update_root)
        if not isinstance(root_document, dict) or set(root_document) != {"ed25519"}:
            raise ValueError
        public = base64.b64decode(root_document["ed25519"], validate=True)
        if root_document["ed25519"] != PINNED_UPDATE_ROOT_B64 or len(public) != 32:
            raise ValueError
        envelope = json.loads(profile)
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
            raise ValueError
        payload = base64.b64decode(envelope["payload"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(public).verify(signature, payload)
        assignment = json.loads(payload)
        expected = {
            "purpose": "robopark-deployment-v1",
            "origin": target["origin"],
            "instance_id": target["instance_id"],
            "environment": "test",
            "channel": "rc",
        }
        if (
            not isinstance(assignment, dict)
            or set(assignment) != {*expected, "expires"}
            or any(assignment[key] != value for key, value in expected.items())
            or type(assignment["expires"]) is not int
            or assignment["expires"] <= int(time.time())
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, InvalidSignature) as error:
        raise SetupError("profile_signature_invalid") from error


def verify_bundle(bundle: Path) -> VerifiedBundle:
    """Authenticate every public embedded input without modifying the host."""
    try:
        if bundle.is_symlink() or not stat.S_ISREG(bundle.stat().st_mode):
            raise SetupError("bundle_invalid")
        with zipfile.ZipFile(bundle) as archive:
            target_raw = _read_member(archive, "target.json", 16 * 1024)
            target = validate_target(json.loads(target_raw))
            profile = _read_member(archive, "profile.json", 16 * 1024)
            update_root = _read_member(archive, "update-root.json", 16 * 1024)
            ota = _read_member(archive, str(target["ota_name"]), 512 * 1024 * 1024)
    except (OSError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError) as error:
        raise SetupError("bundle_invalid") from error
    if len(ota) != target["ota_size"] or _sha256(ota) != target["ota_sha256"]:
        raise SetupError("ota_integrity_invalid")
    if _sha256(profile) != target["profile_sha256"]:
        raise SetupError("profile_integrity_invalid")
    _verify_profile(profile, update_root, target)
    return VerifiedBundle(target=target, ota=ota, profile=profile)


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _patch_env(path: Path, updates: Mapping[str, str]) -> None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SetupError("configuration_missing") from error
    lines = raw.splitlines()
    indexes: dict[str, int] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key = line.partition("=")[0].strip()
        if key in updates:
            if key in indexes:
                raise SetupError("configuration_duplicate_key")
            indexes[key] = index
    for key, value in updates.items():
        replacement = f"{key}={value}"
        if key in indexes:
            lines[indexes[key]] = replacement
        else:
            lines.append(replacement)
    _atomic_write(path, ("\n".join(lines) + "\n").encode(), 0o600)


Runner = Callable[..., subprocess.CompletedProcess]
IsolationCallback = Callable[..., None]


class Transition:
    def __init__(
        self,
        *,
        root: Path = Path("/"),
        runner: Runner = subprocess.run,
        isolation_callback: IsolationCallback | None = None,
        http_get: Callable[[str], Mapping[str, object]] | None = None,
        version_probe: Callable[[], str] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.root = Path(root)
        self.runner = runner
        self.isolation_callback = isolation_callback
        self.http_get = http_get or self._http_get
        self.transition_dir = self.root / "var/lib/robopark-test-transition"
        self.backup_dir = self.transition_dir / "original"
        self.payload_dir = self.transition_dir / "payload"
        self.state_path = self.transition_dir / "state.json"
        self.api_allow = self.transition_dir / "test-api-allow"
        self.tuna_allow = self.transition_dir / "test-tuna-allow"
        self.compose = self.root / "var/lib/robopark/ops/state/current-compose.json"
        self.version_probe = version_probe or self._installed_version
        self.sleep_fn = sleep_fn
        self.target_identity: dict[str, object] | None = None
        self.expected_uid = 0 if self.root == Path("/") else os.getuid()

    def _secure_directory(self, path: Path, *, create: bool = False) -> None:
        try:
            relative = path.relative_to(self.root)
        except ValueError as error:
            raise SetupError("unsafe_transition_path") from error
        current = self.root
        for part in relative.parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if not create or current != path:
                    raise SetupError("unsafe_transition_path")
                current.mkdir(mode=0o700)
                info = current.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != self.expected_uid
                or info.st_mode & 0o022
            ):
                raise SetupError("unsafe_transition_path")
        if path == self.transition_dir:
            os.chmod(path, 0o700)

    def open_lock(self):
        directory = self.root / "run/lock/robopark"
        current = self.root
        for part in directory.relative_to(self.root).parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=0o700)
                info = current.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != self.expected_uid
            ):
                raise SetupError("unsafe_lock_path")
        os.chmod(directory, 0o700)
        path = directory / "khadas-test-transition.lock"
        try:
            descriptor = os.open(
                path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
            )
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != self.expected_uid
                    or info.st_nlink != 1
                ):
                    raise SetupError("unsafe_lock_path")
                os.fchmod(descriptor, 0o600)
                return os.fdopen(descriptor, "r+b")
            except BaseException:
                os.close(descriptor)
                raise
        except OSError as error:
            raise SetupError("unsafe_lock_path") from error

    @staticmethod
    def _target_identity(target: Mapping[str, object]) -> dict[str, object]:
        return {
            "version": target["version"],
            "ota_sha256": target["ota_sha256"],
            "profile_sha256": target["profile_sha256"],
            "instance_id": target["instance_id"],
            "origin": target["origin"],
        }

    def bind_target(self, target: Mapping[str, object]) -> None:
        identity = self._target_identity(validate_target(target))
        self._secure_directory(self.root / "var/lib")
        self._secure_directory(self.transition_dir, create=True)
        state = self.read_state()
        existing = state.get("target")
        if existing is not None and existing != identity:
            raise SetupError("transition_target_changed")
        self.target_identity = identity
        if state["phase"] == "initial":
            self._state("prepared")

    def _run(self, argv: Sequence[object], *, check: bool = True, **kwargs: object):
        options: dict[str, object] = {"check": check}
        if "input" not in kwargs:
            options["stdin"] = subprocess.DEVNULL
        options.update(kwargs)
        try:
            return self.runner([os.fspath(value) for value in argv], **options)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise SetupError("host_command_failed") from error

    def read_state(self) -> dict[str, object]:
        try:
            descriptor = os.open(
                self.state_path,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            )
        except FileNotFoundError:
            return {"phase": "initial", "status": "ready"}
        except OSError as error:
            raise SetupError("transition_state_invalid") from error
        try:
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != self.expected_uid
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > 64 * 1024
                ):
                    raise ValueError
                document = json.loads(source.read(64 * 1024 + 1))
        except (OSError, ValueError, UnicodeError) as error:
            raise SetupError("transition_state_invalid") from error
        if not isinstance(document, dict) or document.get("phase") not in {
            "prepared", "quarantined", "isolated", "configured", "ota_applied", "complete"
        }:
            raise SetupError("transition_state_invalid")
        target = document.get("target")
        if not isinstance(target, dict) or set(target) != {
            "version", "ota_sha256", "profile_sha256", "instance_id", "origin"
        }:
            raise SetupError("transition_state_invalid")
        return document

    def _state(self, phase: str, *, status: str = "ready", error: str | None = None) -> None:
        document: dict[str, object] = {"schema": 1, "phase": phase, "status": status}
        if self.target_identity is None:
            raise SetupError("transition_target_unbound")
        document["target"] = self.target_identity
        if error is not None:
            document["error"] = error
        _atomic_write(self.state_path, (json.dumps(document, sort_keys=True) + "\n").encode(), 0o600)

    def _compose_prefix(self) -> list[str]:
        if not self.compose.is_symlink():
            raise SetupError("existing_compose_invalid")
        try:
            target = self.compose.resolve(strict=True)
            state = (self.root / "var/lib/robopark/ops/state").resolve(strict=True)
            target.relative_to(state)
        except (OSError, ValueError) as error:
            raise SetupError("existing_compose_invalid") from error
        return ["docker", "compose", "--project-name", "robopark", "--file", os.fspath(self.compose)]

    def _require_existing_install(self) -> None:
        current = self.root / "opt/robopark/current"
        try:
            release = current.resolve(strict=True)
            releases = (self.root / "opt/robopark/releases").resolve(strict=True)
            release.relative_to(releases)
        except (OSError, ValueError) as error:
            raise SetupError("existing_install_required") from error
        if not release.name.startswith("0.2.0-rc.43"):
            raise SetupError("rc43_install_required")
        version = release / "VERSION"
        try:
            if version.is_symlink() or version.read_text(encoding="ascii").strip() != "0.2.0-rc.43":
                raise ValueError
        except (OSError, ValueError, UnicodeError) as error:
            raise SetupError("rc43_install_required") from error
        self._compose_prefix()
        for name in ("host.env", "tuna.env", "updater.env"):
            path = self.root / "etc/robopark" / name
            if path.is_symlink() or not path.is_file():
                raise SetupError("existing_install_required")

    def _backup_once(self) -> None:
        self._secure_directory(self.transition_dir)
        self._secure_directory(self.backup_dir, create=True)
        os.chmod(self.backup_dir, 0o700)
        sources = {
            "host.env": self.root / "etc/robopark/host.env",
            "tuna.env": self.root / "etc/robopark/tuna.env",
            "updater.env": self.root / "etc/robopark/updater.env",
            "current-compose.json": self.compose.resolve(strict=True),
        }
        bot = self.root / "var/lib/robopark/data/telegram-bot/enabled.json"
        if bot.exists() and not bot.is_symlink():
            sources["bot-enabled.json"] = bot
        for name, source in sources.items():
            destination = self.backup_dir / name
            if destination.exists() or destination.is_symlink():
                try:
                    info = destination.lstat()
                    if (
                        stat.S_ISLNK(info.st_mode)
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_uid != self.expected_uid
                        or stat.S_IMODE(info.st_mode) != 0o600
                    ):
                        raise ValueError
                except (OSError, ValueError) as error:
                    raise SetupError("original_backup_invalid") from error
                continue
            _atomic_write(destination, source.read_bytes(), 0o600)

    def _disable_bot(self) -> None:
        path = self.root / "var/lib/robopark/data/telegram-bot/enabled.json"
        if path.is_symlink():
            raise SetupError("unsafe_bot_state")
        _atomic_write(path, b'{"schema":1,"enabled":false}\n', 0o600)
        if self.root == Path("/"):
            os.chown(path, 10001, 10001)

    def _project_container_ids(self, *, all_containers: bool) -> list[str]:
        command = ["docker", "ps"]
        if all_containers:
            command.append("-a")
        command += [
            "--filter", "label=com.docker.compose.project=robopark",
            "--format", "{{.ID}}",
        ]
        result = self._run(command, capture_output=True, text=True)
        identifiers = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(set(identifiers)) != len(identifiers) or any(
            not 12 <= len(value) <= 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in identifiers
        ):
            raise SetupError("docker_inventory_invalid")
        return identifiers

    def _stop_project(self) -> None:
        identifiers = self._project_container_ids(all_containers=True)
        if identifiers:
            self._run(["docker", "update", "--restart=no", *identifiers])
            self._run(["docker", "stop", *identifiers])
        if self._project_container_ids(all_containers=False):
            raise SetupError("quarantine_containers_active")

    def _archive_pending_operations(self) -> None:
        request: dict[str, object] | None = None
        for archive_name, relative in _PENDING_OPERATION_PATHS.items():
            source = self.root / relative
            content = self._read_owned_file(source, relative=relative)
            if content is None:
                continue
            if archive_name in {"inbox-approved.json", "command-request.json"}:
                candidate = self._operation_object(content)
                if request is None:
                    request = candidate
            try:
                self._archive_once(f"pending-{archive_name}", content)
                source.unlink()
                directory = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except SetupError:
                raise
            except OSError as error:
                raise SetupError("pending_operation_invalid") from error
        self._cancel_operation_ownership(request)
        self._verify_no_pending_operations()

    def _read_owned_file(self, path: Path, *, relative: str) -> bytes | None:
        try:
            descriptor = os.open(
                path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            )
        except FileNotFoundError:
            return None
        except OSError as error:
            raise SetupError("pending_operation_invalid") from error
        try:
            with os.fdopen(descriptor, "rb") as input_stream:
                info = os.fstat(input_stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or not self._pending_owner_allowed(relative, info.st_uid)
                    or info.st_nlink != 1
                    or info.st_size > 1024 * 1024
                ):
                    raise SetupError("pending_operation_invalid")
                content = input_stream.read(1024 * 1024 + 1)
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise SetupError("pending_operation_invalid")
        except SetupError:
            raise
        except OSError as error:
            raise SetupError("pending_operation_invalid") from error
        if len(content) != info.st_size:
            raise SetupError("pending_operation_invalid")
        return content

    def _archive_once(self, name: str, content: bytes) -> None:
        destination = self.backup_dir / name
        if destination.exists() or destination.is_symlink():
            try:
                info = destination.lstat()
            except OSError as error:
                raise SetupError("pending_operation_changed") from error
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != self.expected_uid
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or destination.read_bytes() != content
            ):
                raise SetupError("pending_operation_changed")
        else:
            _atomic_write(destination, content, 0o600)

    @staticmethod
    def _operation_object(content: bytes) -> dict[str, object]:
        try:
            value = json.loads(content)
        except (UnicodeError, ValueError) as error:
            raise SetupError("pending_operation_invalid") from error
        if not isinstance(value, dict):
            raise SetupError("pending_operation_invalid")
        return value

    def _cancel_operation_ownership(self, request: dict[str, object] | None) -> None:
        for index, relative in enumerate(_API_JOB_PATHS):
            job_path = self.root / relative
            job_raw = self._read_owned_file(job_path, relative=relative)
            if job_raw is None:
                continue
            job = self._operation_object(job_raw)
            if job.get("state") not in {"queued", "running"}:
                continue
            archive_name = "pending-api-ops-job.json"
            if index:
                archive_name = "pending-legacy-api-ops-job.json"
            self._archive_once(archive_name, job_raw)
            extra = job.get("extra")
            if request is None and isinstance(extra, dict):
                host_request = extra.get("host_request")
                if isinstance(host_request, dict):
                    request = host_request
            job.update(
                state="failed",
                phase="failed",
                error="request_superseded",
                log="Операция отменена переходом хоста в TEST.",
                restart_required=False,
                updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            _atomic_write(
                job_path,
                (json.dumps(job, ensure_ascii=False, sort_keys=True) + "\n").encode(),
                0o600,
            )
            if self.root == Path("/"):
                os.chown(job_path, 10001, 10001)

        claim_path = self.root / _COMMAND_CLAIM_PATH
        claim_raw = self._read_owned_file(claim_path, relative=_COMMAND_CLAIM_PATH)
        claim: dict[str, object] | None = None
        if claim_raw is not None:
            claim = self._operation_object(claim_raw)
            if (
                set(claim) != {"job_id", "kind", "actor_user_id", "active"}
                or not isinstance(claim.get("job_id"), str)
                or not isinstance(claim.get("kind"), str)
                or type(claim.get("actor_user_id")) is not int
                or type(claim.get("active")) is not bool
            ):
                raise SetupError("pending_operation_invalid")
            if claim["active"]:
                self._archive_once("pending-command-claim.json", claim_raw)
        if claim is None:
            candidate = request or {}
            claim = {
                "job_id": candidate.get("job_id", str(self.target_identity["instance_id"])),
                "kind": candidate.get("kind", "test-transition"),
                "actor_user_id": candidate.get("actor_user_id", 0),
                "active": False,
            }
            if (
                not isinstance(claim["job_id"], str)
                or not isinstance(claim["kind"], str)
                or type(claim["actor_user_id"]) is not int
            ):
                raise SetupError("pending_operation_invalid")
        claim["active"] = False
        _atomic_write(
            claim_path,
            (json.dumps(claim, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            0o644,
        )

    def _pending_owner_allowed(self, relative: str, uid: int) -> bool:
        if uid == self.expected_uid:
            return True
        return (
            self.root == Path("/")
            and relative
            in {
                _PENDING_OPERATION_PATHS["inbox-approved.json"],
                *_API_JOB_PATHS,
            }
            and uid == 10001
        )

    def _verify_no_pending_operations(self) -> None:
        for relative in _PENDING_OPERATION_PATHS.values():
            path = self.root / relative
            if path.exists() or path.is_symlink():
                raise SetupError("pending_operation_active")
        for relative in _API_JOB_PATHS:
            job_raw = self._read_owned_file(self.root / relative, relative=relative)
            if job_raw is not None and self._operation_object(job_raw).get("state") in {
                "queued", "running",
            }:
                raise SetupError("pending_operation_active")
        claim_raw = self._read_owned_file(
            self.root / _COMMAND_CLAIM_PATH, relative=_COMMAND_CLAIM_PATH
        )
        if claim_raw is None:
            raise SetupError("pending_operation_active")
        claim = self._operation_object(claim_raw)
        if (
            set(claim) != {"job_id", "kind", "actor_user_id", "active"}
            or claim.get("active") is not False
        ):
            raise SetupError("pending_operation_active")

    def _require_units_inactive(self, units: Sequence[str]) -> None:
        for unit in units:
            result = self._run(
                ["systemctl", "is-active", "--quiet", unit],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                raise SetupError("quarantine_unit_active")

    def _require_no_tuna_process(self) -> None:
        result = self._run(
            ["pgrep", "-x", "tuna"], check=False, capture_output=True, text=True
        )
        if result.returncode == 0:
            raise SetupError("remaining_tuna_process")
        if result.returncode != 1:
            raise SetupError("tuna_process_check_failed")

    def _dropin(self, unit: str, content: str) -> None:
        path = self.root / "etc/systemd/system" / f"{unit}.d/90-test-transition.conf"
        _atomic_write(path, content.encode(), 0o644)

    def _remove_transition_dropin(self, unit: str) -> None:
        path = self.root / "etc/systemd/system" / f"{unit}.d/90-test-transition.conf"
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        try:
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != self.expected_uid
                or path.read_text(encoding="utf-8")
                != "[Unit]\nConditionPathExists=/etc/robopark/NEVER_ENABLE_PROD_AUTOMATION\n"
            ):
                raise SetupError("transition_dropin_invalid")
            path.unlink()
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except SetupError:
            raise
        except (OSError, UnicodeError) as error:
            raise SetupError("transition_dropin_invalid") from error

    def _restore_safe_automation(self) -> None:
        self._verify_no_pending_operations()
        for unit in _RESTORED_UNITS:
            self._remove_transition_dropin(unit)
        self._run(["systemctl", "daemon-reload"])
        self._run(["systemctl", "enable", "--now", *_STANDARD_ENABLED_UNITS])
        self._run(["systemctl", "enable", *_STANDARD_ENABLED_INACTIVE_UNITS])
        self._verify_safe_automation_restored()

    def _verify_safe_automation_restored(self) -> None:
        for unit in _RESTORED_UNITS:
            path = self.root / "etc/systemd/system" / f"{unit}.d/90-test-transition.conf"
            if path.exists() or path.is_symlink():
                raise SetupError("test_automation_not_restored")
        for unit in _STANDARD_ENABLED_UNITS:
            enabled = self._run(
                ["systemctl", "is-enabled", "--quiet", unit], check=False
            )
            active = self._run(
                ["systemctl", "is-active", "--quiet", unit], check=False
            )
            if enabled.returncode != 0 or active.returncode != 0:
                raise SetupError("test_automation_not_restored")
        for unit in _STANDARD_ENABLED_INACTIVE_UNITS:
            enabled = self._run(
                ["systemctl", "is-enabled", "--quiet", unit], check=False
            )
            if enabled.returncode != 0:
                raise SetupError("test_automation_not_restored")

    def quarantine(self) -> None:
        if self.target_identity is None:
            raise SetupError("transition_target_unbound")
        self._require_existing_install()
        self._backup_once()
        self._disable_bot()
        self._dropin(
            "robopark.service",
            "[Unit]\nConditionPathExists=/var/lib/robopark-test-transition/test-api-allow\n"
            "[Service]\nExecStartPost=\n",
        )
        blocked = "[Unit]\nConditionPathExists=/etc/robopark/NEVER_ENABLE_PROD_AUTOMATION\n"
        for unit in _BLOCKED_UNITS:
            self._dropin(unit, blocked)
        self._dropin(
            "robopark-tuna.service",
            "[Unit]\nConditionPathExists=/var/lib/robopark-test-transition/test-tuna-allow\n",
        )
        self._run(["systemctl", "daemon-reload"])
        stopped_units = (*_BLOCKED_UNITS, "robopark-tuna.service", "robopark.service")
        self._run(["systemctl", "disable", "--now", *stopped_units], check=False)
        self._run(["systemctl", "stop", *stopped_units], check=False)
        self._require_units_inactive(stopped_units)
        self._require_no_tuna_process()
        self._stop_project()
        self._archive_pending_operations()
        self._state("quarantined")

    def _extract_payload(self, verified: VerifiedBundle) -> Path:
        self._secure_directory(self.payload_dir, create=True)
        os.chmod(self.payload_dir, 0o700)
        ota = self.payload_dir / EXPECTED_OTA_NAME
        if ota.exists() or ota.is_symlink():
            info = ota.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != self.expected_uid
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise SetupError("cached_ota_invalid")
            current = ota.read_bytes()
            if len(current) != EXPECTED_OTA_SIZE or _sha256(current) != EXPECTED_OTA_SHA256:
                raise SetupError("cached_ota_invalid")
        else:
            _atomic_write(ota, verified.ota, 0o600)
        return ota

    def _install_profile(self, profile: bytes) -> None:
        destination = self.root / "var/lib/robopark/data/macos-deployment/profile.json"
        _atomic_write(destination, profile, 0o644)

    def _patch_configuration(self, target: Mapping[str, object]) -> None:
        etc = self.root / "etc/robopark"
        _patch_env(
            etc / "host.env",
            {
                "ROBOPARK_ENVIRONMENT": "test",
                "ROBOPARK_INSTANCE_ID": str(target["instance_id"]),
                "CORS_ORIGINS": TEST_ORIGIN,
                "SESSION_COOKIE_NAME": "robopark_test_session",
                "EMERGENCY_BRIDGE_ENABLED": "true",
                "EMERGENCY_BRIDGE_URL": "http://emergency-bridge:8091",
                "EMERGENCY_DIRECT_FALLBACK_ENABLED": "true",
                "MAC_CORPORATE_GATEWAY_ENABLED": "true",
                "MAC_CORPORATE_SERVER_FALLBACK_ENABLED": "true",
            },
        )
        _patch_env(
            etc / "tuna.env",
            {
                "TUNA_SUBDOMAIN": "roboparktest", "TUNA_DOMAIN": "",
                "TUNA_LOCATION": "ru", "TUNA_BIND": "127.0.0.1:8080",
            },
        )
        _patch_env(
            etc / "updater.env",
            {"GITHUB_ENABLED": "false", "ROBOPARK_UPDATE_CHANNEL": "manual"},
        )

    @staticmethod
    def _http_get(url: str) -> Mapping[str, object]:
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                if response.status != 200:
                    raise SetupError("health_check_failed")
                raw = response.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise SetupError("health_check_failed")
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise SetupError("health_check_failed")
            return document
        except SetupError:
            raise
        except (OSError, ValueError, UnicodeError) as error:
            raise SetupError("health_check_failed") from error

    def _installed_version(self) -> str:
        try:
            current = self.root / "opt/robopark/current"
            release = current.resolve(strict=True)
            releases = (self.root / "opt/robopark/releases").resolve(strict=True)
            release.relative_to(releases)
            version_path = release / "VERSION"
            if version_path.is_symlink() or not version_path.is_file():
                raise ValueError
            version = version_path.read_text(encoding="ascii").strip()
        except (OSError, ValueError, UnicodeError) as error:
            raise SetupError("release_version_unverified") from error
        if version != EXPECTED_VERSION or not release.name.startswith(EXPECTED_VERSION + "-"):
            raise SetupError("release_version_unverified")
        return version

    def _ota_journal(self) -> dict[str, object] | None:
        path = self.root / "var/lib/robopark/ops/state/ota-update-journal.json"
        if not path.exists() and not path.is_symlink():
            return None
        try:
            if path.is_symlink() or path.stat().st_size > 64 * 1024:
                raise ValueError
            document = json.loads(path.read_text())
            request = document["request"]
            if (
                not isinstance(document, dict)
                or document.get("schema") != 1
                or document.get("phase") not in _OTA_JOURNAL_PHASES
                or not isinstance(request, dict)
                or set(request) != {"operation_id", "upload_id", "sha256", "version"}
                or not isinstance(request["sha256"], str)
                or len(request["sha256"]) != 64
                or any(character not in "0123456789abcdef" for character in request["sha256"])
                or not isinstance(request["version"], str)
                or not request["version"]
                or len(request["version"]) > 151
                or any(
                    character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._+-"
                    for character in request["version"]
                )
                or str(UUID(str(request["operation_id"]))) != request["operation_id"]
                or str(UUID(str(request["upload_id"]))) != request["upload_id"]
                or document.get("error") is not None
                and not isinstance(document.get("error"), str)
            ):
                raise ValueError
            return document
        except (KeyError, OSError, TypeError, ValueError, UnicodeError) as error:
            raise SetupError("ota_journal_invalid") from error

    def _recover_pending_ota(self) -> dict[str, object]:
        self._run(
            ["python3", "-I", "-c", _OTA_RECOVERY_CODE, self.root],
            capture_output=True,
            text=True,
        )
        journal = self._ota_journal()
        if journal is None or journal["phase"] not in {"published", "rolled_back", "failed"}:
            raise SetupError("ota_update_incomplete")
        if journal["error"] == "ota_rollback_failed":
            raise SetupError("ota_update_incomplete")
        return journal

    @staticmethod
    def _journal_targets_bundle(journal: Mapping[str, object]) -> bool:
        request = journal["request"]
        assert isinstance(request, Mapping)
        return (
            request["sha256"] == EXPECTED_OTA_SHA256
            and request["version"] == EXPECTED_VERSION
        )

    def _verify_configuration(self, target: Mapping[str, object]) -> None:
        def values(path: Path) -> dict[str, str]:
            result: dict[str, str] = {}
            for line in path.read_text().splitlines():
                if line and not line.lstrip().startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    result[key.strip()] = value
            return result
        try:
            host = values(self.root / "etc/robopark/host.env")
            tuna = values(self.root / "etc/robopark/tuna.env")
            updater = values(self.root / "etc/robopark/updater.env")
        except (OSError, UnicodeError) as error:
            raise SetupError("test_configuration_unverified") from error
        if not (
            host.get("ROBOPARK_ENVIRONMENT") == "test"
            and host.get("ROBOPARK_INSTANCE_ID") == target["instance_id"]
            and host.get("CORS_ORIGINS") == TEST_ORIGIN
            and host.get("SESSION_COOKIE_NAME") == "robopark_test_session"
            and host.get("EMERGENCY_BRIDGE_ENABLED") == "true"
            and host.get("EMERGENCY_BRIDGE_URL") == "http://emergency-bridge:8091"
            and host.get("EMERGENCY_DIRECT_FALLBACK_ENABLED") == "true"
            and host.get("MAC_CORPORATE_GATEWAY_ENABLED") == "true"
            and host.get("MAC_CORPORATE_SERVER_FALLBACK_ENABLED") == "true"
            and tuna.get("TUNA_SUBDOMAIN") == "roboparktest"
            and tuna.get("TUNA_DOMAIN") == ""
            and tuna.get("TUNA_LOCATION") == "ru"
            and tuna.get("TUNA_BIND") == "127.0.0.1:8080"
            and updater.get("GITHUB_ENABLED") == "false"
            and updater.get("ROBOPARK_UPDATE_CHANNEL") == "manual"
        ):
            raise SetupError("test_configuration_unverified")

    def _verify_local(self, verified: VerifiedBundle) -> None:
        self._verify_configuration(verified.target)
        if self.version_probe() != EXPECTED_VERSION:
            raise SetupError("release_version_unverified")
        health = self.http_get("http://127.0.0.1:8080/api/health/ready")
        if (
            health.get("status") != "ready"
            or not isinstance(health.get("checks"), Mapping)
            or health["checks"].get("database") != "ok"
        ):
            raise SetupError("local_health_unverified")

    def _verify_public(self, verified: VerifiedBundle) -> None:
        profile = json.loads(verified.profile)
        public = self.http_get(TEST_ORIGIN + "/api/agent-devices/info")
        if (
            public.get("environment") != "test"
            or public.get("instance_id") != verified.target["instance_id"]
            or public.get("signed_profile") != profile
        ):
            raise SetupError("test_origin_identity_unverified")

    def _wait_public(self, verified: VerifiedBundle) -> None:
        for attempt in range(16):
            try:
                self._verify_public(verified)
                return
            except SetupError as error:
                if str(error) != "health_check_failed" or attempt == 15:
                    raise
                if attempt in {0, 4, 9}:
                    print("Ожидаю запуска TEST Tuna…")
                self.sleep_fn(5)

    def _verify_final(self, verified: VerifiedBundle) -> None:
        self._verify_local(verified)
        self._wait_public(verified)

    def _verify_worker(self) -> None:
        result = self._run(
            self._compose_prefix() + ["ps", "-q", "worker"],
            capture_output=True,
            text=True,
        )
        identifiers = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(identifiers) != 1 or any(
            len(identifier) != 64
            or any(character not in "0123456789abcdef" for character in identifier)
            for identifier in identifiers
        ):
            raise SetupError("worker_health_unverified")
        health = self._run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", identifiers[0]],
            capture_output=True,
            text=True,
        )
        if health.stdout.strip() != "healthy":
            raise SetupError("worker_health_unverified")

    def _enable_worker(self) -> None:
        self._run(
            self._compose_prefix()
            + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "180", "worker"]
        )
        self._verify_worker()
        self._dropin(
            "robopark.service",
            "[Unit]\nConditionPathExists=/var/lib/robopark-test-transition/test-api-allow\n"
            "[Service]\nExecStartPost=\n"
            "ExecStartPost=-/usr/bin/docker compose --project-name robopark --file "
            "/var/lib/robopark/ops/state/current-compose.json up -d --no-build worker\n",
        )
        self._run(["systemctl", "daemon-reload"])

    def apply(self, verified: VerifiedBundle) -> None:
        self.bind_target(verified.target)
        state = self.read_state()
        phase = str(state["phase"])
        try:
            if phase == "prepared":
                print("Включаю постоянный карантин бывшего production-хоста…")
                self.quarantine()
                phase = "quarantined"
            if phase == "quarantined":
                # Reassert the offline boundary after a reboot or after the operator
                # connects the network, before starting the one allowed container.
                self.quarantine()
                print("Запускаю только PostgreSQL и изолирую сохранённые данные…")
                self._run(self._compose_prefix() + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "180", "db"])
                if self.isolation_callback is None:
                    raise SetupError("isolation_callback_required")
                try:
                    self.isolation_callback(
                        root=self.root, runner=self.runner, backup_dir=self.transition_dir
                    )
                except Exception as error:
                    raise SetupError("isolation_failed") from error
                self._state("isolated")
                phase = "isolated"
            if phase == "isolated":
                print("Применяю фиксированную TEST-конфигурацию…")
                self._patch_configuration(verified.target)
                # Install once before any API may start, and again after the OTA.
                self._install_profile(verified.profile)
                _atomic_write(self.api_allow, b"isolated\n", 0o600)
                self._run(["systemctl", "daemon-reload"])
                self._state("configured")
                phase = "configured"
            if phase == "configured":
                print("Устанавливаю проверенный RC44 OTA…")
                ota = self._extract_payload(verified)
                journal = self._ota_journal()
                if journal is not None and journal["phase"] not in {
                    "published", "rolled_back", "failed",
                }:
                    if not self._journal_targets_bundle(journal):
                        raise SetupError("ota_update_incomplete")
                    journal = self._recover_pending_ota()
                    # Recovery may have restarted the old runtime. Reassert every
                    # quarantine boundary before attempting a new operation.
                    self.quarantine()
                    self._run(
                        self._compose_prefix()
                        + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "180", "db"]
                    )
                    self._patch_configuration(verified.target)
                    self._install_profile(verified.profile)
                    _atomic_write(self.api_allow, b"isolated\n", 0o600)
                    self._state("configured")
                journal_phase = None if journal is None else str(journal["phase"])
                if journal_phase == "failed" and journal["error"] == "ota_rollback_failed":
                    raise SetupError("ota_update_incomplete")
                if journal is not None and not self._journal_targets_bundle(journal):
                    # A stale terminal journal is harmless only while the reviewed
                    # RC43 base is still the active installation.
                    self._require_existing_install()
                already_updated = False
                if journal_phase == "published":
                    try:
                        already_updated = self.version_probe() == EXPECTED_VERSION
                    except SetupError:
                        already_updated = False
                if not already_updated:
                    self._run(["python3", ota, "update"], input="ОБНОВИТЬ\n", text=True)
                self._install_profile(verified.profile)
                self._state("ota_applied")
                phase = "ota_applied"
            if phase == "ota_applied":
                print("Проверяю локальный RC44, TEST Tuna и подписанный профиль…")
                self._verify_local(verified)
                _atomic_write(self.tuna_allow, b"test-only\n", 0o600)
                self._run(["systemctl", "daemon-reload"])
                self._run(["systemctl", "enable", "--now", "robopark-tuna.service"])
                self._wait_public(verified)
                self._enable_worker()
                self._restore_safe_automation()
                self._state("complete", status="complete")
            elif phase == "complete":
                self._verify_final(verified)
                self._verify_worker()
                self._verify_safe_automation_restored()
        except KeyboardInterrupt as error:
            safe_phase = self.read_state().get("phase", phase)
            self._state(str(safe_phase), status="failed", error="interrupted")
            raise SetupError("interrupted") from error
        except SetupError as error:
            safe_phase = self.read_state().get("phase", phase)
            if safe_phase != "complete":
                self._state(str(safe_phase), status="failed", error=str(error))
            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="verify embedded payload only")
    parser.add_argument(
        "--non-interactive", action="store_true",
        help="quarantine on first run; continue from a prior quarantine on the next run",
    )
    return parser


def _require_khadas_board(root: Path) -> None:
    candidates = (
        root / "proc/device-tree/model",
        root / "sys/firmware/devicetree/base/model",
    )
    for path in candidates:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 256:
                continue
            model = path.read_bytes().rstrip(b"\0").decode("utf-8")
        except (OSError, UnicodeError):
            continue
        if "khadas" in model.casefold():
            return
    raise SetupError("wrong_board_safe_stop")


def main(
    argv: list[str] | None = None,
    *,
    root: Path = Path("/"),
    runner: Runner = subprocess.run,
    input_fn: Callable[[str], str] = input,
    isolation_callback: IsolationCallback | None = None,
    effective_uid: int | None = None,
    machine: str | None = None,
    bundle: Path | None = None,
    http_get: Callable[[str], Mapping[str, object]] | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    bundle = Path(sys.argv[0]).resolve() if bundle is None else Path(bundle)
    try:
        verified = verify_bundle(bundle)
        if arguments.verify:
            print(json.dumps({"version": verified.target["version"], "state": "verified"}, sort_keys=True))
            return 0
        uid = os.geteuid() if effective_uid is None else effective_uid
        architecture = platform.machine() if machine is None else machine
        if uid != 0:
            raise SetupError("root_required")
        if sys.platform != "linux" and root == Path("/"):
            raise SetupError("linux_required")
        if architecture != "aarch64":
            raise SetupError("aarch64_required")
        _require_khadas_board(root)
        transition = Transition(
            root=root, runner=runner, isolation_callback=isolation_callback, http_get=http_get
        )
        with transition.open_lock() as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SetupError("transition_already_running") from error
            transition.bind_target(verified.target)
            initial_phase = transition.read_state()["phase"]
            if initial_phase == "prepared":
                transition.quarantine()
                if arguments.non_interactive:
                    print("Quarantine active. Connect internet and run this command again.")
                    return RESUME_REQUIRED
                input_fn("Карантин включён. Подключите интернет и нажмите Enter: ")
            transition.apply(verified)
        print(f"Robopark {EXPECTED_VERSION} is ready at {TEST_ORIGIN}")
        return 0
    except SetupError as error:
        print(f"Transition stopped safely: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Transition stopped safely: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
