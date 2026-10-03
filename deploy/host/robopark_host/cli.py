"""Command dispatch for the privileged Robopark host utility."""

from __future__ import annotations

import argparse
import json
import os
import re
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
        with urllib.request.urlopen(url, timeout=timeout) as response:
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
    from .retention import retain_artifacts, retain_storage
    from .storage_compatibility import require_storage_operations
    from .storage_layout import StorageError
    from .updater import SystemRunner

    try:
        require_storage_operations(paths, check_space=False)
    except StorageError:
        pass
    else:
        retain_artifacts(paths)
        retain_storage(paths)
        from .image_retention import scheduled

        scheduled(paths, SystemRunner())
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
    from .operation_capabilities import (
        publish_operation_capabilities,
        publish_operation_context,
    )
    from .storage_compatibility import require_storage_operations

    require_storage_operations(paths)
    effects = _production_typed_effects(paths)
    publish_operation_capabilities(paths, effects)
    publish_operation_context(paths, effects)
    result = run_watchdog(paths, _system_runner, _Http())
    _print(
        {
            "consecutive_failures": result.consecutive_failures,
            "restarted": result.restarted,
            "busy": result.busy,
        }
    )
    return 0


def _backup_handler(paths: HostPaths) -> int:
    from .operational_state import record_backup
    from .scheduled_backup import create_scheduled_backup

    try:
        with host_operation(paths):
            created = create_scheduled_backup(paths)
    except HostBusy:
        _print({"state": "busy", "error": "host_busy"})
        return 75
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Docker stderr and archive data can contain credentials.
        with suppress(OSError, ValueError):
            record_backup(paths, "failed")
        _print({"state": "failed", "error": "scheduled_backup_failed"})
        return 1
    try:
        record_backup(paths, "success")
    except (OSError, ValueError):
        _print({"state": "failed", "error": "backup_receipt_failed"})
        return 1
    _print({"state": "succeeded", "path": str(created)})
    return 0


def _bootstrap_handler(paths: HostPaths) -> int:
    from .runtime import bootstrap_compose, explain_process_failure

    try:
        bootstrap_compose(paths)
    except subprocess.CalledProcessError as error:
        print(
            f"Runtime bootstrap failed: {explain_process_failure(error)} (exit {error.returncode})",
            file=sys.stderr,
        )
        print(
            f"Полный лог сборки: {paths.root / 'var/log/robopark/runtime-bootstrap.log'}",
            file=sys.stderr,
        )
        return 1
    except subprocess.TimeoutExpired:
        print("Runtime bootstrap failed: docker_timeout", file=sys.stderr)
        return 1
    except ValueError as error:
        reason = str(error) if re.fullmatch(r"[a-z0-9_]+", str(error)) else "invalid_runtime_configuration"
        print(f"Runtime bootstrap failed: {reason}", file=sys.stderr)
        return 1
    except OSError:
        print("Runtime bootstrap failed: host_io_error", file=sys.stderr)
        return 1
    return 0


def _production_typed_effects(paths: HostPaths):
    from .commands import (
        SafeProductionTypedHostEffects,
        discover_usb_devices,
    )
    from .ota_update import OtaProductionEffects
    from .updater import SystemRunner

    def devices():
        return discover_usb_devices(paths)

    return SafeProductionTypedHostEffects(
        paths,
        runner=_system_runner,
        http=_Http(),
        device_provider=devices,
        ota_effects=OtaProductionEffects(
            paths,
            SystemRunner(failure_log=paths.root / "var/log/robopark/ota-update.log"),
        ),
    )


def _consume_handler(paths: HostPaths) -> int:
    from .commands import consume_commands

    effects = _production_typed_effects(paths)
    return consume_commands(
        paths,
        _system_runner,
        effects.http,
        typed_effects=effects,
        typed_devices=effects.device_provider,
    )


def _restore_check_handler(paths: HostPaths) -> int:
    from .restore import check_app_start

    return 0 if check_app_start(paths) else 1


def _tuna_start_handler(paths: HostPaths) -> int:
    """Queue enabled ingress from core's post-start without an After= deadlock."""
    del paths
    from robopark_ota.local_update import restart_enabled_tuna

    from .updater import SystemRunner

    state = restart_enabled_tuna(SystemRunner(), wait=False)
    _print({"tuna": state})
    return int(state == "failed")


def _retain_after_terminal_update(paths: HostPaths, state: str) -> None:
    if state not in {"current_healthy", "previous_restored", "rejected"}:
        return
    from .retention import retain_artifacts

    with suppress(OSError, ValueError):
        retain_artifacts(paths)


COMMAND_HANDLERS: dict[str, Handler] = {
    "backup": _backup_handler,
    "consume": _consume_handler,
    "restore-check": _restore_check_handler,
    "restore": _foundation_handler,
    "bootstrap-compose": _bootstrap_handler,
    "status": _status_handler,
    "doctor": _doctor_handler,
    "repair": _repair_handler,
    "update": _foundation_handler,
    "watchdog": _watchdog_handler,
    "tuna-start": _tuna_start_handler,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="robopark")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in COMMAND_HANDLERS:
        child = commands.add_parser(command)
        if command == "restore":
            mode = child.add_mutually_exclusive_group(required=True)
            mode.add_argument("--recover", action="store_true")
            mode.add_argument("--boot-recover", action="store_true")
        if command == "update":
            child.add_argument("--request", type=Path)
            mode = child.add_mutually_exclusive_group()
            mode.add_argument("--worker", action="store_true")
            mode.add_argument("--reconcile", action="store_true")
            mode.add_argument("--recover", action="store_true")
    commands.add_parser("terminal-prepare")
    commands.add_parser("terminal-reconcile")
    commands.add_parser("terminal-setup")
    commands.add_parser("terminal-broker")
    terminal_worker = commands.add_parser("terminal-worker")
    terminal_worker.add_argument("--id", required=True)
    terminal_worker.add_argument("--profile", choices=("maintenance", "root"), required=True)
    recovery = commands.add_parser("recovery-key")
    recovery_commands = recovery.add_subparsers(dest="recovery_key_action", required=True)
    recovery_commands.add_parser("init")
    export = recovery_commands.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    import_key = recovery_commands.add_parser("import")
    import_key.add_argument("--input", type=Path, required=True)
    import_key.add_argument("--confirmation", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse one host command and dispatch it with the resolved host layout."""

    values = list(sys.argv[1:] if argv is None else argv)
    if values == ["--self-test"]:
        from .commands import OperationKind, TypedHostEffects
        from .release import CAPABILITIES, INSTALLER_VERSION
        from .updater import SystemRunner

        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", INSTALLER_VERSION)
        assert "hash-only-ota-v1" in CAPABILITIES
        assert callable(SystemRunner.wait_ready)
        assert OperationKind.OTA_UPDATE.value == "ota-update"
        assert callable(TypedHostEffects.ota_update) and callable(TypedHostEffects.usb_format)
        assert not {"shell", "exec", "command"} & set(COMMAND_HANDLERS)
        for source in Path(__file__).parent.glob("*.py"):
            compile(source.read_bytes(), str(source), "exec")
        return 0
    arguments = build_parser().parse_args(values)
    paths = paths_from_environment()
    if arguments.command not in {"status", "doctor", "terminal-worker"}:
        from .storage_compatibility import require_storage_operations
        from .storage_layout import StorageError

        try:
            require_storage_operations(
                paths,
                check_space=arguments.command not in {"consume", "restore", "update"},
            )
        except StorageError as error:
            _print({"state": "failed", "error": error.code})
            return 2
    if arguments.command in {"terminal-prepare", "terminal-reconcile"}:
        from .terminal_install import (
            prepare_terminal_installation,
            reconcile_terminal_installation,
        )
        from .updater import SystemRunner
        release = paths.current.resolve(strict=True)
        if release.parent != paths.releases.resolve(strict=True):
            raise ValueError("terminal_invalid_release")
        handler = prepare_terminal_installation if arguments.command == "terminal-prepare" else reconcile_terminal_installation
        handler(paths, release, SystemRunner())
        return 0
    if arguments.command == "terminal-setup":
        from .terminal_setup import prepare_terminal_host
        from .updater import SystemRunner
        prepare_terminal_host(
            paths,
            SystemRunner(failure_log=paths.root / "var/log/robopark/terminal-setup.log"),
        )
        return 0
    if arguments.command == "terminal-broker":
        from .terminal_broker import run_broker
        return run_broker(paths)
    if arguments.command == "terminal-worker":
        from .terminal_worker import run_worker
        return run_worker(paths, arguments.id, arguments.profile)
    if arguments.command == "recovery-key":
        from .commands import (
            ensure_backup_recovery_key,
            export_backup_recovery_key,
            import_backup_recovery_key,
        )
        from .release import ReleaseError

        try:
            if arguments.recovery_key_action == "init":
                target = ensure_backup_recovery_key(paths)
                _print({"state": "ready", "path": str(target)})
            elif arguments.recovery_key_action == "export":
                target = export_backup_recovery_key(paths, arguments.output)
                _print({"state": "exported", "path": str(target)})
            else:
                target, previous = import_backup_recovery_key(
                    paths, arguments.input, confirmation=arguments.confirmation,
                )
                _print({
                    "state": "imported", "path": str(target),
                    "previous_path": str(previous) if previous else None,
                })
            return 0
        except ReleaseError as error:
            reason = str(error)
            if not re.fullmatch(r"[a-z0-9_]+", reason):
                reason = "recovery_key_operation_failed"
            _print({"state": "failed", "error": reason})
            return 2
    if arguments.command == "restore":
        from .release import ReleaseError
        from .restore import active_restore, recover_restore
        from .terminal_install import reconcile_terminal_compose
        from .updater import SystemRunner

        # Cache the candidate helper before recovery can switch host-tools back.
        code = recover_restore(paths, SystemRunner(), automatic=arguments.boot_recover)
        if code == 0 and arguments.boot_recover and not active_restore(paths):
            try:
                reconcile_terminal_compose(paths)
            except ReleaseError:
                _print({"state": "failed", "error": "terminal_invalid_compose"})
                return 1
        return code
    if arguments.command == "update":
        from .launcher import launch_update
        from .release import ReleaseError, UpdateRequest
        from .updater import SystemRunner, apply_release, recover_interrupted_update

        runner = SystemRunner()
        runner.failure_log = paths.root / "var/log/robopark/ota-update.log"
        if arguments.reconcile or arguments.recover:
            result = recover_interrupted_update(paths, runner)
            _retain_after_terminal_update(paths, result.state)
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
            _retain_after_terminal_update(paths, result.state)
            return int(result.state not in {"awaiting_reconciliation", "current_healthy"})
        except ReleaseError:
            return 1
    return COMMAND_HANDLERS[arguments.command](paths)
