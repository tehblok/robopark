"""Fail-closed public projection of host release lifecycle state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from robopark_api.ops_schemas import ReleaseStatusOut
from robopark_api.services.ops.host_bridge import read_json


def _date(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def release_status(root: Path, *, now: datetime | None = None) -> ReleaseStatusOut:
    now = now or datetime.now(UTC)
    lifecycle = read_json(root / "public/release-status.json")
    health = read_json(root / "public/system-health.json")
    available = read_json(root / "public/available-update.json")
    retention = read_json(root / "public/retention-status.json")
    released_at = _date(lifecycle.get("released_at"))
    supported_until = _date(lifecycle.get("supported_until"))
    support_status = "unknown"
    if supported_until:
        support_status = (
            "expired"
            if now >= supported_until
            else "ending"
            if now >= supported_until - timedelta(days=30)
            else "supported"
        )
    return ReleaseStatusOut(
        version=lifecycle.get("version") if isinstance(lifecycle.get("version"), str) else health.get("version"),
        build_id=lifecycle.get("build_id") if isinstance(lifecycle.get("build_id"), str) else None,
        git_sha=health.get("git_sha") if isinstance(health.get("git_sha"), str) else None,
        channel=lifecycle.get("channel") if lifecycle.get("channel") in {"stable", "rc", "manual"} else None,
        support_class=lifecycle.get("support_class") if lifecycle.get("support_class") in {"candidate", "standard", "lts"} else None,
        released_at=released_at,
        supported_until=supported_until,
        support_status=support_status,
        operations_blocked=False,
        database_head=lifecycle.get("database_head") if isinstance(lifecycle.get("database_head"), str) else None,
        installer_version=lifecycle.get("installer_version") if isinstance(lifecycle.get("installer_version"), str) else None,
        available_update=available.get("release") if available.get("state") == "available" else None,
        bridges=lifecycle.get("bridges") if isinstance(lifecycle.get("bridges"), list) else [],
        cleanup=retention or None,
    )
