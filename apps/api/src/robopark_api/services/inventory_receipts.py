from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from robopark_api.models import (
    AuditLog,
    InventoryMovement,
    InventoryReceipt,
    InventoryReceiptLine,
    User,
)
from robopark_api.services import inventory_access, inventory_catalog, inventory_stock
from robopark_api.services.inventory_identity import resolve_catalog_part
from robopark_api.services.rbac import PERMISSION_INVENTORY_DOCUMENTS_POST, has_permission


def _clean_text(value: str | None) -> str | None:
    return (value or "").strip() or None


def _append_note(notes: list[str], additional: str | None) -> None:
    clean = _clean_text(additional)
    if clean is None or clean in notes:
        return
    if len("\n".join([*notes, clean])) > 500:
        raise ValueError("inventory_receipt_note_too_long")
    notes.append(clean)


def _audit(db: Session, user: User, receipt: InventoryReceipt, action: str, fields) -> None:
    db.add(
        AuditLog(
            action=action,
            actor_user_id=user.id,
            actor_username=user.username,
            actor_role=user.role,
            park_id=receipt.park_id,
            target_type="inventory_receipt",
            target_id=str(receipt.id),
            detail=json.dumps({"changed_fields": sorted(fields)}, ensure_ascii=False),
        )
    )


def _require_document_post(db: Session, user: User, park_id: int) -> None:
    inventory_access.require_park(db, user, park_id)
    if not has_permission(db, user, PERMISSION_INVENTORY_DOCUMENTS_POST):
        raise PermissionError("forbidden")


def _receipt(db: Session, park_id: int, receipt_id: int, *, lock: bool = False) -> InventoryReceipt:
    if lock and db.get_bind().dialect.name == "sqlite":
        db.execute(
            update(InventoryReceipt)
            .where(InventoryReceipt.id == receipt_id, InventoryReceipt.park_id == park_id)
            .values(status=InventoryReceipt.status)
            .execution_options(synchronize_session=False)
        )
    statement = select(InventoryReceipt).where(
        InventoryReceipt.id == receipt_id, InventoryReceipt.park_id == park_id
    )
    if lock:
        statement = statement.with_for_update(of=InventoryReceipt).execution_options(
            populate_existing=True
        )
    row = db.scalar(statement)
    if row is None:
        raise LookupError("inventory_receipt_not_found")
    return row


def _lines(db: Session, receipt_id: int) -> list[InventoryReceiptLine]:
    return list(
        db.scalars(
            select(InventoryReceiptLine)
            .where(InventoryReceiptLine.receipt_id == receipt_id)
            .order_by(InventoryReceiptLine.catalog_part_id, InventoryReceiptLine.id)
        )
    )


def _normalized_lines(db: Session, values) -> list[dict]:
    combined: dict[int, dict] = {}
    notes_by_part: dict[int, list[str]] = {}
    for value in values:
        data = value.model_dump() if hasattr(value, "model_dump") else dict(value)
        try:
            part = resolve_catalog_part(db, int(data["catalog_part_id"]))
        except LookupError as exc:
            raise ValueError("inventory_receipt_part_invalid") from exc
        quantity = int(data["quantity"])
        if quantity <= 0:
            raise ValueError("inventory_receipt_quantity_invalid")
        existing = combined.get(part.id)
        if existing is None:
            notes_by_part[part.id] = []
            _append_note(notes_by_part[part.id], data.get("note"))
            combined[part.id] = {
                "catalog_part_id": part.id,
                "quantity": quantity,
            }
        else:
            existing["quantity"] += quantity
            if existing["quantity"] > 1_000_000:
                raise ValueError("inventory_receipt_quantity_invalid")
            _append_note(notes_by_part[part.id], data.get("note"))
    if not combined:
        raise ValueError("inventory_receipt_lines_required")
    for part_id, line in combined.items():
        line["note"] = "\n".join(notes_by_part[part_id]) or None
    return [combined[part_id] for part_id in sorted(combined)]


def _receipt_dict(receipt: InventoryReceipt, lines: list[InventoryReceiptLine]) -> dict:
    return {
        "id": receipt.id,
        "park_id": receipt.park_id,
        "supplier": receipt.supplier,
        "document_number": receipt.document_number,
        "received_on": receipt.receipt_date,
        "comment": receipt.comment,
        "status": receipt.status,
        "created_by": receipt.created_by,
        "posted_by": receipt.posted_by,
        "created_at": receipt.created_at,
        "posted_at": receipt.posted_at,
        "lines": [
            {
                "id": line.id,
                "catalog_part_id": line.catalog_part_id,
                "quantity": line.quantity,
                "note": line.note,
            }
            for line in lines
        ],
    }


def receipt_out(db: Session, receipt: InventoryReceipt) -> dict:
    return _receipt_dict(receipt, _lines(db, receipt.id))


def receipts_out(db: Session, receipts: list[InventoryReceipt]) -> list[dict]:
    receipt_ids = [receipt.id for receipt in receipts]
    lines_by_receipt: dict[int, list[InventoryReceiptLine]] = {
        receipt_id: [] for receipt_id in receipt_ids
    }
    if receipt_ids:
        rows = db.scalars(
            select(InventoryReceiptLine)
            .where(InventoryReceiptLine.receipt_id.in_(receipt_ids))
            .order_by(
                InventoryReceiptLine.receipt_id,
                InventoryReceiptLine.catalog_part_id,
                InventoryReceiptLine.id,
            )
        )
        for line in rows:
            lines_by_receipt[line.receipt_id].append(line)
    return [_receipt_dict(receipt, lines_by_receipt[receipt.id]) for receipt in receipts]


def create_receipt(db: Session, user: User, *, park_id: int, payload) -> InventoryReceipt:
    inventory_access.require_park(db, user, park_id, manage=True)
    lines = _normalized_lines(db, payload.lines)
    row = InventoryReceipt(
        park_id=park_id,
        supplier=_clean_text(payload.supplier),
        document_number=_clean_text(payload.document_number),
        receipt_date=payload.received_on,
        comment=_clean_text(payload.comment),
        created_by=user.id,
    )
    try:
        db.add(row)
        db.flush()
        db.add_all(InventoryReceiptLine(receipt_id=row.id, **line) for line in lines)
        _audit(
            db,
            user,
            row,
            "inventory.receipt.created",
            ["supplier", "document_number", "received_on", "comment", "lines"],
        )
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


def update_receipt(
    db: Session, user: User, *, park_id: int, receipt_id: int, changes: dict
) -> InventoryReceipt:
    inventory_access.require_park(db, user, park_id, manage=True)
    try:
        row = _receipt(db, park_id, receipt_id, lock=True)
        if row.status != "draft":
            raise inventory_stock.InventoryConflict("inventory_receipt_not_draft")
        if "lines" in changes:
            normalized = _normalized_lines(db, changes["lines"])
            for line in _lines(db, row.id):
                db.delete(line)
            db.flush()
            db.add_all(InventoryReceiptLine(receipt_id=row.id, **line) for line in normalized)
        if "supplier" in changes:
            row.supplier = _clean_text(changes["supplier"])
        if "document_number" in changes:
            row.document_number = _clean_text(changes["document_number"])
        if "received_on" in changes:
            row.receipt_date = changes["received_on"]
        if "comment" in changes:
            row.comment = _clean_text(changes["comment"])
        _audit(db, user, row, "inventory.receipt.updated", changes.keys())
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


def list_receipts(
    db: Session,
    user: User,
    *,
    park_id: int,
    query: str | None,
    limit: int,
    offset: int,
) -> tuple[list[InventoryReceipt], int]:
    inventory_access.require_park(db, user, park_id)
    filters = [InventoryReceipt.park_id == park_id]
    clean_query = _clean_text(query)
    if clean_query:
        if db.get_bind().dialect.name == "sqlite":
            driver_connection = db.connection().connection.driver_connection
            driver_connection.create_function(
                "inventory_casefold",
                1,
                lambda value: str(value or "").casefold(),
                deterministic=True,
            )
            folded_query = clean_query.casefold()
            filters.append(
                or_(
                    func.instr(
                        func.inventory_casefold(func.coalesce(InventoryReceipt.supplier, "")),
                        folded_query,
                    )
                    > 0,
                    func.instr(
                        func.inventory_casefold(
                            func.coalesce(InventoryReceipt.document_number, "")
                        ),
                        folded_query,
                    )
                    > 0,
                )
            )
        else:
            escaped = clean_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped.casefold()}%"
            russian_upper = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
            russian_lower = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"

            def normalized_text(column):
                return func.lower(
                    func.translate(func.coalesce(column, ""), russian_upper, russian_lower)
                )

            filters.append(
                or_(
                    normalized_text(InventoryReceipt.supplier).like(pattern, escape="\\"),
                    normalized_text(InventoryReceipt.document_number).like(pattern, escape="\\"),
                )
            )
    total = db.scalar(select(func.count(InventoryReceipt.id)).where(*filters))
    rows = list(
        db.scalars(
            select(InventoryReceipt)
            .where(*filters)
            .order_by(InventoryReceipt.created_at.desc(), InventoryReceipt.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, int(total or 0)


def _line_source_id(receipt: InventoryReceipt, line: InventoryReceiptLine) -> str:
    return f"{receipt.id}:{line.id}"


def _stabilize_catalog_aliases(db: Session) -> None:
    inventory_catalog.acquire_alias_graph_read_lock(db)


def _canonical_line_groups(
    db: Session,
    *,
    park_id: int,
    lines: list[InventoryReceiptLine],
    allow_archived: bool = False,
) -> list[tuple[int, list[InventoryReceiptLine]]]:
    groups: dict[int, list[InventoryReceiptLine]] = {}
    for line in lines:
        canonical_id = resolve_catalog_part(
            db,
            line.catalog_part_id,
            allow_archived=allow_archived,
        ).id
        groups.setdefault(canonical_id, []).append(line)
    result = []
    for canonical_id in sorted(groups):
        inventory_stock.ensure_stock(
            db,
            park_id=park_id,
            catalog_part_id=canonical_id,
            allow_archived=allow_archived,
        )
        result.append((canonical_id, sorted(groups[canonical_id], key=lambda line: line.id)))
    return result


def post_receipt(db: Session, user: User, *, park_id: int, receipt_id: int) -> InventoryReceipt:
    _require_document_post(db, user, park_id)
    try:
        _stabilize_catalog_aliases(db)
        row = _receipt(db, park_id, receipt_id, lock=True)
        if row.status == "posted":
            db.commit()
            db.refresh(row)
            return row
        if row.status != "draft":
            raise inventory_stock.InventoryConflict("inventory_receipt_not_draft")
        groups = _canonical_line_groups(db, park_id=park_id, lines=_lines(db, row.id))
        for canonical_id, lines in groups:
            for line in lines:
                inventory_stock.apply_stock_delta(
                    db,
                    user=user,
                    park_id=park_id,
                    catalog_part_id=canonical_id,
                    delta=line.quantity,
                    kind="receipt",
                    source_kind="receipt",
                    source_id=_line_source_id(row, line),
                    note=line.note,
                )
        row.status = "posted"
        row.posted_by = user.id
        row.posted_at = datetime.now(UTC)
        _audit(db, user, row, "inventory.receipt.posted", ["status", "posted_by", "posted_at"])
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


def cancel_receipt(db: Session, user: User, *, park_id: int, receipt_id: int) -> InventoryReceipt:
    inventory_access.require_park(db, user, park_id, manage=True)
    try:
        row = _receipt(db, park_id, receipt_id, lock=True)
        if row.status == "cancelled":
            db.commit()
            db.refresh(row)
            return row
        if row.status != "draft":
            raise inventory_stock.InventoryConflict("inventory_receipt_not_draft")
        row.status = "cancelled"
        _audit(db, user, row, "inventory.receipt.cancelled", ["status"])
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


def reverse_receipt(
    db: Session, user: User, *, park_id: int, receipt_id: int, reason: str
) -> InventoryReceipt:
    clean_reason = _clean_text(reason)
    if clean_reason is None:
        raise ValueError("inventory_receipt_reversal_reason_required")
    _require_document_post(db, user, park_id)
    try:
        _stabilize_catalog_aliases(db)
        row = _receipt(db, park_id, receipt_id, lock=True)
        if row.status != "posted":
            raise inventory_stock.InventoryConflict("inventory_receipt_not_posted")
        lines = _lines(db, row.id)
        source_ids = [_line_source_id(row, line) for line in lines]
        reversed_lines = db.scalar(
            select(func.count(InventoryMovement.id)).where(
                InventoryMovement.park_id == park_id,
                InventoryMovement.source_kind == "receipt_reversal",
                InventoryMovement.source_id.in_(source_ids),
            )
        )
        if reversed_lines == len(lines):
            db.commit()
            db.refresh(row)
            return row
        groups = _canonical_line_groups(
            db,
            park_id=park_id,
            lines=lines,
            allow_archived=True,
        )
        for canonical_id, grouped_lines in groups:
            for line in grouped_lines:
                inventory_stock.apply_stock_delta(
                    db,
                    user=user,
                    park_id=park_id,
                    catalog_part_id=canonical_id,
                    delta=-line.quantity,
                    kind="receipt_reversal",
                    source_kind="receipt_reversal",
                    source_id=_line_source_id(row, line),
                    note=clean_reason,
                    allow_archived=True,
                )
        _audit(db, user, row, "inventory.receipt.reversed", ["reason"])
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise
