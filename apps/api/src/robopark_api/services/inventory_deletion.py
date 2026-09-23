from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from robopark_api.models import (
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryCount,
    InventoryCountLine,
    InventoryMovement,
    InventoryParkStock,
    InventoryPart,
    InventoryReceipt,
    InventoryReceiptLine,
    User,
    UserPark,
)
from robopark_api.services import inventory


def _require_royal(user: User) -> None:
    if user.role != "royal":
        raise PermissionError("forbidden")


def _part_graph_ids(db: Session, part_id: int) -> set[int]:
    if db.get(InventoryCatalogPart, part_id) is None:
        raise LookupError("inventory_part_not_found")
    graph = {part_id}
    while True:
        incoming = set(
            db.scalars(
                select(InventoryCatalogPart.id).where(
                    InventoryCatalogPart.merged_into_part_id.in_(graph)
                )
            )
        )
        expanded = graph | incoming
        if expanded == graph:
            return graph
        graph = expanded


def _document_ids(db: Session, part_ids: set[int]) -> tuple[set[int], set[int]]:
    receipt_ids = set(
        db.scalars(
            select(InventoryReceiptLine.receipt_id).where(
                InventoryReceiptLine.catalog_part_id.in_(part_ids)
            )
        )
    )
    count_ids = set(
        db.scalars(
            select(InventoryCountLine.count_id).where(
                InventoryCountLine.catalog_part_id.in_(part_ids)
            )
        )
    )
    return receipt_ids, count_ids


def _delete_part_rows(db: Session, part_ids: set[int]) -> set[str]:
    parts = list(
        db.scalars(select(InventoryCatalogPart).where(InventoryCatalogPart.id.in_(part_ids)))
    )
    legacy_parts = list(
        db.scalars(select(InventoryPart).where(InventoryPart.catalog_part_id.in_(part_ids)))
    )
    receipt_ids, count_ids = _document_ids(db, part_ids)
    legacy_ids = {row.id for row in legacy_parts}
    storage_keys = {
        key
        for key in [
            *(row.photo_storage_key for row in parts),
            *(row.photo_storage_key for row in legacy_parts),
        ]
        if key
    }

    if receipt_ids:
        db.execute(
            delete(InventoryReceiptLine).where(InventoryReceiptLine.receipt_id.in_(receipt_ids))
        )
    if count_ids:
        db.execute(delete(InventoryCountLine).where(InventoryCountLine.count_id.in_(count_ids)))
    movement_filter = InventoryMovement.catalog_part_id.in_(part_ids)
    if legacy_ids:
        movement_filter = movement_filter | InventoryMovement.part_id.in_(legacy_ids)
    db.execute(delete(InventoryMovement).where(movement_filter))
    if receipt_ids:
        db.execute(delete(InventoryReceipt).where(InventoryReceipt.id.in_(receipt_ids)))
    if count_ids:
        db.execute(delete(InventoryCount).where(InventoryCount.id.in_(count_ids)))
    db.execute(delete(InventoryParkStock).where(InventoryParkStock.catalog_part_id.in_(part_ids)))
    if legacy_ids:
        db.execute(delete(InventoryPart).where(InventoryPart.id.in_(legacy_ids)))
    db.execute(delete(InventoryCatalogPart).where(InventoryCatalogPart.id.in_(part_ids)))
    return storage_keys


def delete_part(db: Session, user: User, part_id: int) -> None:
    _require_royal(user)
    try:
        storage_keys = _delete_part_rows(db, _part_graph_ids(db, part_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    for storage_key in storage_keys:
        inventory._remove_photo(storage_key)


def delete_component(db: Session, user: User, component_id: int) -> None:
    _require_royal(user)
    component = db.get(InventoryCatalogComponent, component_id)
    if component is None:
        raise LookupError("inventory_component_not_found")
    part_ids = set(
        db.scalars(
            select(InventoryCatalogPart.id).where(
                InventoryCatalogPart.component_id == component_id
            )
        )
    )
    graph_ids: set[int] = set()
    for part_id in part_ids:
        graph_ids.update(_part_graph_ids(db, part_id))
    try:
        storage_keys = _delete_part_rows(db, graph_ids) if graph_ids else set()
        if component.photo_storage_key:
            storage_keys.add(component.photo_storage_key)
        db.delete(component)
        db.commit()
    except Exception:
        db.rollback()
        raise
    for storage_key in storage_keys:
        inventory._remove_photo(storage_key)


def delete_count(db: Session, user: User, count_id: int) -> None:
    count = db.get(InventoryCount, count_id)
    if count is None:
        raise LookupError("inventory_count_not_found")
    if user.role == "admin":
        assigned = db.scalar(
            select(UserPark.user_id).where(
                UserPark.user_id == user.id,
                UserPark.park_id == count.park_id,
            )
        )
        if assigned is None:
            raise PermissionError("forbidden")
    elif user.role != "royal":
        raise PermissionError("forbidden")
    try:
        db.execute(delete(InventoryCountLine).where(InventoryCountLine.count_id == count.id))
        db.execute(
            delete(InventoryMovement).where(
                InventoryMovement.source_kind == "count",
                InventoryMovement.source_id.like(f"{count.id}:%"),
            )
        )
        db.delete(count)
        db.commit()
    except Exception:
        db.rollback()
        raise
