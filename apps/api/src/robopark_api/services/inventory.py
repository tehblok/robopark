from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.models import (
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryMovement,
    InventoryParkStock,
    InventoryPart,
    User,
)
from robopark_api.services import (
    audit,
    inventory_access,
    inventory_catalog,
    inventory_stock,
    platform_settings,
    tracker_cache,
    tracker_client,
    tracker_signatures,
)
from robopark_api.services.report_attachments import sanitize_filename
from robopark_api.services.tracker_claims import mechanic_owns_issue
from robopark_api.services.tracker_client import (
    ALLOWED_ATTACHMENT_MIMES,
    MAX_ATTACHMENT_BYTES,
    guess_image_content_type,
    normalize_attachment_content_type,
)


def accessible_park_ids(db: Session, user: User) -> set[int]:
    return inventory_access.accessible_park_ids(db, user)


def require_park(db: Session, user: User, park_id: int):
    return inventory_access.require_park(db, user, park_id)


def _text(value: str, error: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(error)
    return result


def photos_root() -> Path:
    configured = get_settings().inventory_photos_dir
    if configured:
        return Path(configured)
    database = get_settings().database_url.removeprefix("sqlite:///")
    return Path(database).resolve().parent / "inventory-photos"


def save_photo(
    filename: str | None, content: bytes, content_type: str | None
) -> tuple[str, str, str]:
    if not content:
        raise ValueError("inventory_photo_empty")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError("inventory_photo_too_large")
    safe_name = sanitize_filename(filename, fallback="part.jpg")
    resolved_type = normalize_attachment_content_type(
        filename=safe_name, content=content, content_type=content_type
    ) or guess_image_content_type(safe_name, content)
    if not resolved_type or resolved_type not in ALLOWED_ATTACHMENT_MIMES:
        raise ValueError("inventory_photo_invalid_type")
    root = photos_root().resolve()
    root.mkdir(parents=True, exist_ok=True)
    key = uuid4().hex
    destination = (root / key).resolve()
    if not destination.is_relative_to(root):
        raise ValueError("inventory_photo_invalid")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".inventory-", dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        with suppress(OSError):
            temporary.unlink()
    return key, safe_name, resolved_type


def photo_path(storage_key: str | None) -> Path:
    if not storage_key:
        raise LookupError("inventory_photo_not_found")
    root = photos_root().resolve()
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise LookupError("inventory_photo_not_found")
    return path


@dataclass
class LegacyComponentView:
    id: int
    park_id: int
    name: str
    photo_storage_key: str | None


@dataclass
class LegacyPartView:
    id: int
    park_id: int
    component_id: int
    name: str
    article: str
    quantity: int
    minimum_quantity: int
    location: str
    is_active: bool
    photo_storage_key: str | None


def _legacy_part_view(part: InventoryCatalogPart, stock: InventoryParkStock) -> LegacyPartView:
    return LegacyPartView(
        id=part.id,
        park_id=stock.park_id,
        component_id=part.component_id,
        name=part.name,
        article=part.article,
        quantity=stock.quantity,
        minimum_quantity=stock.minimum_quantity,
        location=stock.location or "",
        is_active=part.is_active and stock.is_active,
        photo_storage_key=part.photo_storage_key,
    )


def part_out(part: InventoryPart | LegacyPartView) -> dict:
    return {
        "id": part.id,
        "park_id": part.park_id,
        "component_id": part.component_id,
        "name": part.name,
        "article": part.article,
        "quantity": part.quantity,
        "minimum_quantity": part.minimum_quantity,
        "location": part.location,
        "is_active": part.is_active,
        "has_photo": bool(part.photo_storage_key),
    }


def overview(db: Session, user: User, park_id: int) -> dict:
    result = inventory_catalog.search_catalog(
        db,
        user,
        park_id=park_id,
        query=None,
        component_id=None,
        stock_filter=None,
        limit=1_000_000,
        offset=0,
    )
    component_ids = {item["component_id"] for item in result["items"]}
    components = list(
        db.scalars(
            select(InventoryCatalogComponent)
            .where(InventoryCatalogComponent.id.in_(component_ids))
            .order_by(InventoryCatalogComponent.normalized_name, InventoryCatalogComponent.id)
        )
    )
    grouped = {component.id: [] for component in components}
    for item in result["items"]:
        grouped[item["component_id"]].append(
            {
                "id": item["id"],
                "park_id": park_id,
                "component_id": item["component_id"],
                "name": item["name"],
                "article": item["article"],
                "quantity": item["quantity"],
                "minimum_quantity": item["minimum_quantity"],
                "location": item["location"] or "",
                "is_active": item["is_active"] and item["stock_is_active"],
                "has_photo": item["has_photo"],
            }
        )
    parts = [part for values in grouped.values() for part in values]
    return {
        "park_id": park_id,
        "component_count": len(components),
        "part_count": len(parts),
        "low_stock_count": sum(
            0 < part["quantity"] <= part["minimum_quantity"] for part in parts
        ),
        "out_of_stock_count": sum(part["quantity"] == 0 for part in parts),
        "components": [
            {
                "id": component.id,
                "park_id": park_id,
                "name": component.name,
                "has_photo": bool(component.photo_storage_key),
                "parts": grouped.get(component.id, []),
            }
            for component in components
        ],
    }


def create_component(
    db: Session,
    user: User,
    park_id: int,
    name: str,
    photo: tuple[str | None, bytes, str | None] | None,
) -> LegacyComponentView:
    component = inventory_catalog.create_component(db, user, park_id=park_id, name=name)
    if photo:
        component.photo_storage_key, component.photo_filename, component.photo_content_type = (
            save_photo(*photo)
        )
        db.commit()
    return LegacyComponentView(component.id, park_id, component.name, component.photo_storage_key)


def create_part(
    db: Session,
    user: User,
    *,
    park_id: int,
    component_id: int,
    name: str,
    article: str,
    quantity: int,
    minimum_quantity: int,
    location: str,
    photo: tuple[str | None, bytes, str | None] | None,
) -> LegacyPartView:
    if quantity < 0 or minimum_quantity < 0:
        raise ValueError("inventory_quantity_invalid")
    part = inventory_catalog.create_part(
        db,
        user,
        park_id=park_id,
        component_id=component_id,
        name=name,
        article=article,
    )
    if photo:
        part.photo_storage_key, part.photo_filename, part.photo_content_type = save_photo(*photo)
    stock = inventory_stock.ensure_stock(db, park_id=park_id, catalog_part_id=part.id)
    stock.minimum_quantity = minimum_quantity
    stock.location = _text(location, "inventory_location_required")
    stock.updated_by = user.id
    if quantity:
        inventory_stock.apply_stock_delta(
            db,
            user=user,
            park_id=park_id,
            catalog_part_id=part.id,
            delta=quantity,
            kind="receipt",
            source_kind=None,
            source_id=None,
            note="Начальный остаток",
        )
    db.commit()
    db.refresh(stock)
    return _legacy_part_view(part, stock)


def _part_and_stock(db: Session, user: User, part_id: int):
    part = db.get(InventoryCatalogPart, part_id)
    if part is not None:
        stock = db.scalar(
            select(InventoryParkStock).where(
                InventoryParkStock.catalog_part_id == part.id,
                InventoryParkStock.park_id.in_(accessible_park_ids(db, user)),
            ).with_for_update()
        )
        if stock is not None:
            return part, stock
    legacy = db.get(InventoryPart, part_id)
    if legacy is not None:
        require_park(db, user, legacy.park_id)
        part = db.scalar(
            select(InventoryCatalogPart).where(
                InventoryCatalogPart.normalized_article
                == inventory_catalog.normalize_key(legacy.article)
            )
        )
        if part is not None:
            return part, inventory_stock.ensure_stock(
                db, park_id=legacy.park_id, catalog_part_id=part.id
            )
    raise LookupError("inventory_part_not_found")


def update_part(db: Session, user: User, part_id: int, changes: dict) -> LegacyPartView:
    part, stock = _part_and_stock(db, user, part_id)
    catalog_changes = {
        key: changes[key] for key in ("component_id", "name", "article") if key in changes
    }
    if catalog_changes:
        part = inventory_catalog.update_part(db, user, part.id, catalog_changes)
    inventory_access.require_park(db, user, stock.park_id, manage=True)
    if changes.get("location") is not None:
        stock.location = _text(changes["location"], "inventory_location_required")
    if changes.get("minimum_quantity") is not None:
        stock.minimum_quantity = int(changes["minimum_quantity"])
    if changes.get("is_active") is not None:
        stock.is_active = bool(changes["is_active"])
    stock.updated_by = user.id
    stock.version += 1
    db.commit()
    db.refresh(stock)
    return _legacy_part_view(part, stock)


def move_stock(
    db: Session, user: User, part_id: int, *, kind: str, quantity: int, note: str | None = None
) -> InventoryMovement:
    part, stock = _part_and_stock(db, user, part_id)
    inventory_access.require_park(db, user, stock.park_id, manage=True)
    if (
        quantity == 0
        or kind not in {"receipt", "writeoff", "adjustment"}
        or (kind != "adjustment" and quantity < 0)
    ):
        raise ValueError("inventory_quantity_invalid")
    delta = quantity if kind in {"receipt", "adjustment"} else -quantity
    movement = inventory_stock.apply_stock_delta(
        db,
        user=user,
        park_id=stock.park_id,
        catalog_part_id=part.id,
        delta=delta,
        kind=kind,
        source_kind=None,
        source_id=None,
        note=(note or "").strip() or None,
    )
    db.commit()
    db.refresh(movement)
    return movement


def task_writeoff(
    db: Session, user: User, issue_key: str, part_id: int, quantity: int
) -> InventoryMovement:
    if user.role != "mechanic":
        raise PermissionError("forbidden")
    part, stock = _part_and_stock(db, user, part_id)
    park = require_park(db, user, stock.park_id)
    if quantity <= 0:
        raise ValueError("inventory_quantity_invalid")
    if stock.quantity < quantity:
        raise inventory_stock.InventoryConflict(
            "inventory_out_of_stock", current_quantity=stock.quantity
        )
    token = platform_settings.get_tracker_token(db)
    if not token:
        raise RuntimeError("tracker_token_not_configured")
    try:
        issue = tracker_cache.get_issue(token=token, key=issue_key)
    except tracker_client.TrackerError as exc:
        raise RuntimeError("tracker_upstream_error") from exc
    tags = {str(tag).strip().casefold() for tag in (issue or {}).get("tags") or []}
    if issue is None or park.tag.casefold() not in tags or not mechanic_owns_issue(db, user, issue):
        raise PermissionError("inventory_issue_not_owned")
    body = (
        "Техническое сообщение · Склад\n"
        f"Запчасть: {part.name}\nАртикул: {part.article}\n"
        f"Количество: {quantity}\nМеханик: {user.username}"
    )
    ctx = tracker_signatures.build_signature_context(db, user, issue)
    comment = tracker_signatures.format_signed_comment(
        body=body,
        park_name=ctx.park_name,
        mechanic_login=ctx.mechanic_login,
        operator_login=ctx.operator_login,
        actor_login=ctx.actor_login,
    )
    try:
        tracker_client.add_comment(token=token, key=issue_key, text=comment)
    except tracker_client.TrackerError as exc:
        db.rollback()
        raise RuntimeError("tracker_upstream_error") from exc
    movement = inventory_stock.apply_stock_delta(
        db,
        user=user,
        park_id=stock.park_id,
        catalog_part_id=part.id,
        delta=-quantity,
        kind="task_writeoff",
        source_kind=None,
        source_id=None,
        issue_key=issue_key,
        note=None,
    )
    db.commit()
    tracker_cache.invalidate_issue(issue_key)
    audit.record(
        db,
        action=audit.ACTION_TRACKER_COMMENT,
        actor=user,
        park_id=park.id,
        target_type="tracker_issue",
        target_id=issue_key,
        detail=f"inventory {part.article} x{quantity}",
    )
    db.refresh(movement)
    return movement


def movements(db: Session, user: User, park_id: int, limit: int = 100) -> list[dict]:
    require_park(db, user, park_id)
    rows = db.execute(
        select(InventoryMovement, User.username)
        .join(User, User.id == InventoryMovement.actor_user_id)
        .where(InventoryMovement.park_id == park_id)
        .order_by(InventoryMovement.created_at.desc(), InventoryMovement.id.desc())
        .limit(min(max(limit, 1), 500))
    ).all()
    return [
        {
            "id": row.id,
            "part_id": row.part_id or row.catalog_part_id,
            "park_id": row.park_id,
            "actor_user_id": row.actor_user_id,
            "actor_username": username,
            "kind": row.kind,
            "delta": row.delta,
            "balance_after": row.balance_after,
            "issue_key": row.issue_key,
            "note": row.note,
            "created_at": row.created_at,
        }
        for row, username in rows
    ]


def component_photo(db: Session, user: User, component_id: int) -> tuple[Path, str, str]:
    row = db.get(InventoryCatalogComponent, component_id)
    if row is None:
        raise LookupError("inventory_component_not_found")
    if not accessible_park_ids(db, user):
        raise PermissionError("forbidden")
    return (
        photo_path(row.photo_storage_key),
        row.photo_content_type or "image/jpeg",
        row.photo_filename or "component.jpg",
    )


def part_photo(db: Session, user: User, part_id: int) -> tuple[Path, str, str]:
    row = db.get(InventoryCatalogPart, part_id)
    if row is None:
        raise LookupError("inventory_part_not_found")
    if not accessible_park_ids(db, user):
        raise PermissionError("forbidden")
    return (
        photo_path(row.photo_storage_key),
        row.photo_content_type or "image/jpeg",
        row.photo_filename or "part.jpg",
    )
