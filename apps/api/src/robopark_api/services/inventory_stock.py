from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import (
    InventoryCatalogPart,
    InventoryMovement,
    InventoryParkStock,
    User,
)


class InventoryConflict(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        current_quantity: int | None = None,
        existing_part_id: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.current_quantity = current_quantity
        self.existing_part_id = existing_part_id

    @property
    def detail(self) -> dict[str, int | str]:
        result: dict[str, int | str] = {"code": self.code}
        if self.current_quantity is not None:
            result["current_quantity"] = self.current_quantity
        if self.existing_part_id is not None:
            result["existing_part_id"] = self.existing_part_id
        return result


def ensure_stock(db: Session, *, park_id: int, catalog_part_id: int) -> InventoryParkStock:
    if db.get(InventoryCatalogPart, catalog_part_id) is None:
        raise LookupError("inventory_part_not_found")
    stock = db.scalar(
        select(InventoryParkStock)
        .where(
            InventoryParkStock.park_id == park_id,
            InventoryParkStock.catalog_part_id == catalog_part_id,
        )
        .with_for_update()
    )
    if stock is None:
        stock = InventoryParkStock(park_id=park_id, catalog_part_id=catalog_part_id)
        db.add(stock)
        db.flush()
    return stock


def apply_stock_delta(
    db: Session,
    *,
    user: User,
    park_id: int,
    catalog_part_id: int,
    delta: int,
    kind: str,
    source_kind: str | None,
    source_id: str | None,
    note: str | None,
    issue_key: str | None = None,
) -> InventoryMovement:
    if delta == 0:
        raise ValueError("inventory_quantity_invalid")
    stock = ensure_stock(db, park_id=park_id, catalog_part_id=catalog_part_id)
    before = stock.quantity
    if before + delta < 0:
        raise InventoryConflict("inventory_out_of_stock", current_quantity=before)
    stock.quantity = before + delta
    stock.version += 1
    stock.updated_by = user.id
    movement = InventoryMovement(
        catalog_part_id=catalog_part_id,
        park_id=park_id,
        actor_user_id=user.id,
        kind=kind,
        delta=delta,
        balance_before=before,
        balance_after=stock.quantity,
        source_kind=source_kind,
        source_id=source_id,
        issue_key=issue_key,
        note=(note or "").strip() or None,
    )
    db.add(movement)
    db.flush()
    return movement
