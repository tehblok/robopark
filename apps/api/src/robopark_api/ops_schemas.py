"""Finite public projections at the privileged host boundary."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CHECK_LABELS = {
    "supported_platform": "ОС и архитектура",
    "clock_sync": "Синхронизация часов",
    "dns": "DNS",
    "outbound_https": "Исходящий HTTPS",
    "resources": "Диск",
    "inode_space": "Inode",
    "memory_load_swap": "Память",
    "load": "Нагрузка",
    "temperature": "Температура",
    "docker_daemon": "Docker",
    "compose": "Compose",
    "containers": "Контейнеры",
    "release_layout": "Релиз",
    "configuration": "Конфигурация",
    "database_unavailable": "База данных и миграции",
    "local_endpoint": "Веб-сервис",
    "api_readiness": "Готовность API",
    "tuna_binary": "Tuna",
    "tuna_inactive": "Сервис Tuna",
    "tuna_route": "HTTPS-маршрут",
    "tuna_certificate": "Сертификат HTTPS",
    "updater": "Обновления",
    "integrations": "Интеграции",
    "backup": "Резервная копия",
    "diagnostic_artifacts": "Диагностика",
    "log_growth": "Журналы",
}
REPAIR_IDS = {"restart_docker", "restart_app", "restart_tuna", "daemon_reload"}


class CheckOut(BaseModel):
    code: str
    status: Literal["ok", "warning", "failed"]
    message: str
    repair: str | None = None


def public_checks(value):
    if not isinstance(value, list):
        return []
    return [
        CheckOut(
            code=item["code"],
            status=item["status"],
            message=CHECK_LABELS[item["code"]],
            repair=item.get("repair")
            if isinstance(item.get("repair"), str) and item.get("repair") in REPAIR_IDS
            else None,
        )
        for item in value[:64]
        if isinstance(item, dict)
        and isinstance(item.get("code"), str)
        and item.get("code") in CHECK_LABELS
        and item.get("status") in ("ok", "warning", "failed")
    ]


class UpdateOut(BaseModel):
    state: Literal[
        "idle", "updating", "current_healthy", "rolled_back", "maintenance", "unknown"
    ] = "unknown"
    publication: Literal["degraded"] | None = None


class BackupOut(BaseModel):
    status: Literal["success", "failed", "unknown"] = "unknown"
    completed_at: datetime | None = None


class SystemHealthOut(BaseModel):
    version: str | None = None
    git_sha: str | None = None
    generated_at: datetime | None = None
    overall: Literal["ok", "degraded", "unknown"] = "unknown"
    checks: list[CheckOut] = []
    update: UpdateOut = UpdateOut()
    last_backup: BackupOut = BackupOut()


class UpdateInspectionOut(BaseModel):
    inspection_id: UUID
    version: str
    git_sha: str
    migration_head: str
    notes: str


class UpdateApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inspection_id: UUID
    confirm: str


class HostResultOut(BaseModel):
    before: list[CheckOut] = []
    after: list[CheckOut] = []
    performed: list[str] = []
    failed: list[str] = []


def public_result(value):
    if not isinstance(value, dict):
        return None

    def actions(name):
        raw = value.get(name)
        return (
            [item for item in raw[:8] if isinstance(item, str) and item in REPAIR_IDS]
            if isinstance(raw, list)
            else []
        )

    return HostResultOut(
        before=public_checks(value.get("before")),
        after=public_checks(value.get("after")),
        performed=actions("performed"),
        failed=actions("failed"),
    )


class AvailableReleaseOut(BaseModel):
    release_id: int = Field(strict=True, gt=0, lt=2**63)
    version: str = Field(
        max_length=100,
        pattern=r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$",
    )
    git_sha: str = Field(pattern=r"^[a-fA-F0-9]{40}$")
    size: int = Field(strict=True, gt=0, le=512 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class AvailableUpdateOut(BaseModel):
    state: Literal["available", "up_to_date", "discovery_stale", "disabled", "manual", "approved"] = (
        "discovery_stale"
    )
    checked_at: datetime | None = None
    release: AvailableReleaseOut | None = None


class GithubApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_id: int = Field(strict=True, gt=0, lt=2**63)
    confirm: str


class ReleaseStatusOut(BaseModel):
    version: str | None = None
    build_id: str | None = None
    git_sha: str | None = None
    channel: Literal["stable", "rc", "manual"] | None = None
    support_class: Literal["candidate", "standard", "lts"] | None = None
    released_at: datetime | None = None
    supported_until: datetime | None = None
    support_status: Literal["supported", "ending", "expired", "unknown"] = "unknown"
    operations_blocked: bool = False
    database_head: str | None = None
    installer_version: str | None = None
    available_update: dict | None = None
    bridges: list[str] = []
    cleanup: dict | None = None
