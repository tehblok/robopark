"""Back up and isolate the former production database before any test API starts.

Called only with all application writers stopped by khadas_test_setup. No secret
values are read into stdout, and the original database dump is never overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path


class IsolationError(RuntimeError):
    pass


ISOLATION_SQL = """BEGIN;
DELETE FROM platform_settings WHERE key IN (
  'tracker_token', 'emergency_cookie', 'telegram_bot_token',
  'emergency_cookie_valid', 'emergency_cookie_status',
  'emergency_cookie_checked_at', 'emergency_cookie_checked_robot',
  'emergency_cookie_identity', 'emergency_keepalive_ring',
  'emergency_keepalive_seed_vin', 'emergency_keepalive_last_ok_at'
);
DELETE FROM push_subscriptions;
DELETE FROM sessions;
UPDATE reliable_actions
SET state='needs_attention', error_code='test_instance_quarantined', lease_until=NULL
WHERE state IN ('pending', 'sending', 'retry_wait');
UPDATE task_messages SET sync_state='needs_attention' WHERE sync_state='pending';
UPDATE notification_deliveries
SET state='expired', lease_owner=NULL, lease_until=NULL
WHERE state IN ('pending', 'sending', 'retry_wait');
INSERT INTO native_bot_control (id, queries_paused, deliveries_paused, revision)
VALUES (1, TRUE, TRUE, 1)
ON CONFLICT (id) DO UPDATE SET queries_paused=TRUE, deliveries_paused=TRUE;
UPDATE native_bot_jobs SET enabled=FALSE;
UPDATE native_bot_deliveries SET state='failed', error_code='test_instance_quarantined',
  lease_token_hash=NULL, lease_until=NULL, finished_at=CURRENT_TIMESTAMP
WHERE state IN ('preparing', 'sending');
UPDATE ai_automations SET enabled=FALSE;
UPDATE ai_connectors SET enabled=FALSE;
UPDATE ai_scripts SET enabled=FALSE;
UPDATE ai_jobs SET state='cancelled', error='test_instance_quarantined'
WHERE state IN ('queued', 'running', 'waiting');
UPDATE ai_actions SET state='cancelled', error='test_instance_quarantined'
WHERE state IN ('ready', 'waiting', 'approved', 'running');
UPDATE ai_runs SET state='cancelled', error='test_instance_quarantined'
WHERE state IN ('queued', 'running');
UPDATE host_operation_status SET state='failed', receipt_state='terminal',
  phase='failed', error='request_superseded',
  progress_percent=NULL, updated_at=CURRENT_TIMESTAMP, terminal_at=CURRENT_TIMESTAMP
WHERE state IN ('queued', 'running');
COMMIT;
"""


def _regular(path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise IsolationError("unsafe_isolation_backup")


def _digest(path: Path) -> str:
    _regular(path)
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(part)
    return result.hexdigest()


def _sync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_receipt(path: Path, value: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_receipt(path: Path) -> dict:
    _regular(path)
    if path.stat().st_size > 4096:
        raise IsolationError("invalid_isolation_receipt")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise IsolationError("invalid_isolation_receipt")
    return value


def isolate(root: Path, runner, backup_dir: Path) -> None:
    """Use local Docker's existing db only; caller has stopped all other services."""
    root = root.resolve()
    expected = root / "var/lib/robopark-test-transition"
    if backup_dir != expected or backup_dir.resolve() != expected:
        raise IsolationError("unsafe_isolation_directory")
    info = backup_dir.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise IsolationError("unsafe_isolation_directory")
    config = root / "var/lib/robopark/ops/state/current-compose.json"
    prefix = [
        "docker",
        "compose",
        "--project-name",
        "robopark",
        "--file",
        str(config),
        "exec",
        "-T",
        "db",
    ]
    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "DOCKER_CONFIG": str(backup_dir / "docker-config"),
    }
    options = {
        "check": True,
        "stderr": subprocess.PIPE,
        "env": environment,
        "timeout": 1800,
    }
    dump = backup_dir / "before-test-isolation.dump"
    metadata_path = backup_dir / "database-backup.json"
    marker = backup_dir / "isolation.json"
    try:
        if dump.exists() or dump.is_symlink():
            digest = _digest(dump)
            if metadata_path.exists():
                metadata = _read_receipt(metadata_path)
                if metadata != {"sha256": digest, "size": dump.stat().st_size}:
                    raise IsolationError("isolation_backup_changed")
            elif marker.exists():
                raise IsolationError("isolation_backup_receipt_missing")
        else:
            if metadata_path.exists() or marker.exists():
                raise IsolationError("isolation_backup_missing")
            space = os.statvfs(backup_dir)
            if space.f_bavail * space.f_frsize < 1024**3:
                raise IsolationError("insufficient_isolation_backup_space")
            partial = backup_dir / "before-test-isolation.partial"
            # An interrupted dump is not the immutable original; it is safe to
            # retry only this exact private, uncommitted partial file.
            if partial.exists() or partial.is_symlink():
                _regular(partial)
                partial.unlink()
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                runner(
                    prefix
                    + [
                        "pg_dump",
                        "-U",
                        "robopark",
                        "-d",
                        "robopark",
                        "--format=custom",
                        "--no-owner",
                    ],
                    stdout=stream,
                    stdin=subprocess.DEVNULL,
                    **options,
                )
                stream.flush()
                os.fsync(stream.fileno())
            if partial.stat().st_size < 32:
                raise IsolationError("database_backup_empty")
            with partial.open("rb") as stream:
                if stream.read(5) != b"PGDMP":
                    raise IsolationError("database_backup_invalid")
            os.replace(partial, dump)
            _sync(backup_dir)
            digest = _digest(dump)

        if marker.exists():
            if _read_receipt(marker) != {
                "schema": 1,
                "complete": True,
                "backup_sha256": digest,
            }:
                raise IsolationError("isolation_receipt_changed")
            return

        # Parse every object and compressed data block, not only the header/TOC.
        with dump.open("rb") as stream:
            runner(
                prefix + ["pg_restore", "--file=/dev/null"],
                stdin=stream,
                stdout=subprocess.DEVNULL,
                **options,
            )
        if not metadata_path.exists():
            _write_receipt(
                metadata_path, {"sha256": digest, "size": dump.stat().st_size}
            )
        runner(
            prefix
            + [
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "robopark",
                "-d",
                "robopark",
            ],
            input=ISOLATION_SQL.encode(),
            stdout=subprocess.DEVNULL,
            **options,
        )
        _write_receipt(marker, {"schema": 1, "complete": True, "backup_sha256": digest})
    except IsolationError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        # Command output can contain database values and credentials. It stays
        # private to the process and is never included in the user-facing error.
        raise IsolationError("test_database_isolation_failed") from None
