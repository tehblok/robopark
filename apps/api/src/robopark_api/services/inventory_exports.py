from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime

from openpyxl import Workbook
from sqlalchemy import func, select, true
from sqlalchemy.orm import Session, aliased

from robopark_api.models import (
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryCount,
    InventoryCountLine,
    InventoryMovement,
    InventoryParkStock,
    InventoryReceipt,
    InventoryReceiptLine,
    Park,
    User,
)
from robopark_api.services import inventory_access
from robopark_api.services.rbac import PERMISSION_INVENTORY_EXPORT, has_permission

MAX_EXPORT_ROWS = 100_000
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

STOCK_HEADERS = (
    "ID парка",
    "Парк",
    "ID запчасти",
    "Компонента",
    "Запчасть",
    "Артикул",
    "Остаток",
    "Минимум",
    "Место",
    "Активна в парке",
)
MOVEMENT_HEADERS = (
    "ID движения",
    "ID парка",
    "Парк",
    "ID записи каталога",
    "Канонический ID запчасти",
    "Компонента",
    "Запчасть",
    "Артикул",
    "Тип движения",
    "Изменение",
    "Остаток до",
    "Остаток после",
    "Тип источника",
    "ID источника",
    "Задача",
    "Комментарий",
    "Исполнитель",
    "Дата",
)
RECEIPT_HEADERS = (
    "ID поставки",
    "ID парка",
    "Парк",
    "Статус",
    "Поставщик",
    "Номер документа",
    "Дата поставки",
    "Комментарий",
    "Создал",
    "Провёл",
    "Создано",
    "Проведено",
    "ID строки",
    "ID записи каталога",
    "Канонический ID запчасти",
    "Компонента",
    "Запчасть",
    "Артикул",
    "Количество",
    "Примечание",
)
COUNT_HEADERS = (
    "ID инвентаризации",
    "ID парка",
    "Парк",
    "Название",
    "Статус",
    "Создал",
    "Провёл",
    "Создано",
    "Проведено",
    "ID строки",
    "ID записи каталога",
    "Канонический ID запчасти",
    "Компонента",
    "Запчасть",
    "Артикул",
    "Учетное количество",
    "Фактическое количество",
    "Расхождение",
    "Комментарий",
)


def _park_ids(db: Session, user: User, *, park_id: int | None, all_parks: bool) -> list[int]:
    if not inventory_access.can_view_inventory(db, user) or not has_permission(
        db, user, PERMISSION_INVENTORY_EXPORT
    ):
        raise PermissionError("forbidden")
    if all_parks:
        if park_id is not None:
            raise ValueError("inventory_export_scope_ambiguous")
        if user.role not in {"admin", "royal"}:
            raise PermissionError("forbidden")
        return list(db.scalars(select(Park.id).where(Park.is_active.is_(True)).order_by(Park.id)))
    if park_id is None:
        raise ValueError("inventory_export_scope_required")
    inventory_access.require_park(db, user, park_id)
    return [park_id]


def _stock_statement(park_ids: list[int]):
    return (
        select(
            Park.id,
            Park.name,
            InventoryCatalogPart.id,
            InventoryCatalogComponent.name,
            InventoryCatalogPart.name,
            InventoryCatalogPart.article,
            func.coalesce(InventoryParkStock.quantity, 0),
            func.coalesce(InventoryParkStock.minimum_quantity, 0),
            InventoryParkStock.location,
            func.coalesce(InventoryParkStock.is_active, False),
        )
        .select_from(Park)
        .join(InventoryCatalogPart, true())
        .join(
            InventoryCatalogComponent,
            InventoryCatalogComponent.id == InventoryCatalogPart.component_id,
        )
        .outerjoin(
            InventoryParkStock,
            (InventoryParkStock.park_id == Park.id)
            & (InventoryParkStock.catalog_part_id == InventoryCatalogPart.id),
        )
        .where(
            Park.id.in_(park_ids),
            InventoryCatalogPart.is_active.is_(True),
            InventoryCatalogComponent.is_active.is_(True),
        )
        .order_by(Park.id, InventoryCatalogPart.normalized_name, InventoryCatalogPart.id)
    )


def _movement_statement(park_ids: list[int]):
    return (
        select(
            InventoryMovement.id,
            Park.id,
            Park.name,
            InventoryMovement.catalog_part_id,
            InventoryCatalogPart.merged_into_part_id,
            InventoryCatalogComponent.name,
            InventoryCatalogPart.name,
            InventoryCatalogPart.article,
            InventoryMovement.kind,
            InventoryMovement.delta,
            InventoryMovement.balance_before,
            InventoryMovement.balance_after,
            InventoryMovement.source_kind,
            InventoryMovement.source_id,
            InventoryMovement.issue_key,
            InventoryMovement.note,
            User.username,
            InventoryMovement.created_at,
        )
        .select_from(InventoryMovement)
        .join(Park, Park.id == InventoryMovement.park_id)
        .outerjoin(
            InventoryCatalogPart,
            InventoryCatalogPart.id == InventoryMovement.catalog_part_id,
        )
        .outerjoin(
            InventoryCatalogComponent,
            InventoryCatalogComponent.id == InventoryCatalogPart.component_id,
        )
        .join(User, User.id == InventoryMovement.actor_user_id)
        .where(InventoryMovement.park_id.in_(park_ids))
        .order_by(InventoryMovement.created_at, InventoryMovement.id)
    )


def _receipt_statement(park_ids: list[int]):
    creator = aliased(User)
    poster = aliased(User)
    return (
        select(
            InventoryReceipt.id,
            Park.id,
            Park.name,
            InventoryReceipt.status,
            InventoryReceipt.supplier,
            InventoryReceipt.document_number,
            InventoryReceipt.receipt_date,
            InventoryReceipt.comment,
            creator.username,
            poster.username,
            InventoryReceipt.created_at,
            InventoryReceipt.posted_at,
            InventoryReceiptLine.id,
            InventoryReceiptLine.catalog_part_id,
            InventoryCatalogPart.merged_into_part_id,
            InventoryCatalogComponent.name,
            InventoryCatalogPart.name,
            InventoryCatalogPart.article,
            InventoryReceiptLine.quantity,
            InventoryReceiptLine.note,
        )
        .select_from(InventoryReceipt)
        .join(Park, Park.id == InventoryReceipt.park_id)
        .join(creator, creator.id == InventoryReceipt.created_by)
        .outerjoin(poster, poster.id == InventoryReceipt.posted_by)
        .outerjoin(InventoryReceiptLine, InventoryReceiptLine.receipt_id == InventoryReceipt.id)
        .outerjoin(
            InventoryCatalogPart,
            InventoryCatalogPart.id == InventoryReceiptLine.catalog_part_id,
        )
        .outerjoin(
            InventoryCatalogComponent,
            InventoryCatalogComponent.id == InventoryCatalogPart.component_id,
        )
        .where(InventoryReceipt.park_id.in_(park_ids))
        .order_by(InventoryReceipt.created_at, InventoryReceipt.id, InventoryReceiptLine.id)
    )


def _count_statement(park_ids: list[int]):
    creator = aliased(User)
    poster = aliased(User)
    return (
        select(
            InventoryCount.id,
            Park.id,
            Park.name,
            InventoryCount.name,
            InventoryCount.status,
            creator.username,
            poster.username,
            InventoryCount.created_at,
            InventoryCount.posted_at,
            InventoryCountLine.id,
            InventoryCountLine.catalog_part_id,
            InventoryCatalogPart.merged_into_part_id,
            InventoryCatalogComponent.name,
            InventoryCatalogPart.name,
            InventoryCatalogPart.article,
            InventoryCountLine.expected_quantity,
            InventoryCountLine.actual_quantity,
            InventoryCountLine.difference,
            InventoryCountLine.comment,
        )
        .select_from(InventoryCount)
        .join(Park, Park.id == InventoryCount.park_id)
        .join(creator, creator.id == InventoryCount.created_by)
        .outerjoin(poster, poster.id == InventoryCount.posted_by)
        .outerjoin(InventoryCountLine, InventoryCountLine.count_id == InventoryCount.id)
        .outerjoin(
            InventoryCatalogPart,
            InventoryCatalogPart.id == InventoryCountLine.catalog_part_id,
        )
        .outerjoin(
            InventoryCatalogComponent,
            InventoryCatalogComponent.id == InventoryCatalogPart.component_id,
        )
        .where(InventoryCount.park_id.in_(park_ids))
        .order_by(InventoryCount.created_at, InventoryCount.id, InventoryCountLine.id)
    )


def _safe_cell(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _csv_chunks(rows) -> Iterator[bytes]:
    yield b"\xef\xbb\xbf"
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(STOCK_HEADERS)
    yield buffer.getvalue().encode("utf-8")
    for row in rows:
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow(_safe_cell(value) for value in row)
        yield buffer.getvalue().encode("utf-8")


def _xlsx_cell(value):
    value = _safe_cell(value)
    if isinstance(value, int) and not -(2**53 - 1) <= value <= 2**53 - 1:
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _canonical_ids(db: Session) -> dict[int, int]:
    links = dict(
        db.execute(select(InventoryCatalogPart.id, InventoryCatalogPart.merged_into_part_id))
        .tuples()
        .all()
    )
    resolved: dict[int, int] = {}
    for source_id in links:
        current = source_id
        seen: set[int] = set()
        while links.get(current) is not None and current not in seen:
            seen.add(current)
            current = links[current]
        resolved[source_id] = current
    return resolved


def _build_xlsx(db: Session, statements) -> io.BytesIO:
    canonical_ids = _canonical_ids(db)
    row_count = 0
    collected = []
    for title, headers, statement in statements:
        remaining = MAX_EXPORT_ROWS - row_count
        rows = list(db.execute(statement.limit(remaining + 1)))
        row_count += len(rows)
        if row_count > MAX_EXPORT_ROWS:
            raise OverflowError("inventory_export_too_large")
        collected.append((title, headers, rows))

    book = Workbook(write_only=True)
    for title, headers, rows in collected:
        sheet = book.create_sheet(title)
        sheet.append(headers)
        for raw_row in rows:
            row = list(raw_row)
            if title == "Движения" and row[3] is not None:
                row[4] = canonical_ids.get(row[3], row[3])
            elif title == "Поставки" and row[13] is not None:
                row[14] = canonical_ids.get(row[13], row[13])
            elif title == "Инвентаризации" and row[10] is not None:
                row[11] = canonical_ids.get(row[10], row[10])
            sheet.append([_xlsx_cell(value) for value in row])
    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    return buffer


def _buffer_chunks(buffer: io.BytesIO) -> Iterator[bytes]:
    while chunk := buffer.read(64 * 1024):
        yield chunk


def build_inventory_export(
    db: Session,
    user: User,
    *,
    park_id: int | None,
    all_parks: bool,
    format: str,
) -> tuple[str, str, Iterator[bytes]]:
    park_ids = _park_ids(db, user, park_id=park_id, all_parks=all_parks)
    if format not in {"csv", "xlsx"}:
        raise ValueError("inventory_export_format_invalid")
    stock_statement = _stock_statement(park_ids)
    if format == "csv":
        rows = list(db.execute(stock_statement.limit(MAX_EXPORT_ROWS + 1)))
        if len(rows) > MAX_EXPORT_ROWS:
            raise OverflowError("inventory_export_too_large")
    else:
        statements = [
            ("Остатки", STOCK_HEADERS, stock_statement),
            ("Движения", MOVEMENT_HEADERS, _movement_statement(park_ids)),
            ("Поставки", RECEIPT_HEADERS, _receipt_statement(park_ids)),
            ("Инвентаризации", COUNT_HEADERS, _count_statement(park_ids)),
        ]
        buffer = _build_xlsx(db, statements)
    suffix = "all" if all_parks else f"park-{park_ids[0]}"
    if format == "csv":
        return (
            "text/csv; charset=utf-8",
            f"Склад-{suffix}.csv",
            _csv_chunks(rows),
        )
    return XLSX_MEDIA_TYPE, f"Склад-{suffix}.xlsx", _buffer_chunks(buffer)
