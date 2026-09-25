from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

ScheduleKind = Literal["shift", "vacation", "sick"]
SchedulePattern = Literal["none", "5/2", "2/2", "4/4"]


class ScheduleCreate(BaseModel):
    park_id: int
    kind: ScheduleKind
    start_at: datetime
    end_at: datetime
    owner_user_id: int | None = None

    @model_validator(mode="after")
    def valid_range(self):
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("timezone_required")
        if self.end_at <= self.start_at:
            raise ValueError("invalid_range")
        return self


class ScheduleBulkCreate(ScheduleCreate):
    owner_user_id: None = None
    owner_user_ids: list[int] = Field(min_length=1, max_length=100)
    repeat_count: int = Field(default=1, ge=1, le=52)
    repeat_every_days: int = Field(default=7, ge=1, le=365)


class SchedulePatternCreate(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    park_id: int
    owner_user_ids: list[int] = Field(min_length=1, max_length=50)
    kind: ScheduleKind
    start_date: date
    end_date: date
    start_time: time
    end_time: time
    pattern: SchedulePattern
    timezone: str = "Europe/Moscow"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("invalid_timezone") from exc
        return value

    @model_validator(mode="after")
    def valid_pattern_range(self):
        if len(set(self.owner_user_ids)) != len(self.owner_user_ids):
            raise ValueError("duplicate_owner_ids")
        day_count = (self.end_date - self.start_date).days + 1
        if day_count < 1:
            raise ValueError("invalid_range")
        if day_count > 366:
            raise ValueError("range_too_large")
        on_days, cycle_days = {
            "none": (1, day_count),
            "5/2": (5, 7),
            "2/2": (2, 4),
            "4/4": (4, 8),
        }[self.pattern]
        generated_days = 1 if self.pattern == "none" else sum(
            offset % cycle_days < on_days for offset in range(day_count)
        )
        if generated_days * len(self.owner_user_ids) > 5000:
            raise ValueError("too_many_entries")
        return self


class ScheduleUpdate(BaseModel):
    kind: ScheduleKind | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class ScheduleCopy(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    park_id: int
    source_start: datetime
    source_end: datetime
    target_start: datetime
    owner_user_ids: list[int] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_copy_range(self):
        if any(
            value.tzinfo is None
            for value in (self.source_start, self.source_end, self.target_start)
        ):
            raise ValueError("timezone_required")
        if self.source_end <= self.source_start:
            raise ValueError("invalid_range")
        return self


class ScheduleOut(BaseModel):
    id: str
    owner_user_id: int
    park_id: int
    kind: str
    start_at: datetime
    end_at: datetime
    source: str
    series_id: str | None
    created_by_user_id: int
    updated_by_user_id: int
    created_at: datetime
    updated_at: datetime
    warnings: list[str] = []


class ScheduleParticipantOut(BaseModel):
    id: int
    display_name: str
    role: Literal["mechanic", "operator"]


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=8, max_length=4096)
    p256dh: str = Field(min_length=1, max_length=1024)
    auth: str = Field(min_length=1, max_length=1024)
    expires_at: datetime | None = None


class PushPreferenceIn(BaseModel):
    categories: list[str] = Field(max_length=32)
    system_enabled: bool = True
