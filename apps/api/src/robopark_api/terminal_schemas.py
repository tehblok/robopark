from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Profile = Literal["maintenance", "root"]


class CreateSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    profile: Profile
    capability_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    profile: Profile
    state: Literal["starting", "detached", "active", "ended"]
    expires_at: datetime
    broker_epoch: str
    termination_reason: str | None

    @field_validator("expires_at")
    @classmethod
    def timezone_explicit(cls, value):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
