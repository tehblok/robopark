"""Reserve a durable request before any upstream mutation; never replay ambiguity."""

import fcntl
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from robopark_api.collaboration_models import TrackerSubmission
from robopark_api.services import tracker_cache, tracker_claims, tracker_client
from robopark_api.services.tracker_policy import ensure_action_allowed

UNCERTAIN_RETRY_SECONDS = 60


def begin(db, user, key, action, request, payload, token, *, validate=None):
    request_key = request.headers.get("Idempotency-Key")
    if not request_key:
        if validate is not None:
            validate()
        return None, None  # Compatibility for older clients.
    if not 8 <= len(request_key) <= 128:
        raise HTTPException(400, "tracker_submission_key_invalid")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    query = select(TrackerSubmission).where(
        TrackerSubmission.actor_id == user.id,
        TrackerSubmission.issue_key == key,
        TrackerSubmission.action == action,
        TrackerSubmission.request_key == request_key,
    )
    # Results are protected by the current upstream scope, including after a move.
    issue = tracker_client.get_issue(token=token, key=key)
    if issue is None:
        raise HTTPException(404)
    ensure_action_allowed(db, user, issue, action)
    previous = db.scalar(query)
    if previous:
        if (
            previous.state != "succeeded"
            and time.time() - previous.created_at >= UNCERTAIN_RETRY_SECONDS
        ):
            db.delete(previous)
            db.commit()
        else:
            return replay(previous, digest)
    try:
        expected = json.loads(unquote(request.headers.get("X-Tracker-State", "")))
        if not isinstance(expected, dict) or set(expected) != {"status", "status_key", "assignee"}:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(400, "tracker_state_required") from None
    effective_assignee = tracker_claims.local_assignee(db, issue)
    actual = {
        "status": str(issue.get("status") or ""),
        "status_key": str(issue.get("status_key") or ""),
        "assignee": str((effective_assignee or issue.get("assignee") or {}).get("login") or ""),
    }
    if expected != actual:
        tracker_cache.invalidate_issue(key)
        raise HTTPException(409, "tracker_state_conflict")
    if validate is not None:
        validate()
    row = TrackerSubmission(
        actor_id=user.id,
        issue_key=key,
        action=action,
        request_key=request_key,
        payload_hash=digest,
        state="pending",
        created_at=time.time(),
    )
    db.add(row)
    try:
        db.commit()  # Persist intent before issuing the non-transactional upstream write.
    except IntegrityError:
        db.rollback()
        return replay(db.scalar(query), digest)
    return row, None


def replay(row, digest):
    if row.payload_hash != digest:
        raise HTTPException(409, "tracker_submission_payload_conflict")
    if row.state == "succeeded":
        return row, json.loads(row.result_json)
    # Includes pending entries left by process termination.
    raise HTTPException(409, "tracker_submission_uncertain")


def uncertain(db, row):
    if row is not None:
        row.state = "uncertain"
        db.commit()
        raise HTTPException(409, "tracker_submission_uncertain")


def finish(db, row, result):
    if row is not None:
        row.state = "succeeded"
        row.result_json = result.model_dump_json()
        db.commit()
    return result


# OS leases are process-safe on the single SQLite host and disappear on process
# exit. Files contain no user data; the hash prevents path traversal via keys.


@contextmanager
def task_mutation_lease(db, key):
    database = db.get_bind().url.database
    if not database or database == ":memory:":
        raise HTTPException(503, "tracker_mutation_lease_unavailable")
    folder = Path(database).resolve().parent / ".tracker-mutation-locks"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
    with path.open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(409, "tracker_task_busy") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
