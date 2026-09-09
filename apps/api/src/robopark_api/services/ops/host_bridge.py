"""File-only installed-host bridge. Never executes commands or writes root state."""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from robopark_api.ops_schemas import (
    AvailableReleaseOut,
    AvailableUpdateOut,
    SystemHealthOut,
    UpdateInspectionOut,
    public_checks,
    public_result,
)
from robopark_api.services.ops.archives import KIND_RELEASE, KIND_SNAPSHOT, inspect_archive
from robopark_api.services.ops.jobs import (
    ACTIVE_STATES,
    STATE_RUNNING,
    JobConflict,
    _save_job_unlocked,
    ensure_ops_dir,
    load_job,
    new_job,
    require_idle,
)
from robopark_api.services.ops.maintenance import host_marker_active


class BridgeError(ValueError):
    pass


UPDATE_PROGRESS_PERCENT = {
    "validating": 5,
    "verified": 5,
    "unpacking": 10,
    "unpacked": 15,
    "building": 25,
    "built": 40,
    "testing": 42,
    "tested": 44,
    "smoking": 45,
    "smoked": 55,
    "maintenance": 56,
    "stopping": 58,
    "snapshotting": 60,
    "snapshotted": 65,
    "tools_staging": 66,
    "tools_staged": 68,
    "publishing": 70,
    "published": 74,
    "switching": 76,
    "switched": 80,
    "migrating": 82,
    "migrated": 88,
    "starting": 90,
    "started": 92,
    "activating": 93,
    "activated": 95,
    "health_check": 96,
    "healthy": 98,
    "reconciling": 98,
    "publication": 98,
    "publication_checked": 99,
    "resuming": 99,
    "succeeded": 100,
    "rolling_back": 50,
    "rollback_healthy": 75,
    "rollback_resuming": 90,
    "rolled_back": 100,
    "failed": 100,
    "manual_recovery_required": 100,
}


def update_progress(root: Path, job) -> tuple[str | None, int | None]:
    """Project only the matching update job's allow-listed public host phase."""
    if job.kind != "update" or job.state not in ACTIVE_STATES:
        return None, None
    value = read_json(root / "public/host-status.json")
    phase = value.get("phase")
    if (
        value.get("state") != "updating"
        or value.get("job_id") != job.id
        or type(phase) is not str
        or phase not in UPDATE_PROGRESS_PERCENT
    ):
        return None, None
    return phase, UPDATE_PROGRESS_PERCENT[phase]


def host_root(settings) -> Path:
    raw = settings.ops_host_root
    if not raw:
        raise BridgeError("host_bridge_unavailable")
    root = Path(raw)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise BridgeError("host_bridge_unavailable")
    root = root.resolve()
    ordinary = Path(settings.ops_dir).resolve() if settings.ops_dir else None
    if ordinary and (
        root == ordinary or root.is_relative_to(ordinary) or ordinary.is_relative_to(root)
    ):
        raise BridgeError("host_bridge_unavailable")
    if any(
        (root / part).is_symlink() or not (root / part).is_dir()
        for part in ("inbox", "artifacts", "public")
    ):
        raise BridgeError("host_bridge_unavailable")
    if any((root / part).exists() for part in ("state", "host.lock")):
        raise BridgeError("host_bridge_unavailable")
    return root


def read_json(path: Path, limit=65536):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                return {}
            value = json.loads(stream.read(limit + 1), object_pairs_hook=unique)
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError, RecursionError):
        return {}


def _atomic(path, raw):
    fd, name = tempfile.mkstemp(prefix=".bridge-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        _sync(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _locked(ops):
    paths = ensure_ops_dir(ops)
    with paths["lock"].open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def system_health(root):
    value = read_json(root / "public/system-health.json")
    health = SystemHealthOut()
    if (
        isinstance(value.get("version"), str)
        and len(value["version"]) <= 128
        and re.fullmatch(
            r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
            value["version"],
        )
    ):
        health.version = value["version"]
    if isinstance(value.get("git_sha"), str) and re.fullmatch(r"[a-fA-F0-9]{40}", value["git_sha"]):
        health.git_sha = value["git_sha"]
    health.generated_at = _timestamp(value.get("generated_at"))
    health.overall = (
        value.get("overall") if value.get("overall") in ("ok", "degraded") else "unknown"
    )
    health.checks = public_checks(value.get("checks"))
    update = read_json(root / "public/host-status.json") or value.get("update", {})
    if isinstance(update, dict):
        if update.get("state") == "previous_restored":
            update = {**update, "state": "rolled_back"}
        if update.get("state") in (
            "idle",
            "updating",
            "current_healthy",
            "rolled_back",
            "maintenance",
        ):
            health.update.state = update["state"]
        if update.get("publication") == "degraded":
            health.update.publication = "degraded"
    backup = value.get("last_backup")
    if isinstance(backup, dict):
        if backup.get("status") in ("success", "failed"):
            health.last_backup.status = backup["status"]
        health.last_backup.completed_at = _timestamp(backup.get("completed_at"))
    return health


def release_admission_key(settings, root):
    from .release_signing import projected_admission_key

    anchor = Path(settings.ops_release_public_key_path).read_bytes()
    policy = root / "public/signing-trust.json"
    if policy.exists() or policy.is_symlink():
        try:
            return projected_admission_key(anchor, read_json(policy, 8 * 1024 * 1024))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise BridgeError("host_bridge_unavailable") from exc
    return anchor


MAX_UPLOAD_STORAGE = 2 * 1024**3
MAX_UPLOAD_COUNT = 32
MIN_FREE_STORAGE = 2 * 1024**3


def _admit_upload(root, size):
    # Caller holds API begin.lock, also held by root retention. Admission never
    # deletes artifacts: an open preview or approved job must remain usable.
    total = size
    count = 0
    with os.scandir(root / "artifacts") as entries:
        for entry in entries:
            count += 1
            info = entry.stat(follow_symlinks=False)
            total += info.st_size
            if count >= MAX_UPLOAD_COUNT or total > MAX_UPLOAD_STORAGE:
                raise JobConflict("artifact_storage_full")
    if total > MAX_UPLOAD_STORAGE or shutil.disk_usage(root).free < size + MIN_FREE_STORAGE:
        raise JobConflict("artifact_storage_full")


def inspect_update(settings, ops, root, blob, actor):
    try:
        key = release_admission_key(settings, root)
    except OSError as exc:
        raise BridgeError("host_bridge_unavailable") from exc
    meta = inspect_archive(blob, expected_kind=KIND_RELEASE, public_key=key)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        notes = json.loads(archive.read("manifest.json"))["update_notes"][:8000]
    identity = str(uuid4())
    artifact = f"update-{identity}.zip"
    with _locked(ops):
        _admit_upload(root, len(blob))
        _atomic(root / "artifacts" / artifact, blob)
        directory = ops / "inspections"
        directory.mkdir(exist_ok=True)
        record = {
            "inspection_id": identity,
            "actor_user_id": actor,
            "artifact": artifact,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "created_at": datetime.now(UTC).isoformat(),
            "version": meta.app_version,
            "git_sha": meta.git_sha,
            "migration_head": meta.migration_head,
            "notes": notes,
        }
        _atomic(directory / (identity + ".json"), json.dumps(record).encode())
    return UpdateInspectionOut(**{k: record[k] for k in UpdateInspectionOut.model_fields})


def _definitely_absent(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        pass
    return False


def _dispatch(ops, root, job):
    request = job.extra["host_request"]
    target = root / "inbox/approved.json"
    existing = read_json(target, 4096)
    if existing:
        if existing != request:
            raise JobConflict("host_work_in_progress")
        return
    claim_path = root / "public/command-claim.json"
    claim = read_json(claim_path, 4096)
    if claim.get("job_id") == job.id or (not claim and not _definitely_absent(claim_path)):
        return
    job.extra["host_dispatch"] = "dispatched"
    _save_job_unlocked(ops, job)
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".approved-", dir=target.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w") as stream:
            json.dump(request, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(name, target)
        _sync(target.parent)
    except FileExistsError as exc:
        if read_json(target, 4096) != request:
            raise JobConflict("host_work_in_progress") from exc
    except OSError:
        # Root publishes its durable claim before unlinking the slot. Only
        # definite absence of both permits generic work to become retryable.
        # Update ownership remains irreversible, as required by Task 6.
        if (
            job.kind in {"diagnostics", "repair"}
            and _definitely_absent(target)
            and _definitely_absent(claim_path)
        ):
            job.extra["host_dispatch"] = "pending"
            _save_job_unlocked(ops, job)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def approve_update(settings, ops, root, identity, actor, exempt):
    identity = str(UUID(str(identity)))
    with _locked(ops):
        record = read_json(ops / "inspections" / (identity + ".json"))
        if not record or record.get("actor_user_id") != actor:
            raise BridgeError("inspection_not_found")
        job = load_job(ops)
        if job and job.extra.get("inspection_id") == identity:
            if not record.get("approved_job_id"):
                record["approved_job_id"] = job.id
                _atomic(ops / "inspections" / (identity + ".json"), json.dumps(record).encode())
            if job.state in ACTIVE_STATES:
                _dispatch(ops, root, job)
            return job
        if record.get("approved_job_id"):
            raise JobConflict("inspection_already_approved")
        require_idle(ops)
        require_host_idle(root)
        artifact = f"update-{identity}.zip"
        target = root / "artifacts" / artifact
        if record.get("artifact") != artifact or target.is_symlink() or not target.is_file():
            raise BridgeError("artifact_missing")
        if target.stat().st_size > settings.ops_max_upload_bytes:
            raise BridgeError("archive_too_large")
        blob = target.read_bytes()
        if hashlib.sha256(blob).hexdigest() != record.get("sha256"):
            raise BridgeError("artifact_changed")
        inspect_archive(
            blob,
            expected_kind=KIND_RELEASE,
            public_key=release_admission_key(settings, root),
        )
        job = _new_host_job("update", actor, exempt, artifact)
        job.extra["inspection_id"] = identity
        _save_job_unlocked(ops, job)
        record["approved_job_id"] = job.id
        _atomic(ops / "inspections" / (identity + ".json"), json.dumps(record).encode())
        _dispatch(ops, root, job)
        return job


def require_host_idle(root):
    if (root / "inbox/approved.json").exists():
        raise JobConflict("host_work_in_progress")
    claim = read_json(root / "public/command-claim.json")
    if host_marker_active(root) or claim.get("active") is True:
        raise JobConflict("host_work_in_progress")


def _new_host_job(kind, actor, exempt, artifact=None):
    if type(actor) is not int or not 0 < actor < 2**63:
        raise BridgeError("actor_required")
    job = new_job(kind, exempt_token_hash=exempt)
    job.state = STATE_RUNNING
    job.phase = "awaiting_host"
    request = {"job_id": job.id, "kind": kind, "actor_user_id": actor, "created_at": job.created_at}
    if artifact:
        request["artifact"] = artifact
    job.extra = {"host_updater": True, "host_request": request}
    return job


def enqueue_restore(ops, root, blob, actor, exempt):
    """Approval references immutable bytes; only root may stop writers/replace data."""
    inspect_archive(blob, expected_kind=KIND_SNAPSHOT)
    with _locked(ops):
        _admit_upload(root, len(blob))
        require_idle(ops)
        require_host_idle(root)
        job = _new_host_job("restore", actor, exempt)
        request = job.extra["host_request"]
        request.update(
            artifact="restore-" + job.id + ".zip", sha256=hashlib.sha256(blob).hexdigest()
        )
        _atomic(root / "artifacts" / request["artifact"], blob)
        _save_job_unlocked(ops, job)
        _dispatch(ops, root, job)
        return job


def enqueue_operation(ops, root, kind, actor, exempt):
    if kind not in {"diagnostics", "repair"}:
        raise BridgeError("invalid_command")
    with _locked(ops):
        current = load_job(ops)
        if (
            current
            and current.state in ACTIVE_STATES
            and current.kind == kind
            and current.extra.get("host_request", {}).get("actor_user_id") == actor
        ):
            _dispatch(ops, root, current)
            return current
        require_idle(ops)
        require_host_idle(root)
        job = _new_host_job(kind, actor, exempt)
        _save_job_unlocked(ops, job)
        _dispatch(ops, root, job)
        return job


def reconcile_host_job(ops, root):
    with _locked(ops):
        job = load_job(ops)
        if not job or not job.extra.get("host_updater") or job.state not in ACTIVE_STATES:
            return job
        update = job.kind == "update"
        result = read_json(
            root / ("public/rebuild.result" if update else "public/command-result.json")
        )
        if result.get("job_id") != job.id:
            if job.kind in {"diagnostics", "repair"} and not host_marker_active(root):
                _dispatch(ops, root, job)
            return job
        if update:
            if type(result.get("ok")) is not bool:
                return job
            ok = result["ok"]
        else:
            request = job.extra.get("host_request", {})
            if (
                result.get("kind") != job.kind
                or result.get("actor_user_id") != request.get("actor_user_id")
                or result.get("state") not in ("succeeded", "failed")
            ):
                return job
            ok = result["state"] == "succeeded"
        if host_marker_active(root):
            return job
        if not update:
            job.extra["host_result"] = public_result(result).model_dump(mode="json")
        job.state = "succeeded" if ok else "failed"
        job.phase = "completed" if ok else "failed"
        job.error = None if ok else "host_operation_failed"
        job.log = "Операция на хосте завершена." if ok else "Операция на хосте завершилась ошибкой."
        job.restart_required = False
        if job.kind == "diagnostics" and ok and result.get("artifact") == job.id + ".zip":
            job.artifact_name = job.id + ".zip"
        _save_job_unlocked(ops, job)
        return job


def diagnostic_artifact(root, job):
    if (
        not job
        or job.kind != "diagnostics"
        or job.state != "succeeded"
        or job.artifact_name != job.id + ".zip"
    ):
        return None
    directory = root / "public/artifacts"
    target = directory / job.artifact_name
    if (
        directory.is_symlink()
        or target.is_symlink()
        or not target.is_file()
        or target.resolve().parent != directory.resolve()
    ):
        return None
    return target


def available_update(root):
    value = read_json(root / "public/available-update.json")
    output = AvailableUpdateOut()
    try:
        stamp = _timestamp(value.get("checked_at"))
        if stamp is None or not 0 <= (datetime.now(UTC) - stamp).total_seconds() <= 86400:
            return output
        state = value.get("state")
        if state not in {"available", "up_to_date", "discovery_stale", "disabled", "approved"}:
            return output
        release = None
        if state == "available":
            release = AvailableReleaseOut.model_validate(value.get("release"))
        return AvailableUpdateOut(state=state, checked_at=stamp, release=release)
    except (ValueError, TypeError):
        return output


def approve_github_update(ops, root, release_id, actor, exempt):
    with _locked(ops):
        job = load_job(ops)
        if (
            job
            and job.extra.get("github_release_id") == release_id
            and job.state in ACTIVE_STATES | {"succeeded"}
        ):
            if job.extra.get("host_request", {}).get("actor_user_id") != actor:
                raise BridgeError("github_approval_actor_mismatch")
            if job.state in ACTIVE_STATES:
                _dispatch(ops, root, job)
            return job
        available = available_update(root)
        if available.state != "available" or available.release.release_id != release_id:
            raise BridgeError("github_release_unavailable")
        require_idle(ops)
        require_host_idle(root)
        job = _new_host_job("update", actor, exempt)
        job.extra["github_release_id"] = release_id
        job.extra["host_request"].update(kind="github-update", release_id=release_id)
        _save_job_unlocked(ops, job)
        _dispatch(ops, root, job)
        return job
