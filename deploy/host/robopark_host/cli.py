"""Command dispatch for the privileged Robopark host utility."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from threading import Thread
from typing import Any

from .checks import CommandResult
from .doctor import run_doctor, run_status
from .paths import HostPaths, paths_from_environment
from .repair import DEFAULT_REPAIRS, run_repairs
from .state import HostBusy, host_operation
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
    try:
        with host_operation(paths):
            return _repair_owned(paths)
    except HostBusy:
        _print({"state": "busy", "error": "host_busy"})
        return 75


def _repair_owned(paths: HostPaths) -> int:
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
    _print(
        {
            "consecutive_failures": result.consecutive_failures,
            "restarted": result.restarted,
            "busy": result.busy,
        }
    )
    return 0


def _bootstrap_handler(paths: HostPaths) -> int:
    from .runtime import bootstrap_compose

    try:
        bootstrap_compose(paths)
    except (ValueError, OSError, subprocess.SubprocessError):
        print("Runtime bootstrap failed")
        return 1
    return 0


def _consume_handler(paths: HostPaths) -> int:
    from .commands import consume_commands

    return consume_commands(paths, _system_runner, _Http())


def _check_update_handler(paths: HostPaths) -> int:
    from .github_releases import run_check

    return run_check(paths)


COMMAND_HANDLERS: dict[str, Handler] = {
    "consume": _consume_handler,
    "bootstrap-compose": _bootstrap_handler,
    "status": _status_handler,
    "doctor": _doctor_handler,
    "repair": _repair_handler,
    "update": _foundation_handler,
    "check-update": _check_update_handler,
    "watchdog": _watchdog_handler,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="robopark")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in COMMAND_HANDLERS:
        child = commands.add_parser(command)
        if command == "update":
            child.add_argument("--request", type=Path)
            mode = child.add_mutually_exclusive_group()
            mode.add_argument("--worker", action="store_true")
            mode.add_argument("--reconcile", action="store_true")
            mode.add_argument("--recover", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse one host command and dispatch it with the resolved host layout."""

    values = list(sys.argv[1:] if argv is None else argv)
    if values == ["--self-test"]:
        from .release import INSTALLER_VERSION, version
        from .updater import SystemRunner

        version(INSTALLER_VERSION)
        assert callable(SystemRunner.wait_ready)
        for source in Path(__file__).parent.glob("*.py"):
            compile(source.read_bytes(), str(source), "exec")
        return 0
    arguments = build_parser().parse_args(values)
    paths = paths_from_environment()
    if arguments.command == "update":
        from .launcher import launch_update
        from .release import ReleaseError, UpdateRequest
        from .updater import SystemRunner, apply_release, recover_interrupted_update

        runner = SystemRunner()
        if arguments.reconcile or arguments.recover:
            result = recover_interrupted_update(paths, runner)
            return int(result.state == "maintenance")
        if arguments.request is None and not arguments.worker:
            code = _consume_handler(paths)
            result = recover_interrupted_update(paths, runner)
            return code or int(result.state == "maintenance")
        request_path = arguments.request or paths.ops / "inbox/approved.json"
        if not request_path.exists():
            return 0
        if not arguments.worker:
            return launch_update(paths, request_path, runner)
        try:
            request = UpdateRequest.from_file(request_path)
            result = apply_release(request, paths, runner)
            if result.state == "rejected":
                from .updater import publish_result

                publish_result(
                    paths,
                    {"job_id": request.job_id, "ok": False, "error": result.error},
                )
            if UpdateRequest.from_file(request_path) == request:
                request_path.unlink()
                from .rollback import sync_directory

                sync_directory(request_path.parent)
            return int(result.state not in {"awaiting_reconciliation", "current_healthy"})
        except ReleaseError:
            return 1
    return COMMAND_HANDLERS[arguments.command](paths)
