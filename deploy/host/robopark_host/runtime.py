"""Build bootstrap images once, then publish an immutable, private Compose config.

The installer calls this only after it has authenticated the current release.
OTA owns subsequent replacement/rollback of current-compose.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence

from .paths import HostPaths
from .state import atomic_write_json


def _run(command: Sequence[str]) -> str:
    return subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=1800
    ).stdout


def bootstrap_compose(paths: HostPaths, run: Callable = _run) -> None:
    target = paths.state / "current-compose.json"
    if target.is_symlink():
        raise ValueError("invalid_runtime_config")
    if target.exists():
        return
    release = paths.current.resolve(strict=True)
    if not release.is_relative_to(paths.releases.resolve()) or not release.is_dir():
        raise ValueError("invalid_release")
    source = release / "deploy/docker-compose.yml"
    # --no-env-resolution preserves env_file references without serializing
    # production credentials. Interpolation only needs this public config path.
    command = ["docker", "compose", "--project-name", "robopark", "--file", str(source)]
    previous_env = os.environ.get("HOST_ENV_FILE")
    os.environ["HOST_ENV_FILE"] = str(paths.etc / "host.env")
    try:
        document = json.loads(
            run([*command, "config", "--format", "json", "--no-env-resolution"])
        )
    finally:
        if previous_env is None:
            os.environ.pop("HOST_ENV_FILE", None)
        else:
            os.environ["HOST_ENV_FILE"] = previous_env
    document["name"] = "robopark"
    document["x-robopark-release"] = str(release)
    document["services"] = {name: document["services"][name] for name in ("api", "web")}
    document.pop("volumes", None)
    api = document["services"]["api"]
    api["env_file"] = [str(paths.etc / "host.env")]
    api["environment"].pop("UVICORN_WORKERS", None)
    api["environment"].update(
        OPS_DIR="/ops",
        OPS_HOST_ENV_PATH="",
        OPS_HOST_ROOT="/host-ops",
        OPS_RELEASE_PUBLIC_KEY_PATH="/etc/robopark/release-public-key.pem",
    )
    api["volumes"] = [
        {
            "type": "bind",
            "source": str(paths.etc / "release-public-key.pem"),
            "target": "/etc/robopark/release-public-key.pem",
            "read_only": True,
        },
        {"type": "bind", "source": str(paths.var / "data"), "target": "/data"},
        {"type": "bind", "source": str(paths.var / "api-ops"), "target": "/ops"},
        *[
            {
                "type": "bind",
                "source": str(paths.ops / name),
                "target": "/host-ops/" + name,
                "read_only": name == "public",
            }
            for name in ("inbox", "artifacts", "public")
        ],
    ]
    release_id = hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest()
    for name, service in document["services"].items():
        service["image"] = f"robopark-{name}:release-{release_id}"
        service["build"]["context"] = str(release / "apps" / name)
    build_config = paths.state / "bootstrap-compose.json"
    atomic_write_json(build_config, document)
    try:
        build_command = [
            "docker",
            "compose",
            "--project-name",
            "robopark",
            "--file",
            str(build_config),
        ]
        run([*build_command, "build", "api", "web"])
        for service in document["services"].values():
            image = run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", service["image"]]
            ).strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                raise ValueError("invalid_image_id")
            service["image"] = image
            del service["build"]
        atomic_write_json(target, document)
    finally:
        build_config.unlink(missing_ok=True)
