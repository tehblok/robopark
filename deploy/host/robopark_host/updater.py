"""Journalled immutable-release update, with a small Docker/readiness boundary.

Runner.run(argv, *, timeout, cwd=None, env=None, capture=False) returns bytes.
Runner.wait_ready(*, project, config, timeout) checks core API and web readiness.
Neither boundary may log secret values or include command output in exceptions.
"""

from __future__ import annotations

import calendar
import copy
import json
import os
import re
import secrets
import selectors
import shutil
import signal
import stat
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from .image_retention import maintenance as cleanup_images
from .image_retention import record as record_images
from .image_retention import require_record_capacity
from .image_retention import reserve as reserve_images
from .operational_state import record_backup
from .paths import HostPaths
from .release import (
    INSTALLER_VERSION,
    ReleaseError,
    UpdateRequest,
    check_compatibility,
    safe_member,
    timestamp,
    unique_object,
    verify_archive,
    verify_directory,
)
from .rollback import (
    UNITS,
    atomic_copy,
    atomic_symlink,
    rollback_release,
    snapshot,
    sync_directory,
)
from .runtime import explain_process_failure, pin_images, production_config
from .state import atomic_write_json, exclusive_lock
from .trust import activate as activate_trust
from .trust import admission_key, directory_key

TMPFILES_SOURCE = Path("deploy/tmpfiles.d/robopark.conf")
TMPFILES_TARGET = Path("etc/tmpfiles.d/robopark.conf")


def _activate_system_files(paths, candidate):
    for unit in UNITS:
        source = candidate / "deploy/systemd" / unit
        if source.is_file():
            atomic_copy(source, paths.root / "etc/systemd/system" / unit, 0o644)
    tmpfiles = candidate / TMPFILES_SOURCE
    if tmpfiles.is_symlink():
        raise ReleaseError("unsafe_tmpfiles")
    if tmpfiles.is_file():
        atomic_copy(tmpfiles, paths.root / TMPFILES_TARGET, 0o644)


PRE_MAINTENANCE_PHASES = {
    "verified",
    "unpacking",
    "unpacked",
    "building",
    "built",
    "testing",
    "tested",
    "smoking",
    "smoked",
}

PHASES = {
    "verified",
    "unpacking",
    "unpacked",
    "building",
    "built",
    "testing",
    "tested",
    "smoking",
    "smoked",
    "maintenance",
    "stopping",
    "snapshotting",
    "snapshotted",
    "tools_staging",
    "tools_staged",
    "publishing",
    "published",
    "switching",
    "switched",
    "migrating",
    "migrated",
    "starting",
    "started",
    "activating",
    "activated",
    "health_check",
    "healthy",
    "reconciling",
    "publication",
    "publication_checked",
    "resuming",
    "succeeded",
    "failed",
    "rolling_back",
    "rollback_healthy",
    "rollback_resuming",
    "rolled_back",
    "manual_recovery_required",
}


SAFE_ERRORS = {
    "command_failed",
    "command_timeout",
    "command_output_limit",
    "unsafe_release_path",
    "compose_config_missing",
    "unsafe_config_path",
    "insufficient_space",
    "staging_filesystem_mismatch",
    "compose_invalid",
    "unsafe_build_context",
    "unsafe_path",
    "request_replayed",
    "smoke_failed",
    "cutover_unhealthy",
    "manual_recovery_required",
    "unsafe_data_path",
    "invalid_version",
    "invalid_request",
    "unsafe_artifact",
    "invalid_manifest",
    "signature_invalid",
    "unsupported_format",
    "archive_too_large",
    "duplicate_member",
    "manifest_files_mismatch",
    "checksum_mismatch",
    "invalid_archive",
    "release_missing",
    "quality_gate_inputs_missing",
    "downgrade_rejected",
    "installer_incompatible",
    "capability_missing",
    "migration_incompatible",
    "build_failed",
    "tests_failed",
    "compose_version_unsupported",
    "docker_disk_full",
    "docker_network_failed",
    "docker_out_of_memory",
    "docker_command_failed",
    "frontend_typescript_failed",
    "frontend_arm_dependency_failed",
    "migration_failed",
    "migration_head_mismatch",
    "update_failed",
    "interrupted",
}

RELEASE_NAME = r"[A-Za-z0-9][A-Za-z0-9._+-]{0,200}"
MAX_SUCCESSFUL_RELEASE_RECEIPTS = 256
MAX_SUCCESSFUL_RELEASE_RECEIPT_BYTES = 4096


class Runner(Protocol):
    def run(self, argv, *, timeout, cwd=None, env=None, capture=False) -> bytes: ...

    def wait_ready(self, *, project, config, timeout) -> bool: ...


class SystemRunner:
    """Bound commands and retain a private diagnostic tail when they fail."""

    def __init__(self, failure_log: Path | None = None) -> None:
        self.failure_log = failure_log
        self._failure_logged = False

    def _failure_log(self, output: bytes) -> None:
        if self.failure_log is None or self._failure_logged or not output:
            return
        directory = self.failure_log.parent
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = directory / ("." + self.failure_log.name + ".tmp")
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(output[-2 * 1024 * 1024 :])
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.failure_log)
            os.chmod(self.failure_log, 0o600)
            self._failure_logged = True
        except OSError:
            pass

    def run(self, argv, *, timeout, cwd=None, env=None, capture=False):
        root = Path("/")
        environment = {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if os.environ.get("ROBOPARK_TESTING") == "1":
            root = Path(os.environ["ROBOPARK_ROOT"])
            environment["PATH"] = os.environ["PATH"]
            environment["ROBOPARK_TESTING"] = "1"
            environment["ROBOPARK_ROOT"] = os.environ["ROBOPARK_ROOT"]
            if "TMPDIR" in os.environ:
                environment["TMPDIR"] = os.environ["TMPDIR"]
        environment["DOCKER_CONFIG"] = str(root / "var/lib/robopark/ops/docker-config")
        environment.update(env or {})
        output = bytearray()
        diagnostic = bytearray()
        process = subprocess.Popen(
            list(map(str, argv)),
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        completed = False
        try:
            deadline = time.monotonic() + timeout
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, capture)
                selector.register(process.stderr, selectors.EVENT_READ, False)
                while selector.get_map():
                    left = deadline - time.monotonic()
                    if left <= 0:
                        self._failure_log(bytes(diagnostic))
                        raise ReleaseError("command_timeout")
                    for key, _ in selector.select(min(left, 0.1)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        diagnostic.extend(chunk)
                        if len(diagnostic) > 2 * 1024 * 1024:
                            del diagnostic[: len(diagnostic) - 2 * 1024 * 1024]
                        if key.data:
                            output.extend(chunk)
                            if len(output) > 2 * 1024 * 1024:
                                raise ReleaseError("command_output_limit")
            try:
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                self._failure_log(bytes(diagnostic))
                raise ReleaseError("command_timeout") from exc
            if code:
                self._failure_log(bytes(diagnostic))
                failure = subprocess.CalledProcessError(
                    code,
                    list(map(str, argv)),
                    output=bytes(diagnostic).decode("utf-8", "replace"),
                )
                raise ReleaseError(explain_process_failure(failure))
            completed = True
            return bytes(output)
        finally:
            if not completed:
                # The group can outlive its leader (and retain stdout). Reaping
                # the leader is therefore never evidence that cleanup is done.
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                # Give all group members a short grace, even when the leader
                # has already been reaped; waiting on the leader cannot do that.
                time.sleep(0.2)
                # Reap a dead leader before signalling a now-empty group (macOS
                # reports EPERM for a group containing only an unreaped zombie).
                process.poll()
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=0.2)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

    def run_cleanup(self, argv, *, timeout) -> bool:
        """Run best-effort cleanup without replacing the real failure log."""

        failure_log = self.failure_log
        self.failure_log = None
        try:
            self.run(argv, timeout=timeout)
            return True
        except (OSError, ReleaseError):
            return False
        finally:
            self.failure_log = failure_log

    def wait_ready(self, *, project, config, timeout):
        deadline = time.monotonic() + timeout
        prefix = compose(project, config)
        while time.monotonic() < deadline:
            try:
                self.run(
                    prefix
                    + [
                        "exec",
                        "-T",
                        "api",
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)",
                    ],
                    timeout=min(10, max(0.1, deadline - time.monotonic())),
                )
                self.run(
                    prefix
                    + [
                        "exec",
                        "-T",
                        "web",
                        "wget",
                        "-q",
                        "-O",
                        "/dev/null",
                        "http://127.0.0.1/",
                    ],
                    timeout=min(10, max(0.1, deadline - time.monotonic())),
                )
                self.run(
                    prefix
                    + [
                        "exec",
                        "-T",
                        "web",
                        "wget",
                        "-q",
                        "-O",
                        "/dev/null",
                        "http://127.0.0.1/login",
                    ],
                    timeout=min(10, max(0.1, deadline - time.monotonic())),
                )
                return True
            except (ReleaseError, OSError):
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        return False


@dataclass(frozen=True)
class UpdateResult:
    state: str
    error: str | None = None


RecoveryResult = UpdateResult


def compose(project, config):
    return ["docker", "compose", "-p", project, "-f", str(config)]


def _journal_path(paths):
    return paths.state / "updater-journal.json"


def _phase(paths, journal, phase, **changes):
    journal.update(changes)
    journal["phase"] = phase
    atomic_write_json(_journal_path(paths), journal)
    _publish_status(
        paths, {"state": "updating", "phase": phase, "job_id": journal["job_id"]}
    )


def _public_directory(paths):
    directory = paths.ops / "public"
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    directory.chmod(0o755)
    return directory


def _publish_status(paths, payload):
    _public_directory(paths)
    atomic_write_json(paths.state / "host-status.json", payload)
    atomic_write_json(paths.ops / "public/host-status.json", payload, mode=0o644)


def _configured_update_channel(paths):
    descriptor = os.open(paths.etc / "updater.env", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r", encoding="ascii") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > 16384
            or info.st_mode & 0o077
        ):
            raise ValueError("invalid_update_channel")
        values = {}
        for line in stream:
            key, separator, value = line.strip().partition("=")
            if separator and key in {"GITHUB_CHANNEL", "ROBOPARK_UPDATE_CHANNEL"}:
                if key in values:
                    raise ValueError("invalid_update_channel")
                values[key] = value.strip("'")
    if {"GITHUB_CHANNEL", "ROBOPARK_UPDATE_CHANNEL"} <= set(values):
        raise ValueError("invalid_update_channel")
    channel = values.get(
        "ROBOPARK_UPDATE_CHANNEL", values.get("GITHUB_CHANNEL", "stable")
    )
    channel = "rc" if channel == "prerelease" else channel
    if channel not in {"stable", "rc", "manual"}:
        raise ValueError("invalid_update_channel")
    return channel


def _publish_release_lifecycle(paths, manifest):
    """Publish the installed signed lifecycle contract for the read-only API bridge."""
    released = timestamp(manifest["built_at"]) if manifest.get("format") == 3 else None
    months = manifest.get("support_months", 0)
    supported_until = None
    if released is not None and type(months) is int and months > 0:
        month_index = released.year * 12 + released.month - 1 + months
        year, month_zero = divmod(month_index, 12)
        month = month_zero + 1
        supported_until = released.replace(
            year=year,
            month=month,
            day=min(released.day, calendar.monthrange(year, month)[1]),
        )
    try:
        channel = _configured_update_channel(paths)
    except (OSError, UnicodeError, ValueError):
        channel = None
    policy = manifest.get("upgrade_policy", {})
    bridge = policy.get("bridge_version") if isinstance(policy, dict) else None
    atomic_write_json(
        _public_directory(paths) / "release-status.json",
        {
            "version": manifest.get("app_version"),
            "build_id": manifest.get("build_id"),
            "git_sha": manifest.get("git_sha"),
            "channel": channel,
            "support_class": manifest.get("support_class"),
            "released_at": released.isoformat() if released else None,
            "supported_until": supported_until.isoformat() if supported_until else None,
            "database_head": manifest.get("migration_head"),
            "installer_version": INSTALLER_VERSION,
            "bridges": [bridge] if isinstance(bridge, str) else [],
        },
        mode=0o644,
    )


def _maintenance(paths, enabled):
    _public_directory(paths)
    for path in (
        paths.state / "maintenance.json",
        paths.ops / "public/maintenance.json",
    ):
        if enabled:
            atomic_write_json(
                path,
                {"enabled": True, "reason": "update"},
                mode=0o644 if path.parent.name == "public" else 0o600,
            )
        else:
            path.unlink(missing_ok=True)
            if path.parent.exists():
                sync_directory(path.parent)


def publish_result(paths, payload):
    """The API reads public results without gaining write access to host state."""
    _public_directory(paths)
    atomic_write_json(paths.ops / "rebuild.result", payload)
    atomic_write_json(paths.ops / "public/rebuild.result", payload, mode=0o644)


def _finish(paths, journal, state, error=None):
    result = {
        "job_id": journal["job_id"],
        "ok": state == "current_healthy",
        "error": error,
    }
    publish_result(paths, result)
    status = {"state": state, "error": error, "job_id": journal["job_id"]}
    if journal["publication_degraded"]:
        status["publication"] = "degraded"
    _publish_status(paths, status)
    return UpdateResult(state, error)


def _release_target(paths, link):
    target = link.resolve(strict=True)
    if (
        not link.is_symlink()
        or target.parent != paths.releases.resolve()
        or target.is_symlink()
    ):
        raise ReleaseError("unsafe_release_path")
    return target


def _successful_release_receipts(paths):
    """Return only regular, locally owned receipts for named successful releases."""
    receipts = paths.state / "successful-releases"
    try:
        parent = os.open(receipts, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    try:
        result = []
        with os.scandir(parent) as listing:
            seen = 0
            for entry in listing:
                seen += 1
                if seen > MAX_SUCCESSFUL_RELEASE_RECEIPTS:
                    raise ReleaseError("unsafe_release_path")
                if re.fullmatch(RELEASE_NAME + r"\.json", entry.name) is None:
                    continue
                info = entry.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid not in {0, os.geteuid()}
                    or info.st_nlink != 1
                ):
                    continue
                try:
                    descriptor = os.open(
                        entry.name,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=parent,
                    )
                    with os.fdopen(descriptor, "rb") as stream:
                        current = os.fstat(stream.fileno())
                        if (
                            current.st_dev != info.st_dev
                            or current.st_ino != info.st_ino
                            or current.st_nlink != 1
                            or current.st_size > MAX_SUCCESSFUL_RELEASE_RECEIPT_BYTES
                            or json.loads(
                                stream.read(MAX_SUCCESSFUL_RELEASE_RECEIPT_BYTES + 1),
                                object_pairs_hook=unique_object,
                            )
                            != {"successful": True}
                        ):
                            continue
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                release = entry.name.removesuffix(".json")
                target = paths.releases / release
                if target.is_dir() and not target.is_symlink():
                    result.append((release, receipts / entry.name, info.st_mtime_ns))
        return result
    finally:
        os.close(parent)


def _retained_successful_releases(paths, limit=3, receipts=None):
    keep = {_release_target(paths, paths.current).name}
    if paths.previous.is_symlink():
        keep.add(_release_target(paths, paths.previous).name)
    records = _successful_release_receipts(paths) if receipts is None else receipts
    for release, _, _ in sorted(records, key=lambda item: item[2], reverse=True):
        if len(keep) >= limit:
            break
        keep.add(release)
    return keep


def _configuration_target(paths):
    link = paths.state / "current-compose.json"
    if not link.is_symlink():
        raise ReleaseError("compose_config_missing")
    target = link.resolve(strict=True)
    if not target.is_relative_to(paths.state.resolve()):
        raise ReleaseError("unsafe_config_path")
    return target.relative_to(paths.state.resolve()).as_posix()


def _disk_preflight(paths, release):
    # Account for immutable payload, test copy, build layers, and two data copies.
    data_size = sum(
        p.stat().st_size for p in (paths.var / "data").rglob("*") if p.is_file()
    )
    unpacked = sum(f["size"] for f in release.manifest["files"].values())
    required = unpacked * 3 + data_size * 2 + 2 * 1024**3
    if (
        shutil.disk_usage(paths.releases).free < required
        or shutil.disk_usage(paths.var).free < data_size * 2 + 256 * 1024**2
    ):
        raise ReleaseError("insufficient_space")
    if paths.releases.stat().st_dev != paths.releases.parent.stat().st_dev:
        # Staging is always a direct child of releases, never /var or /tmp.
        raise ReleaseError("staging_filesystem_mismatch")


def _render_configs(paths, journal, runner, stage):
    from .runtime import source_compose_environment

    work = paths.ops / "staging" / journal["job_id"]
    work.mkdir(parents=True, mode=0o700)
    environment_file = work / "test.env"
    environment_file.write_text("SECRET_KEY=isolated-test-key\nDEV_SEED=false\n")
    environment_file.chmod(0o600)
    candidate_password = secrets.token_urlsafe(32)
    database_password = work / "postgres-password"
    database_password.write_text(candidate_password + "\n")
    database_password.chmod(0o600)
    database_pgpass = work / "pgpass"
    database_pgpass.write_text(f"db:5432:robopark:robopark:{candidate_password}\n")
    database_pgpass.chmod(0o600)
    if os.geteuid() == 0:
        os.chown(database_pgpass, 10001, 10001)
    project = "robopark-candidate-" + journal["job_id"]
    raw = runner.run(
        compose(project, stage / "deploy/docker-compose.yml")
        + [
            "--env-file",
            str(environment_file),
            "config",
            "--no-env-resolution",
            "--format",
            "json",
        ],
        timeout=60,
        capture=True,
        env={
            **source_compose_environment(paths),
            "ROBOPARK_DATA_DIR": str(paths.var / "data"),
        },
    )
    try:
        config = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(config.get("services"), dict) or not {"api", "web"} <= set(
            config["services"]
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReleaseError("compose_invalid") from exc
    production = copy.deepcopy(config)
    production.pop("name", None)
    for name, service in production["services"].items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise ReleaseError("compose_invalid")
        if "build" in service:
            service["image"] = "robopark-" + name + ":" + journal["job_id"]
            build = service["build"]
            if isinstance(build, str):
                build = {"context": build}
                service["build"] = build
            context = Path(build.get("context", "."))
            if not context.is_absolute():
                context = (stage / "deploy" / context).resolve()
            if not context.is_relative_to(stage):
                raise ReleaseError("unsafe_build_context")
            build["context"] = str(
                paths.releases / journal["candidate"] / context.relative_to(stage)
            )
    production = production_config(
        production, paths, paths.releases / journal["candidate"], journal["job_id"]
    )
    smoke = copy.deepcopy(production)
    smoke.pop("volumes", None)
    smoke.pop("networks", None)
    smoke.pop("secrets", None)
    smoke.pop("configs", None)
    smoke["services"] = {key: smoke["services"][key] for key in ("db", "api", "web")}
    for service in smoke["services"].values():
        for field in (
            "container_name",
            "network_mode",
            "networks",
            "devices",
            "privileged",
            "pid",
            "ipc",
            "secrets",
            "configs",
            "volumes",
            "ports",
            "depends_on",
            "env_file",
        ):
            service.pop(field, None)
        if "build" in service:
            context = Path(service["build"]["context"])
            service["build"]["context"] = str(
                stage / context.relative_to(paths.releases / journal["candidate"])
            )
        service["restart"] = "no"
    smoke["volumes"] = {"candidate_data": {}, "candidate_postgres": {}}
    smoke["secrets"] = {"candidate-postgres-password": {"file": str(database_password)}}
    smoke["services"]["db"]["volumes"] = [
        {
            "type": "volume",
            "source": "candidate_postgres",
            "target": "/var/lib/postgresql/data",
        }
    ]
    smoke["services"]["db"]["secrets"] = ["candidate-postgres-password"]
    smoke["services"]["db"]["environment"] = {
        "POSTGRES_USER": "robopark",
        "POSTGRES_DB": "robopark",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/candidate-postgres-password",
    }
    smoke["services"]["api"]["volumes"] = [
        {"type": "volume", "source": "candidate_data", "target": "/data"},
        {
            "type": "bind",
            "source": str(database_pgpass),
            "target": "/run/secrets/pgpass",
            "read_only": True,
        },
    ]
    smoke["services"]["api"]["env_file"] = [str(environment_file)]
    smoke["services"]["api"]["environment"] = {
        "DATABASE_URL": "postgresql+psycopg://robopark@db:5432/robopark",
        "PGPASSFILE": "/run/secrets/pgpass",
        "REPORT_ATTACHMENTS_DIR": "/data/attachments",
        "LIVE_MERGE_DIR": "/data/live-merge",
        "STAGED_ATTACHMENTS_DIR": "/data/task-attachments",
        "OPS_DIR": "/data/ops",
        "DEV_SEED": "false",
    }
    smoke["services"]["web"]["environment"] = {}
    smoke["services"]["web"]["ports"] = [
        {"target": 80, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}
    ]
    smoke["services"]["api"]["depends_on"] = {"db": {"condition": "service_healthy"}}
    root = paths.state / "compose"
    atomic_write_json(root / (journal["job_id"] + "-production.json"), production)
    atomic_write_json(root / (journal["job_id"] + "-smoke.json"), smoke)
    return work, root / (journal["job_id"] + "-smoke.json")


def _cleanup_staging(paths, journal, runner, *, discard_displaced=True):
    for target in ("api", "web"):
        commands = [
            [
                "docker",
                "rm",
                "--force",
                "robopark-tests-" + journal["job_id"] + "-" + target,
            ],
            [
                "docker",
                "image",
                "rm",
                "robopark-" + target + "-tests:" + journal["job_id"],
            ],
        ]
        for command in commands:
            # Already removed resources are normal after --rm or interrupted cleanup.
            cleanup = getattr(runner, "run_cleanup", None)
            if cleanup is not None:
                cleanup(command, timeout=60)
            else:
                with suppress(ReleaseError):
                    runner.run(command, timeout=60)
    smoke = paths.state / "compose" / (journal["job_id"] + "-smoke.json")
    if smoke.exists():
        runner.run(
            compose("robopark-candidate-" + journal["job_id"], smoke)
            + ["down", "--remove-orphans", "--volumes"],
            timeout=120,
        )
    for root in (
        paths.releases / (".staging-" + journal["job_id"]),
        paths.ops / "staging" / journal["job_id"],
    ):
        if root.is_symlink():
            raise ReleaseError("unsafe_path")
        if root.exists():
            shutil.rmtree(root)
    smoke.unlink(missing_ok=True)
    displaced = paths.var / (".displaced-" + journal["job_id"])
    if discard_displaced and displaced.is_dir() and not displaced.is_symlink():
        shutil.rmtree(displaced)


def _retention(paths, journal):
    receipts = _successful_release_receipts(paths)
    keep = _retained_successful_releases(paths, receipts=receipts)
    # Only delete targets with root-owned success receipts. Never sweep unknown directories.
    for name, receipt, _ in receipts:
        if name in keep:
            continue
        target = paths.releases / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        receipt.unlink()
    keep_configs = {
        paths.state / journal["previous_config"],
        (paths.state / "current-compose.json").resolve(),
    }
    keep_snapshots = {journal["job_id"]}
    for name in keep:
        try:
            identifier = str(UUID(name[-36:]))
        except ValueError:
            continue
        keep_configs.add(paths.state / "compose" / (identifier + "-production.json"))
        keep_snapshots.add(identifier)
    for config in (paths.state / "compose").glob("*.json"):
        if config not in keep_configs and not config.is_symlink():
            config.unlink()
    # Keep the active rollback plus material belonging to the retained releases.
    rollback_root = paths.ops / "rollbacks"
    if not rollback_root.exists():
        return
    for directory in rollback_root.iterdir():
        if (
            directory.name not in keep_snapshots
            and directory.is_dir()
            and not directory.is_symlink()
        ):
            try:
                UUID(directory.name)
            except ValueError:
                continue
            shutil.rmtree(directory)


def apply_release(
    request: UpdateRequest, paths: HostPaths, runner: Runner
) -> UpdateResult:
    try:
        raw = request.read_artifact(paths)
        key = admission_key(paths)
        previous = _release_target(paths, paths.current)
        current_key = directory_key(paths, previous)
        current_manifest = verify_directory(previous, current_key)
        completed = _load_journal(paths)
        if (
            completed
            and completed["job_id"] == request.job_id
            and completed["phase"] == "succeeded"
        ):
            # The completed bridge used the preceding key. Its exact retained
            # identity can acknowledge this job only; it cannot admit a new job.
            release = verify_archive(raw, current_key)
            # This preliminary historical check never acknowledges the job:
            # current identity and authority must still be checked under ownership.
        else:
            release = verify_archive(raw, key)
            check_compatibility(release.manifest, current_manifest)
            _disk_preflight(paths, release)
    except ReleaseError as exc:
        return UpdateResult("rejected", str(exc))
    except OSError:
        return UpdateResult("rejected", "preflight_failed")
    with exclusive_lock(paths.host_lock):
        from .restore import active_restore

        if active_restore(paths):
            return UpdateResult("rejected", "update_in_progress")
        old = _load_journal(paths)
        if old and old["phase"] not in {"succeeded", "rolled_back", "failed"}:
            return UpdateResult("rejected", "update_in_progress")
        if paths.current.resolve() != previous:
            return UpdateResult("rejected", "current_changed")
        try:
            # A bridge can promote trust without changing this already-switched
            # current link while the preliminary checks wait for host.lock.
            # Only these fresh, owned checks can authorize admission or replay.
            key = admission_key(paths)
            current_key = directory_key(paths, previous)
            current_manifest = verify_directory(previous, current_key)
            if old and old["job_id"] == request.job_id and old["phase"] == "succeeded":
                release = verify_archive(raw, current_key)
                if (
                    current_manifest == release.manifest
                    and previous.name == old["candidate"]
                ):
                    return UpdateResult("current_healthy")
                raise ReleaseError("request_replayed")
            release = verify_archive(raw, key)
            check_compatibility(release.manifest, current_manifest)
            previous_config = _configuration_target(paths)
            _disk_preflight(paths, release)
        except ReleaseError as exc:
            return UpdateResult("rejected", str(exc))
        except OSError:
            return UpdateResult("rejected", "preflight_failed")
        journal = {
            "schema": 1,
            "job_id": request.job_id,
            "actor_user_id": request.actor_user_id,
            "candidate": release.manifest["app_version"] + "-" + request.job_id,
            "previous": previous.name,
            "previous_config": previous_config,
            "original_previous": _release_target(paths, paths.previous).name
            if paths.previous.is_symlink()
            else None,
            "phase": "verified",
            "migration_started": False,
            "writes_resumed": False,
            "snapshot_done": False,
            "cutover_started": False,
            "publication_degraded": False,
            "error": None,
        }
        stage = paths.releases / (".staging-" + request.job_id)
        candidate = paths.releases / journal["candidate"]
        phase = partial(_phase, paths, journal)
        try:
            phase("unpacking")
            release.unpack(stage)
            phase("unpacked")
            require_record_capacity(paths)
            reserve_images(paths, candidate, request.job_id)
            phase("building")
            _, smoke_config = _render_configs(paths, journal, runner, stage)
            prefix = compose("robopark-candidate-" + request.job_id, smoke_config)
            runner.run(prefix + ["config", "--quiet"], timeout=60)
            # Compose otherwise builds independent services concurrently. That
            # creates avoidable memory pressure on the supported 8 GiB ARM host.
            runner.run(prefix + ["build", "--pull", "api"], timeout=1800)
            runner.run(prefix + ["build", "--pull", "web"], timeout=1800)
            production_path = (
                paths.state / "compose" / (request.job_id + "-production.json")
            )
            production = json.loads(production_path.read_text())
            pin_images(
                production, lambda argv: runner.run(argv, timeout=30, capture=True)
            )
            record_images(paths, candidate, request.job_id, production)
            atomic_write_json(production_path, production)
            phase("built")
            phase("smoking")
            runner.run(prefix + ["up", "-d", "--no-build"], timeout=180)
            if not runner.wait_ready(
                project="robopark-candidate-" + request.job_id,
                config=smoke_config,
                timeout=180,
            ):
                raise ReleaseError("smoke_failed")
            runner.run(prefix + ["down", "--volumes", "--remove-orphans"], timeout=120)
            phase("smoked")
            verify_directory(stage, key)
            phase("maintenance")
            _maintenance(paths, True)
            phase("stopping")
            runner.run(["systemctl", "stop", "robopark.service"], timeout=120)
            phase("snapshotting")
            try:
                snapshot(paths, journal, runner)
            except Exception:
                # The status receipt is informational; never hide the original
                # snapshot failure if its own durable write also fails.
                with suppress(OSError, ValueError):
                    record_backup(paths, "failed")
                raise
            record_backup(paths, "success")
            phase("snapshotted", snapshot_done=True)
            phase("tools_staging")
            runner.run(
                ["python3", "-B", str(stage / "deploy/host/robopark"), "--self-test"],
                cwd=stage / "deploy/host",
                timeout=60,
            )
            verify_directory(stage, key)
            phase("tools_staged")
            phase("publishing")
            os.replace(stage, candidate)
            sync_directory(paths.releases)
            phase("published")
            phase("switching", cutover_started=True)
            atomic_symlink(previous, paths.previous)
            atomic_symlink(candidate, paths.current)
            atomic_symlink(
                paths.state / "compose" / (request.job_id + "-production.json"),
                paths.state / "current-compose.json",
            )
            phase("switched")
            phase("migrating", migration_started=True)
            runner.run(
                compose("robopark", paths.state / "current-compose.json")
                + [
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "python",
                    "api",
                    "-m",
                    "alembic",
                    "upgrade",
                    "head",
                ],
                timeout=900,
            )
            _verify_database_head(paths, runner, release.manifest["migration_head"])
            phase("migrated")
            phase("starting")
            runner.run(["systemctl", "restart", "robopark.service"], timeout=900)
            phase("started")
            phase("activating")
            _activate_system_files(paths, candidate)
            atomic_symlink(candidate / "deploy/host", paths.opt / "host-tools")
            phase("activated")
            phase("health_check")
            if not runner.wait_ready(
                project="robopark",
                config=paths.state / "current-compose.json",
                timeout=180,
            ):
                raise ReleaseError("cutover_unhealthy")
            phase("healthy")
            return UpdateResult("awaiting_reconciliation")
        except Exception as exc:
            error = _failure_token(journal["phase"], exc)
            return _handle_failure(paths, journal, runner, error)


def _failure_token(phase, exc):
    if isinstance(exc, ReleaseError) and str(exc) == "command_failed":
        return {
            "building": "build_failed",
            "testing": "tests_failed",
            "migrating": "migration_failed",
            "health_check": "cutover_unhealthy",
        }.get(phase, "update_failed")
    if isinstance(exc, ReleaseError) and str(exc) in SAFE_ERRORS:
        return str(exc)
    return {
        "building": "build_failed",
        "testing": "tests_failed",
        "smoking": "smoke_failed",
        "migrating": "migration_failed",
        "health_check": "cutover_unhealthy",
    }.get(phase, "update_failed")


def _handle_failure(paths, journal, runner, error):
    phase = partial(_phase, paths, journal)
    try:
        phase(journal["phase"], error=error)
        if (
            journal["cutover_started"]
            or journal["snapshot_done"]
            or (paths.state / "maintenance.json").exists()
        ):
            _maintenance(paths, True)
            rollback_release(paths, journal, runner, phase)
            # The restored app is locally ready; publication failure cannot undo it.
            publication_degraded = False
            try:
                runner.run(
                    ["systemctl", "restart", "robopark-tuna.service"], timeout=90
                )
            except Exception:
                publication_degraded = True
            if not _wait_public_ready(paths, runner, timeout=PUBLIC_READY_TIMEOUT):
                publication_degraded = True
            phase("rollback_healthy", publication_degraded=publication_degraded)
            phase("rollback_resuming", writes_resumed=True)
            _maintenance(paths, False)
            phase("rolled_back")
        else:
            phase("failed")
        return _failed_housekeeping(paths, journal, runner)
    except Exception:
        _maintenance(paths, True)
        phase("manual_recovery_required", error="manual_recovery_required")
        return _finish(paths, journal, "maintenance", "manual_recovery_required")


def _discard_unstarted_update(paths, journal, runner):
    """No production operation occurred; discard only this job's candidate work."""
    _phase(paths, journal, "failed", error=journal["error"] or "interrupted")
    try:
        _cleanup_staging(paths, journal, runner, discard_displaced=False)
        config = paths.state / "compose" / (journal["job_id"] + "-production.json")
        if config.resolve() == (paths.state / "current-compose.json").resolve():
            raise ReleaseError("unsafe_config_path")
        config.unlink(missing_ok=True)
        cleanup_images(paths, runner)
    except Exception:
        # Failed staging cleanup must not turn into a production restart or
        # manufacture maintenance. Keep the durable failed journal for retry.
        return _finish(paths, journal, "previous_restored", "manual_recovery_required")
    return _finish(paths, journal, "previous_restored", journal["error"])


def _failed_housekeeping(paths, journal, runner):
    _cleanup_staging(paths, journal, runner)
    failed_candidate = paths.releases / journal["candidate"]
    if (
        failed_candidate.exists()
        and not failed_candidate.is_symlink()
        and failed_candidate != paths.current.resolve()
        and failed_candidate != paths.previous.resolve()
    ):
        shutil.rmtree(failed_candidate)
    _retention(paths, journal)
    cleanup_images(paths, runner)
    return _finish(paths, journal, "previous_restored", journal["error"])


def _load_journal(paths):
    path = _journal_path(paths)
    if not path.exists():
        return None
    try:
        if path.is_symlink() or path.stat().st_size > 16384:
            raise ValueError()
        journal = json.loads(path.read_bytes(), object_pairs_hook=unique_object)
        fields = {
            "schema",
            "job_id",
            "actor_user_id",
            "candidate",
            "previous",
            "previous_config",
            "original_previous",
            "phase",
            "migration_started",
            "writes_resumed",
            "snapshot_done",
            "cutover_started",
            "publication_degraded",
            "error",
        }
        if (
            set(journal) != fields
            or journal["schema"] != 1
            or str(UUID(journal["job_id"])) != journal["job_id"]
        ):
            raise ValueError()
        for key in ("candidate", "previous", "original_previous"):
            if journal[key] is None and key == "original_previous":
                continue
            if not isinstance(journal[key], str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._+-]{0,150}", journal[key]
            ):
                raise ValueError()
        safe_member(journal["previous_config"])
        if (
            not (paths.state / journal["previous_config"])
            .resolve()
            .is_relative_to(paths.state.resolve())
        ):
            raise ValueError()
        for key in (
            "migration_started",
            "writes_resumed",
            "snapshot_done",
            "cutover_started",
            "publication_degraded",
        ):
            if type(journal[key]) is not bool:
                raise ValueError()
        if (
            type(journal["actor_user_id"]) is not int
            or not 0 < journal["actor_user_id"] < 2**63
        ):
            raise ValueError()
        if not isinstance(journal["phase"], str) or journal["phase"] not in PHASES:
            raise ValueError()
        if journal["error"] is not None and (
            not isinstance(journal["error"], str) or journal["error"] not in SAFE_ERRORS
        ):
            raise ValueError()
        return journal
    except (OSError, ValueError, TypeError, UnicodeError, KeyError) as exc:
        raise ReleaseError("manual_recovery_required") from exc


DATABASE_HEAD_QUERY = """import json, os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as connection:
    heads = [row[0] for row in connection.execute(text('SELECT version_num FROM alembic_version'))]
print(json.dumps(heads))
"""
PUBLIC_READY_TIMEOUT = 60


def _verify_database_head(paths, runner, expected):
    try:
        output = runner.run(
            compose("robopark", paths.state / "current-compose.json")
            + [
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "api",
                "-c",
                DATABASE_HEAD_QUERY,
            ],
            timeout=30,
            capture=True,
        )
        if len(output) > 4096 or json.loads(output) != [expected]:
            raise ValueError()
    except (ReleaseError, OSError, ValueError, TypeError) as exc:
        raise ReleaseError("migration_head_mismatch") from exc


def _public_origin(paths):
    """Read only the configured origin from a trusted host file; never source env."""
    owner = 0 if paths.root == Path("/") else os.geteuid()
    directory = paths.etc.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != owner
        or directory.st_mode & 0o022
    ):
        raise ValueError("invalid_public_origin")
    descriptor = os.open(
        paths.etc / "host.env", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner
            or metadata.st_mode & 0o022
            or metadata.st_size > 65536
        ):
            raise ValueError("invalid_public_origin")
        raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("invalid_public_origin")
    origins = {}
    for line in raw.decode("utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key in {"PUBLIC_ORIGIN", "CORS_ORIGINS"}:
            if key in origins:
                raise ValueError("invalid_public_origin")
            value = value.strip()
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            origins[key] = value
    origin = origins.get("PUBLIC_ORIGIN", origins.get("CORS_ORIGINS", ""))
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", parsed.netloc)
        or parsed.port == 0
    ):
        raise ValueError("invalid_public_origin")
    return origin.rstrip("/")


def _wait_public_ready(paths, runner, *, timeout):
    try:
        url = _public_origin(paths) + "/api/health/ready"
    except (OSError, ValueError, UnicodeError):
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            output = runner.run(
                [
                    "curl",
                    "--disable",
                    "--silent",
                    "--fail",
                    "--proto",
                    "=https",
                    "--proto-redir",
                    "=https",
                    "--connect-timeout",
                    "3",
                    "--max-time",
                    "5",
                    "--max-filesize",
                    "4096",
                    "--write-out",
                    "\\n%{http_code}",
                    url,
                ],
                timeout=min(6, max(0.001, deadline - time.monotonic())),
                capture=True,
            )
            body, _, status = output.rpartition(b"\n")
            if (
                len(body) <= 4096
                and status == b"200"
                and json.loads(body).get("status") == "ready"
            ):
                return True
        except (ReleaseError, OSError, ValueError, AttributeError):
            pass
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    return False


def _complete(paths, journal, runner):
    phase = partial(_phase, paths, journal)
    candidate = paths.releases / journal["candidate"]
    manifest = verify_directory(candidate, directory_key(paths, candidate))
    phase("reconciling")
    runner.run(["systemctl", "daemon-reload"], timeout=60)
    runner.run(["systemctl", "restart", "robopark.service"], timeout=900)
    if not runner.wait_ready(
        project="robopark", config=paths.state / "current-compose.json", timeout=180
    ):
        return _handle_failure(paths, journal, runner, "cutover_unhealthy")
    _verify_database_head(paths, runner, manifest["migration_head"])
    phase("publication")
    for unit in (
        "robopark-update-check.timer",
        "robopark-doctor.timer",
        "robopark-watchdog.timer",
        "robopark-commands.path",
    ):
        if (candidate / "deploy/systemd" / unit).is_file():
            runner.run(["systemctl", "try-restart", unit], timeout=60)
    publication_degraded = False
    if (candidate / "deploy/systemd/robopark-tuna.service").is_file():
        try:
            runner.run(["systemctl", "restart", "robopark-tuna.service"], timeout=90)
        except Exception:
            # Publication is independent of core data readiness. Restore the old
            # Tuna unit when available, but never roll back database writes here.
            publication_degraded = True
            previous_unit = (
                paths.ops
                / "rollbacks"
                / journal["job_id"]
                / "units/robopark-tuna.service"
            )
            if previous_unit.is_file():
                atomic_copy(
                    previous_unit,
                    paths.root / "etc/systemd/system/robopark-tuna.service",
                    0o644,
                )
                try:
                    runner.run(["systemctl", "daemon-reload"], timeout=60)
                    runner.run(
                        ["systemctl", "restart", "robopark-tuna.service"], timeout=90
                    )
                except Exception:
                    pass
    if not _wait_public_ready(paths, runner, timeout=PUBLIC_READY_TIMEOUT):
        publication_degraded = True
    phase("publication_checked", publication_degraded=publication_degraded)
    # Persist the irreversible boundary BEFORE opening writes. Recovery must never
    # restore an old snapshot after this record, even if the unlink was interrupted.
    activate_trust(paths, journal)
    phase("resuming", writes_resumed=True)
    _maintenance(paths, False)
    result = _success_housekeeping(paths, journal, runner)
    runner.run(
        ["systemctl", "try-restart", "--no-block", "robopark-updater.service"],
        timeout=60,
    )
    return result


def _success_housekeeping(paths, journal, runner):
    atomic_write_json(
        paths.state / "successful-releases" / (journal["previous"] + ".json"),
        {"successful": True},
    )
    atomic_write_json(
        paths.state / "successful-releases" / (journal["candidate"] + ".json"),
        {"successful": True},
    )
    _phase(paths, journal, "succeeded")
    current = _release_target(paths, paths.current)
    manifest = verify_directory(current, directory_key(paths, current))
    try:
        _publish_release_lifecycle(paths, manifest)
    except (OSError, ReleaseError, TypeError, UnicodeError, ValueError):
        _phase(paths, journal, "succeeded", publication_degraded=True)
    _cleanup_staging(paths, journal, runner)
    _retention(paths, journal)
    cleanup_images(paths, runner)
    return _finish(paths, journal, "current_healthy")


def reconcile_after_exit(paths: HostPaths, runner: Runner) -> RecoveryResult:
    """Called only by the stable parent launcher after the old worker exits."""
    return recover_interrupted_update(paths, runner)


def recover_interrupted_update(paths: HostPaths, runner: Runner) -> RecoveryResult:
    with exclusive_lock(paths.host_lock):
        from .restore import active_restore

        if active_restore(paths):
            _maintenance(paths, True)
            return UpdateResult("maintenance", "manual_recovery_required")
        try:
            journal = _load_journal(paths)
            if journal is None:
                return UpdateResult("idle")
            if (
                journal["phase"] in PRE_MAINTENANCE_PHASES | {"failed"}
                and not any(
                    journal[key]
                    for key in (
                        "cutover_started",
                        "snapshot_done",
                        "migration_started",
                        "writes_resumed",
                    )
                )
                and not (paths.state / "maintenance.json").exists()
            ):
                return _discard_unstarted_update(paths, journal, runner)
            if journal["phase"] in {"succeeded", "resuming"}:
                # New writes may already exist; finish only housekeeping, no rollback.
                _maintenance(paths, False)
                return _success_housekeeping(paths, journal, runner)
            if journal["phase"] in {"rolled_back", "rollback_resuming", "failed"}:
                _maintenance(paths, False)
                return _failed_housekeeping(paths, journal, runner)
            if journal["writes_resumed"]:
                raise ReleaseError("manual_recovery_required")
            _maintenance(paths, True)
            if journal["phase"] in {
                "started",
                "activating",
                "activated",
                "health_check",
                "healthy",
                "reconciling",
                "publication",
                "publication_checked",
            }:
                try:
                    candidate = paths.releases / journal["candidate"]
                    verify_directory(candidate, directory_key(paths, candidate))
                    if paths.current.resolve() != candidate:
                        raise ReleaseError("manual_recovery_required")
                    # Replay activation after a partial unit copy, before accepting health.
                    _activate_system_files(paths, candidate)
                    atomic_symlink(candidate / "deploy/host", paths.opt / "host-tools")
                    return _complete(paths, journal, runner)
                except Exception as exc:
                    if journal["writes_resumed"]:
                        raise ReleaseError("manual_recovery_required") from exc
                    return _handle_failure(
                        paths, journal, runner, _failure_token(journal["phase"], exc)
                    )
            return _handle_failure(
                paths, journal, runner, journal["error"] or "interrupted"
            )
        except Exception:
            _maintenance(paths, True)
            _publish_status(
                paths, {"state": "maintenance", "error": "manual_recovery_required"}
            )
            return UpdateResult("maintenance", "manual_recovery_required")
