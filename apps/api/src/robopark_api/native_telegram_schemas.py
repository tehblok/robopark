from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ParkBotOut(BaseModel):
    id: int
    name: str
    tag: str
    timezone: str
    chat_id: int | None
    thread_id: int | None
    revision: int


class ParkBotUpdate(BaseModel):
    chat_id: int | None = Field(default=None, ge=-(2**52 - 1), le=2**52 - 1)
    thread_id: int | None = Field(default=None, ge=1, le=2**52 - 1)
    revision: int = Field(ge=1)

    @field_validator("chat_id")
    @classmethod
    def nonzero_chat(cls, value: int | None) -> int | None:
        if value == 0:
            raise ValueError("chat_id must not be zero")
        return value

    @model_validator(mode="after")
    def thread_requires_chat(self):
        if self.thread_id is not None and self.chat_id is None:
            raise ValueError("thread_id requires chat_id")
        return self


class BotJobBase(BaseModel):
    park_id: int
    kind: Literal["report", "text", "zoom", "campaign"]
    title: str = Field(min_length=1, max_length=128)
    enabled: bool = False
    schedule: Literal["daily", "hourly", "once"]
    timezone: str | None = Field(default=None, max_length=64)
    time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    run_at: datetime | None = None
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    start_hour: int | None = Field(default=None, ge=0, le=23)
    end_hour: int | None = Field(default=None, ge=0, le=23)
    text: str | None = Field(default=None, max_length=20_000)
    url: str | None = Field(default=None, max_length=2_048)
    tracker_tag: str | None = Field(default=None, max_length=128)
    alternate: Literal["all", "odd", "even"] = "all"
    anchor_date: date | None = None

    @field_validator("title", "text", "url", "tracker_tag")
    @classmethod
    def strip_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("weekdays")
    @classmethod
    def valid_weekdays(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 6 for day in value) or len(set(value)) != len(value):
            raise ValueError("weekdays must be unique values from 0 through 6")
        return sorted(value)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be an IANA timezone") from exc
        return normalized

    @model_validator(mode="after")
    def valid_shape(self):
        if not self.title:
            raise ValueError("title must not be blank")
        if self.schedule == "daily" and self.time is None:
            raise ValueError("daily jobs require time")
        if self.schedule == "hourly" and (
            self.start_hour is None or self.end_hour is None or self.start_hour > self.end_hour
        ):
            raise ValueError("hourly jobs require an ordered hour range")
        if self.schedule == "once" and (
            self.run_at is None or self.run_at.tzinfo is None or self.run_at.utcoffset() is None
        ):
            raise ValueError("once jobs require an aware run_at")
        if self.schedule != "once" and self.run_at is not None:
            raise ValueError("run_at is only valid for once jobs")
        if self.schedule == "once" and (
            self.time is not None
            or self.start_hour is not None
            or self.end_hour is not None
            or self.weekdays
            or self.alternate != "all"
            or self.anchor_date is not None
        ):
            raise ValueError("once jobs cannot use recurring schedule fields")
        if self.enabled and self.kind == "text" and self.text is None:
            raise ValueError("text jobs require text")
        if self.enabled and self.kind == "zoom" and self.url is None:
            raise ValueError("zoom jobs require url")
        if self.enabled and self.kind == "campaign" and self.tracker_tag is None:
            raise ValueError("campaign jobs require tracker_tag")
        if self.alternate != "all" and self.anchor_date is None:
            raise ValueError("alternating jobs require anchor_date")
        if self.url is not None:
            parsed = urlsplit(self.url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("url must be an http(s) URL without credentials")
        if self.enabled:
            body = (self.text or self.title).replace("{link}", self.url or "")
            if self.url and self.url not in body:
                body += "\n" + self.url
            limit = 500 if self.kind in {"report", "campaign"} else 4096
            if len(body) > limit:
                raise ValueError("effective Telegram body is too long")
        return self


class BotJobCreate(BotJobBase):
    pass


class BotJobUpdate(BotJobBase):
    revision: int = Field(ge=1)


class BotJobOut(BotJobBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    revision: int


class DeliveryOut(BaseModel):
    id: int
    job_id: str
    park_id: int
    title: str
    state: str
    scheduled_at: datetime
    finished_at: datetime | None
    error_code: str | None
    manual: bool
    request_id: UUID | None


class JobRunIn(BaseModel):
    revision: int = Field(ge=1)
    request_id: UUID
    allow_disabled: bool = False


class JobRunOut(BaseModel):
    delivery: DeliveryOut
    created: bool


class NativeBotHealthIn(BaseModel):
    telegram_ok: bool
    scheduler_ok: bool
    last_error: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_.-]+$")


class NativeBotHealthOut(BaseModel):
    state: Literal["ready", "degraded", "offline", "unknown"]
    telegram_ok: bool | None
    scheduler_ok: bool | None
    updated_at: datetime | None
    last_error: str | None


class NativeBotAdminOut(BaseModel):
    parks: list[ParkBotOut]
    jobs: list[BotJobOut]
    deliveries: list[DeliveryOut]
    health: NativeBotHealthOut


class BotAccountOut(BaseModel):
    linked: bool
    telegram_user_id: int | None


class LinkCodeOut(BaseModel):
    code: str
    expires_at: datetime


class LinkCodeIn(BaseModel):
    code: str = Field(pattern=r"^\d{8}$")
    telegram_user_id: int = Field(ge=1, le=2**63 - 1)


class ClaimIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=10)


class LeaseIn(BaseModel):
    lease_token: str = Field(min_length=32, max_length=128)


class FinishIn(LeaseIn):
    state: Literal["sent", "failed", "unknown"]
    error_code: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_.-]+$")


class TelegramContextOut(BaseModel):
    user_id: int
    role: str
    name: str
    parks: list[ParkBotOut]
    can_manage: bool


class AccessParkOut(BaseModel):
    id: int
    name: str


class AccessRequestOut(BaseModel):
    id: int
    revision: int
    user_id: int
    username: str
    role: Literal["mechanic", "operator"]
    user_access_status: Literal["pending", "approved", "rejected"]
    park: AccessParkOut
    status: Literal["pending", "approved", "rejected"]
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: int | None


class NativeAccessOut(BaseModel):
    user_id: int
    username: str
    role: Literal["mechanic", "operator", "admin", "royal"]
    access_status: Literal["pending", "approved", "rejected"]
    can_manage: bool
    assigned_parks: list[AccessParkOut]
    available_parks: list[AccessParkOut]
    requests: list[AccessRequestOut]


class AccessRequestCreate(BaseModel):
    park_id: int = Field(ge=1)


class NativeAccessRequestCreate(AccessRequestCreate):
    telegram_user_id: int = Field(ge=1, le=2**63 - 1)


class AccessDecisionIn(BaseModel):
    approve: bool
    revision: int = Field(ge=1)


class AccessDecisionOut(BaseModel):
    request: AccessRequestOut
    user_access_status: Literal["pending", "approved", "rejected"]


class NativeMigrationApplyIn(BaseModel):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class NativeMigrationParkUpdate(BaseModel):
    park_id: int
    park_tag: str
    location_key: str
    chat_id: int
    thread_id: int | None


class NativeMigrationJob(BaseModel):
    source_ref: str
    source: Literal["schedules", "broadcasts", "campaigns"]
    source_id: str
    park_id: int
    park_tag: str
    title: str
    kind: Literal["report", "text", "zoom", "campaign"]
    schedule: Literal["daily", "hourly", "once"]
    timezone: str | None
    time: str | None
    run_at: datetime | None
    start_hour: int | None
    end_hour: int | None
    weekdays: list[int]
    text: str | None
    url: str | None
    tracker_tag: str | None
    alternate: Literal["all", "odd", "even"]
    anchor_date: date | None


class NativeMigrationConflict(BaseModel):
    source: Literal["locations", "schedules", "broadcasts", "campaigns"]
    source_id: str | None
    park_tag: str | None
    reason: Literal[
        "park_not_found",
        "destination_conflict",
        "location_not_found",
        "unsupported_once",
        "unsupported_shape",
        "already_imported",
    ]


class NativeMigrationCounts(BaseModel):
    park_updates: int
    jobs: int
    conflicts: int
    skipped: int


class NativeMigrationPreview(BaseModel):
    fingerprint: str
    already_applied: bool
    park_updates: list[NativeMigrationParkUpdate]
    jobs: list[NativeMigrationJob]
    conflicts: list[NativeMigrationConflict]
    counts: NativeMigrationCounts


class NativeMigrationApplyOut(NativeMigrationPreview):
    applied: bool
    applied_at: datetime
