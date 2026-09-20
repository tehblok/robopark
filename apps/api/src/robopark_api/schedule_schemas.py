from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ScheduleKind = Literal["shift", "vacation", "sick"]


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


class ScheduleUpdate(BaseModel):
    kind: ScheduleKind | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class ScheduleCopy(BaseModel):
    park_id: int
    source_start: datetime
    source_end: datetime
    target_start: datetime
    owner_user_ids: list[int] = Field(default_factory=list, max_length=100)


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


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=8, max_length=4096)
    p256dh: str = Field(min_length=1, max_length=1024)
    auth: str = Field(min_length=1, max_length=1024)
    expires_at: datetime | None = None


class PushPreferenceIn(BaseModel):
    categories: list[str] = Field(max_length=32)
    system_enabled: bool = True
