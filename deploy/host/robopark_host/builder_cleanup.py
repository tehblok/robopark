"""Preview and remove one exact unused record from Robopark's private Buildx builder."""

from __future__ import annotations

import json
import re
import subprocess
import time

from .owned_builder import inspect_owned_builder
from .storage_inventory import MAX_BUILDER_OUTPUT, parse_builder_size

_ID = re.compile(r"[a-z0-9]{1,64}")
_MAX_OUTPUT = MAX_BUILDER_OUTPUT
_MAX_ROWS = 1024
_COMMAND_TIMEOUT = 30


class BuilderCleanupPartialError(ValueError):
    """Prune was attempted; Docker's state must be measured again before retry."""

    def __init__(self, uncertain_target):
        super().__init__("builder_cleanup_partial")
        self.deleted = []
        self.uncertain_target = uncertain_target


def _run(runner, argv, *, maximum, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("builder_inspection_timeout")
    raw = runner.run(argv, timeout=min(maximum, remaining), capture=True)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or len(raw) > _MAX_OUTPUT:
        raise ValueError("invalid_builder_response")
    return raw


def _records(output):
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) > _MAX_ROWS:
        raise ValueError("invalid_builder_response")
    seen = set()
    records = []
    for line in lines:
        row = json.loads(line)
        if not isinstance(row, dict):
            raise TypeError("invalid_builder_response")
        identity, size = row.get("ID"), row.get("Size")
        if (
            not isinstance(identity, str) or _ID.fullmatch(identity) is None
            or identity in seen
            or type(size) not in (int, str)
            or type(row.get("Reclaimable")) is not bool
            or type(row.get("Shared")) is not bool
            or type(row.get("Mutable")) is not bool
            or not isinstance(row.get("Type"), str)
        ):
            raise ValueError("invalid_builder_response")
        amount = parse_builder_size(size)
        if amount is None:
            raise ValueError("invalid_builder_response")
        seen.add(identity)
        records.append({
            "id": identity, "reported_bytes": amount,
            "reclaimable": row["Reclaimable"], "shared": row["Shared"],
            "mutable": row["Mutable"], "type": row["Type"],
        })
    return records


def _read_owned_builder(paths, runner, deadline):
    name = inspect_owned_builder(
        paths, lambda argv: _run(runner, argv, maximum=8, deadline=deadline)
    )
    output = _run(
        runner, ["docker", "buildx", "du", "--builder", name, "--format=json"],
        maximum=8, deadline=deadline,
    )
    return name, _records(output)


def preview_owned_builder_cache(paths, runner):
    """Report one exact private cache record. Size is reported, not freed bytes."""
    result = {
        "blocked": False, "builder": None, "planned": [],
        "total_reported_bytes": 0, "other_candidates": 0,
    }
    try:
        name, records = _read_owned_builder(paths, runner, time.monotonic() + _COMMAND_TIMEOUT)
        candidates = sorted(
            (row for row in records if row["reported_bytes"] > 0
             and row["reclaimable"] and not row["shared"]
             and not row["mutable"] and row["type"] == "regular"),
            key=lambda row: (-row["reported_bytes"], row["id"]),
        )
        result["builder"] = name
        result["other_candidates"] = max(0, len(candidates) - 1)
        if candidates:
            item = candidates[0]
            result["planned"] = [{key: item[key] for key in ("id", "reported_bytes")}]
            result["total_reported_bytes"] = item["reported_bytes"]
    except (OSError, ValueError, TypeError, KeyError, TimeoutError, UnicodeError,
            RecursionError, subprocess.SubprocessError):
        result["blocked"] = True
    return result


def execute_owned_builder_cache_plan(paths, runner, plan):
    """Recheck the single record, then prune by exact ID without broad cache/volume flags."""
    if (
        not isinstance(plan, dict) or set(plan) != {
            "blocked", "builder", "planned", "total_reported_bytes", "other_candidates"
        }
        or plan["blocked"] is not False
        or not isinstance(plan["planned"], list) or len(plan["planned"]) != 1
    ):
        raise ValueError("builder_plan_changed")
    current = preview_owned_builder_cache(paths, runner)
    if current != plan:
        raise ValueError("builder_plan_changed")
    target = plan["planned"][0]
    deadline = time.monotonic() + _COMMAND_TIMEOUT
    try:
        _run(
            runner,
            ["docker", "buildx", "prune", "--builder", plan["builder"],
             "--filter", "id=" + target["id"], "--force"],
            maximum=_COMMAND_TIMEOUT, deadline=deadline,
        )
        name, after = _read_owned_builder(paths, runner, deadline)
        if name != plan["builder"] or any(row["id"] == target["id"] for row in after):
            raise ValueError("builder_record_remains")
    except Exception as exc:
        raise BuilderCleanupPartialError(target) from exc
    return {"deleted": [target], "deleted_count": 1}
