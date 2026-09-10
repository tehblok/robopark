from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class InventoryPartOut(BaseModel):
    id: int
    park_id: int
    component_id: int
    name: str
    article: str
    quantity: int
    minimum_quantity: int
    location: str
    is_active: bool
    has_photo: bool


class InventoryComponentOut(BaseModel):
    id: int
    park_id: int
    name: str
    has_photo: bool
    parts: list[InventoryPartOut]


class InventoryOverviewOut(BaseModel):
    park_id: int
    component_count: int
    part_count: int
    low_stock_count: int
    out_of_stock_count: int
    components: list[InventoryComponentOut]


class InventoryPartUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    article: str | None = Field(default=None, min_length=1, max_length=128)
    component_id: int | None = None
    location: str | None = Field(default=None, min_length=1, max_length=256)
    minimum_quantity: int | None = Field(default=None, ge=0, le=1_000_000)
    is_active: bool | None = None


class InventoryMovementIn(BaseModel):
    kind: Literal["receipt", "writeoff", "adjustment"]
    quantity: int = Field(gt=0, le=1_000_000)
    note: str | None = Field(default=None, max_length=500)


class InventoryTaskWriteoffIn(BaseModel):
    part_id: int
    quantity: int = Field(gt=0, le=1_000_000)


class InventoryMovementOut(BaseModel):
    id: int
    part_id: int
    park_id: int
    actor_user_id: int
    actor_username: str
    kind: str
    delta: int
    balance_after: int
    issue_key: str | None
    note: str | None
    created_at: datetime
