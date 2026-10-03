"""Generate secret-free diagnostics bundles from explicit operational inputs."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

from .checks import DiagnosticReport, Runner, execute
from .compose import compose_command, parse_compose_services, safe_compose_services
from .doctor import _release_metadata
from .paths import HostPaths
from .redaction import redact

_UNITS = ("robopark.service", "robopark-tuna.service", "robopark-updater.service")
_UPDATE_FIELDS = {
    "journal": ("job_id", "phase", "error", "candidate"),
    "result": ("job_id", "ok", "error"),
    "status": ("job_id", "state", "phase", "error", "publication"),
}
_OTA_PHASES = frozenset(
    {
        "accepted",
        "verified",
        "snapshot_done",
        "staged",
        "migration_started",
        "migration_done",
        "cutover_started",
        "health_checked",
        "published",
        "rolled_back",
        "failed",
    }
)
_OTA_ERRORS = frozenset(
    {
        "ota_admission_failed",
        "ota_cache_missing",
        "ota_current_version_unknown",
        "ota_cutover_failed",
        "ota_downgrade_forbidden",
        "ota_hash_mismatch",
        "ota_health_check_failed",
        "ota_incompatible",
        "ota_insufficient_space",
        "ota_interrupted",
        "ota_invalid_container",
        "ota_manifest_invalid",
        "ota_migrate_failed",
        "ota_publish_failed",
        "ota_rollback_failed",
        "ota_snapshot_failed",
        "ota_stage_failed",
        "ota_unsafe_cache",
        "ota_unsafe_current",
        "ota_unsafe_upload",
        "ota_version_mismatch",
    }
)
_OTA_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,150}$")


def _journal_entries(output: str, unit: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in output.splitlines()[:200]:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        timestamp = event.get("__REALTIME_TIMESTAMP", "")
        priority = event.get("PRIORITY", "")
        event_id = event.get("MESSAGE_ID", "")
        if not isinstance(timestamp, str) or not re.fullmatch(
            r"[0-9]{1,20}", timestamp
        ):
            continue
        if priority not in tuple(str(n) for n in range(8)):
            continue
        if not isinstance(event_id, str) or not re.fullmatch(
            r"(?:[a-f0-9]{32}|robopark\.[a-z.]{1,40})", event_id
        ):
            event_id = ""
        entries.append(
            {
                "timestamp": timestamp,
                "priority": priority,
                "unit": unit,
                "event_id": event_id,
            }
        )
    return entries


def _write_json(archive: zipfile.ZipFile, name: str, value: Any) -> None:
    archive.writestr(
        name,
        json.dumps(redact(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def _read_update_state(path: Path, fields: tuple[str, ...]) -> dict[str, Any]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not info.st_size <= 64 * 1024:
                return {}
            value = json.loads(stream.read(64 * 1024 + 1))
        if not isinstance(value, dict):
            return {}
        return {key: value[key] for key in fields if key in value}
    except (OSError, ValueError, UnicodeError):
        return {}


def _read_ota_state(path: Path, *, journal: bool) -> dict[str, Any]:
    value = _read_update_state(
        path, ("schema", "request", "operation_id", "version", "phase", "error")
    )
    request = value.get("request") if journal else value
    if not isinstance(request, dict):
        return {}
    operation_id = request.get("operation_id")
    version = request.get("version")
    phase = value.get("phase")
    error = value.get("error")
    try:
        canonical_operation_id = str(UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        return {}
    if (
        type(value.get("schema")) is not int
        or value["schema"] != 1
        or operation_id != canonical_operation_id
        or not isinstance(version, str)
        or _OTA_VERSION.fullmatch(version) is None
        or not isinstance(phase, str)
        or phase not in _OTA_PHASES
        or error is not None
        and (not isinstance(error, str) or error not in _OTA_ERRORS)
    ):
        return {}
    return {
        "operation_id": operation_id,
        "version": version,
        "phase": phase,
        "error": error,
    }


def _terminal_lifecycle(paths: HostPaths) -> list[dict[str, Any]]:
    """Export only bounded typed metadata, never descriptor or PTY contents."""
    directory = paths.state / "terminal"
    if directory.is_symlink():
        return []
    reasons = {
        None,
        "closed",
        "revoked",
        "expired",
        "disconnected",
        "lease_expired",
        "idle_timeout",
        "output_overflow",
        "input_overflow",
        "broker_restart",
        "broker_stopped",
        "host_operation_requested",
        "shell_exited",
        "start_failed",
    }
    rows = []
    # The private registry is bounded by the broker; export at most 20 records.
    for path in sorted(directory.glob("*.json"), reverse=True)[:4096]:
        try:
            if str(UUID(path.stem)) != path.stem:
                continue
        except ValueError:
            continue
        row = _read_update_state(path, ("session",)).get("session")
        if not isinstance(row, dict) or row.get("id") != path.stem:
            continue
        reason = row.get("termination_reason")
        if (
            row.get("profile") not in ("maintenance", "root")
            or row.get("state") not in ("active", "detached", "ended")
            or not (reason is None or isinstance(reason, str))
            or reason not in reasons
        ):
            continue
        counts = {key: row.get(key) for key in ("input_bytes", "output_bytes")}
        if any(
            type(value) is not int or not 0 <= value < 2**63
            for value in counts.values()
        ):
            continue
        rows.append(
            {
                "id": path.stem,
                "profile": row["profile"],
                "state": row["state"],
                "termination_reason": reason,
                **counts,
            }
        )
        if len(rows) == 20:
            break
    return rows


def create_diagnostic_bundle(
    paths: HostPaths, report: DiagnosticReport, runner: Runner, destination: Path
) -> Path:
    """Write a ZIP containing only a sanitized report, logs, Compose state and release metadata."""

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=".diagnostics-", suffix=".zip"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            _write_json(archive, "report.json", report.as_dict())
            compose = execute(
                runner, compose_command(paths, ["ps", "--format", "json"])
            )
            services = parse_compose_services(compose.stdout) if compose.ok else None
            _write_json(
                archive, "compose-services.json", safe_compose_services(services or [])
            )
            _write_json(archive, "release-metadata.json", _release_metadata(paths))
            _write_json(archive, "terminal-lifecycle.json", _terminal_lifecycle(paths))
            _write_json(
                archive,
                "update-status.json",
                {
                    "journal": _read_update_state(
                        paths.state / "updater-journal.json", _UPDATE_FIELDS["journal"]
                    ),
                    "result": _read_update_state(
                        paths.ops / "public/rebuild.result", _UPDATE_FIELDS["result"]
                    ),
                    "status": _read_update_state(
                        paths.ops / "public/host-status.json", _UPDATE_FIELDS["status"]
                    ),
                    "ota_journal": _read_ota_state(
                        paths.state / "ota-update-journal.json", journal=True
                    ),
                    "ota_status": _read_ota_state(
                        paths.ops / "public/ota-status.json", journal=False
                    ),
                },
            )
            for unit in _UNITS:
                journal = execute(
                    runner,
                    [
                        "journalctl",
                        "--no-pager",
                        "--output=json",
                        "--lines=200",
                        "-u",
                        unit,
                    ],
                )
                _write_json(
                    archive,
                    f"journal/{unit}.json",
                    {
                        "unit": unit,
                        "available": journal.ok,
                        "line_limit": 200,
                        "entries": _journal_entries(journal.stdout, unit),
                    },
                )
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
    return target
