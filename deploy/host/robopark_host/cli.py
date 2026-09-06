"""Command dispatch for the privileged Robopark host utility."""

import argparse
from typing import Callable, Dict, Optional, Sequence

from .paths import HostPaths, paths_from_environment


Handler = Callable[[HostPaths], int]


def _foundation_handler(paths: HostPaths) -> int:
    """Reserved command handler until the corresponding host workflow is added."""

    del paths
    return 0


COMMAND_HANDLERS: Dict[str, Handler] = {
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
        commands.add_parser(command)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse one host command and dispatch it with the resolved host layout."""

    arguments = build_parser().parse_args(argv)
    return COMMAND_HANDLERS[arguments.command](paths_from_environment())
