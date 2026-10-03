"""Bounded disk-failure response that does not read code/Compose from NVMe."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

if __package__:
    from .storage_layout import StorageError, require_storage
else:
    # This directory and both modules are installed root-owned on the OS disk.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from storage_layout import StorageError, require_storage


def _run(argv: list[str]) -> str:
    result = subprocess.run(
        argv,
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=35,
        env={
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C",
            "DOCKER_HOST": "unix:///var/run/docker.sock",
            "DOCKER_CONFIG": "/run/robopark-storage-docker-config",
        },
    )
    if len(result.stdout) > 65536:
        raise RuntimeError("storage_shutdown_output_invalid")
    return result.stdout


def check_and_stop(root: Path = Path("/")) -> dict:
    try:
        return require_storage(root, check_space=False)
    except StorageError as error:
        reason = error.code
    if root != Path("/"):
        return {"state": "failed", "error": reason, "shutdown": "not_attempted"}
    failed = False
    try:
        ids = _run(["docker", "ps", "-q", "--no-trunc"]).split()
        if len(ids) > 256 or any(
            not re.fullmatch(r"[0-9a-f]{64}", value) for value in ids
        ):
            raise RuntimeError("storage_shutdown_output_invalid")
        if ids:
            _run(["docker", "stop", "--time", "20", *ids])
    except (OSError, subprocess.SubprocessError, RuntimeError):
        failed = True
    try:
        # Socket activation must stop too. Explicit container stop above is
        # needed for Docker live-restore; daemon stop alone is insufficient.
        _run(
            [
                "systemctl",
                "stop",
                "--no-block",
                "docker.socket",
                "docker.service",
                "containerd.service",
            ]
        )
    except (OSError, subprocess.SubprocessError, RuntimeError):
        failed = True
    return {
        "state": "failed",
        "error": reason,
        "shutdown": "incomplete" if failed else "requested",
    }


if __name__ == "__main__":
    if os.geteuid() != 0:
        print(json.dumps({"state": "failed", "error": "root_required"}))
        raise SystemExit(2)
    result = check_and_stop()
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(2 if result.get("state") == "failed" else 0)
