from sqlalchemy import select, update
from sqlalchemy.orm import Session

from robopark_api.models import InventoryCatalogPart, InventoryPart


def lock_catalog_parts(db: Session, part_ids: list[int]) -> dict[int, InventoryCatalogPart]:
    # SQLite ignores FOR UPDATE. Acquire its writer reservation before reading
    # aliases so a concurrent merge cannot move stock between resolve and write.
    if db.get_bind().dialect.name == "sqlite":
        db.execute(
            update(InventoryCatalogPart)
            .where(InventoryCatalogPart.id.in_(part_ids))
            .values(updated_at=InventoryCatalogPart.updated_at)
            .execution_options(synchronize_session=False)
        )
    rows = db.scalars(
        select(InventoryCatalogPart)
        .where(InventoryCatalogPart.id.in_(part_ids))
        .order_by(InventoryCatalogPart.id)
        .with_for_update(of=InventoryCatalogPart)
        .execution_options(populate_existing=True)
    )
    return {row.id: row for row in rows}


def resolve_catalog_part(
    db: Session, part_id: int, *, allow_archived: bool = False, lock: bool = False
) -> InventoryCatalogPart:
    """Follow durable merge links without deriving identity from article or stock."""
    visited = set()
    while part_id not in visited:
        visited.add(part_id)
        if lock:
            row = lock_catalog_parts(db, [part_id]).get(part_id)
        else:
            row = db.scalar(
                select(InventoryCatalogPart)
                .where(InventoryCatalogPart.id == part_id)
                .execution_options(populate_existing=True)
            )
        if row is None:
            raise LookupError("inventory_part_not_found")
        if row.merged_into_part_id is not None:
            part_id = row.merged_into_part_id
            continue
        if not row.is_active and not allow_archived:
            raise LookupError("inventory_part_archived")
        return row
    raise LookupError("inventory_merge_alias_cycle")


def resolve_legacy_part(
    db: Session, legacy: InventoryPart, *, lock: bool = False
) -> InventoryCatalogPart:
    db.refresh(legacy)
    if legacy.catalog_part_id is not None:
        return resolve_catalog_part(db, legacy.catalog_part_id, lock=lock)
    # Compatibility for unmigrated/imported rows only. Never choose an arbitrary
    # archived record when an article has been reused.
    normalized = " ".join(legacy.article.strip().casefold().split())
    rows = list(
        db.scalars(
            select(InventoryCatalogPart)
            .where(
                InventoryCatalogPart.normalized_article == normalized,
                InventoryCatalogPart.is_active.is_(True),
            )
            .order_by(InventoryCatalogPart.id)
        )
    )
    if len(rows) != 1:
        raise LookupError("inventory_legacy_part_unresolved")
    return resolve_catalog_part(db, rows[0].id, lock=lock)
