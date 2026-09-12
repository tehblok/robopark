from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from robopark_api.models import (
    AuditLog,
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryCount,
    InventoryCountLine,
    InventoryParkStock,
    User,
)
from robopark_api.services import inventory_access, inventory_catalog, inventory_stock
from robopark_api.services.inventory_identity import resolve_catalog_part
from robopark_api.services.rbac import PERMISSION_INVENTORY_DOCUMENTS_POST, has_permission


class InventoryCountConflict(inventory_stock.InventoryConflict):
    def __init__(self, conflicts: list[dict[str, int]]) -> None:
        super().__init__("inventory_count_stale")
        self.conflicts = conflicts

    @property
    def detail(self) -> dict[str, object]:
        return {"code": self.code, "conflicts": self.conflicts}


def _clean_text(value: str | None) -> str | None:
    return (value or "").strip() or None


def _prefix_upper_bound(value: bytes) -> bytes | None:
    for index in range(len(value) - 1, -1, -1):
        if value[index] < 0xFF:
            return value[:index] + bytes([value[index] + 1])
    return None


def _audit(db: Session, user: User, count: InventoryCount, action: str, fields) -> None:
    db.add(
        AuditLog(
            action=action,
            actor_user_id=user.id,
            actor_username=user.username,
            actor_role=user.role,
            park_id=count.park_id,
            target_type="inventory_count",
            target_id=str(count.id),
            detail=json.dumps({"changed_fields": sorted(fields)}, ensure_ascii=False),
        )
    )


def _require_document_post(db: Session, user: User, park_id: int) -> None:
    inventory_access.require_park(db, user, park_id)
    if not has_permission(db, user, PERMISSION_INVENTORY_DOCUMENTS_POST):
        raise PermissionError("forbidden")


def _count(db: Session, park_id: int, count_id: int, *, lock: bool = False) -> InventoryCount:
    if lock and db.get_bind().dialect.name == "sqlite":
        db.execute(
            update(InventoryCount)
            .where(InventoryCount.id == count_id, InventoryCount.park_id == park_id)
            .values(status=InventoryCount.status)
            .execution_options(synchronize_session=False)
        )
    statement = select(InventoryCount).where(
        InventoryCount.id == count_id,
        InventoryCount.park_id == park_id,
    )
    if lock:
        statement = statement.with_for_update(of=InventoryCount).execution_options(
            populate_existing=True
        )
    row = db.scalar(statement)
    if row is None:
        raise LookupError("inventory_count_not_found")
    return row


def _lines(db: Session, count_id: int) -> list[InventoryCountLine]:
    return list(
        db.scalars(
            select(InventoryCountLine)
            .where(InventoryCountLine.count_id == count_id)
            .order_by(InventoryCountLine.catalog_part_id, InventoryCountLine.id)
        )
    )


def _count_dict(count: InventoryCount, lines: list[InventoryCountLine]) -> dict:
    return {
        "id": count.id,
        "park_id": count.park_id,
        "name": count.name,
        "status": count.status,
        "created_by": count.created_by,
        "posted_by": count.posted_by,
        "created_at": count.created_at,
        "posted_at": count.posted_at,
        "lines": [
            {
                "id": line.id,
                "catalog_part_id": line.catalog_part_id,
                "expected_quantity": line.expected_quantity,
                "actual_quantity": line.actual_quantity,
                "difference": line.difference,
                "comment": line.comment,
            }
            for line in lines
        ],
    }


def count_out(db: Session, count: InventoryCount) -> dict:
    return _count_dict(count, _lines(db, count.id))


def counts_out(db: Session, counts: list[InventoryCount]) -> list[dict]:
    count_ids = [count.id for count in counts]
    lines_by_count: dict[int, list[InventoryCountLine]] = {count_id: [] for count_id in count_ids}
    if count_ids:
        rows = db.scalars(
            select(InventoryCountLine)
            .where(InventoryCountLine.count_id.in_(count_ids))
            .order_by(
                InventoryCountLine.count_id,
                InventoryCountLine.catalog_part_id,
                InventoryCountLine.id,
            )
        )
        for line in rows:
            lines_by_count[line.count_id].append(line)
    return [_count_dict(count, lines_by_count[count.id]) for count in counts]


def _snapshot(db: Session, *, park_id: int, component_id: int | None) -> list[tuple[int, int]]:
    if component_id is not None:
        component = db.get(InventoryCatalogComponent, component_id)
        if component is None or not component.is_active:
            raise LookupError("inventory_component_not_found")
    statement = (
        select(InventoryCatalogPart.id)
        .join(
            InventoryCatalogComponent,
            InventoryCatalogComponent.id == InventoryCatalogPart.component_id,
        )
        .where(
            InventoryCatalogPart.is_active.is_(True),
            InventoryCatalogComponent.is_active.is_(True),
        )
        .order_by(InventoryCatalogPart.id)
    )
    if component_id is not None:
        statement = statement.where(InventoryCatalogPart.component_id == component_id)
    part_ids = list(db.scalars(statement))
    return [
        (
            part_id,
            inventory_stock.ensure_stock(
                db,
                park_id=park_id,
                catalog_part_id=part_id,
            ).quantity,
        )
        for part_id in part_ids
    ]


def create_count(db: Session, user: User, *, park_id: int, payload) -> InventoryCount:
    inventory_access.require_park(db, user, park_id, manage=True)
    name = _clean_text(payload.name)
    if name is None:
        raise ValueError("inventory_count_name_required")
    try:
        inventory_catalog.acquire_alias_graph_read_lock(db)
        component_id = payload.scope.component_id if payload.scope.kind == "component" else None
        snapshot = _snapshot(db, park_id=park_id, component_id=component_id)
        row = InventoryCount(
            park_id=park_id,
            name=name,
            created_by=user.id,
        )
        db.add(row)
        db.flush()
        db.add_all(
            InventoryCountLine(
                count_id=row.id,
                catalog_part_id=part_id,
                expected_quantity=quantity,
            )
            for part_id, quantity in snapshot
        )
        _audit(db, user, row, "inventory.count.created", ["name", "scope", "lines"])
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


def update_count_lines(
    db: Session,
    user: User,
    *,
    park_id: int,
    count_id: int,
    lines,
) -> InventoryCount:
    inventory_access.require_park(db, user, park_id, manage=True)
    values = [
        value.model_dump() if hasattr(value, "model_dump") else dict(value) for value in lines
    ]
    part_ids = [int(value["catalog_part_id"]) for value in values]
    if len(part_ids) != len(set(part_ids)):
        raise ValueError("inventory_count_line_duplicate")
    try:
        row = _count(db, park_id, count_id, lock=True)
        if row.status != "draft":
            raise inventory_stock.InventoryConflict("inventory_count_not_draft")
        rows = {line.catalog_part_id: line for line in _lines(db, row.id)}
        if any(part_id not in rows for part_id in part_ids):
            raise ValueError("inventory_count_line_invalid")
        for value in values:
            line = rows[int(value["catalog_part_id"])]
            actual = int(value["actual_quantity"])
            if actual < 0:
                raise ValueError("inventory_count_actual_invalid")
            inventory_stock.require_int64(actual)
            line.actual_quantity = actual
            line.difference = actual - line.expected_quantity
            line.comment = _clean_text(value.get("comment"))
        _audit(db, user, row, "inventory.count.updated", ["lines"])
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


def list_counts(
    db: Session,
    user: User,
    *,
    park_id: int,
    query: str | None,
    limit: int,
    offset: int,
) -> tuple[list[InventoryCount], int]:
    inventory_access.require_park(db, user, park_id)
    filters = [InventoryCount.park_id == park_id]
    clean_query = _clean_text(query)
    if clean_query:
        prefix = clean_query.casefold().encode("utf-8")
        filters.append(InventoryCount.normalized_name_key >= prefix)
        upper_bound = _prefix_upper_bound(prefix)
        if upper_bound is not None:
            filters.append(InventoryCount.normalized_name_key < upper_bound)
        ordering = (
            InventoryCount.normalized_name_key,
            InventoryCount.id,
        )
    else:
        ordering = (InventoryCount.created_at.desc(), InventoryCount.id.desc())
    total = db.scalar(select(func.count(InventoryCount.id)).where(*filters))
    rows = list(
        db.scalars(
            select(InventoryCount).where(*filters).order_by(*ordering).limit(limit).offset(offset)
        )
    )
    return rows, int(total or 0)


def _group_source_id(count: InventoryCount, canonical_part_id: int) -> str:
    return f"{count.id}:canonical:{canonical_part_id}"


def _canonical_line_groups(
    db: Session,
    *,
    park_id: int,
    lines: list[InventoryCountLine],
) -> list[tuple[int, InventoryParkStock | None, list[InventoryCountLine]]]:
    groups: dict[int, list[InventoryCountLine]] = {}
    canonical_parts: dict[int, InventoryCatalogPart] = {}
    for line in lines:
        part = resolve_catalog_part(db, line.catalog_part_id, allow_archived=True)
        canonical_parts[part.id] = part
        groups.setdefault(part.id, []).append(line)
    result = []
    for canonical_id in sorted(groups):
        grouped_lines = sorted(groups[canonical_id], key=lambda line: line.id)
        part = canonical_parts[canonical_id]
        try:
            stock = inventory_stock.ensure_stock(
                db,
                park_id=park_id,
                catalog_part_id=canonical_id,
                allow_archived=True,
            )
        except LookupError:
            expected = sum(line.expected_quantity for line in grouped_lines)
            actual = sum(int(line.actual_quantity or 0) for line in grouped_lines)
            inventory_stock.require_int64(expected)
            inventory_stock.require_int64(actual)
            if part.is_active or expected != 0 or actual != 0:
                raise
            stock = None
        result.append((canonical_id, stock, grouped_lines))
    return result


def post_count(db: Session, user: User, *, park_id: int, count_id: int) -> InventoryCount:
    _require_document_post(db, user, park_id)
    try:
        inventory_catalog.acquire_alias_graph_read_lock(db)
        row = _count(db, park_id, count_id, lock=True)
        if row.status == "posted":
            db.commit()
            db.refresh(row)
            return row
        if row.status != "draft":
            raise inventory_stock.InventoryConflict("inventory_count_not_draft")
        lines = _lines(db, row.id)
        if any(line.actual_quantity is None for line in lines):
            raise ValueError("inventory_count_actual_required")
        groups = _canonical_line_groups(db, park_id=park_id, lines=lines)
        conflicts = []
        for canonical_id, stock, grouped_lines in groups:
            expected = sum(line.expected_quantity for line in grouped_lines)
            inventory_stock.require_int64(expected)
            current = stock.quantity if stock is not None else 0
            if current != expected:
                conflicts.append(
                    {
                        "catalog_part_id": canonical_id,
                        "expected_quantity": expected,
                        "current_quantity": current,
                        "affected_lines": [
                            {
                                "count_line_id": line.id,
                                "catalog_part_id": line.catalog_part_id,
                            }
                            for line in grouped_lines
                        ],
                    }
                )
        if conflicts:
            raise InventoryCountConflict(conflicts)
        for canonical_id, _stock, grouped_lines in groups:
            expected = sum(int(line.expected_quantity) for line in grouped_lines)
            actual = sum(int(line.actual_quantity) for line in grouped_lines)
            inventory_stock.require_int64(expected)
            inventory_stock.require_int64(actual)
            delta = actual - expected
            if delta == 0:
                continue
            comments = [line.comment for line in grouped_lines if line.comment]
            inventory_stock.apply_stock_delta(
                db,
                user=user,
                park_id=park_id,
                catalog_part_id=canonical_id,
                delta=delta,
                kind="adjustment",
                source_kind="count",
                source_id=_group_source_id(row, canonical_id),
                note="; ".join(comments)[:500] or None,
                allow_archived=True,
            )
        row.status = "posted"
        row.posted_by = user.id
        row.posted_at = datetime.now(UTC)
        _audit(db, user, row, "inventory.count.posted", ["status", "posted_by", "posted_at"])
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


def cancel_count(db: Session, user: User, *, park_id: int, count_id: int) -> InventoryCount:
    inventory_access.require_park(db, user, park_id, manage=True)
    try:
        row = _count(db, park_id, count_id, lock=True)
        if row.status == "cancelled":
            db.commit()
            db.refresh(row)
            return row
        if row.status != "draft":
            raise inventory_stock.InventoryConflict("inventory_count_not_draft")
        row.status = "cancelled"
        _audit(db, user, row, "inventory.count.cancelled", ["status"])
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise
