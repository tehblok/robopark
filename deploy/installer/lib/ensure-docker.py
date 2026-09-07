"""Start Docker and fail within bounded time before any image operation."""

import subprocess
import sys
import time


def ensure_docker(run=subprocess.run, sleep=time.sleep):
    for action in ("enable", "start"):
        run(
            ["systemctl", action, "docker.service"],
            check=True,
            timeout=60,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    for attempt in range(10):
        try:
            run(
                ["docker", "info"],
                check=True,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except (subprocess.SubprocessError, OSError):
            if attempt < 9:
                sleep(1)
    raise RuntimeError("docker_not_ready")


if __name__ == "__main__":
    try:
        ensure_docker()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        print("Docker daemon is unavailable", file=sys.stderr)
        sys.exit(1)
