#!/usr/bin/env python3
"""Start a command detached from the controlling terminal.

``nohup`` alone is not enough when Cursor (or another tool) tears down the
whole shell session with SIGTERM. ``start_new_session=True`` is the portable
equivalent of ``setsid(2)`` and keeps uvicorn/vite alive after the launcher exits.
"""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 5:
        print(
            "usage: daemon-run.py <workdir> <logfile> <pidfile> <command...>",
            file=sys.stderr,
        )
        return 64

    workdir, log_file, pid_file, *cmd = sys.argv[1:]
    log_dir = os.path.dirname(log_file)
    pid_dir = os.path.dirname(pid_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    if pid_dir:
        os.makedirs(pid_dir, exist_ok=True)

    with open(log_file, "ab", buffering=0) as log:
        proc = subprocess.Popen(
            cmd,
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=os.environ.copy(),
        )

    with open(pid_file, "w", encoding="utf-8") as handle:
        handle.write(str(proc.pid))

    print(proc.pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
