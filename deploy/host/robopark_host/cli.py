"""Command dispatch for the privileged Robopark host utility."""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .paths import HostPaths, paths_from_environment

Handler = Callable[[HostPaths], int]


def _foundation_handler(paths: HostPaths) -> int:
    """Reserved command handler until the corresponding host workflow is added."""

    del paths
    return 0


COMMAND_HANDLERS: dict[str, Handler] = {
    "status": _foundation_handler,
    "doctor": _foundation_handler,
    "repair": _foundation_handler,
    "update": _foundation_handler,
    "check-update": _foundation_handler,
    "watchdog": _foundation_handler,
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
