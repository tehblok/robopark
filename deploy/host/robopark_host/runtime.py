"""Build bootstrap images once, then publish an immutable, private Compose config.

The installer calls this only after it has authenticated the current release.
OTA owns subsequent replacement/rollback of current-compose.json.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence

from .paths import HostPaths
from .rollback import atomic_symlink
from .state import atomic_write_json


def _run(command: Sequence[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=1800).stdout


def production_config(document, paths, release, image_tag):
    """One container privilege/configuration contract for installation and OTA."""
    document = copy.deepcopy(document)
    document["name"] = "robopark"
    document["x-robopark-release"] = str(release)
    document["services"] = {name: document["services"][name] for name in ("api", "web")}
    document.pop("volumes", None)
    api = document["services"]["api"]
    api["env_file"] = [str(paths.etc / "host.env")]
    api["environment"].pop("UVICORN_WORKERS", None)
    api["environment"].update(
        DATABASE_URL="sqlite:////data/robopark.db",
        REPORT_ATTACHMENTS_DIR="/data/report-attachments",
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
    workers = "2"
    for line in (paths.etc / "host.env").read_text().splitlines():
        if line.startswith("UVICORN_WORKERS="):
            workers = line.partition("=")[2].strip().strip("\"'")
    if workers not in {"2", "4"}:
        raise ValueError("invalid_host_profile")
    for name, service in document["services"].items():
        service["image"] = f"robopark-{name}:{image_tag}"
        service["build"]["context"] = str(release / "apps" / name)
        service["mem_limit"] = ("12g" if workers == "4" else "3g") if name == "api" else "256m"
        service["pids_limit"] = 512
        service.setdefault("ulimits", {})["nofile"] = {"soft": 65536, "hard": 65536}
    return document


def pin_images(document, run):
    """Resolve built tags once; all subsequent starts use immutable image IDs."""
    for service in document["services"].values():
        image = run(["docker", "image", "inspect", "--format", "{{.Id}}", service["image"]]).strip()
        if isinstance(image, bytes):
            image = image.decode("ascii")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError("invalid_image_id")
        service["image"] = image
        service.pop("build", None)


def bootstrap_compose(paths: HostPaths, run: Callable = _run) -> None:
    target = paths.state / "current-compose.json"
    if target.is_symlink():
        resolved = target.resolve(strict=True)
        if resolved.parent != (paths.state / "compose").resolve() or not resolved.is_file():
            raise ValueError("invalid_runtime_config")
        metadata = resolved.stat()
        if metadata.st_mode & 0o777 != 0o600 or metadata.st_uid != os.geteuid():
            raise ValueError("invalid_runtime_config")
        return
    if target.exists():
        raise ValueError("invalid_runtime_config")
    release = paths.current.resolve(strict=True)
    if not release.is_relative_to(paths.releases.resolve()) or not release.is_dir():
        raise ValueError("invalid_release")
    source = release / "deploy/docker-compose.yml"
    command = ["docker", "compose", "--project-name", "robopark", "--file", str(source)]
    previous_env = os.environ.get("HOST_ENV_FILE")
    os.environ["HOST_ENV_FILE"] = str(paths.etc / "host.env")
    try:
        raw = json.loads(run([*command, "config", "--format", "json", "--no-env-resolution"]))
    finally:
        if previous_env is None:
            os.environ.pop("HOST_ENV_FILE", None)
        else:
            os.environ["HOST_ENV_FILE"] = previous_env
    release_id = hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest()
    document = production_config(raw, paths, release, "release-" + release_id)
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
        from .image_retention import record, require_record_capacity

        require_record_capacity(paths)
        run([*build_command, "build", "api", "web"])
        pin_images(document, run)
        record(paths, release, "release-" + release_id, document)
        immutable = paths.state / "compose" / ("bootstrap-" + release_id + ".json")
        atomic_write_json(immutable, document)
        atomic_symlink(immutable, target)
    finally:
        build_config.unlink(missing_ok=True)
