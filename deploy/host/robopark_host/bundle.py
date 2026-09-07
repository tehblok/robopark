"""Generate secret-free diagnostics bundles from explicit operational inputs."""

from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from .checks import DiagnosticReport, Runner, execute
from .compose import compose_command, parse_compose_services, safe_compose_services
from .doctor import _release_metadata
from .paths import HostPaths
from .redaction import redact

_UNITS = ("robopark.service", "robopark-tuna.service", "robopark-updater.service")


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
        if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9]{1,20}", timestamp):
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
        name, json.dumps(redact(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


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
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write_json(archive, "report.json", report.as_dict())
            compose = execute(runner, compose_command(paths, ["ps", "--format", "json"]))
            services = parse_compose_services(compose.stdout) if compose.ok else None
            _write_json(archive, "compose-services.json", safe_compose_services(services or []))
            _write_json(archive, "release-metadata.json", _release_metadata(paths))
            for unit in _UNITS:
                journal = execute(
                    runner,
                    ["journalctl", "--no-pager", "--output=json", "--lines=200", "-u", unit],
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
