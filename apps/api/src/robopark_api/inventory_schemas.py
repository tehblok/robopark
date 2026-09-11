from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class InventoryPartOut(BaseModel):
    id: int
    catalog_part_id: int
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
    park_id: int | None = None
    catalog_part_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    article: str | None = Field(default=None, min_length=1, max_length=128)
    component_id: int | None = None
    location: str | None = Field(default=None, min_length=1, max_length=256)
    minimum_quantity: int | None = Field(default=None, ge=0, le=1_000_000)
    is_active: bool | None = None


class InventoryMovementIn(BaseModel):
    park_id: int | None = None
    catalog_part_id: int | None = None
    kind: Literal["receipt", "writeoff", "adjustment"]
    quantity: int = Field(ge=-1_000_000, le=1_000_000)
    note: str | None = Field(default=None, max_length=500)


class InventoryTaskWriteoffIn(BaseModel):
    part_id: int
    catalog_part_id: int | None = None
    park_id: int | None = None
    quantity: int = Field(gt=0, le=1_000_000)


class InventoryMovementOut(BaseModel):
    id: int
    part_id: int
    catalog_part_id: int | None
    park_id: int
    actor_user_id: int
    actor_username: str
    kind: str
    delta: int
    balance_after: int
    issue_key: str | None
    note: str | None
    created_at: datetime


class InventoryCatalogComponentCreateIn(BaseModel):
    park_id: int
    name: str = Field(min_length=1, max_length=128)


class InventoryCatalogComponentOut(BaseModel):
    id: int
    name: str
    is_active: bool
    has_photo: bool


class InventoryCatalogComponentUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    is_active: bool | None = None


class InventoryCatalogPartCreateIn(BaseModel):
    park_id: int
    component_id: int
    name: str = Field(min_length=1, max_length=128)
    article: str = Field(min_length=1, max_length=128)


class InventoryCatalogPartUpdateIn(BaseModel):
    component_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    article: str | None = Field(default=None, min_length=1, max_length=128)
    is_active: bool | None = None


class InventoryCatalogPartOut(BaseModel):
    id: int
    component_id: int
    name: str
    article: str
    is_active: bool
    has_photo: bool


class InventoryCatalogPartMergeIn(BaseModel):
    target_part_id: int


class InventoryCatalogSearchItem(InventoryCatalogPartOut):
    component_name: str
    quantity: int
    minimum_quantity: int
    location: str | None
    stock_is_active: bool


class InventoryCatalogSearchOut(BaseModel):
    items: list[InventoryCatalogSearchItem]
    limit: int
    offset: int
    total: int


class InventoryStockUpdateIn(BaseModel):
    minimum_quantity: int = Field(ge=0, le=1_000_000)
    location: str | None = Field(default=None, max_length=256)
    is_active: bool


class InventoryStockOut(BaseModel):
    park_id: int
    catalog_part_id: int
    quantity: int
    minimum_quantity: int
    location: str | None
    is_active: bool
    version: int
