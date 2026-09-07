"""Read-only host health checks and the concise status projection."""

from __future__ import annotations

import json
import os
import platform
import stat
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from .checks import CheckResult, DiagnosticReport, Runner, execute
from .paths import HostPaths
from .redaction import redact
from .state import atomic_write_json

_CONFIG_KEYS = {"host.env": ("SECRET_KEY",), "tuna.env": ("TUNA_TOKEN",)}
_SERVICE_REPAIRS = {
    "docker": "restart_docker",
    "robopark": "restart_app",
    "tuna": "restart_tuna",
}


def _command_check(
    runner: Runner, code: str, command: list[str], message: str, repair: str | None = None
) -> CheckResult:
    return CheckResult(code, "ok" if execute(runner, command).ok else "failed", message, repair)


def _http_get(http: Any, url: str, timeout: int = 5) -> Any:
    request = getattr(http, "get", http)
    return request(url, timeout=timeout)


def _response_ok(response: Any) -> bool:
    return int(getattr(response, "status", getattr(response, "status_code", 200))) < 400


def _platform_check() -> CheckResult:
    system = platform.system().lower()
    machine = platform.machine().lower()
    supported = system == "linux" and machine in {"aarch64", "arm64", "x86_64", "amd64"}
    return CheckResult(
        "supported_platform",
        "ok" if supported else "failed",
        "Поддерживаемая ОС и архитектура" if supported else "Неподдерживаемая ОС или архитектура",
        None,
    )


def _configuration_check(paths: HostPaths) -> CheckResult:
    absent: list[str] = []
    invalid: list[str] = []
    configuration: dict[str, dict[str, str]] = {}
    for filename, required_keys in _CONFIG_KEYS.items():
        target = paths.etc / filename
        if not target.exists():
            absent.append(filename)
            continue
        try:
            values = {
                line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
                for line in target.read_text(encoding="utf-8", errors="replace").splitlines()
                if "=" in line and not line.lstrip().startswith("#")
            }
            configuration[filename] = values
            if not set(required_keys).issubset(values):
                invalid.append(filename)
            if stat.S_IMODE(target.stat().st_mode) & 0o077:
                invalid.append(filename)
        except OSError:
            invalid.append(filename)
    origin = configuration.get("host.env", {}).get("PUBLIC_SITE_ORIGIN")
    public_url = configuration.get("tuna.env", {}).get("PUBLIC_URL")
    if origin and public_url and origin.rstrip("/") != public_url.rstrip("/"):
        invalid.append("public_url")
    if invalid:
        return CheckResult("configuration", "failed", "Конфигурация Robopark требует исправления", None)
    if absent:
        return CheckResult("configuration", "warning", "Часть конфигурации Robopark ещё не создана", None)
    return CheckResult("configuration", "ok", "Конфигурация Robopark проверена", None)


def _release_check(paths: HostPaths) -> CheckResult:
    current = paths.current
    if not current.is_symlink():
        return CheckResult("release_layout", "warning", "Текущий релиз ещё не выбран", None)
    try:
        target = current.resolve(strict=True)
    except OSError:
        return CheckResult("release_layout", "failed", "Ссылка на текущий релиз повреждена", None)
    if paths.releases not in target.parents:
        return CheckResult("release_layout", "failed", "Текущий релиз находится вне каталога релизов", None)
    manifest = target / "manifest.json"
    if not manifest.is_file():
        return CheckResult("release_layout", "failed", "У текущего релиза нет манифеста", None)
    try:
        json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CheckResult("release_layout", "failed", "Манифест текущего релиза повреждён", None)
    return CheckResult("release_layout", "ok", "Текущий релиз и манифест согласованы", None)


def _container_check(runner: Runner) -> CheckResult:
    result = execute(runner, ["docker", "compose", "ps", "--format", "json"])
    if not result.ok:
        return CheckResult("containers", "failed", "Состояние контейнеров недоступно", "restart_app")
    try:
        containers = json.loads(result.stdout or "[]")
    except ValueError:
        return CheckResult("containers", "warning", "Compose вернул неполный статус контейнеров", None)
    unhealthy = [item for item in containers if str(item.get("Health", "")).lower() == "unhealthy"]
    return CheckResult(
        "containers",
        "failed" if unhealthy else "ok",
        "Контейнеры Compose здоровы" if not unhealthy else "Есть нездоровые контейнеры Compose",
        "restart_app" if unhealthy else None,
    )


def _endpoint_check(http: Any) -> CheckResult:
    try:
        response = _http_get(http, "http://127.0.0.1:8080/", timeout=5)
        headers: Mapping[str, str] = getattr(response, "headers", {})
    except Exception:
        return CheckResult("local_endpoint", "failed", "Локальный веб-интерфейс недоступен", "restart_app")
    if not _response_ok(response):
        return CheckResult("local_endpoint", "failed", "Локальный веб-интерфейс вернул ошибку", "restart_app")
    normalized = {str(key).casefold() for key in headers}
    if "x-content-type-options" not in normalized:
        return CheckResult("local_endpoint", "warning", "Локальный веб-интерфейс доступен, но часть защитных заголовков отсутствует", None)
    return CheckResult("local_endpoint", "ok", "Локальный веб-интерфейс доступен", None)


def _state_check(paths: HostPaths, code: str, message: str) -> CheckResult:
    target = paths.state / ("updater.json" if code == "updater" else "last-backup.json")
    if not target.exists():
        return CheckResult(code, "warning", message, None)
    try:
        json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CheckResult(code, "failed", "Состояние Robopark повреждено", None)
    return CheckResult(code, "ok", message, None)


def _integration_check(http: Any) -> CheckResult:
    try:
        response = _http_get(http, "http://127.0.0.1:8000/health/integrations", timeout=5)
    except Exception:
        return CheckResult("integrations", "warning", "Проверка Tracker и Emergency недоступна", None)
    return CheckResult(
        "integrations",
        "ok" if _response_ok(response) else "warning",
        "Интеграции доступны" if _response_ok(response) else "Интеграции работают с деградацией",
        None,
    )


def _write_human_log(paths: HostPaths, report: DiagnosticReport) -> None:
    """Atomically retain a compact report made solely from public check fields."""

    target = paths.root / "var/log/robopark/doctor.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(
        [report.created_at]
        + [f"[{check.status}] {check.code}: {check.message}" for check in report.checks]
    )[:16_383] + "\n"
    descriptor, temporary_name = tempfile.mkstemp(dir=target.parent, prefix=".doctor-", text=True)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()


def run_doctor(paths: HostPaths, runner: Runner, http: Any) -> DiagnosticReport:
    """Collect host health without printing secrets or changing host services."""

    checks: list[CheckResult] = [
        _platform_check(),
        _command_check(runner, "clock_sync", ["timedatectl", "show", "--property=NTPSynchronized", "--value"], "Синхронизация часов проверена"),
        _command_check(runner, "dns", ["getent", "hosts", "github.com"], "DNS доступен"),
        _command_check(runner, "outbound_https", ["curl", "--fail", "--silent", "--show-error", "--max-time", "10", "https://example.com"], "Исходящий HTTPS доступен"),
        _command_check(runner, "resources", ["df", "-Pk", paths.var], "Дисковое пространство проверено"),
        _command_check(runner, "inode_space", ["df", "-Pi", paths.var], "Inode-пространство проверено"),
        _command_check(runner, "memory_load_swap", ["free", "-m"], "Память и swap проверены"),
        _command_check(runner, "temperature", ["sh", "-c", "test -d /sys/class/thermal"], "Датчики температуры проверены"),
        _command_check(runner, "docker_daemon", ["systemctl", "is-active", "docker.service"], "Docker daemon доступен", _SERVICE_REPAIRS["docker"]),
        _command_check(runner, "compose", ["docker", "compose", "version"], "Docker Compose доступен"),
        _container_check(runner),
        _release_check(paths),
        _configuration_check(paths),
        _command_check(runner, "database_unavailable", ["docker", "compose", "exec", "-T", "api", "python", "-c", "from pathlib import Path; assert Path('/data/robopark.db').exists()"], "База данных доступна"),
        _endpoint_check(http),
        _command_check(runner, "tuna_binary", ["tuna", "help"], "Tuna доступна"),
        _command_check(runner, "tuna_inactive", ["systemctl", "is-active", "robopark-tuna.service"], "Tuna запущен", _SERVICE_REPAIRS["tuna"]),
        _state_check(paths, "updater", "Состояние обновлений проверено"),
        _integration_check(http),
        _state_check(paths, "backup", "Последняя резервная копия проверена"),
        _command_check(runner, "diagnostic_artifacts", ["du", "-sk", paths.var / "diagnostics"], "Диагностические артефакты проверены"),
    ]
    report = DiagnosticReport(checks)
    atomic_write_json(paths.var / "diagnostics" / "latest.json", report.as_dict())
    _write_human_log(paths, report)
    return report


def _release_metadata(paths: HostPaths) -> dict[str, str | None]:
    manifest = paths.current / "manifest.json"
    if not manifest.is_file():
        return {"version": None, "git_sha": None}
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": None, "git_sha": None}
    return {"version": str(payload.get("version")) if payload.get("version") else None, "git_sha": str(payload.get("git_sha")) if payload.get("git_sha") else None}


def _public_url(paths: HostPaths) -> str | None:
    target = paths.etc / "tuna.env"
    if not target.is_file():
        return None
    try:
        for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("PUBLIC_URL="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        pass
    return None


def run_status(paths: HostPaths, runner: Runner, http: Any) -> dict[str, Any]:
    """Return a small safe status payload for CLI and Royal health views."""

    report = run_doctor(paths, runner, http)
    metadata = _release_metadata(paths)
    status_by_code = {check.code: check.status for check in report.checks}
    return redact(
        {
            "version": metadata["version"],
            "git_sha": metadata["git_sha"],
            "url": _public_url(paths),
            "services": {
                "application": status_by_code.get("local_endpoint"),
                "docker": status_by_code.get("docker_daemon"),
                "tuna": status_by_code.get("tuna_inactive"),
                "updater": status_by_code.get("updater"),
            },
            "resources": {
                "disk": status_by_code.get("resources"),
                "memory": status_by_code.get("memory_load_swap"),
            },
            "last_backup": status_by_code.get("backup"),
            "failed_check_count": len(report.failed),
            "diagnostic_time": report.created_at,
        }
    )
