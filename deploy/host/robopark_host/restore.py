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
import subprocess
import time
import zipfile
from contextlib import closing, suppress
from pathlib import Path
from urllib.parse import urlsplit
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


def validate_postgres_dump(path, *, expected_head, candidate_dsn, run=None):
    """Validate a custom dump by restoring only into an isolated candidate DB."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise ReleaseError("snapshot_invalid")
    execute = run or (
        lambda argv, **kwargs: subprocess.run(
            argv, check=True, capture_output=True, text=True, timeout=300, **kwargs
        ).stdout
    )
    if not candidate_dsn.startswith("postgresql://") or candidate_dsn.rstrip("/").endswith(
        "/robopark"
    ):
        raise ReleaseError("snapshot_invalid")
    username = urlsplit(candidate_dsn).username
    if username != "robopark":
        raise ReleaseError("snapshot_invalid")
    try:
        listing = str(execute(["pg_restore", "--list", str(path)]))
        if "alembic_version" not in listing:
            raise ValueError()
        execute(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                f"--username={username}",
                f"--dbname={candidate_dsn}",
                str(path),
            ]
        )
        head = str(
            execute(
                [
                    "psql",
                    "--no-psqlrc",
                    "--tuples-only",
                    "--no-align",
                    f"--dbname={candidate_dsn}",
                    "--command=SELECT version_num FROM alembic_version",
                ]
            )
        ).strip()
        if head != expected_head:
            raise ValueError()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ReleaseError("snapshot_invalid") from exc


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
            "database_profile",
        }:
            raise ValueError()
        if journal["schema"] != 2 or journal["phase"] not in PHASES:
            raise ValueError()
        if journal["database_profile"] not in {"postgresql-17", "sqlite-offline-legacy"}:
            raise ValueError()
        if journal["database_profile"] != _database_profile(paths):
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


def _claimed_restore(paths):
    from .commands import _read, _validate

    pending = paths.state / "command-request.json"
    if not pending.exists() and not pending.is_symlink():
        return None
    request = _validate(_read(pending), fresh=False)
    return request if request["kind"] == "restore" else None


def active_restore(paths):
    try:
        journal = _load(paths)
        return bool(_claimed_restore(paths)) or (
            journal is not None and journal["phase"] not in TERMINAL
        )
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
    from .restore_retention import record

    record(paths, journal)
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


def _database_profile(paths):
    """Read the installed database contract; absence defaults to PostgreSQL."""
    target = paths.etc / "host.env"
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise ValueError()
            raw = stream.read(65537).decode("utf-8")
        found = []
        for line in raw.splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and key == "ROBOPARK_DATABASE_PROFILE":
                value = value.strip()
                if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                    value = value[1:-1]
                found.append(value)
        if len(found) > 1 or (found and found[0] not in {"postgresql-17", "sqlite-offline-legacy"}):
            raise ValueError()
        return found[0] if found else "postgresql-17"
    except FileNotFoundError:
        return "postgresql-17"
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReleaseError("snapshot_invalid") from exc


def _database_exec(paths, arguments):
    from .updater import compose

    return compose("robopark", paths.state / "current-compose.json") + [
        "exec",
        "-T",
        "db",
        *arguments,
    ]


def _validate_postgres_candidate(paths, journal, dump, head, runner):
    identity = str(UUID(journal["request"]["job_id"]))
    database = "robopark_restore_" + identity.replace("-", "")
    container_dump = f"/host-restores/{identity}/candidate/{dump.name}"
    try:
        runner.run(_database_exec(paths, ["pg_restore", "--list", container_dump]), timeout=60)
        runner.run(
            _database_exec(paths, ["dropdb", "--if-exists", "-U", "robopark", database]),
            timeout=60,
        )
        runner.run(
            _database_exec(paths, ["createdb", "-U", "robopark", database]), timeout=60
        )
        runner.run(
            _database_exec(
                paths,
                [
                    "pg_restore",
                    "--no-owner",
                    "--no-privileges",
                    "--username=robopark",
                    "--dbname=" + database,
                    container_dump,
                ],
            ),
            timeout=600,
        )
        output = runner.run(
            _database_exec(
                paths,
                [
                    "psql",
                    "-U",
                    "robopark",
                    "--dbname=" + database,
                    "--tuples-only",
                    "--no-align",
                    "--command=SELECT version_num FROM alembic_version",
                ],
            ),
            timeout=30,
            capture=True,
        )
        if output.decode("utf-8", "strict").strip() != head:
            raise ReleaseError("snapshot_invalid")
    finally:
        cleanup = getattr(runner, "run_cleanup", None)
        command = _database_exec(paths, ["dropdb", "--if-exists", "-U", "robopark", database])
        if cleanup:
            cleanup(command, timeout=60)
        else:
            with suppress(Exception):
                runner.run(command, timeout=60)


def _prepare(paths, journal, runner):
    root = _root(paths, journal)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    candidate = root / "candidate"
    if candidate.exists():
        shutil.rmtree(candidate)
    artifact_dir = paths.ops / "artifacts"
    if artifact_dir.is_symlink():
        raise ReleaseError("snapshot_invalid")
    fd = os.open(
        artifact_dir / journal["request"]["artifact"],
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    private = root / "snapshot.zip"
    digest = hashlib.sha256()
    from .retention import require_capacity

    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE:
            raise ReleaseError("snapshot_invalid")
        require_capacity(paths, info.st_size)
        with private.open("wb") as destination:
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
        profile = _database_profile(paths)
        expected_database = (
            "data/robopark.db"
            if profile == "sqlite-offline-legacy"
            else "data/robopark.dump"
        )
        database_members = {"data/robopark.db", "data/robopark.dump"} & set(files)
        if (
            set(names) != {"manifest.json", *files}
            or database_members != {expected_database}
        ):
            raise ReleaseError("snapshot_invalid")
        expanded = sum(item.file_size for item in infos)
        live_size = sum(p.stat().st_size for p in (paths.var / "data").rglob("*") if p.is_file())
        require_capacity(paths, 2 * (expanded + live_size))
        for filesystem in (paths.state, paths.var):
            if shutil.disk_usage(filesystem).free < 2 * (expanded + live_size) + 64 * 1024 * 1024:
                raise ReleaseError("snapshot_invalid")
        candidate.mkdir(mode=0o700)
        for name, expected in files.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise ReleaseError("snapshot_invalid")
            if not name.startswith("data/") and name not in {
                "config/host.env",
                "config/env",
            }:
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
    release = verify_directory(current, None)
    if (candidate / "robopark.dump").is_file():
        _validate_postgres_candidate(
            paths, journal, candidate / "robopark.dump", release["migration_head"], runner
        )
    else:
        _validate_database(candidate / "robopark.db", release["migration_head"])
    owner = (paths.var / "data").stat()
    for item in [candidate, *candidate.rglob("*")]:
        if (item.stat().st_uid, item.stat().st_gid) != (owner.st_uid, owner.st_gid):
            os.chown(item, owner.st_uid, owner.st_gid)
        item.chmod(0o700 if item.is_dir() else 0o600)
        if item.is_dir():
            sync_directory(item)
    sync_directory(root)


def _replace(paths, source, journal, runner=None):
    identity = journal["request"]["job_id"]
    temporary = paths.var / (".manual-restore-" + identity)
    displaced = paths.var / (".manual-displaced-" + identity)
    if temporary.exists():
        shutil.rmtree(temporary)
    durable_copy_tree(source, temporary)
    database_dump = temporary / "robopark.dump"
    if database_dump.is_file():
        database_dump.unlink()
        relative = source.relative_to(_root(paths, journal)).as_posix()
        runner.run(
            _database_exec(
                paths,
                [
                    "pg_restore",
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--no-privileges",
                    "--username=robopark",
                    "--dbname=robopark",
                    f"/host-restores/{journal['request']['job_id']}/{relative}/robopark.dump",
                ],
            ),
            timeout=600,
        )
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
        _replace(paths, _root(paths, journal) / "previous", journal, runner)
    _phase(paths, journal, "rollback_starting")
    _ready(paths, runner)
    _phase(
        paths,
        journal,
        "rollback_ready",
        publication_degraded=_publication(paths, runner),
    )
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
            "schema": 2,
            "request": request,
            "database_profile": _database_profile(paths),
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
        _prepare(paths, journal, runner)
        _phase(paths, journal, "prepared")
        _phase(paths, journal, "maintenance")
        _maintenance(paths, True)
        _phase(paths, journal, "stopping")
        runner.run(["systemctl", "stop", "robopark.service"], timeout=120)
        _phase(paths, journal, "snapshotting")
        root = _root(paths, journal)
        durable_copy_tree(paths.var / "data", root / "previous.partial")
        if (root / "candidate/robopark.dump").is_file():
            runner.run(
                _database_exec(
                    paths,
                    [
                        "pg_dump",
                        "--format=custom",
                        f"--file=/host-restores/{journal['request']['job_id']}/previous.partial/robopark.dump",
                        "--username=robopark",
                        "--dbname=robopark",
                    ],
                ),
                timeout=300,
            )
        os.replace(root / "previous.partial", root / "previous")
        sync_directory(root)
        _phase(paths, journal, "snapshotted", snapshot_done=True)
        _phase(paths, journal, "replacing")
        _replace(paths, root / "candidate", journal, runner)
        _phase(paths, journal, "replaced")
        _phase(paths, journal, "starting")
        _ready(paths, runner)
        _phase(paths, journal, "ready", publication_degraded=_publication(paths, runner))
        _phase(paths, journal, "resuming", writes_resumed=True)
        _maintenance(paths, False)
        _phase(paths, journal, "succeeded")
        from .restore_retention import cleanup_terminal

        cleanup_terminal(paths)
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
            _phase(
                paths,
                journal,
                "manual_recovery_required",
                error="manual_recovery_required",
            )
            return _result(journal)


class _BootRunner:
    """ExecStartPre cannot recursively activate the app or its Tuna dependent.

    Start the same pinned Compose services directly while systemd is still running
    ExecStartPre. Local readiness is checked under maintenance; systemd subsequently
    adopts the healthy containers in ExecStart and starts Tuna through its normal
    After/Requires ordering. Publication is conservatively degraded until that start.
    """

    def __init__(self, paths, runner):
        from .updater import compose

        self.runner = runner
        self.command = compose("robopark", paths.state / "current-compose.json")
        self.database_ready = False

    def run(self, argv, *, timeout):
        if argv == ["systemctl", "stop", "robopark.service"]:
            return self.runner.run(
                self.command + ["stop", "--timeout", "30", "worker", "api", "web"],
                timeout=timeout,
            )
        if argv == ["systemctl", "restart", "robopark.service"]:
            try:
                return self.runner.run(
                    self.command
                    + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "180", "api", "web"],
                    timeout=timeout,
                )
            except Exception:
                self.run(["systemctl", "stop", "robopark.service"], timeout=120)
                raise
        database_prefix = self.command + ["exec", "-T", "db", "pg_restore"]
        if argv[: len(database_prefix)] == database_prefix:
            options = argv[len(database_prefix) :]
            required = {
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "--username=robopark",
                "--dbname=robopark",
            }
            if (
                set(options[:-1]) == required
                and len(options) == len(required) + 1
                and re.fullmatch(
                    r"/host-restores/[0-9a-f-]{36}/previous/robopark\.dump",
                    options[-1],
                )
            ):
                if not self.database_ready:
                    self.runner.run(
                        self.command
                        + [
                            "up",
                            "-d",
                            "--no-build",
                            "--wait",
                            "--wait-timeout",
                            "180",
                            "db",
                        ],
                        timeout=180,
                    )
                    self.database_ready = True
                return self.runner.run(argv, timeout=timeout)
        # A synchronous Tuna start would wait for this very ExecStartPre to finish.
        raise ReleaseError("publication_pending")

    def wait_ready(self, **kwargs):
        ready = self.runner.wait_ready(**kwargs)
        if not ready:
            self.run(["systemctl", "stop", "robopark.service"], timeout=120)
        return ready


def _recover_owned(paths, runner, *, automatic):
    from .commands import _allow_attempt, _finish, _public, _read
    from .updater import _maintenance

    journal = _load(paths)
    request = _claimed_restore(paths)
    if request is None:
        if journal is None or journal["phase"] in TERMINAL:
            return 0
        request = journal["request"]
    if journal and journal["request"] != request and journal["phase"] not in TERMINAL:
        return 75
    pending = paths.state / "command-request.json"
    if pending.exists() and _read(pending) != request:
        return 75
    if journal and journal["request"] == request and journal["phase"] in TERMINAL:
        _finish(paths, request, _result(journal))
        return 0
    if automatic:
        runner = _BootRunner(paths, runner)
    # The shared durable counter includes the original consumer attempt. An
    # interrupted process cannot obtain three fresh attempts on every reboot.
    for _ in range(3 if automatic else 1):
        if automatic and not _allow_attempt(paths, request):
            break
        _maintenance(paths, True)
        result = run_restore(paths, request, runner)
        if result["state"] != "maintenance":
            _maintenance(paths, False)
            _finish(paths, request, result)
            return 0
    if automatic:
        journal = _load(paths)
        if journal is None or journal["request"] != request:
            journal = {
                "schema": 2,
                "request": request,
                "database_profile": _database_profile(paths),
                "snapshot_done": False,
                "writes_resumed": False,
                "publication_degraded": True,
            }
        _phase(paths, journal, "manual_recovery_required", error="manual_recovery_required")
        atomic_write_json(_public(paths) / "command-result.json", _result(journal), mode=0o644)
    _maintenance(paths, True)
    return 1


def recover_restore(paths, runner, *, automatic=False):
    """Recover a private claim; boot retries are bounded, explicit root retry is not."""
    from .state import exclusive_lock
    from .updater import _maintenance

    if paths.root == Path("/") and os.geteuid() != 0:
        return 1
    if automatic and not active_restore(paths):
        return 0
    try:
        with exclusive_lock(paths.host_lock, blocking=False):
            return _recover_owned(paths, runner, automatic=automatic)
    except BlockingIOError:
        # A normal root restore owns host.lock while synchronously restarting the
        # app. Only its durable startup phases permit this ExecStartPre handoff.
        if automatic:
            try:
                journal = _load(paths)
                if (
                    journal
                    and journal["phase"] not in TERMINAL
                    and check_app_start(paths)
                    and _claimed_restore(paths) in (None, journal["request"])
                ):
                    return 0
            except ReleaseError:
                pass
        return 75
    except Exception:
        _maintenance(paths, True)
        return 1
