"""Single in-flight ops job persisted under the ops directory."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

JOB_FILENAME = "job.json"
LOCK_FILENAME = "begin.lock"
ARTIFACTS_DIR = "artifacts"
STAGING_DIR = "staging"
ROLLBACKS_DIR = "rollbacks"
REBUILD_REQUESTED = "rebuild.requested"
REBUILD_RESULT = "rebuild.result"

STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"

KIND_SNAPSHOT = "snapshot"
KIND_RESTORE = "restore"
KIND_UPDATE = "update"

PHASE_AWAITING_REBUILD = "awaiting_rebuild"

ACTIVE_STATES = frozenset({STATE_QUEUED, STATE_RUNNING})


class JobConflict(RuntimeError):
    """Another ops job is already running."""


class JobAborted(RuntimeError):
    """Disk job is already failed; refusing to resurrect it."""


@dataclass
class OpsJob:
    id: str
    kind: str
    state: str
    phase: str
    exempt_token_hash: str
    log: str = ""
    error: str | None = None
    artifact_name: str | None = None
    restart_required: bool = False
    created_at: str = ""
    updated_at: str = ""
    extra: dict = field(default_factory=dict)

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "phase": self.phase,
            "log": self.log,
            "error": self.error,
            "artifact_ready": bool(self.artifact_name) and self.state == STATE_SUCCEEDED,
            "restart_required": self.restart_required,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_job(kind: str, *, exempt_token_hash: str, phase: str = "starting") -> OpsJob:
    stamp = _now()
    return OpsJob(
        id=str(uuid.uuid4()),
        kind=kind,
        state=STATE_QUEUED,
        phase=phase,
        exempt_token_hash=exempt_token_hash,
        created_at=stamp,
        updated_at=stamp,
    )


def ops_paths(ops_dir: Path) -> dict[str, Path]:
    root = ops_dir.resolve()
    return {
        "root": root,
        "job": root / JOB_FILENAME,
        "lock": root / LOCK_FILENAME,
        "artifacts": root / ARTIFACTS_DIR,
        "staging": root / STAGING_DIR,
        "rollbacks": root / ROLLBACKS_DIR,
        "rebuild_requested": root / REBUILD_REQUESTED,
        "rebuild_result": root / REBUILD_RESULT,
    }


def ensure_ops_dir(ops_dir: Path) -> dict[str, Path]:
    paths = ops_paths(ops_dir)
    for key in ("root", "artifacts", "staging", "rollbacks"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def load_job(ops_dir: Path) -> OpsJob | None:
    path = ops_paths(ops_dir)["job"]
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    extra = data.pop("extra", {}) or {}
    known = {f.name for f in OpsJob.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    filtered = {k: v for k, v in data.items() if k in known}
    return OpsJob(**filtered, extra=extra)


def _save_job_unlocked(ops_dir: Path, job: OpsJob) -> None:
    """Write job.json. Caller must hold the exclusive ops lock."""
    paths = ensure_ops_dir(ops_dir)
    disk = load_job(ops_dir)
    if disk is not None and disk.id == job.id and disk.state == STATE_FAILED:
        raise JobAborted(disk.error or "aborted")
    job.updated_at = _now()
    payload = asdict(job)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=paths["root"], suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp).replace(paths["job"])
        directory = os.open(paths["root"], os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_job(ops_dir: Path, job: OpsJob) -> None:
    paths = ensure_ops_dir(ops_dir)
    with paths["lock"].open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        _save_job_unlocked(ops_dir, job)


def append_log(ops_dir: Path, job: OpsJob, line: str) -> None:
    job.log = (job.log + line + "\n") if job.log else (line + "\n")
    save_job(ops_dir, job)


def require_idle(ops_dir: Path) -> None:
    job = load_job(ops_dir)
    if job is not None and job.state in ACTIVE_STATES:
        raise JobConflict("job_in_progress")


def begin_exclusive(ops_dir: Path, kind: str, *, exempt_token_hash: str) -> OpsJob:
    """Atomically claim the single in-flight job slot."""
    paths = ensure_ops_dir(ops_dir)
    with paths["lock"].open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        require_idle(ops_dir)
        job = new_job(kind, exempt_token_hash=exempt_token_hash)
        job.state = STATE_RUNNING
        # Already holding the lock; nested flock deadlocks on Darwin.
        _save_job_unlocked(ops_dir, job)
        return job


def is_maintenance_active(ops_dir: Path, *, ttl_seconds: int | None = None) -> bool:
    expire_stale_job(ops_dir, ttl_seconds=ttl_seconds)
    job = load_job(ops_dir)
    return job is not None and job.state in ACTIVE_STATES


def is_exempt_session(ops_dir: Path, token_hash: str | None) -> bool:
    if not token_hash:
        return False
    job = load_job(ops_dir)
    if job is None or job.state not in ACTIVE_STATES:
        return False
    return job.exempt_token_hash == token_hash


def expire_stale_job(
    ops_dir: Path, *, ttl_seconds: int | None = None, expected_id: str | None = None
) -> OpsJob | None:
    """Fail a job stuck in ACTIVE longer than *ttl_seconds* (default 2h)."""
    from robopark_api.config import get_settings

    ttl = ttl_seconds
    if ttl is None:
        ttl = get_settings().ops_job_ttl_seconds
    if not ttl or ttl <= 0:
        return None
    paths = ensure_ops_dir(ops_dir)
    with paths["lock"].open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        job = load_job(ops_dir)
        if (
            job is None
            or job.state not in ACTIVE_STATES
            or host_has_job(job)
            or (expected_id is not None and job.id != expected_id)
        ):
            return None
        try:
            created = datetime.fromisoformat(job.created_at)
        except ValueError:
            return None
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - created).total_seconds()
        if age < ttl:
            return None
        job.state = STATE_FAILED
        job.phase = "failed"
        job.error = "job_expired"
        job.log = (job.log + "\n" if job.log else "") + "Задание истекло по таймауту."
        _save_job_unlocked(ops_dir, job)
        paths["rebuild_requested"].unlink(missing_ok=True)
        return job


def host_has_job(job: OpsJob) -> bool:
    """Dispatch is irreversible in the API, even before a host claim is observed."""
    return job.extra.get("host_updater") is True and job.extra.get("host_dispatch") in {
        "dispatched",
        "claimed",
    }


def publish_host_approval(ops_dir: Path, job: OpsJob, prepared: Path, approval: Path) -> None:
    """Reserve host ownership durably before making the approval visible.

    A crash between the two commits leaves an active dispatched reservation.
    Task 7 must reconcile it with the host result/claim; it cannot abort or expire it.
    The exact request is retained in job.extra for controlled dispatch recovery.
    """
    paths = ensure_ops_dir(ops_dir)
    with paths["lock"].open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = load_job(ops_dir)
        if current is None or current.id != job.id or current.state not in ACTIVE_STATES:
            raise JobAborted("aborted")
        job.extra["host_dispatch"] = "dispatched"
        _save_job_unlocked(ops_dir, job)
        try:
            os.replace(prepared, approval)
            directory = os.open(approval.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            # Publication may have happened. Never turn this into failed/idle:
            # only a matching host result can relinquish the reservation.
            return


def abort_job(ops_dir: Path, *, error: str = "aborted") -> OpsJob | None:
    """Abort API-owned work; host dispatch requires host-side reconciliation."""
    paths = ensure_ops_dir(ops_dir)
    with paths["lock"].open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        job = load_job(ops_dir)
        if job is None or job.state not in ACTIVE_STATES:
            return None
        if host_has_job(job):
            raise JobConflict("host_update_dispatched")
        job.state = STATE_FAILED
        job.phase = "failed"
        job.error = error
        job.log = (job.log + "\n" if job.log else "") + "Задание прервано."
        _save_job_unlocked(ops_dir, job)
        paths["rebuild_requested"].unlink(missing_ok=True)
        return job
