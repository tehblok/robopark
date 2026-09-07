"""Command dispatch for the privileged Robopark host utility."""

import argparse
import json
import os
import signal
import subprocess
import time
import urllib.request
from collections.abc import Callable, Sequence
from contextlib import suppress
from threading import Thread
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
    """Run a command without a shell while draining but retaining bounded output."""

    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        return CommandResult(returncode=1, stderr=str(error)[:max_output])
    stdout = bytearray()
    stderr = bytearray()

    def drain(stream: Any, retained: bytearray) -> None:
        while chunk := stream.read(4096):
            remaining = max_output - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])

    assert process.stdout is not None and process.stderr is not None
    readers = [
        Thread(target=drain, args=(process.stdout, stdout)),
        Thread(target=drain, args=(process.stderr, stderr)),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))
    while any(reader.is_alive() for reader in readers) and time.monotonic() < deadline:
        for reader in readers:
            reader.join(timeout=min(0.02, max(0, deadline - time.monotonic())))
    timed_out = process.poll() is None or any(reader.is_alive() for reader in readers)
    if timed_out:
        with suppress(OSError):
            os.killpg(process.pid, signal.SIGTERM)
        grace_deadline = time.monotonic() + 0.2
        while any(reader.is_alive() for reader in readers) and time.monotonic() < grace_deadline:
            for reader in readers:
                reader.join(timeout=min(0.02, max(0, grace_deadline - time.monotonic())))
        with suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
        process.stdout.close()
        process.stderr.close()
        for reader in readers:
            reader.join(timeout=0.1)
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=0.1)
        returncode = 124
    else:
        returncode = process.wait()
    return CommandResult(
        returncode=returncode,
        stdout=bytes(stdout).decode("utf-8", errors="replace"),
        stderr=bytes(stderr).decode("utf-8", errors="replace"),
    )


class _Http:
    def get(self, url: str, *, timeout: int) -> Any:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - URLs are fixed checks
            body = response.read(16_385)
            return _HttpResponse(
                response.status, dict(response.headers), body[:16_384], len(body) <= 16_384
            )


class _HttpResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes, complete: bool) -> None:
        self.status = status
        self.headers = headers
        self._body = body
        self._complete = complete

    def json(self) -> Any:
        if not self._complete:
            raise ValueError("response body exceeds diagnostic limit")
        return json.loads(self._body.decode("utf-8"))


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _doctor_handler(paths: HostPaths) -> int:
    report = run_doctor(paths, _system_runner, _Http())
    _print(report.as_dict())
    return 2 if report.failed else 0


def _status_handler(paths: HostPaths) -> int:
    status = run_status(paths, _system_runner, _Http())
    _print(status)
    return 2 if status["failed_check_count"] else 0


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
    return 2 if result.failed or post_check.failed else 0


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
