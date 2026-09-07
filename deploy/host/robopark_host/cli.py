"""Command dispatch for the privileged Robopark host utility."""

import argparse
import json
import subprocess
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from .checks import CommandResult
from .doctor import run_doctor, run_status
from .paths import HostPaths, paths_from_environment
from .repair import DEFAULT_REPAIRS, run_repairs
from .watchdog import run_watchdog

Handler = Callable[[HostPaths], int]


def _foundation_handler(paths: HostPaths) -> int:
    """Reserved command handler until the corresponding host workflow is added."""

    del paths
    return 0


def _system_runner(command: Sequence[str], *, timeout: int, max_output: int) -> CommandResult:
    """Run a host command without shell interpolation and cap collected output."""

    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return CommandResult(returncode=1, stderr=str(error)[:max_output])
    return CommandResult(
        returncode=completed.returncode,
        stdout=(completed.stdout or "")[:max_output],
        stderr=(completed.stderr or "")[:max_output],
    )


class _Http:
    def get(self, url: str, *, timeout: int) -> Any:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - URLs are fixed checks
            return type("HttpResponse", (), {"status": response.status, "headers": dict(response.headers)})()


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _doctor_handler(paths: HostPaths) -> int:
    _print(run_doctor(paths, _system_runner, _Http()).as_dict())
    return 0


def _status_handler(paths: HostPaths) -> int:
    _print(run_status(paths, _system_runner, _Http()))
    return 0


def _repair_handler(paths: HostPaths) -> int:
    report = run_doctor(paths, _system_runner, _Http())
    result = run_repairs(report, DEFAULT_REPAIRS, _system_runner)
    post_check = run_doctor(paths, _system_runner, _Http())
    _print(
        {
            "performed": result.performed,
            "skipped": result.skipped,
            "failed": result.failed,
            "post_check": post_check.as_dict(),
        }
    )
    return 0


def _watchdog_handler(paths: HostPaths) -> int:
    result = run_watchdog(paths, _system_runner, _Http())
    _print({"consecutive_failures": result.consecutive_failures, "restarted": result.restarted})
    return 0


COMMAND_HANDLERS: dict[str, Handler] = {
    "status": _status_handler,
    "doctor": _doctor_handler,
    "repair": _repair_handler,
    "update": _foundation_handler,
    "check-update": _foundation_handler,
    "watchdog": _watchdog_handler,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="robopark")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in COMMAND_HANDLERS:
        commands.add_parser(command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse one host command and dispatch it with the resolved host layout."""

    arguments = build_parser().parse_args(argv)
    return COMMAND_HANDLERS[arguments.command](paths_from_environment())
