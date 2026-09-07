"""Installed data restore; caller owns host.lock, workers never replace a live DB.

A root-private journal and backup survive process/power loss. Until the durable
resuming boundary, recovery restores the prior complete data directory. Host
configuration, credentials, API jobs and executable release files are never imported.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import time
import zipfile
from contextlib import closing
from pathlib import Path
from uuid import UUID

from .release import ReleaseError, safe_member, unique_object, verify_directory
from .rollback import durable_copy_tree, sync_directory
from .state import atomic_write_json

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
PHASES = {
    "validating",
    "prepared",
    "maintenance",
    "stopping",
    "snapshotting",
    "snapshotted",
    "replacing",
    "replaced",
    "starting",
    "ready",
    "resuming",
    "succeeded",
    "rolling_back",
    "rollback_starting",
    "rollback_ready",
    "rollback_resuming",
    "rolled_back",
    "failed",
    "manual_recovery_required",
}
TERMINAL = {"succeeded", "rolled_back", "failed"}


def _path(paths):
    return paths.state / "restore-journal.json"


def _load(paths):
    target = _path(paths)
    if not target.exists() and not target.is_symlink():
        return None
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
                raise ValueError()
            journal = json.loads(stream.read(8193), object_pairs_hook=unique_object)
        if set(journal) != {
            "schema",
            "request",
            "phase",
            "snapshot_done",
            "writes_resumed",
            "error",
            "publication_degraded",
        }:
            raise ValueError()
        if journal["schema"] != 1 or journal["phase"] not in PHASES:
            raise ValueError()
        from .commands import _validate

        _validate(journal["request"], fresh=False)
        if journal["request"]["kind"] != "restore":
            raise ValueError()
        if any(
            type(journal[key]) is not bool
            for key in ("snapshot_done", "writes_resumed", "publication_degraded")
        ):
            raise ValueError()
        if journal["error"] not in (
            None,
            "restore_failed",
            "request_expired",
            "snapshot_invalid",
            "interrupted",
            "manual_recovery_required",
        ):
            raise ValueError()
        return journal
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ReleaseError("manual_recovery_required") from exc


def active_restore(paths):
    try:
        journal = _load(paths)
        return journal is not None and journal["phase"] not in TERMINAL
    except ReleaseError:
        return True


def check_app_start(paths):
    """ExecStartPre gate: boot cannot open an absent/partially replaced data tree."""
    try:
        journal = _load(paths)
        return journal is None or journal["phase"] in TERMINAL | {
            "starting",
            "ready",
            "resuming",
            "rollback_starting",
            "rollback_ready",
            "rollback_resuming",
        }
    except ReleaseError:
        return False


def _phase(paths, journal, phase, **changes):
    journal.update(changes, phase=phase)
    atomic_write_json(_path(paths), journal)


def _root(paths, journal):
    identity = str(UUID(journal["request"]["job_id"]))
    return paths.state / "restores" / identity


def _validate_database(path, head):
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("snapshot_invalid")
    deadline = time.monotonic() + 30
    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=5)
        ) as connection:
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            connection.execute("PRAGMA trusted_schema=OFF")
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError()
            if connection.execute("SELECT version_num FROM alembic_version").fetchall() != [
                (head,)
            ]:
                raise ValueError()
    except (sqlite3.Error, ValueError) as exc:
        raise ReleaseError("snapshot_invalid") from exc


def _prepare(paths, journal):
    root = _root(paths, journal)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    candidate = root / "candidate"
    if candidate.exists():
        shutil.rmtree(candidate)
    artifact_dir = paths.ops / "artifacts"
    if artifact_dir.is_symlink():
        raise ReleaseError("snapshot_invalid")
    fd = os.open(
        artifact_dir / journal["request"]["artifact"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    private = root / "snapshot.zip"
    digest = hashlib.sha256()
    with os.fdopen(fd, "rb") as source, private.open("wb") as destination:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE:
            raise ReleaseError("snapshot_invalid")
        size = 0
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            if size > MAX_ARCHIVE:
                raise ReleaseError("snapshot_invalid")
            digest.update(chunk)
            destination.write(chunk)
        destination.flush()
        os.fchmod(destination.fileno(), 0o600)
        os.fsync(destination.fileno())
    if digest.hexdigest() != journal["request"]["sha256"]:
        raise ReleaseError("snapshot_invalid")
    with zipfile.ZipFile(private) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(infos) > 20000 or len(names) != len(set(names)) or "manifest.json" not in names:
            raise ReleaseError("snapshot_invalid")
        if sum(item.file_size for item in infos) > MAX_EXPANDED:
            raise ReleaseError("snapshot_invalid")
        for item in infos:
            safe_member(item.filename)
            mode = item.external_attr >> 16
            if item.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or item.flag_bits & 1:
                raise ReleaseError("snapshot_invalid")
            if item.file_size > max(1, item.compress_size) * 200:
                raise ReleaseError("snapshot_invalid")
        if archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
            raise ReleaseError("snapshot_invalid")
        manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=unique_object)
        if not isinstance(manifest, dict) or set(manifest) - {
            "kind",
            "format",
            "app_version",
            "files",
            "extra",
        }:
            raise ReleaseError("snapshot_invalid")
        required = {"kind", "format", "app_version", "files"}
        if (
            not required <= set(manifest)
            or not isinstance(manifest["app_version"], str)
            or not 0 < len(manifest["app_version"]) <= 100
        ):
            raise ReleaseError("snapshot_invalid")
        files = manifest.get("files")
        if (
            manifest.get("kind") != "snapshot"
            or type(manifest.get("format")) is not int
            or manifest["format"] != 1
            or not isinstance(files, dict)
        ):
            raise ReleaseError("snapshot_invalid")
        if set(names) != {"manifest.json", *files} or "data/robopark.db" not in files:
            raise ReleaseError("snapshot_invalid")
        expanded = sum(item.file_size for item in infos)
        live_size = sum(p.stat().st_size for p in (paths.var / "data").rglob("*") if p.is_file())
        for filesystem in (paths.state, paths.var):
            if shutil.disk_usage(filesystem).free < 2 * (expanded + live_size) + 64 * 1024 * 1024:
                raise ReleaseError("snapshot_invalid")
        candidate.mkdir(mode=0o700)
        for name, expected in files.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise ReleaseError("snapshot_invalid")
            if not name.startswith("data/") and name not in {"config/host.env", "config/env"}:
                raise ReleaseError("snapshot_invalid")
            if name in {"data/robopark.db-wal", "data/robopark.db-shm"}:
                raise ReleaseError("snapshot_invalid")
            hashed = hashlib.sha256()
            target = candidate / name.removeprefix("data/") if name.startswith("data/") else None
            if target:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with archive.open(name) as source:
                destination = target.open("xb") if target else None
                try:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        hashed.update(chunk)
                        if destination:
                            destination.write(chunk)
                    if destination:
                        destination.flush()
                        os.fchmod(destination.fileno(), 0o600)
                        os.fsync(destination.fileno())
                finally:
                    if destination:
                        destination.close()
            if hashed.hexdigest() != expected:
                raise ReleaseError("snapshot_invalid")
    current = paths.current.resolve(strict=True)
    if not paths.current.is_symlink() or current.parent != paths.releases.resolve():
        raise ReleaseError("snapshot_invalid")
    release = verify_directory(current, (paths.etc / "release-public-key.pem").read_bytes())
    _validate_database(candidate / "robopark.db", release["migration_head"])
    owner = (paths.var / "data").stat()
    for item in [candidate, *candidate.rglob("*")]:
        if (item.stat().st_uid, item.stat().st_gid) != (owner.st_uid, owner.st_gid):
            os.chown(item, owner.st_uid, owner.st_gid)
        item.chmod(0o700 if item.is_dir() else 0o600)
        if item.is_dir():
            sync_directory(item)
    sync_directory(root)


def _replace(paths, source, journal):
    identity = journal["request"]["job_id"]
    temporary = paths.var / (".manual-restore-" + identity)
    displaced = paths.var / (".manual-displaced-" + identity)
    if temporary.exists():
        shutil.rmtree(temporary)
    durable_copy_tree(source, temporary)
    data = paths.var / "data"
    if data.is_symlink():
        raise ReleaseError("unsafe_data_path")
    if data.exists():
        if displaced.exists():
            shutil.rmtree(displaced)
        os.replace(data, displaced)
        sync_directory(paths.var)
    os.replace(temporary, data)
    sync_directory(paths.var)


def _ready(paths, runner):
    runner.run(["systemctl", "restart", "robopark.service"], timeout=900)
    if not runner.wait_ready(
        project="robopark", config=paths.state / "current-compose.json", timeout=180
    ):
        raise ReleaseError("restore_failed")


def _publication(paths, runner):
    from .updater import PUBLIC_READY_TIMEOUT, _wait_public_ready

    try:
        runner.run(["systemctl", "restart", "robopark-tuna.service"], timeout=90)
    except Exception:
        return True
    return not _wait_public_ready(paths, runner, timeout=PUBLIC_READY_TIMEOUT)


def _result(journal):
    request = journal["request"]
    return {
        "job_id": request["job_id"],
        "actor_user_id": request["actor_user_id"],
        "kind": "restore",
        "state": "succeeded"
        if journal["phase"] == "succeeded"
        else "maintenance"
        if journal["phase"] == "manual_recovery_required"
        else "failed",
        "error": journal["error"],
        "publication": "degraded" if journal["publication_degraded"] else None,
    }


def _recover(paths, journal, runner):
    from .updater import _maintenance

    if journal["writes_resumed"]:
        # Accepted writes are irreversible, even if publication/housekeeping failed.
        _phase(paths, journal, "starting")
        _ready(paths, runner)
        degraded = _publication(paths, runner)
        _phase(paths, journal, "resuming", publication_degraded=degraded)
        _maintenance(paths, False)
        _phase(paths, journal, "succeeded" if journal["error"] is None else "rolled_back")
        return _result(journal)
    _maintenance(paths, True)
    _phase(paths, journal, "rolling_back", error=journal["error"] or "interrupted")
    runner.run(["systemctl", "stop", "robopark.service"], timeout=120)
    if journal["snapshot_done"]:
        _replace(paths, _root(paths, journal) / "previous", journal)
    _phase(paths, journal, "rollback_starting")
    _ready(paths, runner)
    _phase(paths, journal, "rollback_ready", publication_degraded=_publication(paths, runner))
    _phase(paths, journal, "rollback_resuming", writes_resumed=True)
    _maintenance(paths, False)
    _phase(paths, journal, "rolled_back")
    return _result(journal)


def run_restore(paths, request, runner):
    """Run/recover one authenticated root claim under its caller's host.lock."""
    from .updater import _maintenance

    journal = _load(paths)
    if journal and journal["request"] == request and journal["phase"] in TERMINAL:
        return _result(journal)
    if journal and journal["request"] != request and journal["phase"] not in TERMINAL:
        raise ReleaseError("manual_recovery_required")
    resumed = bool(journal and journal["request"] == request)
    if not resumed:
        journal = {
            "schema": 1,
            "request": request,
            "phase": "validating",
            "snapshot_done": False,
            "writes_resumed": False,
            "error": None,
            "publication_degraded": False,
        }
        _phase(paths, journal, "validating")
    try:
        if resumed:
            return _recover(paths, journal, runner)
        _prepare(paths, journal)
        _phase(paths, journal, "prepared")
        _phase(paths, journal, "maintenance")
        _maintenance(paths, True)
        _phase(paths, journal, "stopping")
        runner.run(["systemctl", "stop", "robopark.service"], timeout=120)
        _phase(paths, journal, "snapshotting")
        root = _root(paths, journal)
        durable_copy_tree(paths.var / "data", root / "previous.partial")
        os.replace(root / "previous.partial", root / "previous")
        sync_directory(root)
        _phase(paths, journal, "snapshotted", snapshot_done=True)
        _phase(paths, journal, "replacing")
        _replace(paths, root / "candidate", journal)
        _phase(paths, journal, "replaced")
        _phase(paths, journal, "starting")
        _ready(paths, runner)
        _phase(paths, journal, "ready", publication_degraded=_publication(paths, runner))
        _phase(paths, journal, "resuming", writes_resumed=True)
        _maintenance(paths, False)
        _phase(paths, journal, "succeeded")
        return _result(journal)
    except Exception:
        if journal["phase"] in {"validating", "prepared"}:
            _phase(paths, journal, "failed", error="snapshot_invalid")
            return _result(journal)
        try:
            journal["error"] = "restore_failed"
            return _recover(paths, journal, runner)
        except Exception:
            _maintenance(paths, True)
            _phase(paths, journal, "manual_recovery_required", error="manual_recovery_required")
            return _result(journal)


def recover_restore(paths, runner):
    """Explicit root retry after automatic attempts are exhausted; never accepts input."""
    from .commands import _finish, _read
    from .state import exclusive_lock
    from .updater import _maintenance

    if paths.root == Path("/") and os.geteuid() != 0:
        return 1
    try:
        with exclusive_lock(paths.ops / "host.lock", blocking=False):
            journal = _load(paths)
            if journal is None or journal["phase"] in TERMINAL:
                return 0
            pending = paths.state / "command-request.json"
            if pending.exists() and _read(pending) != journal["request"]:
                return 75
            result = run_restore(paths, journal["request"], runner)
            if result["state"] == "maintenance":
                return 1
            _finish(paths, journal["request"], result)
            return 0
    except BlockingIOError:
        return 75
    except Exception:
        _maintenance(paths, True)
        return 1
