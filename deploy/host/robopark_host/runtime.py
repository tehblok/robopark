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
import threading
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path

from .paths import HostPaths
from .rollback import atomic_symlink
from .state import atomic_write_json


def _stream_build(command: Sequence[str], log_path: Path | None) -> str:
    """Stream a long build while retaining its output for diagnosis."""

    output_tail: deque[str] = deque(maxlen=500)
    log = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("w", encoding="utf-8")
        os.chmod(log_path, 0o600)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def copy_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            output_tail.append(line)
            if log is not None:
                log.write(line)
                log.flush()

    reader = threading.Thread(target=copy_output, name="robopark-build-output", daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        reader.join(timeout=10)
        if log is not None:
            log.close()
    detail = "".join(output_tail)
    if returncode:
        raise subprocess.CalledProcessError(
            returncode,
            command,
            output=detail,
            stderr=detail,
        )
    return detail


def _run(command: Sequence[str], *, build_log: Path | None = None) -> str:
    if "build" in command:
        return _stream_build(command, build_log)
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=1800).stdout


def explain_process_failure(error: subprocess.CalledProcessError) -> str:
    """Return a stable, non-secret reason instead of exposing command output."""

    detail = "\n".join(
        value for value in (getattr(error, "stdout", ""), getattr(error, "stderr", "")) if value
    ).lower()
    if "no-env-resolution" in detail and ("unknown" in detail or "flag" in detail):
        return "compose_version_unsupported"
    if "no space left" in detail or "disk quota" in detail:
        return "docker_disk_full"
    if any(
        value in detail
        for value in (
            "network is unreachable",
            "temporary failure",
            "connection timed out",
            "tls handshake timeout",
        )
    ):
        return "docker_network_failed"
    if (
        "out of memory" in detail
        or "cannot allocate memory" in detail
        or error.returncode in {137, -9}
    ):
        return "docker_out_of_memory"
    if re.search(r"\berror ts\d+:", detail):
        return "frontend_typescript_failed"
    if any(package in detail for package in ("rollup", "rolldown", "esbuild")) and any(
        marker in detail
        for marker in (
            "cannot find module",
            "unsupported platform",
            "not supported on this platform",
        )
    ):
        return "frontend_arm_dependency_failed"
    return "docker_command_failed"


def _progress(step: int, message: str) -> None:
    print(f"  [Docker {step}/4] {message}", flush=True)


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
    if run is _run:
        runtime_log = paths.root / "var/log/robopark/runtime-bootstrap.log"

        def logged_run(command):
            return _run(command, build_log=runtime_log)

        run = logged_run
    release = paths.current.resolve(strict=True)
    if not release.is_relative_to(paths.releases.resolve()) or not release.is_dir():
        raise ValueError("invalid_release")
    target = paths.state / "current-compose.json"
    if target.is_symlink():
        resolved = target.resolve(strict=True)
        if resolved.parent != (paths.state / "compose").resolve() or not resolved.is_file():
            raise ValueError("invalid_runtime_config")
        metadata = resolved.stat()
        if metadata.st_mode & 0o777 != 0o600 or metadata.st_uid != os.geteuid():
            raise ValueError("invalid_runtime_config")
        if metadata.st_size > 1024 * 1024:
            raise ValueError("invalid_runtime_config")
        try:
            active_config = json.loads(resolved.read_text())
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError("invalid_runtime_config") from error
        if not isinstance(active_config, dict):
            raise ValueError("invalid_runtime_config")
        if active_config.get("x-robopark-release") == str(release):
            return
    if target.exists():
        if not target.is_symlink():
            raise ValueError("invalid_runtime_config")
    source = release / "deploy/docker-compose.yml"
    command = ["docker", "compose", "--project-name", "robopark", "--file", str(source)]
    previous_env = os.environ.get("HOST_ENV_FILE")
    os.environ["HOST_ENV_FILE"] = str(paths.etc / "host.env")
    try:
        _progress(1, "Проверяю конфигурацию Docker Compose")
        raw = json.loads(run([*command, "config", "--format", "json", "--no-env-resolution"]))
    finally:
        if previous_env is None:
            os.environ.pop("HOST_ENV_FILE", None)
        else:
            os.environ["HOST_ENV_FILE"] = previous_env
    release_id = hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest()
    document = production_config(raw, paths, release, "release-" + release_id)
    _progress(2, "Готовлю защищённую конфигурацию контейнеров")
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
        _progress(3, "Собираю API и web; на ARM это может занять несколько минут")
        # Keep peak RAM predictable on the 8 GiB Armbian target. Compose builds
        # independent services concurrently when they are passed together.
        print("    • API", flush=True)
        run([*build_command, "build", "api"])
        print("    • Web", flush=True)
        run([*build_command, "build", "web"])
        _progress(4, "Проверяю и закрепляю собранные образы")
        pin_images(document, run)
        record(paths, release, "release-" + release_id, document)
        immutable = paths.state / "compose" / ("bootstrap-" + release_id + ".json")
        atomic_write_json(immutable, document)
        atomic_symlink(immutable, target)
    finally:
        build_config.unlink(missing_ok=True)
