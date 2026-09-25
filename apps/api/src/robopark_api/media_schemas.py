from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class MediaUploadCreateIn(BaseModel):
    media_id: str = Field(min_length=8, max_length=64)
    issue_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=240)
    mime_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(gt=0, le=15 * 1024 * 1024)
    sha256: str = Field(min_length=64, max_length=64)
    dependent_action_id: str | None = Field(default=None, min_length=1, max_length=64)
    device_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("sha256")
    @classmethod
    def valid_sha256(cls, value: str) -> str:
        value = value.lower()
        if any(char not in "0123456789abcdef" for char in value):
            raise ValueError("media_sha256_invalid")
        return value

    @field_validator("dependent_action_id", "device_id")
    @classmethod
    def valid_dependency_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            value != value.strip()
            or not value.isprintable()
            or any(char.isspace() for char in value)
        ):
            raise ValueError("media_dependency_identity_invalid")
        return value

    @model_validator(mode="after")
    def complete_dependency_identity(self):
        if (self.dependent_action_id is None) != (self.device_id is None):
            raise ValueError("media_dependency_identity_incomplete")
        return self


class MediaUploadSessionOut(BaseModel):
    upload_id: str
    received_offset: int
    completed: bool
    media_id: str | None = None
    status: Literal["active", "reinitialized", "completed"]


class MediaChunkOut(BaseModel):
    received_offset: int


class MediaCompleteOut(BaseModel):
    upload_id: str
    media_id: str
    completed: bool = True
