from __future__ import annotations

from pydantic import BaseModel, Field


class NativeUsageStatsOut(BaseModel):
    today: int = Field(ge=0)
    month: int = Field(ge=0)
    total: int = Field(ge=0)


class NativeUsageUserOut(BaseModel):
    user_id: int
    username: str
    telegram_user_id: int | None
    stats: NativeUsageStatsOut


class NativeUsageQueryOut(BaseModel):
    stats: NativeUsageStatsOut
    greeting: str | None


class NativeBotControlOut(BaseModel):
    queries_paused: bool
    deliveries_paused: bool
    revision: int = Field(ge=1)


class NativeBotControlUpdate(BaseModel):
    queries_paused: bool
    deliveries_paused: bool
    revision: int = Field(ge=1)
