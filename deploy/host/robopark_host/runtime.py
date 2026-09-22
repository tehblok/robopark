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
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .capabilities import HostCapabilities, probe_host_capabilities
from .paths import HostPaths
from .rollback import atomic_symlink
from .state import atomic_write_json


def source_compose_environment(paths: HostPaths) -> dict[str, str]:
    """Return only paths needed to render the signed source Compose file."""
    return {
        "HOST_ENV_FILE": str(paths.etc / "host.env"),
        "ROBOPARK_POSTGRES_PASSWORD_FILE": str(paths.etc / "postgres-password"),
        "ROBOPARK_PGPASS_FILE": str(paths.etc / "pgpass"),
        "ROBOPARK_SNAPSHOT_CONFIG_FILE": str(paths.etc / "snapshot.env"),
    }


@dataclass(frozen=True)
class HostProfile:
    """Resource ceilings selected from probes, never from a board name."""

    name: str
    api_workers: int
    api_memory: str
    postgres_memory: str
    postgres_shared_buffers: str
    postgres_max_connections: int


def select_host_profile(*, memory_kib: int, cpu_count: int) -> HostProfile:
    if memory_kib >= 24 * 1024 * 1024 and cpu_count >= 8:
        return HostProfile("orin", 4, "8g", "6g", "2GB", 100)
    return HostProfile("vim4-safe", 2, "2g", "1536m", "512MB", 40)


def probe_host_profile(root: Path = Path("/")) -> HostProfile:
    """Select from the host's real RAM/CPU probes, defaulting conservatively."""
    memory_kib = 0
    cpu_count = 0
    try:
        memory = (root / "proc/meminfo").read_text()
        match = re.search(r"^MemTotal:\s+(\d+) kB", memory, re.MULTILINE)
        memory_kib = int(match.group(1)) if match else 0
    except OSError:
        if root == Path("/"):
            with suppress(OSError, ValueError):
                memory_kib = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 1024
    try:
        cpuinfo = (root / "proc/cpuinfo").read_text()
        cpu_count = len(re.findall(r"^processor\s*:", cpuinfo, re.MULTILINE))
    except OSError:
        if root == Path("/"):
            cpu_count = os.cpu_count() or 0
    return select_host_profile(memory_kib=memory_kib, cpu_count=cpu_count)


def probe_runtime_capabilities(root: Path = Path("/")) -> HostCapabilities:
    """Expose fail-soft acceleration separately from mandatory runtime sizing."""
    return probe_host_capabilities(root)


@dataclass(frozen=True)
class DatabaseProfile:
    """Stable PostgreSQL endpoints and commands without embedding credentials."""

    user: str
    database: str
    host: str = "db"
    port: int = 5432
    password_file: str = "/run/secrets/postgres-password"

    @classmethod
    def from_environment(cls, values: dict[str, str]) -> DatabaseProfile:
        user = values.get("POSTGRES_USER", "robopark")
        database = values.get("POSTGRES_DB", "robopark")
        password_file = values.get(
            "POSTGRES_PASSWORD_FILE", "/run/secrets/postgres-password"
        )
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", user) or not re.fullmatch(
            r"[a-z_][a-z0-9_]{0,62}", database
        ):
            raise ValueError("invalid_database_profile")
        return cls(user=user, database=database, password_file=password_file)

    @property
    def dsn(self) -> str:
        return f"postgresql+psycopg://{self.user}@{self.host}:{self.port}/{self.database}"

    @property
    def libpq_dsn(self) -> str:
        return f"postgresql://{self.user}@{self.host}:{self.port}/{self.database}"

    def dump_command(self, target: Path) -> list[str]:
        return [
            "pg_dump",
            "--format=custom",
            f"--file={target}",
            f"--dbname={self.libpq_dsn}",
        ]

    @property
    def health_command(self) -> list[str]:
        return ["pg_isready", "-U", self.user, "-d", self.database]


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
            "dns error",
            "failed to lookup address information",
            "name resolution",
            "could not resolve host",
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
    # Compose config was rendered under a candidate project. Its generated
    # default network name must not follow the release into production: the
    # running database remains attached to robopark_default during migration.
    if "default" in document.get("networks", {}):
        document["networks"]["default"]["name"] = "robopark_default"
    document["x-robopark-release"] = str(release)
    document["services"].setdefault(
        "db",
        {
            "image": "postgres:17.6-alpine",
            "environment": {
                "POSTGRES_USER": "robopark",
                "POSTGRES_DB": "robopark",
                "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres-password",
            },
            "secrets": ["postgres-password"],
            "volumes": [
                {
                    "type": "volume",
                    "source": "robopark_postgres",
                    "target": "/var/lib/postgresql/data",
                }
            ],
            "ports": [{"host_ip": "127.0.0.1", "published": "5432", "target": 5432}],
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -U robopark -d robopark"]
            },
        },
    )
    document["services"] = {name: document["services"][name] for name in ("db", "api", "web")}
    document["volumes"] = {"robopark_postgres": {}}
    document["secrets"] = {
        "postgres-password": {"file": str(paths.etc / "postgres-password")}
    }
    api = document["services"]["api"]
    api["depends_on"] = {"db": {"condition": "service_healthy"}}
    api["env_file"] = [str(paths.etc / "host.env")]
    api["environment"].pop("UVICORN_WORKERS", None)
    api["environment"].update(
        DATABASE_URL="postgresql+psycopg://robopark@db:5432/robopark",
        PGPASSFILE="/run/secrets/pgpass",
        REPORT_ATTACHMENTS_DIR="/data/report-attachments",
        LIVE_MERGE_DIR="/data/live-merge",
        STAGED_ATTACHMENTS_DIR="/data/task-attachments",
        OPS_DIR="/ops",
        OPS_HOST_ENV_PATH="",
        OPS_HOST_ROOT="/host-ops",
        OPS_RELEASE_PUBLIC_KEY_PATH="/etc/robopark/release-public-key.pem",
        HOST_DATA_PATH="/data",
        HOST_HEALTH_PATH="/ops/host-health.json",
    )
    api["volumes"] = [
        {
            "type": "bind",
            "source": str(paths.etc / "release-public-key.pem"),
            "target": "/etc/robopark/release-public-key.pem",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": str(paths.etc / "pgpass"),
            "target": "/run/secrets/pgpass",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": str(paths.etc / "snapshot.env"),
            "target": "/run/robopark/snapshot.env",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": str(paths.etc / "snapshot.env"),
            "target": "/host-repo/deploy/host.env",
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
    profile = probe_host_profile(paths.root)
    if int(workers) > profile.api_workers:
        raise ValueError("invalid_host_profile")
    db = document["services"]["db"]
    db["volumes"] = [
        *[
            volume
            for volume in db.get("volumes", [])
            if volume.get("target") == "/var/lib/postgresql/data"
        ],
        {
            "type": "bind",
            "source": str(paths.ops / "rollbacks"),
            "target": "/host-rollbacks",
        },
        {
            "type": "bind",
            "source": str(paths.state / "restores"),
            "target": "/host-restores",
        },
    ]
    db["ports"] = [{"host_ip": "127.0.0.1", "published": "5432", "target": 5432}]
    db["mem_limit"] = profile.postgres_memory
    db["pids_limit"] = 256
    db["command"] = [
        "postgres",
        "-c",
        f"shared_buffers={profile.postgres_shared_buffers}",
        "-c",
        f"max_connections={profile.postgres_max_connections}",
    ]
    for name in ("api", "web"):
        service = document["services"][name]
        service["image"] = f"robopark-{name}:{image_tag}"
        service["build"]["context"] = str(release / "apps" / name)
        service["mem_limit"] = profile.api_memory if name == "api" else "256m"
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
    compose_environment = source_compose_environment(paths)
    previous_environment = {key: os.environ.get(key) for key in compose_environment}
    os.environ.update(compose_environment)
    try:
        _progress(1, "Проверяю конфигурацию Docker Compose")
        raw = json.loads(run([*command, "config", "--format", "json", "--no-env-resolution"]))
    finally:
        for key, previous in previous_environment.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
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
