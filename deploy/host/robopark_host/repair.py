"""Explicit, conservative repair actions for failed host checks."""

from __future__ import annotations

import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .checks import DiagnosticReport, Runner, execute
from .paths import HostPaths

DEFAULT_REPAIRS: dict[str, list[str]] = {
    "restart_docker": ["systemctl", "restart", "docker.service"],
    "restart_app": ["systemctl", "restart", "robopark.service"],
    "restart_tuna": ["systemctl", "restart", "robopark-tuna.service"],
    "daemon_reload": ["systemctl", "daemon-reload"],
}

_FORBIDDEN_TERMS = {
    "database",
    "sqlite",
    "postgres",
    "psql",
    "mysql",
    "drop",
    "truncate",
    "firewall",
    "ufw",
    "iptables",
    "user",
    "passwd",
    "secret",
    "token",
    "volume",
}


@dataclass(frozen=True)
class RepairReport:
    performed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    post_check: DiagnosticReport | None = None


def _allowed_action(action: str, allowlist: Mapping[str, Sequence[str]]) -> list[str] | None:
    command = allowlist.get(action)
    canonical = DEFAULT_REPAIRS.get(action)
    if command is None or canonical is None or list(command) != canonical:
        return None
    words = " ".join(command).casefold()
    if any(term in words for term in _FORBIDDEN_TERMS):
        return None
    return list(command)


def run_repairs(
    report: DiagnosticReport, allowlist: Mapping[str, Sequence[str]], runner: Runner
) -> RepairReport:
    """Execute only canonical repair IDs attached to failed diagnostic checks."""

    performed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    seen: set[str] = set()
    for check in report.checks:
        action = check.repair
        if check.status != "failed" or not action or action in seen:
            if check.status == "failed" and not action:
                skipped.append(check.code)
            continue
        seen.add(action)
        command = _allowed_action(action, allowlist)
        if command is None:
            skipped.append(check.code)
            continue
        if execute(runner, command).ok:
            performed.append(action)
        else:
            failed.append(action)
    return RepairReport(performed=performed, skipped=skipped, failed=failed)


def cleanup_retained(
    paths: HostPaths, *, now: float | None = None, retention_seconds: int = 7 * 86400
) -> list[Path]:
    """Delete expired entries only from the named staging and diagnostics directories."""

    current_time = time.time() if now is None else now
    deleted: list[Path] = []
    for directory in (paths.ops / "staging", paths.var / "diagnostics"):
        if not directory.is_dir():
            continue
        for item in directory.iterdir():
            try:
                if item.is_symlink() or current_time - item.stat().st_mtime < retention_seconds:
                    continue
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
                else:
                    continue
                deleted.append(item)
            except OSError:
                continue
    return deleted
