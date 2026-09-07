"""Bounded readers for the actual host/API operational state producers."""

import json
import os
import stat
from datetime import datetime, timedelta

from .release import UTC, timestamp, unique_object, version
from .state import atomic_write_json


def read_object(path, limit=65536):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                return {}
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                return {}
        value = json.loads(raw, object_pairs_hook=unique_object)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError, RecursionError):
        return {}


def public_version(value):
    try:
        version(value)
        return value
    except (ValueError, TypeError):
        return None


def update_state(paths):
    state = read_object(paths.ops / "public/host-status.json")
    outcome = state.get("state")
    if outcome == "previous_restored":
        outcome = "rolled_back"
    if not isinstance(outcome, str) or outcome not in {
        "idle",
        "updating",
        "current_healthy",
        "rolled_back",
        "maintenance",
    }:
        outcome = "unknown"
    return {
        "state": outcome,
        "publication": "degraded" if state.get("publication") == "degraded" else None,
    }


def record_backup(paths, status, completed_at=None):
    if status not in {"success", "failed"}:
        raise ValueError("invalid_backup_status")
    stamp = timestamp(completed_at or datetime.now(UTC).isoformat())
    atomic_write_json(
        paths.state / "last-backup.json",
        {
            "status": status,
            "completed_at": stamp.astimezone(UTC).isoformat(),
        },
    )


def backup_state(paths):
    candidates = []
    for path in (paths.state / "last-backup.json", paths.var / "api-ops/last-backup.json"):
        # The API receipt is informational, never an authority for restore.
        if path.parent.is_symlink():
            continue
        raw = read_object(path)
        try:
            stamp = timestamp(raw.get("completed_at"))
            if raw.get("status") not in {"success", "failed"} or stamp > datetime.now(
                UTC
            ) + timedelta(minutes=5):
                continue
            candidates.append(
                (
                    stamp,
                    {"status": raw["status"], "completed_at": stamp.astimezone(UTC).isoformat()},
                )
            )
        except (ValueError, TypeError):
            continue
    return max(candidates, key=lambda item: item[0])[1] if candidates else {"status": "unknown"}
