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
from .doctor import _release_metadata
from .paths import HostPaths
from .redaction import redact

_UNITS = ("robopark.service", "robopark-tuna.service", "robopark-updater.service")
_SECRET_TEXT = re.compile(
    r"(?im)\b([A-Z][A-Z0-9_]*(?:TOKEN|PASSWORD|PASSWD|SECRET|API_KEY|AUTHORIZATION|CREDENTIAL)[A-Z0-9_]*)\s*[:=]\s*\S+"
)


def _safe_text(value: str) -> str:
    return _SECRET_TEXT.sub(lambda match: match.group(1) + "=[REDACTED]", value)[:16_384]


def _write_json(archive: zipfile.ZipFile, name: str, value: Any) -> None:
    archive.writestr(name, json.dumps(redact(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def create_diagnostic_bundle(
    paths: HostPaths, report: DiagnosticReport, runner: Runner, destination: Path
) -> Path:
    """Write a ZIP containing only a sanitized report, logs, Compose state and release metadata."""

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=target.parent, prefix=".diagnostics-", suffix=".zip")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write_json(archive, "report.json", report.as_dict())
            compose = execute(runner, ["docker", "compose", "ps", "--format", "json"])
            archive.writestr("compose-services.json", _safe_text(compose.stdout))
            _write_json(archive, "release-metadata.json", _release_metadata(paths))
            for unit in _UNITS:
                journal = execute(
                    runner,
                    ["journalctl", "--no-pager", "--output=short-iso", "--lines=200", "-u", unit],
                )
                archive.writestr(f"journal/{unit}.log", _safe_text(journal.stdout))
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
    return target
