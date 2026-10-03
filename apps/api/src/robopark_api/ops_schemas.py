"""Finite public projections at the privileged host boundary."""

import re
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
    "ota_storage": "Хранилище OTA",
    "ota_rollback": "Готовность отката OTA",
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


class UsbDeviceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_uuid: UUID
    removable: bool = Field(strict=True)
    mounted: bool = Field(strict=True)


class CleanupPreviewItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["diagnostics", "logs", "backups", "releases", "ota_cache"]
    path: str
    bytes: int


class CleanupPreviewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: UUID
    blocked: bool
    planned: list[CleanupPreviewItemOut]
    total_bytes: int


class CleanupResultOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deleted: list[CleanupPreviewItemOut]
    deleted_count: int
    uncertain_target: CleanupPreviewItemOut | None = None


class DockerImagePreviewItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tag: str
    reported_bytes: int


class DockerImagePreviewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: UUID | None = None
    blocked: bool
    planned: list[DockerImagePreviewItemOut]
    total_reported_bytes: int
    unverified_tags: int


class DockerImageResultOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deleted: list[DockerImagePreviewItemOut]
    deleted_count: int
    uncertain_target: DockerImagePreviewItemOut | None = None


class BuilderCacheItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    reported_bytes: int


class BuilderCachePreviewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: UUID | None = None
    blocked: bool
    planned: list[BuilderCacheItemOut]
    total_reported_bytes: int
    other_candidates: int


class BuilderCacheResultOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deleted: list[BuilderCacheItemOut]
    deleted_count: int
    uncertain_target: BuilderCacheItemOut | None = None


class PackageResultOut(BaseModel):
    package: Literal[
        "docker-ce", "docker-ce-cli", "containerd.io", "openssl", "python3-cryptography"
    ]
    installed: bool | None = Field(default=None, strict=True)
    version: str | None = Field(default=None, pattern=r"^[A-Za-z0-9.+:~_-]{1,200}$")
    updated: bool | None = Field(default=None, strict=True)


class BackupResultOut(BaseModel):
    backup_id: UUID
    verified: bool | None = Field(default=None, strict=True)
    restored: bool | None = Field(default=None, strict=True)


class UsbResultOut(BaseModel):
    device_uuid: UUID
    selected: bool | None = Field(default=None, strict=True)
    formatted: bool | None = Field(default=None, strict=True)


class ActionResultOut(BaseModel):
    release: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,150}$")
    rolled_back: bool | None = Field(default=None, strict=True)
    service: Literal["robopark.service", "robopark-tuna.service", "docker.service"] | None = None
    restarted: bool | None = Field(default=None, strict=True)
    reboot_scheduled: bool | None = Field(default=None, strict=True)


class HostResultOut(BaseModel):
    before: list[CheckOut] = []
    after: list[CheckOut] = []
    performed: list[str] = []
    failed: list[str] = []
    devices: list[UsbDeviceOut] = []
    cleanup_preview: CleanupPreviewOut | None = None
    cleanup_result: CleanupResultOut | None = None
    docker_image_preview: DockerImagePreviewOut | None = None
    docker_image_result: DockerImageResultOut | None = None
    builder_cache_preview: BuilderCachePreviewOut | None = None
    builder_cache_result: BuilderCacheResultOut | None = None
    package_result: PackageResultOut | None = None
    backup_result: BackupResultOut | None = None
    usb_result: UsbResultOut | None = None
    action_result: ActionResultOut | None = None
    diagnostics_ready: bool = False


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

    def cleanup_item(item):
        if (
            not isinstance(item, dict)
            or item.get("category")
            not in {"diagnostics", "logs", "backups", "releases", "ota_cache"}
            or not isinstance(item.get("path"), str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}", item["path"]) is None
            or type(item.get("bytes")) is not int
            or not 0 <= item["bytes"] < 2**63
        ):
            return None
        return CleanupPreviewItemOut(
            category=item["category"], path=item["path"], bytes=item["bytes"]
        )

    def docker_item(item):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("tag"), str)
            or re.fullmatch(
                r"robopark-(?:api|web):(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|release-[a-f0-9]{64})",
                item["tag"],
            )
            is None
            or type(item.get("reported_bytes")) is not int
            or not 0 <= item["reported_bytes"] < 2**63
        ):
            return None
        return DockerImagePreviewItemOut(tag=item["tag"], reported_bytes=item["reported_bytes"])

    def builder_item(item):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or re.fullmatch(r"[a-z0-9]{1,64}", item["id"]) is None
            or type(item.get("reported_bytes")) is not int
            or not 0 <= item["reported_bytes"] < 2**63
        ):
            return None
        return BuilderCacheItemOut(id=item["id"], reported_bytes=item["reported_bytes"])

    devices = []
    detail = value.get("detail")
    raw_devices = value.get("devices")
    if not isinstance(raw_devices, list):
        raw_devices = detail.get("devices") if isinstance(detail, dict) else None
    if isinstance(raw_devices, list):
        for item in raw_devices[:8]:
            if not isinstance(item, dict):
                continue
            if type(item.get("removable")) is not bool or type(item.get("mounted")) is not bool:
                continue
            try:
                devices.append(
                    UsbDeviceOut(
                        device_uuid=item.get("device_uuid"),
                        removable=item["removable"],
                        mounted=item["mounted"],
                    )
                )
            except ValueError:
                continue

    cleanup_preview = None
    raw_preview = value.get("cleanup_preview")
    if not isinstance(raw_preview, dict):
        raw_preview = (
            detail
            if value.get("kind") == "cleanup-preview"
            and value.get("state") == "succeeded"
            and isinstance(detail, dict)
            else None
        )
    if isinstance(raw_preview, dict):
        raw_plan = raw_preview.get("planned")
        raw_id = raw_preview.get("plan_id")
        if (
            isinstance(raw_id, str)
            and isinstance(raw_preview.get("blocked"), bool)
            and isinstance(raw_plan, list)
            and len(raw_plan) <= 128
        ):
            try:
                plan_id = UUID(raw_id)
            except ValueError:
                plan_id = None
            if plan_id is not None and str(plan_id) == raw_id:
                planned = []
                invalid = False
                for item in raw_plan:
                    cleaned = cleanup_item(item)
                    if cleaned is None:
                        invalid = True
                        continue
                    planned.append(cleaned)
                cleanup_preview = CleanupPreviewOut(
                    plan_id=plan_id,
                    blocked=raw_preview["blocked"] or invalid,
                    planned=planned,
                    total_bytes=sum(item.bytes for item in planned),
                )

    cleanup_result = None
    raw_result = value.get("cleanup_result")
    if not isinstance(raw_result, dict):
        raw_result = (
            detail
            if value.get("kind") == "cleanup-execute"
            and value.get("state") in {"succeeded", "failed"}
            and isinstance(detail, dict)
            else None
        )
    if isinstance(raw_result, dict) and isinstance(raw_result.get("deleted"), list):
        raw_deleted = raw_result["deleted"]
        if len(raw_deleted) <= 128:
            deleted = [cleanup_item(item) for item in raw_deleted]
            if all(item is not None for item in deleted):
                uncertain_raw = raw_result.get("uncertain_target")
                uncertain = cleanup_item(uncertain_raw) if uncertain_raw is not None else None
                cleanup_result = CleanupResultOut(
                    deleted=deleted,
                    deleted_count=len(deleted),
                    uncertain_target=uncertain,
                )

    docker_image_preview = None
    raw_docker = value.get("docker_image_preview")
    if not isinstance(raw_docker, dict):
        raw_docker = (
            detail
            if value.get("kind") == "docker-image-preview"
            and value.get("state") == "succeeded"
            and isinstance(detail, dict)
            else None
        )
    if (
        isinstance(raw_docker, dict)
        and type(raw_docker.get("blocked")) is bool
        and isinstance(raw_docker.get("planned"), list)
        and len(raw_docker["planned"]) <= 32
        and type(raw_docker.get("unverified_tags")) is int
        and 0 <= raw_docker["unverified_tags"] <= 512
    ):
        planned = []
        invalid = False
        for item in raw_docker["planned"]:
            cleaned = docker_item(item)
            if cleaned is None:
                invalid = True
                continue
            planned.append(cleaned)
        total = raw_docker.get("total_reported_bytes")
        upper_bound = sum(item.reported_bytes for item in planned)
        if type(total) is not int or not 0 <= total <= upper_bound:
            invalid = True
            total = upper_bound
        raw_id = raw_docker.get("plan_id")
        try:
            plan_id = UUID(raw_id) if isinstance(raw_id, str) else None
        except ValueError:
            plan_id = None
        if plan_id is not None and str(plan_id) != raw_id:
            plan_id = None
        docker_image_preview = DockerImagePreviewOut(
            plan_id=plan_id if not invalid and not raw_docker["blocked"] else None,
            blocked=raw_docker["blocked"] or invalid,
            planned=planned,
            total_reported_bytes=total,
            unverified_tags=raw_docker["unverified_tags"],
        )

    docker_image_result = None
    raw_image_result = value.get("docker_image_result")
    if not isinstance(raw_image_result, dict):
        raw_image_result = (
            detail
            if value.get("kind") == "docker-image-execute"
            and value.get("state") in {"succeeded", "failed"}
            and isinstance(detail, dict)
            else None
        )
    if isinstance(raw_image_result, dict) and isinstance(raw_image_result.get("deleted"), list):
        raw_deleted = raw_image_result["deleted"]
        if len(raw_deleted) <= 32:
            deleted = [docker_item(item) for item in raw_deleted]
            if all(item is not None for item in deleted):
                uncertain_raw = raw_image_result.get("uncertain_target")
                uncertain = docker_item(uncertain_raw) if uncertain_raw is not None else None
                docker_image_result = DockerImageResultOut(
                    deleted=deleted,
                    deleted_count=len(deleted),
                    uncertain_target=uncertain,
                )

    builder_cache_preview = None
    raw_builder = value.get("builder_cache_preview")
    if not isinstance(raw_builder, dict):
        raw_builder = (
            detail
            if value.get("kind") == "builder-cache-preview"
            and value.get("state") == "succeeded"
            and isinstance(detail, dict)
            else None
        )
    if (
        isinstance(raw_builder, dict)
        and type(raw_builder.get("blocked")) is bool
        and isinstance(raw_builder.get("planned"), list)
        and len(raw_builder["planned"]) <= 1
        and type(raw_builder.get("other_candidates")) is int
        and 0 <= raw_builder["other_candidates"] <= 1024
    ):
        planned = [builder_item(item) for item in raw_builder["planned"]]
        invalid = any(item is None for item in planned)
        planned = [item for item in planned if item is not None]
        total = raw_builder.get("total_reported_bytes")
        expected = sum(item.reported_bytes for item in planned)
        if type(total) is not int or total != expected:
            invalid = True
            total = expected
        raw_id = raw_builder.get("plan_id")
        try:
            plan_id = UUID(raw_id) if isinstance(raw_id, str) else None
        except ValueError:
            plan_id = None
        if plan_id is not None and str(plan_id) != raw_id:
            plan_id = None
        builder_cache_preview = BuilderCachePreviewOut(
            plan_id=plan_id if not invalid and not raw_builder["blocked"] else None,
            blocked=raw_builder["blocked"] or invalid,
            planned=planned,
            total_reported_bytes=total,
            other_candidates=raw_builder["other_candidates"],
        )

    builder_cache_result = None
    raw_builder_result = value.get("builder_cache_result")
    if not isinstance(raw_builder_result, dict):
        raw_builder_result = (
            detail
            if value.get("kind") == "builder-cache-execute"
            and value.get("state") in {"succeeded", "failed"}
            and isinstance(detail, dict)
            else None
        )
    if isinstance(raw_builder_result, dict) and isinstance(raw_builder_result.get("deleted"), list):
        raw_deleted = raw_builder_result["deleted"]
        if len(raw_deleted) <= 1:
            deleted = [builder_item(item) for item in raw_deleted]
            if all(item is not None for item in deleted):
                uncertain_raw = raw_builder_result.get("uncertain_target")
                uncertain = builder_item(uncertain_raw) if uncertain_raw is not None else None
                builder_cache_result = BuilderCacheResultOut(
                    deleted=deleted,
                    deleted_count=len(deleted),
                    uncertain_target=uncertain,
                )

    def finite_result(name, model, kinds):
        raw = value.get(name)
        if not isinstance(raw, dict):
            raw = (
                detail if value.get("kind") in kinds and value.get("state") == "succeeded" else None
            )
        if not isinstance(raw, dict):
            return None
        # Select only known fields: host stdout, filesystem paths and secrets never leave root.
        fields = {key: raw[key] for key in model.model_fields if key in raw}
        if model is UsbResultOut and value.get("kind") == "usb-select":
            fields["selected"] = True
        try:
            return model.model_validate(fields)
        except ValueError:
            return None

    return HostResultOut(
        before=public_checks(value.get("before")),
        after=public_checks(value.get("after")),
        performed=actions("performed"),
        failed=actions("failed"),
        devices=devices,
        cleanup_preview=cleanup_preview,
        cleanup_result=cleanup_result,
        docker_image_preview=docker_image_preview,
        docker_image_result=docker_image_result,
        builder_cache_preview=builder_cache_preview,
        builder_cache_result=builder_cache_result,
        package_result=finite_result(
            "package_result", PackageResultOut, {"package-inspect", "package-update"}
        ),
        backup_result=finite_result(
            "backup_result", BackupResultOut, {"backup", "backup-verify", "backup-restore"}
        ),
        usb_result=finite_result("usb_result", UsbResultOut, {"usb-select", "usb-format"}),
        action_result=finite_result(
            "action_result", ActionResultOut, {"rollback", "service-restart", "reboot"}
        ),
        diagnostics_ready=value.get("diagnostics_ready") is True
        or (
            value.get("kind") == "diagnostics"
            and value.get("state") == "succeeded"
            and isinstance(value.get("job_id"), str)
            and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value["job_id"])
            is not None
            and (
                value.get("artifact")
                or (detail.get("artifact") if isinstance(detail, dict) else None)
            )
            == value["job_id"] + ".zip"
        ),
    )


class HostOperationKind(StrEnum):
    """Protocol kinds, not an executable inventory; consult HostCapabilitiesOut."""

    OTA_UPDATE = "ota-update"
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
    DOCKER_IMAGE_PREVIEW = "docker-image-preview"
    DOCKER_IMAGE_EXECUTE = "docker-image-execute"
    BUILDER_CACHE_PREVIEW = "builder-cache-preview"
    BUILDER_CACHE_EXECUTE = "builder-cache-execute"
    DIAGNOSTICS = "diagnostics"
    USB_DISCOVER = "usb-discover"
    USB_FORMAT = "usb-format"
    USB_SELECT = "usb-select"


class HostOperationCapabilityOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool = Field(strict=True)
    unavailable_reason: (
        Literal["capability_unavailable", "capabilities_unavailable", "context_unavailable"] | None
    )


class HostCapabilitiesOut(BaseModel):
    """Only available=true from a ready snapshot enables a console control."""

    state: Literal["ready", "unavailable"]
    generated_at: datetime | None = None
    expires_at: datetime | None = None
    revision: str | None = None
    operations: dict[HostOperationKind, HostOperationCapabilityOut]


class OperationDeviceOut(UsbDeviceOut):
    path: str | None = Field(default=None, pattern=r"^/dev/[A-Za-z0-9._+-]+$")
    bytes: int | None = Field(default=None, strict=True, ge=0, lt=2**63)


class OperationBackupOut(BaseModel):
    backup_id: UUID
    bytes: int = Field(strict=True, ge=0, lt=2**63)
    verified: bool = Field(strict=True)
    created_at: datetime | None = None


class OperationContextOut(BaseModel):
    packages: list[
        Literal["docker-ce", "docker-ce-cli", "containerd.io", "openssl", "python3-cryptography"]
    ] = Field(max_length=5)
    services: list[Literal["robopark.service", "robopark-tuna.service", "docker.service"]] = Field(
        max_length=3
    )
    rollback_release: str | None = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,150}$")
    selected_device_uuid: UUID | None
    devices: list[OperationDeviceOut] = Field(max_length=8)
    backups: list[OperationBackupOut] = Field(max_length=32)
    generated_at: datetime
    expires_at: datetime


class _HostOperationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    capability_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation: str = Field(min_length=1, max_length=160)


class HostOtaUpdateIn(_HostOperationBase):
    kind: Literal[HostOperationKind.OTA_UPDATE]
    upload_id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,150}$")
    confirmation: str


class HostRollbackIn(_HostOperationBase):
    kind: Literal[HostOperationKind.ROLLBACK]
    release: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,150}$")
    confirmation: str


class HostPackageInspectIn(_HostOperationBase):
    kind: Literal[HostOperationKind.PACKAGE_INSPECT]
    package: Literal[
        "docker-ce", "docker-ce-cli", "containerd.io", "openssl", "python3-cryptography"
    ]


class HostPackageUpdateIn(_HostOperationBase):
    kind: Literal[HostOperationKind.PACKAGE_UPDATE]
    package: Literal[
        "docker-ce", "docker-ce-cli", "containerd.io", "openssl", "python3-cryptography"
    ]
    confirmation: str


class HostServiceRestartIn(_HostOperationBase):
    kind: Literal[HostOperationKind.SERVICE_RESTART]
    service: Literal[
        "robopark.service",
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
    categories: list[Literal["diagnostics", "logs", "backups", "releases", "ota_cache"]] = Field(
        min_length=1, max_length=5
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


class HostDockerImageExecuteIn(_HostOperationBase):
    kind: Literal[HostOperationKind.DOCKER_IMAGE_EXECUTE]
    plan_id: UUID
    confirmation: str


class HostBuilderCacheExecuteIn(_HostOperationBase):
    kind: Literal[HostOperationKind.BUILDER_CACHE_EXECUTE]
    plan_id: UUID
    confirmation: str


class HostNoArgumentIn(_HostOperationBase):
    kind: Literal[
        HostOperationKind.DIAGNOSTICS,
        HostOperationKind.USB_DISCOVER,
        HostOperationKind.DOCKER_IMAGE_PREVIEW,
        HostOperationKind.BUILDER_CACHE_PREVIEW,
    ]


class HostUsbFormatIn(_HostOperationBase):
    kind: Literal[HostOperationKind.USB_FORMAT]
    device_uuid: UUID
    confirmation: str
    confirmation_repeat: str


class HostUsbSelectIn(_HostOperationBase):
    kind: Literal[HostOperationKind.USB_SELECT]
    device_uuid: UUID


HostOperationIn = Annotated[
    HostOtaUpdateIn
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
    | HostDockerImageExecuteIn
    | HostBuilderCacheExecuteIn
    | HostNoArgumentIn
    | HostUsbFormatIn
    | HostUsbSelectIn,
    Field(
        discriminator="kind",
        description=(
            "Protocol request shape only. GET /admin/ops/capabilities determines which kinds "
            "are executable on this host; unavailable kinds reject reauthorization and enqueue."
        ),
    ),
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
