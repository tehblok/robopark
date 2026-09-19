#!/usr/bin/env python3
"""Linux host operations. Never print or source operator secrets."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

DEPLOY = Path(__file__).resolve().parent
COMPOSE_SECRET_DIR = Path("/etc/robopark")


class HostError(Exception):
    pass


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise HostError("Некорректный формат env-файла (ожидается KEY=value).")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def private_env(path: Path) -> tuple[dict[str, str], list[str]]:
    if not path.is_file():
        return {}, [f"Не найден {path.name}."]
    issues = []
    if path.stat().st_mode & 0o077:
        issues.append(f"Для {path.name} требуются права 600 или 400.")
    return read_env(path), issues


def validate_config(path: Path) -> list[str]:
    values, issues = private_env(path)
    if values.get("DEV_SEED", "false").lower() != "false":
        issues.append("DEV_SEED должен быть false.")
    if values.get("COOKIE_SECURE", "").lower() != "true":
        issues.append("COOKIE_SECURE должен быть true.")
    if values.get("COOKIE_SAMESITE", "lax").lower() not in {"lax", "strict"}:
        issues.append("COOKIE_SAMESITE должен быть lax или strict.")
    origins = values.get("CORS_ORIGINS", "").split(",")
    for origin in origins:
        url = urlsplit(origin.strip())
        if (
            url.scheme != "https"
            or not url.hostname
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or url.username
            or "*" in origin
            or "example" in (url.hostname or "")
            or url.hostname == "localhost"
        ):
            issues.append("CORS_ORIGINS: укажите точный рабочий HTTPS-адрес без шаблонов.")
            break
    try:
        key = values.get("SECRET_KEY", "")
        if len(base64.b64decode(key, altchars=b"-_", validate=True)) != 32:
            raise ValueError
    except (ValueError, TypeError):
        issues.append("SECRET_KEY должен быть действительным ключом Fernet.")
    if values.get("UVICORN_WORKERS", "2") not in {"1", "2", "3", "4"}:
        issues.append("UVICORN_WORKERS: допустимо 1–4; для 8 ГБ рекомендуется 2.")
    return issues


def validate_tuna(path: Path) -> list[str]:
    values, issues = private_env(path)
    if not values.get("TUNA_TOKEN") or values.get("TUNA_TOKEN") == "tt_replace_me":
        issues.append("Настройте TUNA_TOKEN.")
    if bool(values.get("TUNA_DOMAIN")) == bool(values.get("TUNA_SUBDOMAIN")):
        issues.append("Задайте один постоянный TUNA_DOMAIN или TUNA_SUBDOMAIN.")
    if values.get("TUNA_BIND", "127.0.0.1:8080") != "127.0.0.1:8080":
        issues.append("TUNA_BIND должен быть 127.0.0.1:8080.")
    return issues


def compose(*args: str, capture_output: bool = False):
    # Canonical location is also used by royal snapshots and the ops-agent.
    generated = subprocess.run(
        [
            sys.executable,
            "-I",
            str(DEPLOY / "compose_secrets.py"),
            "--directory",
            str(COMPOSE_SECRET_DIR),
            "--host-env",
            str(DEPLOY / "host.env"),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    env = {
        **os.environ,
        "HOST_ENV_FILE": str(DEPLOY / "host.env"),
        "ROBOPARK_POSTGRES_PASSWORD_FILE": str(COMPOSE_SECRET_DIR / "postgres-password"),
        "ROBOPARK_PGPASS_FILE": str(COMPOSE_SECRET_DIR / "pgpass"),
        "ROBOPARK_SNAPSHOT_CONFIG_FILE": str(COMPOSE_SECRET_DIR / "snapshot.env"),
    }
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            generated,
            "-f",
            str(DEPLOY / "docker-compose.yml"),
            *args,
        ],
        cwd=DEPLOY,
        env=env,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def check(tuna_env: Path) -> None:
    issues = validate_config(DEPLOY / "host.env") + validate_tuna(tuna_env)
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64", "x86_64"}:
        issues.append("Рабочий профиль поддерживает Linux ARM64 / x86_64.")
    if not shutil.which("docker"):
        issues.append("Установите Docker Engine и Compose plugin.")
    if shutil.disk_usage(DEPLOY).free < 8 * 1024**3:
        issues.append("Для сборки и снимков требуется минимум 8 ГБ свободного места.")
    if issues:
        raise HostError("\n".join(issues))
    subprocess.run(
        ["docker", "info"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    compose("config", "--quiet", capture_output=True)
    print("Конфигурация проверена. Доступность домена и нагрузку проверяют после запуска.")


def backup(destination: Path, keep: int = 14) -> Path:
    if keep < 1:
        raise HostError("Нужно хранить хотя бы один снимок.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.stat().st_mode & 0o077:
        raise HostError("Каталог резервных копий должен иметь права 700.")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%f")
    final = destination / f"robopark-{stamp}.zip"
    partial = final.with_suffix(".partial")
    try:
        result = compose(
            "exec",
            "-T",
            "api",
            "python",
            "/host-repo/deploy/backup-snapshot.py",
            capture_output=True,
        )
        artifact = result.stdout.strip()
        if not artifact.startswith("/data/ops/artifacts/") or Path(
            artifact
        ).name != artifact.removeprefix("/data/ops/artifacts/"):
            raise HostError("Сервер не вернул допустимый путь снимка.")
        # Reserve a private file before docker cp opens it.
        with partial.open("xb"):
            partial.chmod(0o600)
        compose("cp", f"api:{artifact}", str(partial), capture_output=True)
        partial.chmod(0o600)
        with zipfile.ZipFile(partial) as archive:
            if (
                archive.testzip()
                or json.loads(archive.read("manifest.json")).get("kind") != "snapshot"
            ):
                raise HostError("Проверка резервной копии не прошла.")
        with partial.open("rb") as stream:
            os.fsync(stream.fileno())
        partial.replace(final)
        directory_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        compose(
            "exec",
            "-T",
            "api",
            "python",
            "/host-repo/deploy/backup-snapshot.py",
            "--ack",
            Path(artifact).name,
            capture_output=True,
        )
        # Remove only scheduled copies after a complete, validated new copy exists.
        for old in sorted(destination.glob("robopark-*.zip"), reverse=True)[keep:]:
            old.unlink()
        print(f"Резервная копия готова: {final}")
        return final
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise HostError("Проверка резервной копии не прошла; старые копии сохранены.") from exc
    finally:
        partial.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "start", "stop", "status", "backup"])
    parser.add_argument("--tuna-env", type=Path, default=Path("/etc/robopark/tuna.env"))
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=Path("/var/backups/robopark"))
    parser.add_argument("--keep", type=int, default=14)
    args = parser.parse_args()
    try:
        if args.action in {"check", "start"}:
            check(args.tuna_env)
        if args.action == "start":
            compose("up", "-d", "--wait", *([] if args.no_build else ["--build"]))
        elif args.action == "stop":
            compose("stop")
        elif args.action == "status":
            compose("ps")
        elif args.action == "backup":
            backup(args.backup_dir, args.keep)
    except (HostError, OSError, subprocess.CalledProcessError) as exc:
        # Never echo subprocess output: Compose diagnostics may contain env values.
        message = (
            str(exc)
            if isinstance(exc, HostError)
            else "Операция хоста не завершилась; проверьте Docker, права и свободное место."
        )
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
