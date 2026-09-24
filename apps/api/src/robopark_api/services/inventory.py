from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.models import (
    AuditLog,
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
from robopark_api.services.inventory_identity import resolve_catalog_part, resolve_legacy_part
from robopark_api.services.reliable_actions import begin_action, canonical_payload
from robopark_api.services.report_attachments import sanitize_filename
from robopark_api.services.tracker_claims import get_claim, mechanic_owns_issue
from robopark_api.services.tracker_client import (
    ALLOWED_ATTACHMENT_MIMES,
    MAX_ATTACHMENT_BYTES,
    guess_image_content_type,
    normalize_attachment_content_type,
)
from robopark_api.task_workflow_models import TaskMessage


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


def _remove_photo(storage_key: str | None) -> None:
    if storage_key:
        with suppress(OSError):
            (photos_root().resolve() / storage_key).unlink()


def audit_inventory_change(
    db: Session,
    user: User,
    *,
    action: str,
    park_id: int | None,
    target_id: int,
    changed_fields,
) -> None:
    audit.record(
        db,
        action=action,
        actor=user,
        park_id=park_id,
        target_type="inventory_part",
        target_id=target_id,
        detail=json.dumps({"changed_fields": sorted(changed_fields)}, ensure_ascii=False),
    )


@dataclass
class LegacyComponentView:
    id: int
    park_id: int
    name: str
    photo_storage_key: str | None


@dataclass
class LegacyPartView:
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
    photo_storage_key: str | None


def part_adapter_id(db: Session, catalog_part_id: int, *, legacy_part_id: int | None = None) -> int:
    return -resolve_catalog_part(db, catalog_part_id, allow_archived=True).id


def _legacy_part_view(
    part: InventoryCatalogPart,
    stock: InventoryParkStock,
    *,
    adapter_part_id: int | None = None,
) -> LegacyPartView:
    return LegacyPartView(
        id=adapter_part_id if adapter_part_id is not None else part.id,
        catalog_part_id=part.id,
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
        "catalog_part_id": getattr(part, "catalog_part_id", part.id),
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
    components = list(
        db.scalars(
            select(InventoryCatalogComponent)
            .where(InventoryCatalogComponent.is_active.is_(True))
            .order_by(InventoryCatalogComponent.normalized_name, InventoryCatalogComponent.id)
        )
    )
    grouped = {component.id: [] for component in components}
    for item in result["items"]:
        grouped[item["component_id"]].append(
            {
                "id": -item["id"],
                "catalog_part_id": item["id"],
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
        "low_stock_count": sum(0 < part["quantity"] <= part["minimum_quantity"] for part in parts),
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
    storage_key = None
    try:
        component = inventory_catalog.create_component(
            db, user, park_id=park_id, name=name, commit=False
        )
        if photo:
            component.photo_storage_key, component.photo_filename, component.photo_content_type = (
                save_photo(*photo)
            )
            storage_key = component.photo_storage_key
        db.commit()
        db.refresh(component)
    except Exception:
        db.rollback()
        _remove_photo(storage_key)
        raise
    audit_inventory_change(
        db,
        user,
        action="inventory.catalog.component.created",
        park_id=park_id,
        target_id=component.id,
        changed_fields=["name", *(["photo"] if photo else [])],
    )
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
    inventory_stock.require_int64(quantity)
    inventory_stock.require_int64(minimum_quantity)
    clean_location = _text(location, "inventory_location_required")
    storage_key = None
    try:
        part = inventory_catalog.create_part(
            db,
            user,
            park_id=park_id,
            component_id=component_id,
            name=name,
            article=article,
            commit=False,
        )
        if photo:
            part.photo_storage_key, part.photo_filename, part.photo_content_type = save_photo(
                *photo
            )
            storage_key = part.photo_storage_key
        stock = inventory_stock.ensure_stock(db, park_id=park_id, catalog_part_id=part.id)
        stock.minimum_quantity = minimum_quantity
        stock.location = clean_location
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
    except Exception:
        db.rollback()
        _remove_photo(storage_key)
        raise
    db.refresh(stock)
    audit_inventory_change(
        db,
        user,
        action="inventory.catalog.part.created",
        park_id=park_id,
        target_id=part.id,
        changed_fields=[
            "article",
            "component_id",
            "location",
            "minimum_quantity",
            "name",
            "quantity",
            *(["photo"] if photo else []),
        ],
    )
    return _legacy_part_view(part, stock, adapter_part_id=part_adapter_id(db, part.id))


def _part_and_stock(
    db: Session,
    user: User,
    part_id: int,
    *,
    park_id: int | None = None,
    catalog_part_id: int | None = None,
):
    legacy = None
    if catalog_part_id is not None:
        part = resolve_catalog_part(db, catalog_part_id, lock=True)
    elif part_id < 0:
        part = resolve_catalog_part(db, -part_id, lock=True)
    elif (legacy := db.get(InventoryPart, part_id)) is not None:
        if park_id is not None and park_id != legacy.park_id:
            raise inventory_stock.InventoryConflict("inventory_park_part_mismatch")
        park_id = legacy.park_id
        require_park(db, user, legacy.park_id)
        part = resolve_legacy_part(db, legacy, lock=True)
    else:
        part = resolve_catalog_part(db, part_id, lock=True)
    if park_id is not None:
        inventory_access.require_park(db, user, park_id)
        return (
            part,
            inventory_stock.ensure_stock(db, park_id=park_id, catalog_part_id=part.id),
            legacy.id if legacy is not None else None,
        )
    stocks = list(
        db.scalars(
            select(InventoryParkStock)
            .where(
                InventoryParkStock.catalog_part_id == part.id,
                InventoryParkStock.park_id.in_(accessible_park_ids(db, user)),
            )
            .order_by(InventoryParkStock.park_id)
            .with_for_update()
        )
    )
    if len(stocks) > 1:
        raise inventory_stock.InventoryConflict(
            "inventory_park_required", park_ids=[stock.park_id for stock in stocks]
        )
    if not stocks:
        raise LookupError("inventory_stock_not_found")
    return part, stocks[0], legacy.id if legacy is not None else None


def update_part(db: Session, user: User, part_id: int, changes: dict) -> LegacyPartView:
    park_id = changes.pop("park_id", None)
    catalog_part_id = changes.pop("catalog_part_id", None)
    part, stock, legacy_part_id = _part_and_stock(
        db,
        user,
        part_id,
        park_id=park_id,
        catalog_part_id=catalog_part_id,
    )
    catalog_changes = {
        key: changes[key] for key in ("component_id", "name", "article") if key in changes
    }
    inventory_access.require_park(db, user, stock.park_id, manage=True)
    clean_location = None
    if changes.get("location") is not None:
        clean_location = _text(changes["location"], "inventory_location_required")
    try:
        if catalog_changes:
            part = inventory_catalog.update_part(db, user, part.id, catalog_changes, commit=False)
        if clean_location is not None:
            stock.location = clean_location
        if changes.get("minimum_quantity") is not None:
            minimum_quantity = int(changes["minimum_quantity"])
            if minimum_quantity < 0:
                raise ValueError("inventory_quantity_invalid")
            stock.minimum_quantity = inventory_stock.require_int64(minimum_quantity)
        if changes.get("is_active") is not None:
            stock.is_active = bool(changes["is_active"])
        stock.updated_by = user.id
        inventory_stock.increment_stock_version(stock)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(part)
    db.refresh(stock)
    if catalog_changes:
        audit.record(
            db,
            action="inventory.catalog.part.updated",
            actor=user,
            target_type="inventory_part",
            target_id=part.id,
            detail=json.dumps({"changed_fields": sorted(catalog_changes)}, ensure_ascii=False),
        )
    audit_inventory_change(
        db,
        user,
        action="inventory.stock.configured",
        park_id=stock.park_id,
        target_id=part.id,
        changed_fields=changes,
    )
    return _legacy_part_view(
        part,
        stock,
        adapter_part_id=part_adapter_id(db, part.id, legacy_part_id=legacy_part_id),
    )


def move_stock(
    db: Session,
    user: User,
    part_id: int,
    *,
    park_id: int | None = None,
    catalog_part_id: int | None = None,
    kind: str,
    quantity: int,
    note: str | None = None,
) -> InventoryMovement:
    part, stock, legacy_part_id = _part_and_stock(
        db,
        user,
        part_id,
        park_id=park_id,
        catalog_part_id=catalog_part_id,
    )
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
    movement.part_id = legacy_part_id
    db.commit()
    db.refresh(movement)
    audit_inventory_change(
        db,
        user,
        action="inventory.stock.moved",
        park_id=stock.park_id,
        target_id=part.id,
        changed_fields=["kind", "quantity", *(["note"] if note else [])],
    )
    db.refresh(movement)
    return movement


def task_writeoff(
    db: Session,
    user: User,
    issue_key: str,
    part_id: int,
    quantity: int,
    *,
    park_id: int | None = None,
    catalog_part_id: int | None = None,
    idempotency_key: str,
) -> InventoryMovement:
    if user.role != "mechanic":
        raise PermissionError("forbidden")
    if quantity <= 0:
        raise ValueError("inventory_quantity_invalid")
    inventory_stock.require_int64(quantity)
    idempotency_key = _text(idempotency_key, "inventory_idempotency_key_required")
    claim = get_claim(db, issue_key)
    if claim is None or claim.owner_user_id != user.id:
        raise PermissionError("inventory_issue_not_owned")
    if claim.state != "active":
        raise inventory_stock.InventoryConflict("tracker_issue_claim_not_active")
    if park_id is not None and park_id != claim.park_id:
        raise inventory_stock.InventoryConflict("inventory_park_part_mismatch")
    part, stock, legacy_part_id = _part_and_stock(
        db,
        user,
        part_id,
        park_id=claim.park_id,
        catalog_part_id=catalog_part_id,
    )
    resolved_park_id = stock.park_id
    resolved_part_id = part.id
    park = require_park(db, user, stock.park_id)
    existing = db.scalar(
        select(InventoryMovement).where(InventoryMovement.idempotency_key == idempotency_key)
    )
    if existing is not None:
        existing_part = resolve_catalog_part(db, existing.catalog_part_id, allow_archived=True)
        if (
            existing.actor_user_id != user.id
            or existing.issue_key != issue_key
            or existing.park_id != resolved_park_id
            or existing_part.id != resolved_part_id
            or existing.delta != -quantity
        ):
            raise inventory_stock.InventoryConflict("inventory_idempotency_conflict")
        return existing
    token = platform_settings.get_tracker_token(db)
    if not token:
        raise RuntimeError("tracker_token_not_configured")
    try:
        issue = tracker_cache.get_issue(token=token, key=issue_key)
    except tracker_client.TrackerError as exc:
        raise RuntimeError("tracker_upstream_error") from exc
    # The first lookup can race a request that commits while this one waits for
    # the stock writer lock. Re-read the receipt after acquiring that lock.
    stock = inventory_stock.ensure_stock(
        db, park_id=resolved_park_id, catalog_part_id=resolved_part_id
    )
    existing = db.scalar(
        select(InventoryMovement).where(InventoryMovement.idempotency_key == idempotency_key)
    )
    if existing is not None:
        existing_part = resolve_catalog_part(db, existing.catalog_part_id, allow_archived=True)
        if (
            existing.actor_user_id != user.id
            or existing.issue_key != issue_key
            or existing.park_id != resolved_park_id
            or existing_part.id != resolved_part_id
            or existing.delta != -quantity
        ):
            raise inventory_stock.InventoryConflict("inventory_idempotency_conflict")
        return existing
    if stock.quantity < quantity:
        raise inventory_stock.InventoryConflict(
            "inventory_out_of_stock", current_quantity=stock.quantity
        )
    tags = {str(tag).strip().casefold() for tag in (issue or {}).get("tags") or []}
    if issue is None or park.tag.casefold() not in tags or not mechanic_owns_issue(db, user, issue):
        raise PermissionError("inventory_issue_not_owned")
    body = f"Списано со склада: {part.name} ({part.article}) × {quantity}"
    try:
        movement = inventory_stock.apply_stock_delta(
            db,
            user=user,
            park_id=stock.park_id,
            catalog_part_id=part.id,
            delta=-quantity,
            kind="task_writeoff",
            source_kind="task_writeoff",
            source_id=idempotency_key,
            issue_key=issue_key,
            idempotency_key=idempotency_key,
            note=None,
        )
        movement.part_id = legacy_part_id
        db.flush()
        action = begin_action(
            db,
            actor=user,
            resource_type="tracker_issue",
            resource_id=issue_key,
            action="comment",
            idempotency_key=f"inventory-writeoff:{movement.id}",
            payload={"text": body, "movement_id": movement.id},
        )
        signature = tracker_signatures.build_signature_context(db, user, issue)
        tracker_text = tracker_signatures.format_signed_comment(
            body=body,
            park_name=signature.park_name,
            mechanic_login=signature.mechanic_login,
            operator_login=signature.operator_login,
            actor_login=signature.actor_login,
            occurred_at=datetime.fromtimestamp(action.row.created_at, UTC),
        )
        action.row.payload_json, action.row.payload_hash = canonical_payload(
            {"text": body, "movement_id": movement.id, "tracker_text": tracker_text}
        )
        now = time.time()
        db.add(
            TaskMessage(
                id=str(uuid4()),
                issue_key=issue_key,
                kind="system",
                author_user_id=user.id,
                author_name=user.username,
                text=body,
                action_id=action.row.id,
                sync_state="pending",
                visibility="participants",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            AuditLog(
                action="inventory.stock.moved",
                actor_user_id=user.id,
                actor_username=user.username,
                actor_role=user.role,
                park_id=park.id,
                target_type="inventory_part",
                target_id=str(part.id),
                outcome="success",
                detail=json.dumps(
                    {"issue_key": issue_key, "quantity": quantity, "movement_id": movement.id}
                ),
            )
        )
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(InventoryMovement).where(InventoryMovement.idempotency_key == idempotency_key)
        )
        if existing is None:
            raise
        existing_part = resolve_catalog_part(db, existing.catalog_part_id, allow_archived=True)
        if (
            existing.actor_user_id != user.id
            or existing.issue_key != issue_key
            or existing.park_id != resolved_park_id
            or existing_part.id != resolved_part_id
            or existing.delta != -quantity
        ):
            raise inventory_stock.InventoryConflict("inventory_idempotency_conflict") from None
        return existing
    except Exception:
        db.rollback()
        raise
    db.commit()
    tracker_cache.invalidate_issue(issue_key)
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
            "part_id": part_adapter_id(db, row.catalog_part_id, legacy_part_id=row.part_id),
            "catalog_part_id": row.catalog_part_id,
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
    legacy = db.get(InventoryPart, part_id) if part_id >= 0 else None
    if legacy is not None:
        require_park(db, user, legacy.park_id)
        row = resolve_legacy_part(db, legacy)
    else:
        row = resolve_catalog_part(db, -part_id if part_id < 0 else part_id)
    if not accessible_park_ids(db, user):
        raise PermissionError("forbidden")
    return (
        photo_path(row.photo_storage_key),
        row.photo_content_type or "image/jpeg",
        row.photo_filename or "part.jpg",
    )
