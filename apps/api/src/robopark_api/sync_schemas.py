from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SyncActionIn(BaseModel):
    client_action_id: str = Field(min_length=1, max_length=64)
    resource_type: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=128)
    base_revision: str | None = Field(default=None, max_length=128)
    park_id: int | None = Field(default=None, ge=1)
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("dependencies")
    @classmethod
    def unique_dependencies(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item or len(item) > 64 for item in value):
            raise ValueError("sync_dependencies_invalid")
        return value


class SyncBatchIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    known_revisions: dict[str, int] = Field(default_factory=dict)
    actions: list[SyncActionIn] = Field(default_factory=list, max_length=50)

    @field_validator("known_revisions")
    @classmethod
    def valid_revisions(cls, value: dict[str, int]) -> dict[str, int]:
        if any(len(key) > 64 or revision < 0 for key, revision in value.items()):
            raise ValueError("sync_revisions_invalid")
        return value


class SyncActionResultOut(BaseModel):
    client_action_id: str
    state: Literal["confirmed", "conflict", "attention", "rejected"]
    code: str | None = None
    result: dict[str, Any] | None = None


class SyncBatchOut(BaseModel):
    results: list[SyncActionResultOut]
    deltas: dict[str, list[dict[str, Any]]]
    revisions: dict[str, int]
    revoked_scopes: list[str]
