from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from robopark_api.models import (
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryParkStock,
    User,
)
from robopark_api.services import inventory_access
from robopark_api.services.inventory_stock import InventoryConflict


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


def create_component(db: Session, user: User, *, park_id: int, name: str):
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
        raise InventoryConflict("inventory_component_exists")
    row = InventoryCatalogComponent(
        name=clean_name, normalized_name=normalized, created_by=user.id, updated_by=user.id
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_part(
    db: Session, user: User, *, park_id: int, component_id: int, name: str, article: str
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
    db.commit()
    db.refresh(row)
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
            raise InventoryConflict("inventory_component_exists")
        row.name = name
        row.normalized_name = normalized
    if changes.get("is_active") is not None:
        row.is_active = bool(changes["is_active"])
    row.updated_by = user.id
    db.commit()
    db.refresh(row)
    return row


def update_part(db: Session, user: User, part_id: int, changes: dict):
    inventory_access.require_catalog_manage(db, user)
    row = db.get(InventoryCatalogPart, part_id)
    if row is None:
        raise LookupError("inventory_part_not_found")
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
        row.is_active = bool(changes["is_active"])
    row.updated_by = user.id
    db.commit()
    db.refresh(row)
    return row


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
) -> dict:
    inventory_access.require_park(db, user, park_id)
    quantity = func.coalesce(InventoryParkStock.quantity, 0)
    minimum = func.coalesce(InventoryParkStock.minimum_quantity, 0)
    statement = (
        select(InventoryCatalogPart, InventoryParkStock)
        .outerjoin(
            InventoryParkStock,
            (InventoryParkStock.catalog_part_id == InventoryCatalogPart.id)
            & (InventoryParkStock.park_id == park_id),
        )
        .where(InventoryCatalogPart.is_active.is_(True))
    )
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
            or_(InventoryParkStock.id.is_(None), InventoryParkStock.location.is_(None))
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
