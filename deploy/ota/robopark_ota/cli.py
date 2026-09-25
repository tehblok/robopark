from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .model import OtaError
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


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "diagnose":
        print("Диагностика будет доступна после установки host lifecycle.", file=sys.stderr)
        return 2
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
