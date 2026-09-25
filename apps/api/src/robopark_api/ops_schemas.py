"""Finite public projections at the privileged host boundary."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class SyncHealthOut(BaseModel):
    cursor_age_seconds: int | None = None
    pending_action_count: int = 0
    oldest_pending_action_age_seconds: int | None = None
    retry_count: int = 0
    needs_attention_count: int = 0
    last_success_at: datetime | None = None
    last_error: Literal["tracker_unavailable", "poll_failed"] | None = None
    worker_lease_state: Literal["active", "stale", "unknown"] = "unknown"


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


class HostOperationKind(StrEnum):
    RELEASE_UPDATE = "release-update"
    REINSTALL = "reinstall"
    ROLLBACK = "rollback"
    PACKAGE_INSPECT = "package-inspect"
    PACKAGE_UPDATE = "package-update"
    SERVICE_RESTART = "service-restart"
    REBOOT = "reboot"
    BACKUP = "backup"
    BACKUP_VERIFY = "backup-verify"
    BACKUP_RESTORE = "backup-restore"
    CLEANUP_PREVIEW = "cleanup-preview"
    CLEANUP_EXECUTE = "cleanup-execute"
    DIAGNOSTICS = "diagnostics"
    USB_DISCOVER = "usb-discover"
    USB_FORMAT = "usb-format"
    USB_SELECT = "usb-select"


class _HostOperationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID


class HostReleaseUpdateIn(_HostOperationBase):
    kind: Literal[HostOperationKind.RELEASE_UPDATE]
    release_id: int = Field(strict=True, gt=0, lt=2**63)
    confirmation: str


class HostReinstallIn(_HostOperationBase):
    kind: Literal[HostOperationKind.REINSTALL]
    confirmation: str


class HostRollbackIn(_HostOperationBase):
    kind: Literal[HostOperationKind.ROLLBACK]
    release: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,150}$")
    confirmation: str


class HostPackageInspectIn(_HostOperationBase):
    kind: Literal[HostOperationKind.PACKAGE_INSPECT]
    package: Literal["docker-ce", "docker-ce-cli", "containerd.io", "openssl"]


class HostPackageUpdateIn(_HostOperationBase):
    kind: Literal[HostOperationKind.PACKAGE_UPDATE]
    package: Literal["docker-ce", "docker-ce-cli", "containerd.io", "openssl"]
    confirmation: str


class HostServiceRestartIn(_HostOperationBase):
    kind: Literal[HostOperationKind.SERVICE_RESTART]
    service: Literal[
        "robopark-api.service",
        "robopark-worker.service",
        "robopark-tuna.service",
        "docker.service",
    ]
    confirmation: str


class HostRebootIn(_HostOperationBase):
    kind: Literal[HostOperationKind.REBOOT]
    confirmation: str


class HostBackupIn(_HostOperationBase):
    kind: Literal[HostOperationKind.BACKUP]
    device_uuid: UUID


class HostBackupReferenceIn(_HostOperationBase):
    kind: Literal[HostOperationKind.BACKUP_VERIFY]
    backup_id: UUID


class HostBackupRestoreIn(_HostOperationBase):
    kind: Literal[HostOperationKind.BACKUP_RESTORE]
    backup_id: UUID
    confirmation: str


class HostCleanupPreviewIn(_HostOperationBase):
    kind: Literal[HostOperationKind.CLEANUP_PREVIEW]
    categories: list[Literal["diagnostics", "logs", "backups", "releases"]] = Field(
        min_length=1, max_length=4
    )

    @field_validator("categories")
    @classmethod
    def unique_categories(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("duplicate_cleanup_category")
        return value


class HostCleanupExecuteIn(_HostOperationBase):
    kind: Literal[HostOperationKind.CLEANUP_EXECUTE]
    plan_id: UUID
    confirmation: str


class HostNoArgumentIn(_HostOperationBase):
    kind: Literal[HostOperationKind.DIAGNOSTICS, HostOperationKind.USB_DISCOVER]


class HostUsbFormatIn(_HostOperationBase):
    kind: Literal[HostOperationKind.USB_FORMAT]
    device_uuid: UUID
    confirmation: str
    confirmation_repeat: str


class HostUsbSelectIn(_HostOperationBase):
    kind: Literal[HostOperationKind.USB_SELECT]
    device_uuid: UUID


HostOperationIn = Annotated[
    HostReleaseUpdateIn
    | HostReinstallIn
    | HostRollbackIn
    | HostPackageInspectIn
    | HostPackageUpdateIn
    | HostServiceRestartIn
    | HostRebootIn
    | HostBackupIn
    | HostBackupReferenceIn
    | HostBackupRestoreIn
    | HostCleanupPreviewIn
    | HostCleanupExecuteIn
    | HostNoArgumentIn
    | HostUsbFormatIn
    | HostUsbSelectIn,
    Field(discriminator="kind"),
]


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
