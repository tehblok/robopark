from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from robopark_api.models import INVENTORY_INT64_MAX, INVENTORY_INT64_MIN


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
    minimum_quantity: int | None = Field(default=None, ge=0, le=INVENTORY_INT64_MAX)
    is_active: bool | None = None


class InventoryMovementIn(BaseModel):
    park_id: int | None = None
    catalog_part_id: int | None = None
    kind: Literal["receipt", "writeoff", "adjustment"]
    quantity: int = Field(ge=INVENTORY_INT64_MIN, le=INVENTORY_INT64_MAX)
    note: str | None = Field(default=None, max_length=500)


class InventoryTaskWriteoffIn(BaseModel):
    part_id: int
    catalog_part_id: int | None = None
    park_id: int | None = None
    quantity: int = Field(gt=0, le=INVENTORY_INT64_MAX)
    idempotency_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(
        max_length=128
    )


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


class InventoryCatalogComponentListOut(BaseModel):
    items: list[InventoryCatalogComponentOut]
    limit: int
    offset: int
    total: int


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
    minimum_quantity: int = Field(ge=0, le=INVENTORY_INT64_MAX)
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


class InventoryReceiptLineIn(BaseModel):
    catalog_part_id: int
    quantity: int = Field(gt=0, le=INVENTORY_INT64_MAX)
    note: str | None = Field(default=None, max_length=500)


class InventoryReceiptCreateIn(BaseModel):
    supplier: str | None = Field(default=None, max_length=256)
    document_number: str | None = Field(default=None, max_length=128)
    received_on: date
    comment: str | None = None
    lines: list[InventoryReceiptLineIn] = Field(min_length=1)


class InventoryReceiptUpdateIn(BaseModel):
    supplier: str | None = Field(default=None, max_length=256)
    document_number: str | None = Field(default=None, max_length=128)
    received_on: date | None = None
    comment: str | None = None
    lines: list[InventoryReceiptLineIn] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        if "received_on" in self.model_fields_set and self.received_on is None:
            raise ValueError("inventory_receipt_date_required")
        if "lines" in self.model_fields_set and self.lines is None:
            raise ValueError("inventory_receipt_lines_required")
        return self


class InventoryReceiptLineOut(BaseModel):
    id: int
    catalog_part_id: int
    catalog_part_name: str
    catalog_part_article: str
    catalog_component_id: int
    catalog_component_name: str
    quantity: int
    note: str | None


class InventoryReceiptOut(BaseModel):
    id: int
    park_id: int
    supplier: str | None
    document_number: str | None
    received_on: date
    comment: str | None
    status: Literal["draft", "posted", "cancelled"]
    created_by: int
    posted_by: int | None
    created_at: datetime
    posted_at: datetime | None
    lines: list[InventoryReceiptLineOut]


class InventoryReceiptListOut(BaseModel):
    items: list[InventoryReceiptOut]
    limit: int
    offset: int
    total: int


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class InventoryReceiptReversalIn(BaseModel):
    reason: NonEmptyStr = Field(max_length=500)


class InventoryCountScopeIn(BaseModel):
    kind: Literal["all", "component"]
    component_id: int | None = None

    @model_validator(mode="after")
    def validate_component_scope(self):
        if self.kind == "component" and self.component_id is None:
            raise ValueError("inventory_count_component_required")
        if self.kind == "all" and self.component_id is not None:
            raise ValueError("inventory_count_component_unexpected")
        return self


class InventoryCountCreateIn(BaseModel):
    name: NonEmptyStr = Field(max_length=128)
    scope: InventoryCountScopeIn


class InventoryCountLineUpdateIn(BaseModel):
    catalog_part_id: int
    actual_quantity: int = Field(ge=0, le=INVENTORY_INT64_MAX)
    comment: str | None = Field(default=None, max_length=500)


class InventoryCountUpdateIn(BaseModel):
    lines: list[InventoryCountLineUpdateIn] = Field(min_length=1)


class InventoryCountLineOut(BaseModel):
    id: int
    catalog_part_id: int
    catalog_part_name: str
    catalog_part_article: str
    catalog_component_id: int
    catalog_component_name: str
    expected_quantity: int
    actual_quantity: int | None
    difference: int | None
    comment: str | None


class InventoryCountOut(BaseModel):
    id: int
    park_id: int
    name: str
    status: Literal["draft", "posted", "cancelled"]
    created_by: int
    posted_by: int | None
    created_at: datetime
    posted_at: datetime | None
    lines: list[InventoryCountLineOut]


class InventoryCountListOut(BaseModel):
    items: list[InventoryCountOut]
    limit: int
    offset: int
    total: int
