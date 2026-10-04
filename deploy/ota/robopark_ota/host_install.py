from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import storage
from .credentials import TunaConfiguration, seed_command, write_tuna_configuration
from .diagnose import collect_local_diagnostics
from .model import OtaRequirements
from .remove import DockerCli, RemovalPlan, local_docker_environment
from .verify import VerifiedOta, verify_ota

# Docker's Ubuntu and Debian repository key, checked on 2026-09-27. A rotated
# key must be reviewed before the installer trusts packages signed by it.
_DOCKER_KEY_SHA256 = "1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570"
# Tuna's Debian repository key, checked on 2026-09-27. Review rotations.
_TUNA_KEY_SHA256 = "c9844bf13fb6fab684ae2eacf9c5c01ce1917eeb52321727759195a6536330c0"


def validate_host_platform(root: Path, requirements: OtaRequirements) -> None:
    """Reject an unsupported Linux host before unpacking or changing services."""
    if platform.system() != "Linux":
        raise RuntimeError("linux_required")
    machine = platform.machine().lower()
    machine = {"arm64": "aarch64", "amd64": "x86_64"}.get(machine, machine)
    if machine not in requirements.architectures:
        raise RuntimeError("unsupported_arch")
    release_path = root / "etc/os-release"
    try:
        if release_path.stat().st_size > 64 * 1024:
            raise RuntimeError("unsupported_os")
        release = release_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("unsupported_os") from exc
    fields: dict[str, str] = {}
    for line in release.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"ID", "VERSION_ID"}:
            value = value.strip()
            if len(value) >= 2 and value[0] in {'"', "'"} and value[-1] == value[0]:
                value = value[1:-1]
            fields[key] = value
    os_id = fields.get("ID", "")
    armbian = (
        (root / "etc/armbian-release").is_file()
        and os_id in {"armbian", "ubuntu", "debian"}
    )
    system = "armbian" if armbian else os_id
    if system not in requirements.systems:
        raise RuntimeError("unsupported_os")
    if os_id == "ubuntu":
        version = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", fields.get("VERSION_ID", ""))
        if version is None or tuple(map(int, version.groups())) < (22, 4):
            raise RuntimeError("unsupported_os_release")
    if os_id == "debian" and armbian:
        version = fields.get("VERSION_ID", "")
        if not version.isdigit() or int(version) < 12:
            raise RuntimeError("unsupported_os_release")
    if shutil.which("systemctl") is None or not (root / "run/systemd/system").is_dir():
        raise RuntimeError("systemd_required")
    try:
        meminfo = (root / "proc/meminfo").read_text(encoding="ascii")
        match = re.search(r"^MemTotal:\s*(\d+)\s+kB$", meminfo, re.MULTILINE)
    except (OSError, ValueError) as exc:
        raise RuntimeError("memory_unavailable") from exc
    if match is None:
        raise RuntimeError("memory_unavailable")
    memory_mb = int(match.group(1)) // 1024
    # Nominal 8 GB boards expose less in MemTotal after firmware reservations.
    minimum_mb = min(requirements.memory_profiles_mb) * 7 // 8
    if memory_mb < minimum_mb:
        raise RuntimeError("insufficient_memory")


def _require_install_space(destination: Path, required_bytes: int) -> None:
    receiving = destination
    while not receiving.exists():
        receiving = receiving.parent
    try:
        usage = shutil.disk_usage(receiving)
    except OSError as exc:
        raise RuntimeError("ota_disk_check_failed") from exc
    # Match the reserve required by subsequent OTA admissions.
    reserve = max(required_bytes, 6 * 1024**3, int(usage.total * 0.15))
    if usage.free < reserve:
        raise RuntimeError("ota_insufficient_space")


def _docker_repository_target(root: Path) -> tuple[str, str, str]:
    """Select only Docker's documented base-distribution repositories."""
    try:
        release = (root / "etc/os-release").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("docker_install_unsupported_release") from exc
    fields: dict[str, str] = {}
    for line in release.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"ID", "VERSION_ID"}:
            fields[key] = value.strip().strip('"\'')
    releases = {
        ("ubuntu", "22.04"): "jammy",
        ("ubuntu", "24.04"): "noble",
        ("ubuntu", "26.04"): "resolute",
        ("debian", "12"): "bookworm",
        ("debian", "13"): "trixie",
    }
    distro = fields.get("ID", "")
    codename = releases.get((distro, fields.get("VERSION_ID", "")))
    if distro == "debian" and not (root / "etc/armbian-release").is_file():
        codename = None
    machine = platform.machine().lower()
    architecture = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(machine)
    if codename is None or architecture is None:
        raise RuntimeError("docker_install_unsupported_release")
    return distro, codename, architecture


def _write_apt_repo_file(path: Path, content: bytes, *, error: str) -> None:
    if path.is_symlink() or (path.exists() and path.read_bytes() != content):
        raise RuntimeError(error)
    for parent in (path.parent, path.parent.parent, path.parent.parent.parent):
        if parent.is_symlink():
            raise RuntimeError(error)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if path.exists():
        return
    with path.open("xb") as target:
        target.write(content)
        target.flush()
        os.fsync(target.fileno())
    path.chmod(0o644)


def _reject_conflicting_apt_source(path: Path, marker: bytes, error: str) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(error)
    try:
        if path.stat().st_size > 1024 * 1024:
            raise RuntimeError(error)
        if marker in path.read_bytes():
            raise RuntimeError(error)
    except OSError as exc:
        raise RuntimeError(error) from exc


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
        ops / "docker-config",
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
        root / "var/backups/robopark",
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
        self, command: list[str], *, check: bool = True, env: dict[str, str] | None = None,
        capture_output: bool = False,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if command and command[0] == "docker":
            env = local_docker_environment(env)
        return subprocess.run(
            command,
            check=check,
            stdin=subprocess.DEVNULL,
            text=True,
            env=env,
            capture_output=capture_output,
            timeout=timeout,
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

    def prepare_missing_docker(self) -> None:
        storage.require_storage(self.root)
        if self.root == Path("/"):
            if os.geteuid() != 0:
                raise PermissionError("root_required")
            probe = self._run(
                ["/usr/bin/python3", "-I", "-c", "import cryptography"],
                check=False,
                capture_output=True,
            )
            if probe.returncode != 0:
                apt_env = {
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "DEBIAN_FRONTEND": "noninteractive",
                }
                self._run(["apt-get", "update"], env=apt_env)
                self._run(
                    [
                        "apt-get", "install", "-y", "--no-remove",
                        "--no-install-recommends", "python3-cryptography",
                    ],
                    env=apt_env,
                )
        docker_available = shutil.which("docker") is not None
        source_path = self.root / "etc/apt/sources.list.d/robopark-docker.sources"
        if docker_available and not (source_path.exists() or source_path.is_symlink()):
            return
        self._ensure_empty_robopark_paths()
        if self.root == Path("/") and os.geteuid() != 0:
            raise PermissionError("root_required")
        if sys.version_info < (3, 10):  # noqa: UP036 -- OTA runs on host Python 3.10+
            raise RuntimeError("python_3_10_required")
        validate_host_platform(self.root, self.verified.manifest.requirements)
        if not docker_available:
            self.ensure_empty_host()
        if self.tuna.enabled and shutil.which("tuna") is None:
            raise RuntimeError("tuna_required")
        for destination in (
            self.root,
            self.root / "opt/robopark/releases",
            self.root / "var/lib/robopark",
            self.root / "var/lib/containerd",
        ):
            _require_install_space(destination, self.verified.manifest.required_free_bytes)
        distro, codename, architecture = _docker_repository_target(self.root)
        key_path = self.root / "etc/apt/keyrings/robopark-docker.asc"
        source = (
            "Types: deb\n"
            f"URIs: https://download.docker.com/linux/{distro}\n"
            f"Suites: {codename}\n"
            "Components: stable\n"
            f"Architectures: {architecture}\n"
            "Signed-By: /etc/apt/keyrings/robopark-docker.asc\n"
        ).encode("ascii")
        docker_marker = b"download.docker.com/linux/"
        _reject_conflicting_apt_source(self.root / "etc/apt/sources.list", docker_marker, "docker_repo_conflict")
        source_dir = source_path.parent
        if source_dir.is_dir():
            for existing in source_dir.iterdir():
                if existing == source_path or existing.suffix not in {".list", ".sources"}:
                    continue
                _reject_conflicting_apt_source(existing, docker_marker, "docker_repo_conflict")
        if source_path.is_symlink() or (source_path.exists() and source_path.read_bytes() != source):
            raise RuntimeError("docker_repo_conflict")
        try:
            with urllib.request.urlopen(f"https://download.docker.com/linux/{distro}/gpg", timeout=15) as response:
                key = response.read(64 * 1024 + 1)
        except OSError as exc:
            raise RuntimeError("docker_key_unavailable") from exc
        if (len(key) > 64 * 1024 or not key.startswith(b"-----BEGIN PGP PUBLIC KEY BLOCK-----")
                or b"-----END PGP PUBLIC KEY BLOCK-----" not in key
                or hashlib.sha256(key).hexdigest() != _DOCKER_KEY_SHA256):
            raise RuntimeError("docker_key_invalid")
        _write_apt_repo_file(key_path, key, error="docker_repo_conflict")
        _write_apt_repo_file(source_path, source, error="docker_repo_conflict")
        if docker_available:
            # A prior interrupted apt transaction may have installed the CLI
            # but not its plugins or started the daemon. Inspect exact Docker
            # resources before retrying packages; never prune them here.
            self._run(["systemctl", "enable", "--now", "docker"])
            self.ensure_empty_host()
        apt_env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "DEBIAN_FRONTEND": "noninteractive"}
        self._run(["apt-get", "update"], env=apt_env)
        self._run([
            "apt-get", "install", "-y", "--no-remove", "--no-install-recommends",
            "docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin",
        ], env=apt_env)
        self._run(["systemctl", "enable", "--now", "docker"])

    def prepare_missing_tuna(self) -> None:
        storage.require_storage(self.root)
        if not self.tuna.enabled or shutil.which("tuna") is not None:
            return
        if self.root == Path("/") and os.geteuid() != 0:
            raise PermissionError("root_required")
        if sys.version_info < (3, 10):  # noqa: UP036 -- OTA runs on host Python 3.10+
            raise RuntimeError("python_3_10_required")
        validate_host_platform(self.root, self.verified.manifest.requirements)
        self.ensure_empty_host()
        _require_install_space(self.root, self.verified.manifest.required_free_bytes)
        _, _, architecture = _docker_repository_target(self.root)
        key_path = self.root / "etc/apt/keyrings/robopark-tuna.asc"
        source_path = self.root / "etc/apt/sources.list.d/robopark-tuna.sources"
        source = (
            "Types: deb\n"
            "URIs: https://code.tuna.am/api/packages/tuna/debian\n"
            "Suites: stable\n"
            "Components: main\n"
            f"Architectures: {architecture}\n"
            "Signed-By: /etc/apt/keyrings/robopark-tuna.asc\n"
        ).encode("ascii")
        tuna_marker = b"code.tuna.am/api/packages/tuna/debian"
        _reject_conflicting_apt_source(self.root / "etc/apt/sources.list", tuna_marker, "tuna_repo_conflict")
        if source_path.parent.is_dir():
            for existing in source_path.parent.iterdir():
                if existing != source_path and existing.suffix in {".list", ".sources"}:
                    _reject_conflicting_apt_source(existing, tuna_marker, "tuna_repo_conflict")
        if source_path.is_symlink() or (source_path.exists() and source_path.read_bytes() != source):
            raise RuntimeError("tuna_repo_conflict")
        try:
            with urllib.request.urlopen(
                "https://code.tuna.am/api/packages/tuna/debian/repository.key", timeout=15
            ) as response:
                key = response.read(64 * 1024 + 1)
        except OSError as exc:
            raise RuntimeError("tuna_key_unavailable") from exc
        if (len(key) > 64 * 1024 or not key.startswith(b"-----BEGIN PGP PUBLIC KEY BLOCK-----")
                or b"-----END PGP PUBLIC KEY BLOCK-----" not in key
                or hashlib.sha256(key).hexdigest() != _TUNA_KEY_SHA256):
            raise RuntimeError("tuna_key_invalid")
        _write_apt_repo_file(key_path, key, error="tuna_repo_conflict")
        _write_apt_repo_file(source_path, source, error="tuna_repo_conflict")
        apt_env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "DEBIAN_FRONTEND": "noninteractive"}
        self._run(["apt-get", "update"], env=apt_env)
        self._run([
            "apt-get", "install", "-y", "--no-remove", "--no-install-recommends", "tuna-cli",
        ], env=apt_env)

    def basic_preflight(self) -> None:
        """Reject unsupported hosts and low disk space before asking for secrets."""
        if self.root == Path("/") and os.geteuid() != 0:
            raise PermissionError("root_required")
        storage.require_storage(self.root)
        if sys.version_info < (3, 10):  # noqa: UP036 -- OTA runs on host Python 3.10+
            raise RuntimeError("python_3_10_required")
        if self.root == Path("/"):
            validate_host_platform(self.root, self.verified.manifest.requirements)
        for destination in (
            self.root,
            self.root / "opt/robopark/releases",
            self.root / "var/lib/robopark",
            self.root / "var/lib/containerd",
        ):
            _require_install_space(destination, self.verified.manifest.required_free_bytes)

    def preflight(self) -> None:
        storage_status = storage.require_storage(self.root)
        if self.root == Path("/") and os.geteuid() != 0:
            raise PermissionError("root_required")
        if sys.version_info < (3, 10):  # noqa: UP036 -- OTA runs on host Python 3.10+
            raise RuntimeError("python_3_10_required")
        if self.root == Path("/"):
            validate_host_platform(self.root, self.verified.manifest.requirements)
        if shutil.which("docker") is None:
            raise RuntimeError("docker_required")
        if self.tuna.enabled and shutil.which("tuna") is None:
            raise RuntimeError("tuna_required")
        for destination in (
            self.root,
            self.root / "opt/robopark/releases",
            self.root / "var/lib/robopark",
            self.root / "var/lib/containerd",
        ):
            _require_install_space(destination, self.verified.manifest.required_free_bytes)
        self._run(["docker", "compose", "version"])
        try:
            version_output = self._run(
                ["docker", "buildx", "version"], capture_output=True
            ).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError("buildx_unavailable") from exc
        match = re.search(r"\bv(\d+)\.(\d+)\.\d+\b", version_output or "")
        if match is None or tuple(map(int, match.groups())) < (0, 14):
            raise RuntimeError("buildx_version_unsupported")
        for command, flag, error in (
            (["docker", "compose", "build", "--help"], "--builder", "compose_builder_unsupported"),
            (["docker", "buildx", "prune", "--help"], "--max-used-space", "buildx_budget_unsupported"),
        ):
            try:
                help_output = self._run(command, capture_output=True).stdout
            except (OSError, subprocess.CalledProcessError) as exc:
                raise RuntimeError(error) from exc
            if not isinstance(help_output, str) or flag not in help_output:
                raise RuntimeError(error)
        try:
            self._run(
                ["docker", "info"],
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("docker_daemon_unavailable") from exc
        try:
            docker_root_output = self._run(
                ["docker", "info", "--format", "{{.DockerRootDir}}"],
                capture_output=True,
                timeout=15,
            ).stdout
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("docker_root_unavailable") from exc
        if (
            not isinstance(docker_root_output, str)
            or len(docker_root_output) > 4096
            or "\n" in docker_root_output.strip()
        ):
            raise RuntimeError("docker_root_unavailable")
        docker_root = Path(docker_root_output.strip())
        if not docker_root.is_absolute():
            raise RuntimeError("docker_root_unavailable")
        docker_root_path = docker_root
        if storage_status.get("state") != "unmanaged":
            if docker_root != Path("/var/lib/docker"):
                raise RuntimeError("storage_custom_container_root")
            docker_root_path = self.root / "var/lib/docker"
        if not docker_root_path.is_dir():
            raise RuntimeError("docker_root_unavailable")
        _require_install_space(
            docker_root_path, self.verified.manifest.required_free_bytes
        )

    def _ensure_empty_robopark_paths(self) -> None:
        status = storage.require_storage(self.root)
        plan = RemovalPlan.for_root(self.root)
        blocked = [path for path in plan.paths if (path.exists() or path.is_symlink())
                   and not storage.prepared_empty_target(self.root, path, status)]
        if blocked:
            raise RuntimeError(
                "clean_install_requires_empty_host: " + ", ".join(str(path) for path in blocked)
            )

    def ensure_empty_host(self) -> None:
        self._ensure_empty_robopark_paths()
        if shutil.which("docker") is None:
            for directory in (self.root / "var/lib/docker", self.root / "var/lib/containerd"):
                if directory.is_symlink() or (directory.exists() and (not directory.is_dir() or any(directory.iterdir()))):
                    raise RuntimeError("docker_state_unknown")
            return
        try:
            targets = DockerCli().discover_owned()
        except (OSError, subprocess.CalledProcessError):
            # The Docker CLI may be installed while the local daemon is stopped.
            # Start it only after confirming that Robopark's filesystem paths
            # are absent, then inspect ownership before touching any resources.
            try:
                self._run(["systemctl", "enable", "--now", "docker"])
                targets = DockerCli().discover_owned()
            except (OSError, subprocess.CalledProcessError) as error:
                raise RuntimeError("docker_daemon_unavailable") from error
        if any((targets.containers, targets.volumes, targets.networks, targets.images)):
            names = (*targets.containers, *targets.volumes, *targets.networks, *targets.images)
            raise RuntimeError("clean_install_requires_empty_host: " + ", ".join(names))

    def extract_release(self) -> None:
        storage.require_storage(self.root)
        self.release = extract_release(self.bundle, root=self.root)

    def configure(self) -> None:
        storage.require_storage(self.root)
        storage.refresh_storage_guard(self.root, self.release)
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
        if (self.release / "deploy/systemd/robopark-terminal-setup.service").is_file():
            install = self.release / "deploy/installer/lib/install-services.py"
            self._run([sys.executable, str(install), str(self.root)])
            self._run([sys.executable, "-I", str(self.release / "deploy/host/robopark"), "terminal-prepare"])
        self._run(
            [
                sys.executable,
                "-I",
                str(self.release / "deploy/host/robopark"),
                "bootstrap-compose",
            ]
        )

    def start_database(self) -> None:
        storage.require_storage(self.root)
        prefix = self._compose_prefix()
        self._run([*prefix, "up", "-d", "--no-build", "--wait", "db"])

    def migrate(self) -> None:
        storage.require_storage(self.root)
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
        storage.require_storage(self.root)
        self._run(
            seed_command(credential_path, compose_prefix=self._compose_prefix()),
        )

    def start_application(self) -> None:
        storage.require_storage(self.root)
        self._run(
            [
                *self._compose_prefix(),
                "up",
                "-d",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "180",
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
        storage.require_storage(self.root)
        install = self.release / "deploy/installer/lib/install-services.py"
        self._run([sys.executable, str(install), str(self.root)])
        self._run([
            sys.executable,
            "-I",
            str(self.release / "deploy/host/robopark"),
            "recovery-key",
            "init",
        ])
        self._run(["systemctl", "daemon-reload"])
        units = (
            "docker.service",
            "robopark.service",
            "robopark-updater.service",
            "robopark-doctor.timer",
            "robopark-backup.timer",
            "robopark-watchdog.timer",
            "robopark-commands.path",
        )
        for unit in units:
            self._run(["systemctl", "enable", unit])
        self._run(["systemctl", "start", "robopark.service"])
        if (self.release / "deploy/systemd/robopark-terminal-setup.service").is_file():
            self._run([sys.executable, "-I", str(self.release / "deploy/host/robopark"), "terminal-reconcile"])
        self._run(["systemctl", "start", "robopark-watchdog.service"])
        for unit in (
            "robopark-doctor.timer",
            "robopark-backup.timer",
            "robopark-watchdog.timer",
            "robopark-commands.path",
        ):
            self._run(["systemctl", "start", unit])
        if self.tuna.enabled:
            self._run(["systemctl", "enable", "--now", "robopark-tuna.service"])
            self._wait_tuna_ready()
        # A degraded check may make the oneshot service exit nonzero, but it
        # must still publish fresh host state for the owner before success.
        self._run(["systemctl", "start", "robopark-doctor.service"], check=False)
        projection = self.root / "var/lib/robopark/api-ops/host-health.json"
        try:
            descriptor = os.open(projection, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
                    raise ValueError("invalid host-health projection")
                raw = source.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise ValueError("invalid host-health projection")
            health = json.loads(raw)
            checked_at = health.get("services_checked_at") if isinstance(health, dict) else None
            if not isinstance(checked_at, str) or not checked_at.strip():
                raise TypeError("missing host-health check timestamp")
            checked_time = datetime.fromisoformat(checked_at)
            if checked_time.tzinfo is None or abs(time.time() - checked_time.timestamp()) > 300:
                raise ValueError("stale host-health check timestamp")
            services = health.get("services")
            if not isinstance(services, dict) or any(
                services.get(name) not in {"ok", "degraded", "unknown"}
                for name in ("docker", "tuna", "internet", "wifi")
            ):
                raise TypeError("missing host service states")
        except (OSError, TypeError, ValueError, UnicodeError) as exc:
            raise RuntimeError("host_health_projection_missing") from exc

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
