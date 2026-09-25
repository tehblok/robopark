from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from .credentials import TunaConfiguration, seed_command, write_tuna_configuration
from .diagnose import collect_local_diagnostics
from .remove import DockerCli, RemovalPlan, remove_owned_installation
from .verify import VerifiedOta, verify_ota


def _atomic_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise ValueError("unsafe_config_directory")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            os.fchmod(target.fileno(), 0o600)
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_host_configuration(
    root: Path,
    *,
    memory_mb: int,
    cpu_count: int,
    public_origin: str | None = None,
) -> Path:
    profile = "orin" if memory_mb >= 24 * 1024 and cpu_count >= 8 else "vim4-safe"
    workers = "4" if profile == "orin" else "2"
    secret_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    values = {
        "ROBOPARK_ROLE": "host",
        "ROBOPARK_DATABASE_PROFILE": "postgresql-17",
        "ROBOPARK_HOST_PROFILE": profile,
        "ROBOPARK_UPDATE_CHANNEL": "manual",
        "CORS_ORIGINS": public_origin or "",
        "SESSION_COOKIE_NAME": "robopark_session",
        "COOKIE_SECURE": "true",
        "COOKIE_SAMESITE": "lax",
        "OPERATOR_SHARED_PASSWORD": "",
        "SECRET_KEY": secret_key,
        "PASSWORD_MIN_LENGTH": "12",
        "PASSWORD_REQUIRE_COMPLEXITY": "true",
        "LOGIN_MAX_ATTEMPTS": "5",
        "LOGIN_ATTEMPT_WINDOW_SECONDS": "300",
        "LOGIN_LOCKOUT_SECONDS": "900",
        "REGISTER_MAX_ATTEMPTS": "10",
        "IP_GEO_PROVIDER": "off",
        "SEED_USERNAME": "",
        "SEED_ROLE": "royal",
        "DEV_SEED": "false",
        "UVICORN_WORKERS": workers,
    }
    host_env = Path(root) / "etc/robopark/host.env"
    _atomic_private(host_env, "".join(f"{key}={value}\n" for key, value in values.items()))
    return host_env


def _prepare_directory(path: Path, *, mode: int, uid: int, gid: int) -> None:
    if path.is_symlink():
        raise ValueError("unsafe_host_directory")
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    os.chmod(path, mode)
    if os.geteuid() == 0:
        os.chown(path, uid, gid)


def prepare_host_layout(root: Path) -> None:
    root = Path(root)
    production = root.resolve() == Path("/")
    api_uid = 10001 if production else os.geteuid()
    api_gid = 10001 if production else os.getegid()
    root_uid = 0 if production else os.geteuid()
    root_gid = 0 if production else os.getegid()
    var = root / "var/lib/robopark"
    ops = var / "ops"
    for path in (var, ops):
        _prepare_directory(path, mode=0o750, uid=root_uid, gid=api_gid)
    for path in (
        ops / "state",
        ops / "compose",
        ops / "rollbacks",
        ops / "state/compose",
        ops / "state/restores",
    ):
        _prepare_directory(path, mode=0o700, uid=root_uid, gid=root_gid)
    for path in (ops / "inbox", ops / "artifacts", ops / "ota-uploads"):
        _prepare_directory(path, mode=0o700, uid=api_uid, gid=api_gid)
    for path in (var / "data", var / "api-ops"):
        _prepare_directory(path, mode=0o700, uid=api_uid, gid=api_gid)
    _prepare_directory(ops / "public", mode=0o755, uid=root_uid, gid=root_gid)
    for path in (
        var / "diagnostics",
        root / "var/log/robopark",
        root / "run/lock/robopark",
    ):
        _prepare_directory(path, mode=0o700, uid=root_uid, gid=root_gid)


def _replace_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.{secrets.token_hex(8)}")
    temporary.symlink_to(target)
    try:
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def _executable(relative: PurePosixPath) -> bool:
    return relative.suffix == ".sh" or relative.as_posix() in {
        "deploy/host/robopark",
        "deploy/ops-agent.sh",
        "deploy/compose-production.sh",
    }


def extract_release(bundle: Path, *, root: Path = Path("/")) -> Path:
    verified = verify_ota(bundle)
    releases = Path(root) / "opt/robopark/releases"
    if releases.is_symlink():
        raise ValueError("unsafe_release_path")
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / verified.manifest.app_version
    if target.exists() or target.is_symlink():
        raise ValueError("release_already_exists")
    staging = Path(tempfile.mkdtemp(prefix=".install-", dir=releases))
    try:
        with zipfile.ZipFile(bundle) as archive:
            for info in archive.infolist():
                if not info.filename.startswith("release/"):
                    continue
                relative = PurePosixPath(info.filename).relative_to("release")
                destination = staging.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                destination.chmod(0o755 if _executable(relative) else 0o644)
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "format_version": verified.manifest.format_version,
                    "app_version": verified.manifest.app_version,
                    "git_sha": verified.manifest.git_sha,
                    "migration_head": verified.manifest.migration_head,
                    "package_sha256": verified.sha256,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        manifest_path.chmod(0o644)
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


class HostInstallRuntime:
    def __init__(
        self,
        bundle: Path,
        *,
        root: Path = Path("/"),
        tuna: TunaConfiguration | None = None,
    ) -> None:
        self.bundle = Path(bundle).resolve(strict=True)
        self.root = Path(root).resolve()
        self.verified: VerifiedOta = verify_ota(self.bundle)
        self.release = self.root / "opt/robopark/releases" / self.verified.manifest.app_version
        self.etc = self.root / "etc/robopark"
        self.tuna = tuna or TunaConfiguration()

    def _run(
        self, command: list[str], *, check: bool = True, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=check,
            stdin=subprocess.DEVNULL,
            text=True,
            env=env,
        )

    def _compose_prefix(self) -> list[str]:
        return [
            "docker",
            "compose",
            "--project-name",
            "robopark",
            "--file",
            str(self.root / "var/lib/robopark/ops/state/current-compose.json"),
        ]

    def preflight(self) -> None:
        if self.root == Path("/") and os.geteuid() != 0:
            raise PermissionError("root_required")
        if sys.version_info < (3, 10):  # noqa: UP036 -- OTA runs on host Python 3.10+
            raise RuntimeError("python_3_10_required")
        if shutil.which("docker") is None:
            raise RuntimeError("docker_required")
        if self.tuna.enabled and shutil.which("tuna") is None:
            raise RuntimeError("tuna_required")
        usage = shutil.disk_usage(self.root)
        if usage.free < self.verified.manifest.required_free_bytes:
            raise RuntimeError("ota_insufficient_space")
        self._run(["docker", "compose", "version"])

    def stop_and_remove(self) -> None:
        self._run(
            ["systemctl", "stop", *RemovalPlan.for_root(self.root).services],
            check=False,
        )
        docker = DockerCli()
        targets = docker.discover_owned()
        remove_owned_installation(RemovalPlan.for_root(self.root), docker, targets)

    def extract_release(self) -> None:
        self.release = extract_release(self.bundle, root=self.root)

    def configure(self) -> None:
        prepare_host_layout(self.root)
        memory_kib = 0
        meminfo = self.root / "proc/meminfo"
        if meminfo.is_file():
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemTotal:"):
                    memory_kib = int(line.split()[1])
                    break
        host_env = write_host_configuration(
            self.root,
            memory_mb=memory_kib // 1024,
            cpu_count=os.cpu_count() or 1,
            public_origin=self.tuna.public_origin,
        )
        write_tuna_configuration(self.etc, self.tuna)
        self._run(
            [
                sys.executable,
                str(self.release / "deploy/compose_secrets.py"),
                "--directory",
                str(self.etc),
                "--host-env",
                str(host_env),
            ]
        )
        opt = self.root / "opt/robopark"
        _replace_symlink(opt / "current", self.release)
        _replace_symlink(opt / "host-tools", self.release / "deploy/host")
        self._run(
            [
                sys.executable,
                "-I",
                str(self.release / "deploy/host/robopark"),
                "bootstrap-compose",
            ]
        )

    def start_database(self) -> None:
        prefix = self._compose_prefix()
        self._run([*prefix, "up", "-d", "--no-build", "--wait", "db"])

    def migrate(self) -> None:
        self._run(
            [
                *self._compose_prefix(),
                "run",
                "--rm",
                "--no-deps",
                "api",
                "alembic",
                "upgrade",
                "head",
            ],
        )

    def seed_royal(self, credential_path: Path) -> None:
        self._run(
            seed_command(credential_path, compose_prefix=self._compose_prefix()),
        )

    def start_application(self) -> None:
        self._run(
            [
                *self._compose_prefix(),
                "up",
                "-d",
                "--no-build",
                "--wait",
                "api",
                "web",
                "worker",
            ],
        )

    def wait_ready(self) -> None:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8080/api/health/ready", timeout=4
                ) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(1)
        raise RuntimeError("ota_healthcheck_failed")

    def smoke_check(self) -> None:
        with urllib.request.urlopen(
            "http://127.0.0.1:8080/api/health/ready", timeout=5
        ) as response:
            if response.status != 200:
                raise RuntimeError("ota_healthcheck_failed")

    def publish(self) -> None:
        install = self.release / "deploy/installer/lib/install-services.py"
        self._run([sys.executable, str(install), str(self.root)])
        self._run(["systemctl", "daemon-reload"])
        units = (
            "docker.service",
            "robopark.service",
            "robopark-updater.service",
            "robopark-doctor.timer",
            "robopark-watchdog.timer",
            "robopark-commands.path",
        )
        for unit in units:
            self._run(["systemctl", "enable", unit])
        self._run(["systemctl", "start", "robopark.service"])
        self._run(["systemctl", "start", "robopark-watchdog.service"])
        for unit in (
            "robopark-doctor.timer",
            "robopark-watchdog.timer",
            "robopark-commands.path",
        ):
            self._run(["systemctl", "start", unit])
        if self.tuna.enabled:
            self._run(["systemctl", "enable", "--now", "robopark-tuna.service"])
            self._wait_tuna_ready()
        # Doctor publishes the first host-health projection. A degraded
        # optional check must not roll back an otherwise healthy install.
        self._run(["systemctl", "start", "robopark-doctor.service"], check=False)

    def _wait_tuna_ready(self) -> None:
        public_origin = self.tuna.public_origin
        if public_origin is None:
            return
        self._run(["systemctl", "is-active", "robopark-tuna.service"])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"{public_origin}/api/health/ready", timeout=8
                ) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(2)
        raise RuntimeError("tuna_healthcheck_failed")

    def collect_diagnostics(self) -> Path:
        return collect_local_diagnostics(self.root / "var/lib/robopark/diagnostics", root=self.root)
