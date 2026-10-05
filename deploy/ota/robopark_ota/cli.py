from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import storage
from .credentials import (
    TunaConfiguration,
    collect_royal_credentials,
    collect_tuna_configuration,
    credential_file,
)
from .diagnose import collect_local_diagnostics
from .host_install import HostInstallRuntime, validate_host_platform
from .install import CleanInstallCoordinator, clean_install_lock
from .local_update import apply_local_update
from .menu import Action, run_menu
from .model import OtaError
from .remove import (
    DockerCli,
    RemovalPlan,
    RemovalPreviewEntry,
    preview_owned_installation,
    remove_owned_installation,
)
from .verify import verify_ota

_INTERACTIVE_ERRORS = {
    "invalid_tuna_address": (
        "Домен Tuna указан неверно. Введите полный адрес вида robopark.ru.tuna.am "
        "или короткий поддомен зоны ru."
    ),
    "docker_install_unsupported_release": (
        "Базовая ОС не распознана для установки Docker. Проверьте "
        "/etc/os-release и /etc/armbian-release; установка остановлена без удаления данных."
    ),
    "confirmation_required": "Операция отменена. Данные не удалялись.",
    "knowledge_install_pending": (
        "Robopark установлен и запущен, но полная база знаний не подключена. "
        "Повторите отдельно: sudo /opt/robopark/host-tools/robopark knowledge-install"
    ),
}

_STORAGE_ERRORS = {
    "storage_daemon_running": (
        "Подготовка NVMe остановлена: Docker/containerd или контейнеры работают. "
        "На новом хосте завершите контейнеры и остановите docker.socket, "
        "docker.service и containerd.service, затем повторите подготовку."
    ),
    "storage_target_not_empty": (
        "На хосте уже есть данные в рабочих каталогах. Автоматический перенос "
        "не выполняется; используйте отдельный план миграции с резервной копией."
    ),
    "storage_device_not_empty": "Выбранный раздел содержит файлы. Выберите пустой ext4; форматирование не выполнялось.",
    "storage_confirmation_required": "Подготовка отменена: фраза с UUID диска не совпала.",
    "storage_space_low": "На рабочем диске недостаточно места или inode. Освободите место и повторите операцию.",
    "storage_custom_container_root": "Найден нестандартный каталог Docker/containerd. Нужен отдельный план переноса; конфигурация сохранена.",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="robopark.ota",
        description=(
            "Единый пакет Robopark: Чистая установка, Обычное обновление, "
            "Диагностика и Полное удаление."
        ),
    )
    commands = parser.add_subparsers(dest="command")
    verify = commands.add_parser("verify", help="Проверить целостность пакета")
    verify.add_argument("--json", action="store_true", dest="as_json")
    commands.add_parser("update", help="Обновить существующую установку с сохранением данных")
    diagnose = commands.add_parser("diagnose", help="Собрать диагностику")
    diagnose.add_argument("--output", type=Path, required=True)
    disk = commands.add_parser("storage", help="Проверить или подготовить размещение на NVMe")
    disk.add_argument("action", choices=("inspect", "check", "plan", "prepare"))
    disk.add_argument("--mode", choices=("nvme-root", "emmc-nvme-data"))
    disk.add_argument("--device")
    disk.add_argument("--confirmation", default="")
    return parser


def _bundle_path() -> Path:
    return Path(sys.argv[0]).resolve()


def _size_label(value: int) -> str:
    for unit, divisor in (("ГиБ", 1024**3), ("МиБ", 1024**2), ("КиБ", 1024)):
        if value >= divisor:
            return f"{value / divisor:.1f} {unit} ({value} Б)"
    return f"{value} Б"


def _print_removal_plan(preview: tuple[RemovalPreviewEntry, ...]) -> None:
    print("Будут безвозвратно удалены только объекты Robopark:")
    for item in preview:
        location = f" · {item.path}" if item.path is not None else ""
        print(f"  {item.kind}: {item.name}{location} · {_size_label(item.bytes_used)}")
    print(
        "Итого по перечисленным объектам: "
        f"{_size_label(sum(item.bytes_used for item in preview))}"
    )


def _require_delete_confirmation(preview: tuple[RemovalPreviewEntry, ...]) -> None:
    _print_removal_plan(preview)
    if input("Введите УДАЛИТЬ ВСЕ ДАННЫЕ: ").strip() != "УДАЛИТЬ ВСЕ ДАННЫЕ":
        raise RuntimeError("confirmation_required")


def _remove(root: Path) -> None:
    storage.require_storage(root, check_space=False)
    plan = RemovalPlan.for_root(root)
    docker = DockerCli()
    targets = docker.discover_owned()
    preview = preview_owned_installation(plan, docker, targets)
    _require_delete_confirmation(preview)
    storage.require_storage(root, check_space=False)
    if (
        docker.discover_owned() != targets
        or preview_owned_installation(plan, docker, targets) != preview
    ):
        raise RuntimeError("removal_preview_changed")
    subprocess.run(
        ["systemctl", "disable", "--now", *plan.services],
        check=False,
        stdin=subprocess.DEVNULL,
    )
    try:
        remove_owned_installation(plan, docker, targets)
    finally:
        subprocess.run(
            ["systemctl", "daemon-reload"],
            check=False,
            stdin=subprocess.DEVNULL,
        )
    print("Robopark полностью удалён.")


def _clean_install(bundle: Path, root: Path) -> None:
    with clean_install_lock(root):
        runtime = HostInstallRuntime(bundle, root=root, tuna=TunaConfiguration())
        if root == Path("/"):
            validate_host_platform(root, runtime.verified.manifest.requirements)
        storage.choose_install_storage(root)
        runtime.ensure_empty_host()
        runtime.basic_preflight()
        runtime.tuna = collect_tuna_configuration()
        runtime.prepare_missing_tuna()
        runtime.prepare_missing_docker()
        runtime.ensure_empty_host()
        runtime.preflight()
        credentials = collect_royal_credentials()
        with credential_file(credentials, root / "run/robopark") as secret:
            _run_clean_install(runtime, secret)
        print(
            f"Robopark {runtime.verified.manifest.app_version} установлен. "
            "Откройте адрес сервера и войдите под созданным royal."
        )


def _run_clean_install(runtime: HostInstallRuntime, credential_path: Path) -> None:
    CleanInstallCoordinator(runtime).run(credential_path)
    try:
        runtime.install_knowledge()
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("knowledge_install_pending") from error


def _update(bundle: Path) -> None:
    if input("Введите ОБНОВИТЬ: ").strip() != "ОБНОВИТЬ":
        raise RuntimeError("confirmation_required")
    result = apply_local_update(bundle)
    print(f"Robopark {result['version']} обновлён. SHA-256: {result['sha256']}")
    if result.get("publication") == "degraded":
        print("Приложение обновлено, но Tuna не запустилась. Проверьте состояние robopark-tuna.service.", file=sys.stderr)


def run_interactive() -> int:
    root = Path("/")
    bundle = _bundle_path()
    handlers = {
        Action.CLEAN_INSTALL: lambda: _clean_install(bundle, root),
        Action.UPDATE: lambda: _update(bundle),
        Action.DIAGNOSE: lambda: print(
            collect_local_diagnostics(Path.cwd(), root=root)
        ),
        Action.REMOVE: lambda: _remove(root),
    }
    try:
        run_menu(handlers=handlers, preflight=lambda _action: None)
    except (ValueError, RuntimeError) as error:
        message = _INTERACTIVE_ERRORS.get(str(error))
        if isinstance(error, storage.StorageError):
            message = _STORAGE_ERRORS.get(error.code, (
                f"Проверка рабочего диска не пройдена ({error.code}). "
                "Выполните storage inspect и storage check; не запускайте установку "
                "до восстановления нужного диска и его точек монтирования."
            ))
        if str(error).startswith("clean_install_requires_empty_host: "):
            paths = str(error).partition(": ")[2]
            message = (
                "Чистая установка остановлена: найдены объекты Robopark: "
                f"{paths}. Данные не удалялись."
            )
        if message is None:
            raise
        print(message, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        return run_interactive()
    if args.command == "storage":
        try:
            verify_ota(_bundle_path())
            if args.action == "inspect":
                result = storage.inspect_storage()
            elif args.action == "check":
                result = storage.require_storage(check_space=False)
            else:
                if args.mode is None:
                    parser.error("storage plan/prepare requires --mode")
                if args.action == "plan":
                    result = storage.plan_storage(args.mode, args.device)
                else:
                    result = storage.prepare_storage(args.mode, args.device, confirmation=args.confirmation)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        except (storage.StorageError, OtaError) as error:
            print(json.dumps({"state": "failed", "error": str(error)}, ensure_ascii=False))
            return 2
    if args.command == "diagnose":
        print(collect_local_diagnostics(args.output))
        return 0
    if args.command == "update":
        try:
            _update(_bundle_path())
        except RuntimeError as error:
            if str(error) != "confirmation_required":
                raise
            print(_INTERACTIVE_ERRORS["confirmation_required"], file=sys.stderr)
            return 1
        return 0
    try:
        verified = verify_ota(_bundle_path())
    except OtaError as error:
        print(str(error), file=sys.stderr)
        return 1
    result = {
        "path": str(verified.path),
        "version": verified.manifest.app_version,
        "size": verified.size,
        "sha256": verified.sha256,
    }
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"OTA {result['version']} · {result['sha256']}")
    return 0
