from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.crypto import MissingSecretKeyError
from robopark_api.db import get_db
from robopark_api.deps import require_royal
from robopark_api.models import User
from robopark_api.routers.internal_bot import require_bot_key
from robopark_api.services import audit, bot_import, bot_settings, platform_settings

router = APIRouter(tags=["admin-bot"])

_MAX_IMPORT_ARCHIVE_BYTES = 16 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024


class BotStatusOut(BaseModel):
    desired_enabled: bool
    runtime_state: str
    token_configured: bool
    token_masked: str | None
    token_updated_at: str | None
    token_encrypted: bool


class TelegramTokenUpdate(BaseModel):
    token: str = Field(min_length=1, max_length=256)

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str) -> str:
        token = value.strip()
        if not token:
            raise ValueError("token must not be blank")
        return token


class BotEnabledUpdate(BaseModel):
    enabled: bool


class TelegramTokenOut(BaseModel):
    token: str


class ImportedFileOut(BaseModel):
    filename: str
    byte_count: int
    sha256: str


class BotImportOut(BaseModel):
    files: list[ImportedFileOut]
    total_bytes: int


def _control_error(exc: Exception) -> HTTPException:
    detail = (
        "bot_host_control_state_invalid"
        if isinstance(exc, bot_settings.BotControlStateInvalid)
        else "bot_host_control_unavailable"
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


def _status(db: Session, settings: Settings) -> BotStatusOut:
    try:
        data = bot_settings.status(db, settings.host_data_path)
    except (bot_settings.BotControlUnavailable, bot_settings.BotControlStateInvalid) as exc:
        raise _control_error(exc) from None
    return BotStatusOut(**data)


def _import_out(report: bot_import.BotImportReport) -> BotImportOut:
    return BotImportOut(
        files=[
            ImportedFileOut(
                filename=item.filename,
                byte_count=item.byte_count,
                sha256=item.sha256,
            )
            for item in report.files
        ],
        total_bytes=report.total_bytes,
    )


def _import_error(exc: bot_import.BotImportError) -> HTTPException:
    code = str(exc)
    conflict = {
        "destination_changed",
        "destination_not_empty",
    }
    return HTTPException(
        status_code=(
            status.HTTP_409_CONFLICT if code in conflict else status.HTTP_422_UNPROCESSABLE_CONTENT
        ),
        detail=code,
    )


@asynccontextmanager
async def _staged_archive(upload: UploadFile):
    path: Path | None = None
    size = 0
    try:
        with tempfile.NamedTemporaryFile(
            prefix="robopark-bot-import-",
            suffix=".zip",
            delete=False,
        ) as target:
            path = Path(target.name)
            while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > _MAX_IMPORT_ARCHIVE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="bot_import_archive_too_large",
                    )
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if size == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="bot_import_archive_empty",
            )
        yield path
    finally:
        await upload.close()
        if path is not None:
            with suppress(FileNotFoundError):
                path.unlink()


@router.get("/admin/bot", response_model=BotStatusOut)
def get_bot_status(
    db: Session = Depends(get_db),
    _royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
) -> BotStatusOut:
    return _status(db, settings)


@router.put("/admin/bot/token", response_model=BotStatusOut)
def put_bot_token(
    payload: TelegramTokenUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
) -> BotStatusOut:
    try:
        with bot_settings.operation_lock(settings.host_data_path):
            if bot_settings.desired_enabled(settings.host_data_path):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="telegram_bot_disable_before_rotation",
                )
            bot_settings.set_token(db, payload.token)
    except MissingSecretKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="secret_key_required",
        ) from exc
    except (bot_settings.BotControlUnavailable, bot_settings.BotControlStateInvalid) as exc:
        raise _control_error(exc) from None
    audit.record(
        db,
        action=audit.ACTION_SETTINGS_CHANGED,
        actor=actor,
        detail="Telegram bot token updated",
    )
    return _status(db, settings)


@router.put("/admin/bot/enabled", response_model=BotStatusOut)
def put_bot_enabled(
    payload: BotEnabledUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
) -> BotStatusOut:
    if payload.enabled and not platform_settings.get_telegram_bot_token(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="telegram_bot_token_required",
        )
    try:
        bot_settings.set_desired_enabled(settings.host_data_path, payload.enabled)
    except (bot_settings.BotControlUnavailable, bot_settings.BotControlStateInvalid) as exc:
        raise _control_error(exc) from None
    audit.record(
        db,
        action=audit.ACTION_SETTINGS_CHANGED,
        actor=actor,
        detail=f"Telegram bot {'enabled' if payload.enabled else 'disabled'}",
    )
    return _status(db, settings)


@router.post("/admin/bot/import/preview", response_model=BotImportOut)
async def preview_bot_import(
    archive: UploadFile = File(...),
    _royal: User = Depends(require_royal),
) -> BotImportOut:
    async with _staged_archive(archive) as archive_path:
        try:
            return _import_out(bot_import.preview_bot_export(archive_path))
        except bot_import.BotImportError as exc:
            raise _import_error(exc) from None


@router.post("/admin/bot/import/execute", response_model=BotImportOut)
async def execute_bot_import(
    archive: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
) -> BotImportOut:
    destination = Path(settings.host_data_path) / "telegram-bot" / "data"
    async with _staged_archive(archive) as archive_path:
        try:
            with bot_settings.operation_lock(settings.host_data_path):
                if bot_settings.desired_enabled(settings.host_data_path):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="telegram_bot_must_be_disabled",
                    )
                report = bot_import.import_bot_export(archive_path, destination)
        except HTTPException:
            raise
        except (
            bot_settings.BotControlUnavailable,
            bot_settings.BotControlStateInvalid,
        ) as exc:
            raise _control_error(exc) from None
        except bot_import.BotImportError as exc:
            raise _import_error(exc) from None
    audit.record(
        db,
        action=audit.ACTION_SETTINGS_CHANGED,
        actor=actor,
        detail=f"Telegram bot data imported ({report.total_bytes} bytes)",
    )
    return _import_out(report)


@router.get(
    "/internal/bot/telegram-token",
    response_model=TelegramTokenOut,
    dependencies=[Depends(require_bot_key)],
    include_in_schema=False,
)
def get_internal_telegram_token(
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TelegramTokenOut:
    try:
        token = bot_settings.runtime_token(db, settings.host_data_path)
    except bot_settings.TelegramBotDisabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="telegram_bot_disabled",
        ) from None
    except bot_settings.TelegramBotTokenNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="telegram_bot_token_not_configured",
        ) from None
    except (bot_settings.BotControlUnavailable, bot_settings.BotControlStateInvalid) as exc:
        raise _control_error(exc) from None
    response.headers["Cache-Control"] = "no-store"
    return TelegramTokenOut(token=token)
