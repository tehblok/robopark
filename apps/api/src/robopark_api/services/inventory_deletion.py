from __future__ import annotations

from sqlalchemy import delete, func, or_, select, update
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
from robopark_api.services import inventory_photo_cleanup, inventory_stock
from robopark_api.services.inventory_identity import resolve_catalog_part


def _require_royal(user: User) -> None:
    if user.role != "royal":
        raise PermissionError("forbidden")


def _lock_catalog_rows(db: Session, model, row_ids: set[int]):
    if not row_ids:
        return []
    if db.get_bind().dialect.name == "sqlite":
        db.execute(
            update(model)
            .where(model.id.in_(row_ids))
            .values(photo_storage_key=model.photo_storage_key)
            .execution_options(synchronize_session=False)
        )
    return list(
        db.scalars(
            select(model)
            .where(model.id.in_(row_ids))
            .order_by(model.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


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
            delete(InventoryReceiptLine).where(
                InventoryReceiptLine.receipt_id.in_(receipt_ids),
                InventoryReceiptLine.catalog_part_id.in_(part_ids),
            )
        )
    if count_ids:
        db.execute(
            delete(InventoryCountLine).where(
                InventoryCountLine.count_id.in_(count_ids),
                InventoryCountLine.catalog_part_id.in_(part_ids),
            )
        )
    movement_filter = InventoryMovement.catalog_part_id.in_(part_ids)
    if legacy_ids:
        movement_filter = movement_filter | InventoryMovement.part_id.in_(legacy_ids)
    db.execute(delete(InventoryMovement).where(movement_filter))
    if receipt_ids:
        db.execute(
            delete(InventoryReceipt).where(
                InventoryReceipt.id.in_(receipt_ids),
                ~select(InventoryReceiptLine.id)
                .where(InventoryReceiptLine.receipt_id == InventoryReceipt.id)
                .exists(),
            )
        )
    if count_ids:
        db.execute(
            delete(InventoryCount).where(
                InventoryCount.id.in_(count_ids),
                ~select(InventoryCountLine.id)
                .where(InventoryCountLine.count_id == InventoryCount.id)
                .exists(),
            )
        )
    db.execute(delete(InventoryParkStock).where(InventoryParkStock.catalog_part_id.in_(part_ids)))
    if legacy_ids:
        db.execute(delete(InventoryPart).where(InventoryPart.id.in_(legacy_ids)))
    db.execute(delete(InventoryCatalogPart).where(InventoryCatalogPart.id.in_(part_ids)))
    return storage_keys


def _delete_summary(db: Session, part_ids: set[int], *, query: str | None, mode: str) -> dict:
    if mode not in {"active", "archived", "all"}:
        raise ValueError("inventory_catalog_mode_invalid")
    deleted_parts = [
        {
            "id": part.id,
            "name": part.name,
            "article": part.article,
            "is_active": part.is_active,
            "component_is_active": component_is_active,
            "merged_into_part_id": part.merged_into_part_id,
        }
        for part, component_is_active in db.execute(
            select(InventoryCatalogPart, InventoryCatalogComponent.is_active)
            .join(
                InventoryCatalogComponent,
                InventoryCatalogComponent.id == InventoryCatalogPart.component_id,
            )
            .where(InventoryCatalogPart.id.in_(part_ids))
            .order_by(InventoryCatalogPart.id)
        )
    ]
    matches = (
        select(InventoryCatalogPart.id)
        .join(
            InventoryCatalogComponent,
            InventoryCatalogComponent.id == InventoryCatalogPart.component_id,
        )
        .where(InventoryCatalogPart.id.in_(part_ids))
    )
    if mode == "active":
        matches = matches.where(
            InventoryCatalogPart.is_active.is_(True),
            InventoryCatalogComponent.is_active.is_(True),
        )
    elif mode == "archived":
        matches = matches.where(
            InventoryCatalogPart.is_active.is_(False),
            InventoryCatalogPart.merged_into_part_id.is_(None),
        )
    else:
        matches = matches.where(InventoryCatalogPart.merged_into_part_id.is_(None))
    if query:
        from robopark_api.services.inventory_catalog import normalize_key

        pattern = f"%{normalize_key(query)}%"
        matches = matches.where(
            or_(
                InventoryCatalogPart.normalized_name.like(pattern),
                InventoryCatalogPart.normalized_article.like(pattern),
            )
        )
    matched_deleted_count = db.scalar(select(func.count()).select_from(matches.subquery())) or 0
    return {
        "deleted_part_count": len(deleted_parts),
        "deleted_part_ids": [part["id"] for part in deleted_parts],
        "matched_deleted_count": matched_deleted_count,
        "deleted_parts": deleted_parts,
    }


def delete_part(
    db: Session, user: User, part_id: int, *, query: str | None = None, mode: str = "active"
) -> dict:
    _require_royal(user)
    part_ids = _part_graph_ids(db, part_id)
    _lock_catalog_rows(db, InventoryCatalogPart, part_ids)
    summary = _delete_summary(db, part_ids, query=query, mode=mode)
    try:
        storage_keys = _delete_part_rows(db, part_ids)
        inventory_photo_cleanup.enqueue(db, storage_keys)
        db.commit()
    except Exception:
        db.rollback()
        raise
    summary["_cleanup_pending"] = inventory_photo_cleanup.cleanup_pending(db, storage_keys)
    return summary


def delete_component(
    db: Session, user: User, component_id: int, *, query: str | None = None, mode: str = "active"
) -> dict:
    _require_royal(user)
    components = _lock_catalog_rows(db, InventoryCatalogComponent, {component_id})
    if not components:
        raise LookupError("inventory_component_not_found")
    component = components[0]
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
    _lock_catalog_rows(db, InventoryCatalogPart, graph_ids)
    summary = _delete_summary(db, graph_ids, query=query, mode=mode)
    try:
        storage_keys = _delete_part_rows(db, graph_ids) if graph_ids else set()
        if component.photo_storage_key:
            storage_keys.add(component.photo_storage_key)
        inventory_photo_cleanup.enqueue(db, storage_keys)
        db.delete(component)
        db.commit()
    except Exception:
        db.rollback()
        raise
    summary["_cleanup_pending"] = inventory_photo_cleanup.cleanup_pending(db, storage_keys)
    return summary


def delete_count(db: Session, user: User, count_id: int) -> None:
    if db.get_bind().dialect.name == "sqlite":
        db.execute(
            update(InventoryCount)
            .where(InventoryCount.id == count_id)
            .values(status=InventoryCount.status)
            .execution_options(synchronize_session=False)
        )
    count = db.scalar(
        select(InventoryCount)
        .where(InventoryCount.id == count_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
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
        movements = list(
            db.scalars(
                select(InventoryMovement).where(
                    InventoryMovement.park_id == count.park_id,
                    InventoryMovement.source_kind == "count",
                    InventoryMovement.source_id.like(f"{count.id}:%"),
                ).with_for_update()
            )
        )
        reverse_by_part: dict[int, int] = {}
        for movement in movements:
            if movement.catalog_part_id is None:
                raise inventory_stock.InventoryConflict("inventory_count_adjustment_invalid")
            canonical = resolve_catalog_part(
                db, movement.catalog_part_id, allow_archived=True, lock=True
            )
            reverse_by_part[canonical.id] = reverse_by_part.get(canonical.id, 0) - int(
                movement.delta
            )
        for catalog_part_id, reverse_delta in reverse_by_part.items():
            stock = inventory_stock.ensure_stock(
                db,
                park_id=count.park_id,
                catalog_part_id=catalog_part_id,
                allow_archived=True,
            )
            restored = inventory_stock.require_int64(int(stock.quantity) + reverse_delta)
            if restored < 0:
                raise inventory_stock.InventoryConflict(
                    "inventory_count_delete_stock_conflict",
                    current_quantity=int(stock.quantity),
                )
            stock.quantity = restored
            inventory_stock.increment_stock_version(stock)
            stock.updated_by = user.id
        db.execute(delete(InventoryCountLine).where(InventoryCountLine.count_id == count.id))
        if movements:
            db.execute(
                delete(InventoryMovement).where(
                    InventoryMovement.id.in_([movement.id for movement in movements])
                )
            )
        db.delete(count)
        db.commit()
    except Exception:
        db.rollback()
        raise
