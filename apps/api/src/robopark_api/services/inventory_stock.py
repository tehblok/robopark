from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from robopark_api.models import (
    InventoryCatalogPart,
    InventoryMovement,
    InventoryParkStock,
    User,
)
from robopark_api.services.inventory_identity import resolve_catalog_part


class InventoryConflict(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        current_quantity: int | None = None,
        existing_part_id: int | None = None,
        existing_component_id: int | None = None,
        park_ids: list[int] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.current_quantity = current_quantity
        self.existing_part_id = existing_part_id
        self.existing_component_id = existing_component_id
        self.park_ids = park_ids

    @property
    def detail(self) -> dict[str, object]:
        result: dict[str, object] = {"code": self.code}
        if self.current_quantity is not None:
            result["current_quantity"] = self.current_quantity
        if self.existing_part_id is not None:
            result["existing_part_id"] = self.existing_part_id
        if self.existing_component_id is not None:
            result["existing_component_id"] = self.existing_component_id
        if self.park_ids is not None:
            result["park_ids"] = self.park_ids
        return result


def stock_insert_if_missing_statement(dialect_name: str, *, park_id: int, catalog_part_id: int):
    values = {
        "park_id": park_id,
        "catalog_part_id": catalog_part_id,
        "quantity": 0,
        "minimum_quantity": 0,
        "is_active": True,
        "version": 1,
    }
    if dialect_name == "sqlite":
        statement = sqlite_insert(InventoryParkStock).values(**values)
    elif dialect_name == "postgresql":
        statement = postgresql_insert(InventoryParkStock).values(**values)
    else:
        return None
    return statement.on_conflict_do_nothing(index_elements=["park_id", "catalog_part_id"])


def ensure_stock(
    db: Session,
    *,
    park_id: int,
    catalog_part_id: int,
    allow_archived: bool = False,
) -> InventoryParkStock:
    part = resolve_catalog_part(
        db,
        catalog_part_id,
        allow_archived=allow_archived,
        lock=True,
    )
    catalog_part_id = part.id
    if part.is_active:
        insert_statement = stock_insert_if_missing_statement(
            db.get_bind().dialect.name,
            park_id=park_id,
            catalog_part_id=catalog_part_id,
        )
        if insert_statement is not None:
            db.execute(insert_statement)
    stock = db.scalar(
        select(InventoryParkStock)
        .where(
            InventoryParkStock.park_id == park_id,
            InventoryParkStock.catalog_part_id == catalog_part_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if stock is None:
        if not part.is_active:
            raise LookupError("inventory_stock_not_found")
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
    allow_archived: bool = False,
) -> InventoryMovement:
    if delta == 0:
        raise ValueError("inventory_quantity_invalid")
    stock = ensure_stock(
        db,
        park_id=park_id,
        catalog_part_id=catalog_part_id,
        allow_archived=allow_archived,
    )
    catalog_part_id = stock.catalog_part_id
    if source_kind is not None and source_id is not None:
        # ensure_stock holds the canonical catalog/stock locks (SQLite's writer
        # reservation). Merges and other sourced writes cannot change this family
        # between the history lookup and the insert. Keep the immutable event on
        # its original catalog row, including through transitive merge aliases.
        family = (
            select(InventoryCatalogPart.id)
            .where(InventoryCatalogPart.id == catalog_part_id)
            .cte("inventory_alias_family", recursive=True)
        )
        family = family.union(
            select(InventoryCatalogPart.id).join(
                family, InventoryCatalogPart.merged_into_part_id == family.c.id
            )
        )
        existing = db.scalar(
            select(InventoryMovement)
            .where(
                InventoryMovement.catalog_part_id.in_(select(family.c.id)),
                InventoryMovement.park_id == park_id,
                InventoryMovement.source_kind == source_kind,
                InventoryMovement.source_id == source_id,
            )
            .order_by(InventoryMovement.created_at, InventoryMovement.id)
            .limit(1)
        )
        if existing is not None:
            return existing
    if db.get_bind().dialect.name == "sqlite":
        result = db.execute(
            update(InventoryParkStock)
            .where(
                InventoryParkStock.id == stock.id,
                InventoryParkStock.quantity + delta >= 0,
            )
            .values(
                quantity=InventoryParkStock.quantity + delta,
                version=InventoryParkStock.version + 1,
                updated_by=user.id,
            )
            .returning(InventoryParkStock.quantity)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if result is None:
            db.refresh(stock)
            raise InventoryConflict("inventory_out_of_stock", current_quantity=stock.quantity)
        before = int(result) - delta
        after = int(result)
        db.expire(stock)
    else:
        before = stock.quantity
        if before + delta < 0:
            raise InventoryConflict("inventory_out_of_stock", current_quantity=before)
        stock.quantity = before + delta
        stock.version += 1
        stock.updated_by = user.id
        after = stock.quantity
    movement = InventoryMovement(
        catalog_part_id=catalog_part_id,
        park_id=park_id,
        actor_user_id=user.id,
        kind=kind,
        delta=delta,
        balance_before=before,
        balance_after=after,
        source_kind=source_kind,
        source_id=source_id,
        issue_key=issue_key,
        note=(note or "").strip() or None,
    )
    db.add(movement)
    db.flush()
    return movement
