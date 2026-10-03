from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_royal
from robopark_api.models import User
from robopark_api.services import audit, bot_shared_settings

router = APIRouter(prefix="/admin/bot/config", tags=["admin-bot"])


class SectionOut(BaseModel):
    revision: str
    value: Any


class ConfigOut(BaseModel):
    sections: dict[str, SectionOut]


class SectionUpdate(BaseModel):
    value: Any


def _out(section: bot_shared_settings.Section) -> SectionOut:
    return SectionOut(revision=section.revision, value=section.value)


def _error(exc: bot_shared_settings.BotConfigError, section: str | None = None) -> HTTPException:
    name = section or (str(exc) if str(exc) else None)
    if isinstance(exc, bot_shared_settings.UnknownSection):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="bot_config_section_not_found"
        )
    if isinstance(exc, bot_shared_settings.RevisionConflict):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="bot_config_revision_conflict"
        )
    if isinstance(exc, bot_shared_settings.BotMustBeDisabled):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="telegram_bot_must_be_disabled"
        )
    if isinstance(exc, bot_shared_settings.UnsafeState):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"bot_config_unsafe_state:{name or 'unknown'}",
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"bot_config_invalid_schema:{name or 'unknown'}",
    )


@router.get("", response_model=ConfigOut)
def get_bot_config(
    _royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
) -> ConfigOut:
    try:
        sections = bot_shared_settings.read_all(settings.host_data_path)
    except bot_shared_settings.BotConfigError as exc:
        raise _error(exc) from None
    return ConfigOut(sections={name: _out(section) for name, section in sections.items()})


@router.put("/{section}", response_model=SectionOut)
def put_bot_config_section(
    section: str,
    payload: SectionUpdate,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    actor: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
) -> SectionOut:
    if section not in bot_shared_settings.SECTION_NAMES:
        raise _error(bot_shared_settings.UnknownSection())
    if if_match is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="bot_config_revision_required",
        )
    try:
        saved = bot_shared_settings.update(
            settings.host_data_path,
            section,
            payload.value,
            if_match,
        )
    except bot_shared_settings.BotConfigError as exc:
        raise _error(exc, section) from None
    audit.record(
        db,
        action=audit.ACTION_SETTINGS_CHANGED,
        actor=actor,
        detail=f"Telegram bot configuration changed: {section}",
    )
    return _out(saved)
