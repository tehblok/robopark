from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.models import (
    AccessStatus,
    InventoryComponent,
    InventoryMovement,
    InventoryPart,
    Park,
    User,
    UserPark,
)
from robopark_api.services import (
    audit,
    platform_settings,
    tracker_cache,
    tracker_client,
    tracker_signatures,
)
from robopark_api.services.rbac import PERMISSION_NAV_INVENTORY, has_permission
from robopark_api.services.report_attachments import sanitize_filename
from robopark_api.services.tracker_claims import mechanic_owns_issue
from robopark_api.services.tracker_client import (
    ALLOWED_ATTACHMENT_MIMES,
    MAX_ATTACHMENT_BYTES,
    guess_image_content_type,
    normalize_attachment_content_type,
)


def _approved(db: Session, user: User) -> bool:
    return bool(
        user.is_active
        and user.access_status == AccessStatus.approved.value
        and has_permission(db, user, PERMISSION_NAV_INVENTORY)
    )


def accessible_park_ids(db: Session, user: User) -> set[int]:
    if not _approved(db, user):
        return set()
    stmt = select(Park.id).where(Park.is_active.is_(True))
    if user.role not in {"admin", "royal", "operator"}:
        stmt = stmt.join(UserPark).where(UserPark.user_id == user.id)
    return set(db.scalars(stmt))


def require_park(db: Session, user: User, park_id: int) -> Park:
    if park_id not in accessible_park_ids(db, user):
        raise PermissionError("forbidden")
    park = db.get(Park, park_id)
    if park is None or not park.is_active:
        raise LookupError("park_not_found")
    return park


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


def part_out(part: InventoryPart) -> dict:
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
    require_park(db, user, park_id)
    components = list(
        db.scalars(
            select(InventoryComponent)
            .where(InventoryComponent.park_id == park_id)
            .order_by(InventoryComponent.name, InventoryComponent.id)
        )
    )
    parts = list(
        db.scalars(
            select(InventoryPart)
            .where(InventoryPart.park_id == park_id, InventoryPart.is_active.is_(True))
            .order_by(InventoryPart.name, InventoryPart.id)
        )
    )
    grouped = {component.id: [] for component in components}
    for part in parts:
        grouped.setdefault(part.component_id, []).append(part_out(part))
    return {
        "park_id": park_id,
        "component_count": len(components),
        "part_count": len(parts),
        "low_stock_count": sum(0 < part.quantity <= part.minimum_quantity for part in parts),
        "out_of_stock_count": sum(part.quantity == 0 for part in parts),
        "components": [
            {
                "id": component.id,
                "park_id": component.park_id,
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
) -> InventoryComponent:
    require_park(db, user, park_id)
    normalized_name = _text(name, "inventory_component_name_required")
    if db.scalar(
        select(InventoryComponent.id).where(
            InventoryComponent.park_id == park_id, InventoryComponent.name == normalized_name
        )
    ):
        raise ValueError("inventory_component_exists")
    component = InventoryComponent(park_id=park_id, name=normalized_name)
    if photo:
        component.photo_storage_key, component.photo_filename, component.photo_content_type = (
            save_photo(*photo)
        )
    db.add(component)
    db.commit()
    db.refresh(component)
    return component


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
) -> InventoryPart:
    require_park(db, user, park_id)
    component = db.get(InventoryComponent, component_id)
    if component is None or component.park_id != park_id:
        raise ValueError("inventory_component_invalid")
    if quantity < 0 or minimum_quantity < 0:
        raise ValueError("inventory_quantity_invalid")
    normalized_article = _text(article, "inventory_article_required")
    if db.scalar(
        select(InventoryPart.id).where(
            InventoryPart.park_id == park_id, InventoryPart.article == normalized_article
        )
    ):
        raise ValueError("inventory_article_exists")
    part = InventoryPart(
        park_id=park_id,
        component_id=component_id,
        name=_text(name, "inventory_part_name_required"),
        article=normalized_article,
        quantity=quantity,
        minimum_quantity=minimum_quantity,
        location=_text(location, "inventory_location_required"),
    )
    if photo:
        part.photo_storage_key, part.photo_filename, part.photo_content_type = save_photo(*photo)
    db.add(part)
    db.flush()
    if quantity:
        db.add(
            InventoryMovement(
                part_id=part.id,
                park_id=park_id,
                actor_user_id=user.id,
                kind="receipt",
                delta=quantity,
                balance_after=quantity,
                note="Начальный остаток",
            )
        )
    db.commit()
    db.refresh(part)
    return part


def update_part(db: Session, user: User, part_id: int, changes: dict) -> InventoryPart:
    part = db.get(InventoryPart, part_id)
    if part is None:
        raise LookupError("inventory_part_not_found")
    require_park(db, user, part.park_id)
    if changes.get("component_id") is not None:
        component = db.get(InventoryComponent, changes["component_id"])
        if component is None or component.park_id != part.park_id:
            raise ValueError("inventory_component_invalid")
        part.component_id = component.id
    for field, error in (
        ("name", "inventory_part_name_required"),
        ("article", "inventory_article_required"),
        ("location", "inventory_location_required"),
    ):
        if changes.get(field) is not None:
            setattr(part, field, _text(changes[field], error))
    if changes.get("minimum_quantity") is not None:
        part.minimum_quantity = int(changes["minimum_quantity"])
    if changes.get("is_active") is not None:
        part.is_active = bool(changes["is_active"])
    db.commit()
    db.refresh(part)
    return part


def move_stock(
    db: Session, user: User, part_id: int, *, kind: str, quantity: int, note: str | None = None
) -> InventoryMovement:
    part = db.scalar(select(InventoryPart).where(InventoryPart.id == part_id).with_for_update())
    if part is None:
        raise LookupError("inventory_part_not_found")
    require_park(db, user, part.park_id)
    if quantity <= 0 or kind not in {"receipt", "writeoff", "adjustment"}:
        raise ValueError("inventory_quantity_invalid")
    delta = quantity if kind in {"receipt", "adjustment"} else -quantity
    if part.quantity + delta < 0:
        raise ValueError("inventory_out_of_stock")
    part.quantity += delta
    movement = InventoryMovement(
        part_id=part.id,
        park_id=part.park_id,
        actor_user_id=user.id,
        kind=kind,
        delta=delta,
        balance_after=part.quantity,
        note=(note or "").strip() or None,
    )
    db.add(movement)
    db.commit()
    db.refresh(movement)
    return movement


def task_writeoff(
    db: Session, user: User, issue_key: str, part_id: int, quantity: int
) -> InventoryMovement:
    if user.role != "mechanic":
        raise PermissionError("forbidden")
    part = db.scalar(select(InventoryPart).where(InventoryPart.id == part_id).with_for_update())
    if part is None:
        raise LookupError("inventory_part_not_found")
    park = require_park(db, user, part.park_id)
    if quantity <= 0 or part.quantity < quantity:
        raise ValueError("inventory_out_of_stock")
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
    part.quantity -= quantity
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
    movement = InventoryMovement(
        part_id=part.id,
        park_id=part.park_id,
        actor_user_id=user.id,
        kind="task_writeoff",
        delta=-quantity,
        balance_after=part.quantity,
        issue_key=issue_key,
    )
    db.add(movement)
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
            "part_id": row.part_id,
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
    row = db.get(InventoryComponent, component_id)
    if row is None:
        raise LookupError("inventory_component_not_found")
    require_park(db, user, row.park_id)
    return (
        photo_path(row.photo_storage_key),
        row.photo_content_type or "image/jpeg",
        row.photo_filename or "component.jpg",
    )


def part_photo(db: Session, user: User, part_id: int) -> tuple[Path, str, str]:
    row = db.get(InventoryPart, part_id)
    if row is None:
        raise LookupError("inventory_part_not_found")
    require_park(db, user, row.park_id)
    return (
        photo_path(row.photo_storage_key),
        row.photo_content_type or "image/jpeg",
        row.photo_filename or "part.jpg",
    )
