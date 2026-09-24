"""Verify the embedded release and run its OTA worker over an existing install."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

PHASE_LABELS = {
    "unpacking": "Распаковываю проверенный релиз",
    "building": "Собираю API и веб-интерфейс по очереди",
    "testing": "Запускаю проверки кандидата",
    "smoking": "Проверяю изолированный запуск",
    "snapshotting": "Создаю снимок данных для отката",
    "publishing": "Готовлю системные файлы",
    "migrating": "Обновляю структуру базы",
    "health_check": "Проверяю новую версию",
    "reconciling": "Настраиваю службы после переключения",
    "publication": "Проверяю публикацию через Tuna",
    "succeeded": "Обновление подтверждено",
    "rolled_back": "Предыдущая версия восстановлена",
    "failed": "Кандидат отклонён",
    "manual_recovery_required": "Требуется восстановление через диагностику",
}

SAFE_ERRORS = {
    "archive_too_large",
    "build_failed",
    "capability_missing",
    "checksum_mismatch",
    "command_failed",
    "command_output_limit",
    "command_timeout",
    "compose_config_missing",
    "compose_invalid",
    "compose_version_unsupported",
    "current_changed",
    "cutover_unhealthy",
    "docker_command_failed",
    "docker_disk_full",
    "docker_network_failed",
    "docker_out_of_memory",
    "downgrade_rejected",
    "duplicate_member",
    "frontend_arm_dependency_failed",
    "frontend_typescript_failed",
    "insufficient_space",
    "installer_incompatible",
    "interrupted",
    "invalid_archive",
    "invalid_manifest",
    "invalid_request",
    "invalid_version",
    "manual_recovery_required",
    "manifest_files_mismatch",
    "migration_failed",
    "migration_head_mismatch",
    "migration_incompatible",
    "preflight_failed",
    "quality_gate_inputs_missing",
    "release_missing",
    "request_replayed",
    "signature_invalid",
    "smoke_failed",
    "staging_filesystem_mismatch",
    "tests_failed",
    "unsafe_artifact",
    "unsafe_build_context",
    "unsafe_config_path",
    "unsafe_data_path",
    "unsafe_path",
    "unsafe_release_path",
    "unsupported_format",
    "update_in_progress",
    "update_failed",
}


@dataclass(frozen=True)
class InstallationState:
    mode: Literal["clean", "upgrade", "repair", "remove"]
    current_version: str | None
    migration_head: str | None
    preserved_paths: tuple[Path, ...]


def inspect_installation(root: Path) -> InstallationState:
    """Read local identity before opening a release; never execute installed code."""
    root = Path(root)
    opt = root / "opt/robopark"
    current = opt / "current"
    releases = opt / "releases"
    etc = root / "etc/robopark"
    var = root / "var/lib/robopark"
    preserved = tuple(
        path
        for path in (etc, var / "data", var / "backups", var / "ops")
        if path.exists() or path.is_symlink()
    )
    if current.is_symlink():
        try:
            release = current.resolve(strict=True)
            if not release.is_dir() or not release.is_relative_to(
                releases.resolve(strict=True)
            ):
                raise ValueError("invalid_existing_link")
            version = read_regular(release / "VERSION", 256).decode("ascii").strip()
            manifest = json.loads(read_regular(release / "manifest.json", 1024 * 1024))
            head = manifest["migration_head"]
            if not version or not isinstance(head, str) or not head:
                raise ValueError("invalid_installation")
            return InstallationState("upgrade", version, head, preserved)
        except (OSError, ValueError, UnicodeError, KeyError, TypeError):
            return InstallationState("repair", None, None, preserved)
    if opt.exists() or current.exists():
        return InstallationState("repair", None, None, preserved)
    if var.exists() or etc.exists():
        return InstallationState("remove", None, None, preserved)
    return InstallationState("clean", None, None, preserved)


def read_regular(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("unsafe_file")
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("unsafe_file")
    return value


def atomic_write(path: Path, value: bytes, mode: int = 0o600) -> None:
    temporary = path.with_name("." + path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def extract_host_tools(raw: bytes, target: Path) -> Path:
    target.mkdir(parents=True, mode=0o700)
    with zipfile.ZipFile(__import__("io").BytesIO(raw)) as archive:
        for info in archive.infolist():
            if not info.filename.startswith("deploy/host/"):
                continue
            relative = Path(info.filename).relative_to("deploy/host")
            if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                raise ValueError("unsafe_path")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(destination, archive.read(info), 0o700 if relative.name == "robopark" else 0o600)
    entrypoint = target / "robopark"
    if not entrypoint.is_file():
        raise ValueError("host_tools_missing")
    return entrypoint


def update_status(path: Path, job_id: str) -> tuple[str | None, str | None]:
    try:
        value = json.loads(read_regular(path, 64 * 1024))
    except (OSError, ValueError, UnicodeError):
        return None, None
    if not isinstance(value, dict) or value.get("job_id") != job_id:
        return None, None
    phase = value.get("phase")
    error = value.get("error")
    return (
        phase if isinstance(phase, str) and phase in PHASE_LABELS else None,
        error if isinstance(error, str) and error in SAFE_ERRORS else None,
    )


def run_with_progress(
    argv: list[str], environment: dict[str, str], status: Path, result: Path, job_id: str
) -> int:
    process = subprocess.Popen(argv, env=environment)
    last_phase = None
    delay = 0.05 if environment.get("ROBOPARK_TESTING") == "1" else 1.0
    while process.poll() is None:
        phase, _ = update_status(status, job_id)
        if phase is not None and phase != last_phase:
            print("  • " + PHASE_LABELS[phase], flush=True)
            last_phase = phase
        time.sleep(delay)
    phase, error = update_status(status, job_id)
    if error is None:
        _, error = update_status(result, job_id)
    if phase is not None and phase != last_phase:
        print("  • " + PHASE_LABELS[phase], flush=True)
    if process.returncode and error:
        print("Код ошибки OTA: " + error, file=sys.stderr, flush=True)
    return process.returncode


def main(argv: list[str]) -> int:
    testing = os.environ.get("ROBOPARK_TESTING") == "1"
    if (os.geteuid() != 0 and not testing) or len(argv) != 3:
        return 2
    root = Path(argv[1]).resolve()
    bundle = Path(argv[2]).resolve()
    installation = inspect_installation(root)
    if installation.mode != "upgrade":
        raise ValueError("existing_installation_requires_repair")
    print("  • Выбрано действие: обновление существующей установки", flush=True)
    payload = bundle / "payload/robopark-release.zip"
    sys.path.insert(0, str(bundle / "verifier"))
    from robopark_api.services.ops.archives import (
        KIND_RELEASE,
        MAX_ARCHIVE_BYTES,
        inspect_archive,
    )

    raw = read_regular(payload, MAX_ARCHIVE_BYTES)
    inspect_archive(raw, expected_kind=KIND_RELEASE)
    print("  • Целостность архива проверена", flush=True)

    identity = str(uuid4())
    ops = root / "var/lib/robopark/ops"
    artifact = ops / "artifacts" / ("update-" + identity + ".zip")
    request = ops / "state" / ("local-update-" + identity + ".json")
    staging = ops / "staging" / ("local-updater-" + identity)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    request.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(artifact, raw)
    atomic_write(
        request,
        json.dumps(
            {
                "job_id": identity,
                "kind": "update",
                "artifact": artifact.name,
                "actor_user_id": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            sort_keys=True,
        ).encode(),
    )
    entrypoint = extract_host_tools(raw, staging)
    print("  • Исправленный механизм OTA подготовлен", flush=True)
    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if root != Path("/"):
        environment.update(
            ROBOPARK_TESTING="1",
            ROBOPARK_ROOT=str(root),
            PATH=os.environ["PATH"],
            TMPDIR=os.environ.get("TMPDIR", "/tmp"),
        )
    try:
        status = ops / "public/host-status.json"
        result = ops / "public/rebuild.result"
        print("  • Начинаю сборку; на ARM-хосте это может занять несколько минут", flush=True)
        worker_code = run_with_progress(
            ["python3", "-B", str(entrypoint), "update", "--request", str(request), "--worker"],
            environment,
            status,
            result,
            identity,
        )
        if worker_code and update_status(result, identity)[1] is not None:
            return worker_code
        successor = root / "opt/robopark/host-tools/robopark"
        reconciler_code = run_with_progress(
            ["python3", "-B", str(successor.resolve()), "update", "--reconcile"],
            environment,
            status,
            result,
            identity,
        )
        return worker_code or reconciler_code
    finally:
        request.unlink(missing_ok=True)
        if staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except Exception as error:  # noqa: BLE001 -- fail closed without leaking archive data
        print("local_update_failed:" + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
