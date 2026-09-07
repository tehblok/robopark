"""Stable parent process: bootstrap installs this outside the replaceable tools.

The launcher waits for the worker to exit before resolving and starting the
successor. It does not reload the running updater in place. Run as
``python3 -B -m robopark_host.launcher [--request path]`` from the bootstrap copy.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .paths import paths_from_environment
from .release import ReleaseError
from .updater import SystemRunner


def launch_update(paths, request, runner):
    environment = {}
    if paths.root != Path("/"):
        environment = {"ROBOPARK_TESTING": "1", "ROBOPARK_ROOT": str(paths.root)}
    worker = (paths.opt / "host-tools").resolve(strict=True) / "robopark"
    failed = False
    try:
        runner.run(
            ["python3", "-B", str(worker), "update", "--request", str(request), "--worker"],
            timeout=14400,
            env=environment,
        )
    except (ReleaseError, OSError):
        failed = True
    # Resolve again only after the child process exited, including failed workers.
    successor = (paths.opt / "host-tools").resolve(strict=True) / "robopark"
    try:
        runner.run(
            ["python3", "-B", str(successor), "update", "--reconcile"],
            timeout=1800,
            env=environment,
        )
    except (ReleaseError, OSError):
        return 1
    return int(failed)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="robopark-launcher")
    parser.add_argument("--request", type=Path)
    arguments = parser.parse_args(argv)
    paths = paths_from_environment()
    request = arguments.request or paths.ops / "inbox/approved.json"
    if not request.exists():
        from .updater import recover_interrupted_update

        result = recover_interrupted_update(paths, SystemRunner())
        return int(result.state == "maintenance")
    return launch_update(paths, request, SystemRunner())


if __name__ == "__main__":
    raise SystemExit(main())
