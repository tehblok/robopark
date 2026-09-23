import json

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.models import (
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryParkStock,
    InventoryPart,
    User,
)
from robopark_api.services import audit, inventory_access, inventory_stock
from robopark_api.services.inventory_identity import lock_catalog_parts, resolve_catalog_part
from robopark_api.services.inventory_stock import InventoryConflict
from robopark_api.services.rbac import (
    PERMISSION_INVENTORY_CATALOG_MANAGE,
    PERMISSION_INVENTORY_STOCK_MANAGE,
    has_permission,
)

# One transaction-scoped PostgreSQL reader/writer lock protects the alias graph:
# receipts share it while resolving aliases and merges hold it exclusively while
# changing an alias. The fixed signed-bigint key is private to this application.
_ALIAS_GRAPH_ADVISORY_LOCK_KEY = 7_262_625_726_568_766_785


def _acquire_alias_graph_lock(db: Session, *, shared: bool) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    lock_function = func.pg_advisory_xact_lock_shared if shared else func.pg_advisory_xact_lock
    db.execute(select(lock_function(_ALIAS_GRAPH_ADVISORY_LOCK_KEY)))


def acquire_alias_graph_read_lock(db: Session) -> None:
    _acquire_alias_graph_lock(db, shared=True)


def acquire_alias_graph_write_lock(db: Session) -> None:
    _acquire_alias_graph_lock(db, shared=False)


def normalize_key(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def required_text(value: str, error: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(error)
    return result


def component_out(row: InventoryCatalogComponent) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "is_active": row.is_active,
        "has_photo": bool(row.photo_storage_key),
    }


def part_out(row: InventoryCatalogPart) -> dict:
    return {
        "id": row.id,
        "component_id": row.component_id,
        "name": row.name,
        "article": row.article,
        "is_active": row.is_active,
        "has_photo": bool(row.photo_storage_key),
    }


def _catalog_photo_row(db: Session, kind: str, row_id: int):
    model = InventoryCatalogComponent if kind == "component" else InventoryCatalogPart
    row = db.get(model, row_id)
    if row is None:
        raise LookupError(f"inventory_{kind}_not_found")
    return row


def _require_catalog_photo_manage(db: Session, user: User) -> None:
    if not inventory_access.can_view_inventory(db, user) or not (
        has_permission(db, user, PERMISSION_INVENTORY_CATALOG_MANAGE)
        or has_permission(db, user, PERMISSION_INVENTORY_STOCK_MANAGE)
    ):
        raise PermissionError("forbidden")


def set_catalog_photo(
    db: Session,
    user: User,
    *,
    kind: str,
    row_id: int,
    photo: tuple[str | None, bytes, str | None],
):
    from robopark_api.services import inventory

    _require_catalog_photo_manage(db, user)
    row = _catalog_photo_row(db, kind, row_id)
    filename, content, content_type = photo
    resolved_type = inventory.normalize_attachment_content_type(
        filename=filename or "photo",
        content=content,
        content_type=content_type,
    )
    if resolved_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("inventory_photo_invalid_type")
    old_key = row.photo_storage_key
    new_key = None
    try:
        new_key, safe_name, stored_type = inventory.save_photo(filename, content, resolved_type)
        row.photo_storage_key = new_key
        row.photo_filename = safe_name
        row.photo_content_type = stored_type
        row.updated_by = user.id
        db.commit()
    except Exception:
        db.rollback()
        inventory._remove_photo(new_key)
        raise
    inventory._remove_photo(old_key)
    db.refresh(row)
    return row


def remove_catalog_photo(db: Session, user: User, *, kind: str, row_id: int) -> None:
    from robopark_api.services import inventory

    _require_catalog_photo_manage(db, user)
    row = _catalog_photo_row(db, kind, row_id)
    old_key = row.photo_storage_key
    try:
        row.photo_storage_key = None
        row.photo_filename = None
        row.photo_content_type = None
        row.updated_by = user.id
        db.commit()
    except Exception:
        db.rollback()
        raise
    inventory._remove_photo(old_key)


def _audit_detail(fields) -> str:
    return json.dumps({"changed_fields": sorted(fields)}, ensure_ascii=False)


def list_components(db: Session, user: User, *, park_id: int, limit: int, offset: int) -> dict:
    inventory_access.require_park(db, user, park_id)
    base = select(InventoryCatalogComponent).where(InventoryCatalogComponent.is_active.is_(True))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(
        db.scalars(
            base.order_by(InventoryCatalogComponent.normalized_name, InventoryCatalogComponent.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return {
        "items": [component_out(row) for row in rows],
        "limit": limit,
        "offset": offset,
        "total": total,
    }


def catalog_item(db: Session, user: User, *, park_id: int, part_id: int) -> dict:
    inventory_access.require_park(db, user, park_id)
    part = resolve_catalog_part(db, part_id)
    stock = db.scalar(
        select(InventoryParkStock).where(
            InventoryParkStock.park_id == park_id, InventoryParkStock.catalog_part_id == part.id
        )
    )
    return {
        **part_out(part),
        "component_name": part.component.name,
        "quantity": stock.quantity if stock else 0,
        "minimum_quantity": stock.minimum_quantity if stock else 0,
        "location": stock.location if stock else None,
        "stock_is_active": stock.is_active if stock else False,
    }


def create_component(db: Session, user: User, *, park_id: int, name: str, commit: bool = True):
    inventory_access.require_park(db, user, park_id, manage=True)
    clean_name = required_text(name, "inventory_component_name_required")
    normalized = normalize_key(clean_name)
    existing = db.scalar(
        select(InventoryCatalogComponent).where(
            InventoryCatalogComponent.normalized_name == normalized,
            InventoryCatalogComponent.is_active.is_(True),
        )
    )
    if existing is not None:
        raise InventoryConflict("inventory_component_exists", existing_component_id=existing.id)
    row = InventoryCatalogComponent(
        name=clean_name, normalized_name=normalized, created_by=user.id, updated_by=user.id
    )
    db.add(row)
    try:
        db.flush()
        if commit:
            db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(InventoryCatalogComponent).where(
                InventoryCatalogComponent.normalized_name == normalized,
                InventoryCatalogComponent.is_active.is_(True),
            )
        )
        if existing is not None:
            raise InventoryConflict(
                "inventory_component_exists", existing_component_id=existing.id
            ) from exc
        raise
    if commit:
        db.refresh(row)
        audit.record(
            db,
            action="inventory.catalog.component.created",
            actor=user,
            park_id=park_id,
            target_type="inventory_component",
            target_id=row.id,
            detail=_audit_detail(["name"]),
        )
    return row


def create_part(
    db: Session,
    user: User,
    *,
    park_id: int,
    component_id: int,
    name: str,
    article: str,
    commit: bool = True,
):
    inventory_access.require_park(db, user, park_id, manage=True)
    component = db.get(InventoryCatalogComponent, component_id)
    if component is None or not component.is_active:
        raise ValueError("inventory_component_invalid")
    clean_name = required_text(name, "inventory_part_name_required")
    clean_article = required_text(article, "inventory_article_required")
    normalized_article = normalize_key(clean_article)
    existing = db.scalar(
        select(InventoryCatalogPart).where(
            InventoryCatalogPart.normalized_article == normalized_article,
            InventoryCatalogPart.is_active.is_(True),
        )
    )
    if existing is not None:
        raise InventoryConflict("inventory_article_exists", existing_part_id=existing.id)
    row = InventoryCatalogPart(
        component_id=component_id,
        name=clean_name,
        normalized_name=normalize_key(clean_name),
        article=clean_article,
        normalized_article=normalized_article,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    try:
        db.flush()
        if commit:
            db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(InventoryCatalogPart).where(
                InventoryCatalogPart.normalized_article == normalized_article,
                InventoryCatalogPart.is_active.is_(True),
            )
        )
        if existing is not None:
            raise InventoryConflict(
                "inventory_article_exists", existing_part_id=existing.id
            ) from exc
        raise
    if commit:
        db.refresh(row)
        audit.record(
            db,
            action="inventory.catalog.part.created",
            actor=user,
            park_id=park_id,
            target_type="inventory_part",
            target_id=row.id,
            detail=_audit_detail(["article", "component_id", "name"]),
        )
    return row


def update_component(db: Session, user: User, component_id: int, changes: dict):
    inventory_access.require_catalog_manage(db, user)
    row = db.get(InventoryCatalogComponent, component_id)
    if row is None:
        raise LookupError("inventory_component_not_found")
    if changes.get("name") is not None:
        name = required_text(changes["name"], "inventory_component_name_required")
        normalized = normalize_key(name)
        existing = db.scalar(
            select(InventoryCatalogComponent.id).where(
                InventoryCatalogComponent.id != row.id,
                InventoryCatalogComponent.normalized_name == normalized,
                InventoryCatalogComponent.is_active.is_(True),
            )
        )
        if existing is not None:
            raise InventoryConflict("inventory_component_exists", existing_component_id=existing)
        row.name = name
        row.normalized_name = normalized
    if changes.get("is_active") is not None:
        if changes["is_active"] and not row.is_active:
            existing = db.scalar(
                select(InventoryCatalogComponent.id).where(
                    InventoryCatalogComponent.id != row.id,
                    InventoryCatalogComponent.normalized_name == row.normalized_name,
                    InventoryCatalogComponent.is_active.is_(True),
                )
            )
            if existing is not None:
                raise InventoryConflict(
                    "inventory_component_exists", existing_component_id=existing
                )
        row.is_active = bool(changes["is_active"])
    row.updated_by = user.id
    intended_normalized_name = row.normalized_name
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(InventoryCatalogComponent.id).where(
                InventoryCatalogComponent.id != component_id,
                InventoryCatalogComponent.normalized_name == intended_normalized_name,
                InventoryCatalogComponent.is_active.is_(True),
            )
        )
        if existing is not None:
            raise InventoryConflict(
                "inventory_component_exists", existing_component_id=existing
            ) from exc
        raise
    db.refresh(row)
    audit.record(
        db,
        action="inventory.catalog.component.updated",
        actor=user,
        target_type="inventory_component",
        target_id=row.id,
        detail=_audit_detail(changes),
    )
    return row


def update_part(db: Session, user: User, part_id: int, changes: dict, *, commit: bool = True):
    inventory_access.require_catalog_manage(db, user)
    row = resolve_catalog_part(db, part_id, allow_archived=True, lock=True)
    if row.id != part_id and not row.is_active:
        raise LookupError("inventory_part_archived")
    part_id = row.id
    if changes.get("component_id") is not None:
        component = db.get(InventoryCatalogComponent, changes["component_id"])
        if component is None or not component.is_active:
            raise ValueError("inventory_component_invalid")
        row.component_id = component.id
    if changes.get("name") is not None:
        row.name = required_text(changes["name"], "inventory_part_name_required")
        row.normalized_name = normalize_key(row.name)
    if changes.get("article") is not None:
        article = required_text(changes["article"], "inventory_article_required")
        normalized = normalize_key(article)
        existing = db.scalar(
            select(InventoryCatalogPart.id).where(
                InventoryCatalogPart.id != row.id,
                InventoryCatalogPart.normalized_article == normalized,
                InventoryCatalogPart.is_active.is_(True),
            )
        )
        if existing is not None:
            raise InventoryConflict("inventory_article_exists", existing_part_id=existing)
        row.article = article
        row.normalized_article = normalized
    if changes.get("is_active") is not None:
        if changes["is_active"] and not row.is_active:
            existing = db.scalar(
                select(InventoryCatalogPart.id).where(
                    InventoryCatalogPart.id != row.id,
                    InventoryCatalogPart.normalized_article == row.normalized_article,
                    InventoryCatalogPart.is_active.is_(True),
                )
            )
            if existing is not None:
                raise InventoryConflict("inventory_article_exists", existing_part_id=existing)
        row.is_active = bool(changes["is_active"])
    row.updated_by = user.id
    intended_normalized_article = row.normalized_article
    try:
        db.flush()
        if commit:
            db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(InventoryCatalogPart.id).where(
                InventoryCatalogPart.id != part_id,
                InventoryCatalogPart.normalized_article == intended_normalized_article,
                InventoryCatalogPart.is_active.is_(True),
            )
        )
        if existing is not None:
            raise InventoryConflict("inventory_article_exists", existing_part_id=existing) from exc
        raise
    if commit:
        db.refresh(row)
        audit.record(
            db,
            action="inventory.catalog.part.updated",
            actor=user,
            target_type="inventory_part",
            target_id=row.id,
            detail=_audit_detail(changes),
        )
    return row


def merge_parts(db: Session, user: User, source_part_id: int, target_part_id: int):
    inventory_access.require_catalog_manage(db, user)
    if source_part_id == target_part_id:
        raise ValueError("inventory_merge_same_part")
    acquire_alias_graph_write_lock(db)
    locked = lock_catalog_parts(db, [source_part_id, target_part_id])
    source = locked.get(source_part_id)
    target = locked.get(target_part_id)
    if source is None or target is None:
        raise LookupError("inventory_part_not_found")
    if not source.is_active or not target.is_active:
        raise InventoryConflict("inventory_merge_inactive_part")

    source_stocks = list(
        db.scalars(
            select(InventoryParkStock)
            .where(InventoryParkStock.catalog_part_id == source.id)
            .order_by(InventoryParkStock.park_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    for source_stock in source_stocks:
        target_stock = db.scalar(
            select(InventoryParkStock)
            .where(
                InventoryParkStock.park_id == source_stock.park_id,
                InventoryParkStock.catalog_part_id == target.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if target_stock is None:
            source_stock.catalog_part_id = target.id
            source_stock.updated_by = user.id
            inventory_stock.increment_stock_version(source_stock)
            continue
        target_stock.minimum_quantity = max(
            target_stock.minimum_quantity, source_stock.minimum_quantity
        )
        if not (target_stock.location or "").strip():
            target_stock.location = (source_stock.location or "").strip() or None
        target_stock.is_active = target_stock.is_active or source_stock.is_active
        target_stock.updated_by = user.id
        if source_stock.quantity:
            inventory_stock.apply_stock_delta(
                db,
                user=user,
                park_id=source_stock.park_id,
                catalog_part_id=target.id,
                delta=source_stock.quantity,
                kind="merge",
                source_kind="catalog_merge",
                source_id=f"{source.id}:target",
                note=f"Merged catalog part {source.id}",
            )
            inventory_stock.apply_stock_delta(
                db,
                user=user,
                park_id=source_stock.park_id,
                catalog_part_id=source.id,
                delta=-source_stock.quantity,
                kind="merge",
                source_kind="catalog_merge",
                source_id=f"{source.id}:source",
                note=f"Merged into catalog part {target.id}",
            )
        else:
            inventory_stock.increment_stock_version(target_stock)
        source_stock.is_active = False
        source_stock.updated_by = user.id

    source.is_active = False
    # Bind imported legacy rows before freeing the article for reuse. Migrated
    # rows already carry this durable identity from 0026.
    for legacy in db.scalars(select(InventoryPart).where(InventoryPart.catalog_part_id.is_(None))):
        if normalize_key(legacy.article) == source.normalized_article:
            legacy.catalog_part_id = source.id
    source.merged_into_part_id = target.id
    source.updated_by = user.id
    db.commit()
    db.refresh(target)
    affected_park_ids = {stock.park_id for stock in source_stocks}
    for park_id in affected_park_ids or {None}:
        audit.record(
            db,
            action="inventory.catalog.part.merged",
            actor=user,
            park_id=park_id,
            target_type="inventory_part",
            target_id=target.id,
            detail=_audit_detail(["catalog_part_id", "is_active", "stock", "history"]),
        )
    return target


def search_catalog(
    db: Session,
    user: User,
    *,
    park_id: int,
    query: str | None,
    component_id: int | None,
    stock_filter: str | None,
    limit: int,
    offset: int,
    mode: str = "active",
) -> dict:
    inventory_access.require_park(db, user, park_id)
    if mode not in {"active", "archived", "all"}:
        raise ValueError("inventory_catalog_mode_invalid")
    if mode != "active":
        inventory_access.require_catalog_manage(db, user)
    quantity = func.coalesce(InventoryParkStock.quantity, 0)
    minimum = func.coalesce(InventoryParkStock.minimum_quantity, 0)
    statement = (
        select(InventoryCatalogPart, InventoryParkStock)
        .join(
            InventoryCatalogComponent,
            InventoryCatalogComponent.id == InventoryCatalogPart.component_id,
        )
        .outerjoin(
            InventoryParkStock,
            (InventoryParkStock.catalog_part_id == InventoryCatalogPart.id)
            & (InventoryParkStock.park_id == park_id),
        )
    )
    if mode == "active":
        statement = statement.where(
            InventoryCatalogPart.is_active.is_(True),
            InventoryCatalogComponent.is_active.is_(True),
        )
    elif mode == "archived":
        statement = statement.where(
            InventoryCatalogPart.is_active.is_(False),
            InventoryCatalogPart.merged_into_part_id.is_(None),
        )
    else:
        statement = statement.where(InventoryCatalogPart.merged_into_part_id.is_(None))
    if query:
        pattern = f"%{normalize_key(query)}%"
        statement = statement.where(
            or_(
                InventoryCatalogPart.normalized_name.like(pattern),
                InventoryCatalogPart.normalized_article.like(pattern),
            )
        )
    if component_id is not None:
        statement = statement.where(InventoryCatalogPart.component_id == component_id)
    if stock_filter == "in_stock":
        statement = statement.where(quantity > 0)
    elif stock_filter == "below_minimum":
        statement = statement.where(quantity < minimum)
    elif stock_filter == "without_location":
        statement = statement.where(
            or_(
                InventoryParkStock.id.is_(None),
                func.trim(func.coalesce(InventoryParkStock.location, "")) == "",
            )
        )
    elif stock_filter not in {None, ""}:
        raise ValueError("inventory_stock_filter_invalid")
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.execute(
        statement.order_by(
            InventoryCatalogPart.normalized_name,
            InventoryCatalogPart.normalized_article,
            InventoryCatalogPart.id,
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "items": [
            {
                **part_out(part),
                "component_name": part.component.name,
                "quantity": stock.quantity if stock else 0,
                "minimum_quantity": stock.minimum_quantity if stock else 0,
                "location": stock.location if stock else None,
                "stock_is_active": stock.is_active if stock else False,
            }
            for part, stock in rows
        ],
        "limit": limit,
        "offset": offset,
        "total": total,
    }
