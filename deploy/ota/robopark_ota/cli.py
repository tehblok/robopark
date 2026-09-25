from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .credentials import collect_royal_credentials, credential_file
from .diagnose import collect_local_diagnostics
from .host_install import HostInstallRuntime
from .install import CleanInstallCoordinator
from .menu import Action, run_menu
from .model import OtaError
from .remove import DockerCli, RemovalPlan, remove_owned_installation
from .verify import verify_ota


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
    diagnose = commands.add_parser("diagnose", help="Собрать диагностику")
    diagnose.add_argument("--output", type=Path, required=True)
    return parser


def _bundle_path() -> Path:
    return Path(sys.argv[0]).resolve()


def _print_removal_plan(plan: RemovalPlan) -> None:
    print("Будут безвозвратно удалены только объекты Robopark:")
    for path in plan.paths:
        print(f"  {path}")
    print("  Docker project: robopark")


def _require_delete_confirmation(plan: RemovalPlan) -> None:
    _print_removal_plan(plan)
    if input("Введите УДАЛИТЬ ВСЕ ДАННЫЕ: ").strip() != "УДАЛИТЬ ВСЕ ДАННЫЕ":
        raise RuntimeError("confirmation_required")


def _remove(root: Path) -> None:
    plan = RemovalPlan.for_root(root)
    _require_delete_confirmation(plan)
    subprocess.run(
        ["systemctl", "stop", *plan.services],
        check=False,
        stdin=subprocess.DEVNULL,
    )
    docker = DockerCli()
    remove_owned_installation(plan, docker, docker.discover_owned())
    print("Robopark полностью удалён.")


def _clean_install(bundle: Path, root: Path) -> None:
    plan = RemovalPlan.for_root(root)
    _require_delete_confirmation(plan)
    credentials = collect_royal_credentials()
    runtime = HostInstallRuntime(bundle, root=root)
    with credential_file(credentials, root / "run/robopark") as secret:
        CleanInstallCoordinator(runtime).run(secret)
    print(
        f"Robopark {runtime.verified.manifest.app_version} установлен. "
        "Откройте адрес сервера и войдите под созданным royal."
    )


def run_interactive() -> int:
    root = Path("/")
    bundle = _bundle_path()
    handlers = {
        Action.CLEAN_INSTALL: lambda: _clean_install(bundle, root),
        Action.UPDATE: lambda: (_ for _ in ()).throw(RuntimeError("ota_update_unavailable")),
        Action.DIAGNOSE: lambda: print(collect_local_diagnostics(Path.cwd(), root=root)),
        Action.REMOVE: lambda: _remove(root),
    }
    run_menu(handlers=handlers, preflight=lambda _action: None)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        return run_interactive()
    if args.command == "diagnose":
        print(collect_local_diagnostics(args.output))
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
