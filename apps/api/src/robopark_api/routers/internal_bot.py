"""Private read-only integration routes for the transition Telegram bot."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.services import bot_shared_settings, bot_tracker_gateway, tracker_client

router = APIRouter(prefix="/internal/bot/tracker", tags=["internal-bot"])

_ISSUE_KEY_PATTERN = r"^[A-Z][A-Z0-9_]{1,63}-[1-9][0-9]{0,18}$"
_ORDER_PATTERN = re.compile(r"^[+-]?[A-Za-z][A-Za-z0-9_.]{0,63}$")
IssueKey = Annotated[str, Path(pattern=_ISSUE_KEY_PATTERN, max_length=84)]


class TrackerSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    order: str | list[str] = "+created"
    per_page: int = Field(default=50, ge=1, le=50)
    max_pages: int = Field(default=10, ge=1, le=40)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        if any(ord(character) < 32 for character in stripped):
            raise ValueError("query contains control characters")
        if re.search(r"(?:^|[\s(])queue\s*:", stripped, flags=re.IGNORECASE) is None:
            raise ValueError("query must include an explicit queue scope")
        return stripped

    @field_validator("order")
    @classmethod
    def validate_order(cls, value: str | list[str]) -> str | list[str]:
        fields = [value] if isinstance(value, str) else value
        if not fields or len(fields) > 4:
            raise ValueError("order must contain between one and four fields")
        if any(not isinstance(item, str) or not _ORDER_PATTERN.fullmatch(item) for item in fields):
            raise ValueError("invalid order field")
        return value

    def order_list(self) -> list[str]:
        return [self.order] if isinstance(self.order, str) else self.order


def require_bot_key(request: Request) -> None:
    try:
        authorized = bot_tracker_gateway.bridge_key_matches(
            request.headers.get("X-Robopark-Bot-Key")
        )
    except bot_tracker_gateway.BotBridgeUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="bot_bridge_unavailable",
        ) from None
    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_bot_bridge_key",
        )


def _token(db: Session) -> str:
    try:
        return bot_tracker_gateway.tracker_token(db)
    except bot_tracker_gateway.TrackerNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_not_configured",
        ) from None


def _allowed_tracker_queues(db: Session, settings: Settings) -> tuple[str, ...]:
    try:
        auxiliary_queues = bot_shared_settings.auxiliary_tracker_queues(settings.host_data_path)
    except bot_shared_settings.BotConfigError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="bot_config_unavailable",
        ) from None
    return tuple(
        sorted(
            {
                *bot_tracker_gateway.active_tracker_queues(db),
                *auxiliary_queues,
            }
        )
    )


def _tracker_call(operation):
    try:
        return operation()
    except bot_tracker_gateway.TrackerIssueNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="tracker_issue_not_found",
        ) from None
    except bot_tracker_gateway.TrackerResponseTooLarge:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_response_too_large",
        ) from None
    except bot_tracker_gateway.TrackerQueueNotAuthorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tracker_queue_not_authorized",
        ) from None
    except tracker_client.TrackerError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from None


@router.post("/search", dependencies=[Depends(require_bot_key)])
def search_tracker(
    payload: TrackerSearchIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    token = _token(db)
    allowed_queues = _allowed_tracker_queues(db, settings)
    return _tracker_call(
        lambda: bot_tracker_gateway.search(
            token=token,
            query=payload.query,
            order=payload.order_list(),
            per_page=payload.per_page,
            max_pages=payload.max_pages,
            allowed_queues=allowed_queues,
        )
    )


@router.get("/issue/{key}", dependencies=[Depends(require_bot_key)])
def get_tracker_issue(
    key: IssueKey,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    token = _token(db)
    allowed_queues = _allowed_tracker_queues(db, settings)
    return _tracker_call(
        lambda: bot_tracker_gateway.issue(
            token=token,
            key=key,
            allowed_queues=allowed_queues,
        )
    )


@router.get("/issue/{key}/links", dependencies=[Depends(require_bot_key)])
def get_tracker_issue_links(
    key: IssueKey,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    token = _token(db)
    allowed_queues = _allowed_tracker_queues(db, settings)
    return _tracker_call(
        lambda: bot_tracker_gateway.links(
            token=token,
            key=key,
            allowed_queues=allowed_queues,
        )
    )


@router.get("/issue/{key}/status-changelog", dependencies=[Depends(require_bot_key)])
def get_tracker_status_changelog(
    key: IssueKey,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    token = _token(db)
    allowed_queues = _allowed_tracker_queues(db, settings)
    return _tracker_call(
        lambda: bot_tracker_gateway.status_changelog(
            token=token,
            key=key,
            allowed_queues=allowed_queues,
        )
    )
