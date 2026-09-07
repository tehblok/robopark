"""Read-only host health checks and the concise status projection."""

from __future__ import annotations

import json
import os
import platform
import re
import socket
import ssl
import stat
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .checks import CheckResult, DiagnosticReport, Runner, execute
from .compose import EXPECTED_SERVICES, compose_command, parse_compose_services
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


def _clock_check(runner: Runner) -> CheckResult:
    result = execute(runner, ["timedatectl", "show", "--property=NTPSynchronized", "--value"])
    synchronized = result.stdout.strip().casefold() in {"yes", "true", "1"}
    return CheckResult(
        "clock_sync",
        "ok" if result.ok and synchronized else "failed",
        "Часы синхронизированы" if result.ok and synchronized else "Часы не синхронизированы",
        None,
    )


def _df_check(runner: Runner, command: list[str], code: str, label: str) -> CheckResult:
    result = execute(runner, command)
    percentages = [
        int(token[:-1])
        for line in result.stdout.splitlines()[1:]
        for token in line.split()
        if token.endswith("%") and token[:-1].isdigit()
    ]
    if not result.ok or not percentages:
        return CheckResult(code, "warning", f"Не удалось измерить {label}", None)
    used = max(percentages)
    status = "failed" if used >= 95 else "warning" if used >= 85 else "ok"
    return CheckResult(code, status, f"{label}: занято {used}%", None)


def _memory_check(runner: Runner) -> CheckResult:
    result = execute(runner, ["free", "-m"])
    lines = [line.split() for line in result.stdout.splitlines() if line.startswith("Mem:")]
    if not result.ok or not lines or len(lines[0]) < 7:
        return CheckResult("memory_load_swap", "warning", "Не удалось измерить память и swap", None)
    try:
        available = int(lines[0][-1])
    except ValueError:
        return CheckResult("memory_load_swap", "warning", "Не удалось измерить память и swap", None)
    swap = [line.split() for line in result.stdout.splitlines() if line.startswith("Swap:")]
    if not swap or len(swap[0]) < 4:
        return CheckResult("memory_load_swap", "warning", "Не удалось измерить swap", None)
    try:
        swap_total, swap_used = int(swap[0][1]), int(swap[0][2])
    except ValueError:
        return CheckResult("memory_load_swap", "warning", "Не удалось измерить swap", None)
    exhausted_swap = swap_total > 0 and swap_used / swap_total >= 0.95
    status = "failed" if available < 128 or exhausted_swap else "warning" if available < 512 else "ok"
    return CheckResult(status=status, code="memory_load_swap", message=f"Доступно памяти: {available} MiB; swap: {swap_used}/{swap_total} MiB")


def _load_check(runner: Runner) -> CheckResult:
    result = execute(runner, ["uptime"])
    matched = re.search(r"load averages?:\s*([0-9]+(?:[.,][0-9]+)?)", result.stdout)
    if not result.ok or matched is None:
        return CheckResult("load", "warning", "Не удалось измерить нагрузку", None)
    load = float(matched.group(1).replace(",", "."))
    cores = max(1, os.cpu_count() or 1)
    status = "failed" if load >= cores * 2 else "warning" if load >= cores else "ok"
    return CheckResult("load", status, f"Нагрузка за минуту: {load:.2f}", None)


def _temperature_check(runner: Runner) -> CheckResult:
    result = execute(runner, ["cat", "/sys/class/thermal/thermal_zone0/temp"])
    try:
        temperature = int(result.stdout.strip()) / 1000
    except ValueError:
        return CheckResult("temperature", "warning", "Температурные датчики недоступны", None)
    status = "failed" if temperature >= 90 else "warning" if temperature >= 80 else "ok"
    return CheckResult("temperature", status, f"Температура: {temperature:.1f} °C", None)


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
            if not set(required_keys).issubset(values) or any(
                not values[key] for key in required_keys if key in values
            ):
                invalid.append(filename)
            if stat.S_IMODE(target.stat().st_mode) & 0o077:
                invalid.append(filename)
            if os.environ.get("ROBOPARK_TESTING") != "1" and target.stat().st_uid != 0:
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
    if not paths.previous.is_symlink() or not manifest.is_file():
        return CheckResult("release_layout", "failed", "У текущего релиза нет манифеста", None)
    try:
        previous_target = paths.previous.resolve(strict=True)
    except OSError:
        return CheckResult("release_layout", "failed", "Ссылка на предыдущий релиз повреждена", None)
    if paths.releases not in previous_target.parents:
        return CheckResult("release_layout", "failed", "Предыдущий релиз находится вне каталога релизов", None)
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CheckResult("release_layout", "failed", "Манифест текущего релиза повреждён", None)
    if (
        not isinstance(payload, Mapping)
        or not isinstance(payload.get("app_version"), str)
        or not payload["app_version"]
        or not isinstance(payload.get("format"), int)
        or not isinstance(payload.get("migration_head"), str)
        or not payload["migration_head"]
    ):
        return CheckResult("release_layout", "failed", "Манифест текущего релиза неполный", None)
    return CheckResult("release_layout", "ok", "Текущий релиз и манифест согласованы", None)


def _container_check(paths: HostPaths, runner: Runner) -> CheckResult:
    result = execute(runner, compose_command(paths, ["ps", "--format", "json"]))
    if not result.ok:
        return CheckResult("containers", "failed", "Состояние контейнеров недоступно", "restart_app")
    containers = parse_compose_services(result.stdout)
    if containers is None:
        return CheckResult("containers", "failed", "Compose вернул неполный статус контейнеров", "restart_app")
    by_service = {str(item.get("Service", "")): item for item in containers}
    missing = EXPECTED_SERVICES - set(by_service)
    def number(item: Mapping[str, Any], name: str) -> int:
        try:
            return int(str(item.get(name, "0") or "0"))
        except ValueError:
            return 0

    unhealthy = [
        item
        for item in containers
        if str(item.get("State", "")).casefold() != "running"
        or (
            str(item.get("Service", "")) in {"api", "web"}
            and str(item.get("Health", "")).casefold() in {"unhealthy", ""}
        )
        or number(item, "ExitCode") != 0
    ]
    restarted = [
        item
        for item in containers
        if number(item, "RestartCount") >= 5
    ]
    if missing or unhealthy or restarted:
        return CheckResult("containers", "failed", "Есть остановленные, нездоровые или отсутствующие контейнеры Compose", "restart_app")
    return CheckResult(
        "containers",
        "ok",
        "Контейнеры Compose запущены и здоровы",
        None,
    )


def _response_payload(response: Any) -> Mapping[str, Any]:
    value = getattr(response, "json", None)
    if callable(value):
        try:
            parsed = value()
        except Exception:
            return {}
    else:
        parsed = value
    return parsed if isinstance(parsed, Mapping) else {}


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


def _api_readiness_check(http: Any) -> CheckResult:
    try:
        response = _http_get(http, "http://127.0.0.1:8080/api/health/ready", timeout=5)
    except Exception:
        return CheckResult("api_readiness", "failed", "API readiness недоступен", "restart_app")
    payload = _response_payload(response)
    database = _response_payload(response).get("checks", {}).get("database")
    if not _response_ok(response) or payload.get("status") != "ready" or database != "ok":
        return CheckResult("api_readiness", "failed", "API или база данных не готовы", "restart_app")
    return CheckResult("api_readiness", "ok", "API и база данных готовы", None)


def _state_check(paths: HostPaths, code: str, message: str) -> CheckResult:
    target = paths.state / ("updater.json" if code == "updater" else "last-backup.json")
    if not target.exists():
        return CheckResult(code, "warning", message, None)
    try:
        state = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CheckResult(code, "failed", "Состояние Robopark повреждено", None)
    if not isinstance(state, Mapping):
        return CheckResult(code, "failed", "Состояние Robopark повреждено", None)
    if code == "updater":
        outcome = str(state.get("status", state.get("state", ""))).casefold()
        if outcome in {"failed", "stuck", "manual_recovery_required"}:
            return CheckResult(code, "failed", "Обновление требует внимания", None)
        if outcome == "running":
            started = state.get("started_at")
            try:
                started_at = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            except ValueError:
                return CheckResult(code, "warning", "Не удалось определить возраст выполняемого обновления", None)
            if started_at.tzinfo is None or started_at < datetime.now(UTC) - timedelta(hours=2):
                return CheckResult(code, "warning", "Обновление выполняется слишком долго", None)
            return CheckResult(code, "warning", "Обновление выполняется", None)
        if outcome not in {"success", "ready", "idle", "completed"}:
            return CheckResult(code, "warning", "Нет подтверждённого состояния обновления", None)
        if not isinstance(state.get("source"), str) or not state.get("source"):
            return CheckResult(code, "warning", "Не указан источник последнего обновления", None)
    if code == "backup" and (
        state.get("status") not in {"success", "completed", "ok"} or not state.get("completed_at")
    ):
        return CheckResult(code, "warning", "Нет подтверждённой свежей резервной копии", None)
    if code == "backup":
        try:
            completed_at = datetime.fromisoformat(str(state["completed_at"]).replace("Z", "+00:00"))
        except ValueError:
            return CheckResult(code, "warning", "Время резервной копии не распознано", None)
        if completed_at.tzinfo is None:
            return CheckResult(code, "warning", "Время резервной копии указано без часового пояса", None)
        if completed_at < datetime.now(UTC) - timedelta(days=7):
            return CheckResult(code, "warning", "Резервная копия устарела", None)
    return CheckResult(code, "ok", message, None)


def _integration_check(http: Any) -> CheckResult:
    try:
        response = _http_get(http, "http://127.0.0.1:8080/api/health/ready", timeout=5)
    except Exception:
        return CheckResult("integrations", "warning", "Проверка Tracker и Emergency недоступна", None)
    integration_state = _response_payload(response).get("checks", {}).get("integrations")
    return CheckResult(
        "integrations",
        "ok" if integration_state == "ok" else "warning",
        "Интеграции доступны" if integration_state == "ok" else "Интеграции работают с деградацией",
        None,
    )


def _database_check(paths: HostPaths, runner: Runner, http: Any) -> CheckResult:
    try:
        response = _http_get(http, "http://127.0.0.1:8080/api/health/ready", timeout=5)
    except Exception:
        return CheckResult("database_unavailable", "failed", "База данных недоступна", None)
    database = _response_payload(response).get("checks", {}).get("database")
    migration = execute(runner, compose_command(paths, ["exec", "-T", "api", "alembic", "current"]))
    try:
        expected_head = json.loads((paths.current / "manifest.json").read_text(encoding="utf-8"))[
            "migration_head"
        ]
    except (OSError, ValueError, KeyError):
        expected_head = None
    actual_head = migration.stdout.strip().split(maxsplit=1)[0] if migration.stdout.strip() else None
    return CheckResult(
        "database_unavailable",
        "ok" if _response_ok(response) and database == "ok" and migration.ok and actual_head == expected_head else "failed",
        "База данных и миграции доступны" if _response_ok(response) and database == "ok" and migration.ok and actual_head == expected_head else "База данных или миграции недоступны",
        None,
    )


def _tuna_route_check(paths: HostPaths, http: Any) -> CheckResult:
    public_url = _public_url(paths)
    if not public_url or not public_url.startswith("https://"):
        return CheckResult("tuna_route", "warning", "Публичный HTTPS-маршрут Tuna не настроен", None)
    try:
        response = _http_get(http, public_url, timeout=10)
    except Exception:
        return CheckResult("tuna_route", "failed", "Публичный HTTPS-маршрут Tuna недоступен", "restart_tuna")
    return CheckResult(
        "tuna_route",
        "ok" if _response_ok(response) else "failed",
        "Публичный HTTPS-маршрут Tuna доступен" if _response_ok(response) else "Публичный HTTPS-маршрут Tuna вернул ошибку",
        None if _response_ok(response) else "restart_tuna",
    )


def _tuna_certificate_check(paths: HostPaths, http: Any) -> CheckResult:
    public_url = _public_url(paths)
    if not public_url:
        return CheckResult("tuna_certificate", "warning", "Срок сертификата Tuna не установлен", None)
    try:
        parsed = urlsplit(public_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("not HTTPS")
        with (
            socket.create_connection((parsed.hostname, parsed.port or 443), timeout=10) as connection,
            ssl.create_default_context().wrap_socket(
                connection, server_hostname=parsed.hostname
            ) as secured,
        ):
            expiry = secured.getpeercert().get("notAfter")
        if not expiry:
            raise ValueError("missing certificate expiry")
        expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(expiry), tz=UTC)
    except (OSError, ssl.SSLError, ValueError):
        return CheckResult("tuna_certificate", "warning", "Не удалось проверить срок сертификата Tuna", None)
    status = "failed" if expires <= datetime.now(UTC) else "warning" if expires <= datetime.now(UTC) + timedelta(days=14) else "ok"
    return CheckResult("tuna_certificate", status, "Срок сертификата Tuna проверен", None)


def _artifact_check(paths: HostPaths, runner: Runner) -> CheckResult:
    result = execute(runner, ["du", "-sk", paths.var / "diagnostics"])
    amount = next((int(token) for token in result.stdout.split() if token.isdigit()), None)
    if not result.ok or amount is None:
        return CheckResult("diagnostic_artifacts", "warning", "Не удалось измерить объём диагностических артефактов", None)
    status = "failed" if amount >= 1_048_576 else "warning" if amount >= 524_288 else "ok"
    return CheckResult("diagnostic_artifacts", status, f"Диагностические артефакты: {amount} KiB", None)


def _log_growth_check(paths: HostPaths, runner: Runner) -> CheckResult:
    result = execute(runner, ["du", "-sk", paths.root / "var/log/robopark"])
    amount = next((int(token) for token in result.stdout.split() if token.isdigit()), None)
    if not result.ok or amount is None:
        return CheckResult("log_growth", "warning", "Не удалось измерить рост журналов", None)
    status = "failed" if amount >= 1_048_576 else "warning" if amount >= 524_288 else "ok"
    return CheckResult("log_growth", status, f"Журналы Robopark: {amount} KiB", None)


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
        _clock_check(runner),
        _command_check(runner, "dns", ["getent", "hosts", "github.com"], "DNS доступен"),
        _command_check(runner, "outbound_https", ["curl", "--fail", "--silent", "--show-error", "--max-time", "10", "https://example.com"], "Исходящий HTTPS доступен"),
        _df_check(runner, ["df", "-Pk", paths.var], "resources", "Дисковое пространство"),
        _df_check(runner, ["df", "-Pi", paths.var], "inode_space", "Inode-пространство"),
        _memory_check(runner),
        _load_check(runner),
        _temperature_check(runner),
        _command_check(runner, "docker_daemon", ["systemctl", "is-active", "docker.service"], "Docker daemon доступен", _SERVICE_REPAIRS["docker"]),
        _command_check(runner, "compose", ["docker", "compose", "version"], "Docker Compose доступен"),
        _container_check(paths, runner),
        _release_check(paths),
        _configuration_check(paths),
        _database_check(paths, runner, http),
        _endpoint_check(http),
        _api_readiness_check(http),
        _command_check(runner, "tuna_binary", ["tuna", "help"], "Tuna доступна"),
        _command_check(runner, "tuna_inactive", ["systemctl", "is-active", "robopark-tuna.service"], "Tuna запущен", _SERVICE_REPAIRS["tuna"]),
        _tuna_route_check(paths, http),
        _tuna_certificate_check(paths, http),
        _state_check(paths, "updater", "Состояние обновлений проверено"),
        _integration_check(http),
        _state_check(paths, "backup", "Последняя резервная копия проверена"),
        _artifact_check(paths, runner),
        _log_growth_check(paths, runner),
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
    return {"version": str(payload.get("app_version")) if payload.get("app_version") else None, "git_sha": str(payload.get("git_sha")) if payload.get("git_sha") else None}


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
