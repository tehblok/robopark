from __future__ import annotations

import csv
import io
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from sqlalchemy import and_, func, or_, select, true, tuple_
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
from robopark_api.services import inventory_access, inventory_catalog
from robopark_api.services.rbac import PERMISSION_INVENTORY_EXPORT, has_permission

MAX_EXPORT_ROWS = 100_000
MAX_ALIAS_ROWS = 100_000
MAX_XLSX_SIGNIFICANT_DIGITS = 15
XLSX_TEXT_CHUNK_SIZE = 32_766
XLSX_SPOOL_MEMORY_BYTES = 8 * 1024 * 1024
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
CONTINUATION_HEADERS = ("Часть текста", "Всего частей")
SNAPSHOT_BATCH_SIZE = 400


@dataclass(frozen=True)
class ExportQuery:
    title: str
    headers: tuple[str, ...]
    statement: object
    identity_statement: object


@dataclass(frozen=True)
class ExportSnapshot:
    identities: dict[str, list[tuple[int, ...]]]
    canonical_ids: dict[int, int]
    exported_rows: int


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
        return list(db.scalars(select(Park.id).order_by(Park.id)))
    if park_id is None:
        raise ValueError("inventory_export_scope_required")
    if user.role in {"admin", "royal"}:
        park = db.get(Park, park_id)
        if park is None:
            raise LookupError("park_not_found")
        return [park.id]
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


def _stock_identity_statement(park_ids: list[int]):
    return (
        select(Park.id, InventoryCatalogPart.id)
        .select_from(Park)
        .join(InventoryCatalogPart, true())
        .join(
            InventoryCatalogComponent,
            InventoryCatalogComponent.id == InventoryCatalogPart.component_id,
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


def _movement_identity_statement(park_ids: list[int]):
    return (
        select(InventoryMovement.id)
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


def _receipt_identity_statement(park_ids: list[int]):
    return (
        select(InventoryReceipt.id, InventoryReceiptLine.id)
        .select_from(InventoryReceipt)
        .outerjoin(InventoryReceiptLine, InventoryReceiptLine.receipt_id == InventoryReceipt.id)
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


def _count_identity_statement(park_ids: list[int]):
    return (
        select(InventoryCount.id, InventoryCountLine.id)
        .select_from(InventoryCount)
        .outerjoin(InventoryCountLine, InventoryCountLine.count_id == InventoryCount.id)
        .where(InventoryCount.park_id.in_(park_ids))
        .order_by(InventoryCount.created_at, InventoryCount.id, InventoryCountLine.id)
    )


def _stock_query(park_ids: list[int]) -> ExportQuery:
    return ExportQuery(
        "Остатки", STOCK_HEADERS, _stock_statement(park_ids), _stock_identity_statement(park_ids)
    )


def _xlsx_queries(park_ids: list[int]) -> tuple[ExportQuery, ...]:
    return (
        _stock_query(park_ids),
        ExportQuery(
            "Движения",
            MOVEMENT_HEADERS,
            _movement_statement(park_ids),
            _movement_identity_statement(park_ids),
        ),
        ExportQuery(
            "Поставки",
            RECEIPT_HEADERS,
            _receipt_statement(park_ids),
            _receipt_identity_statement(park_ids),
        ),
        ExportQuery(
            "Инвентаризации",
            COUNT_HEADERS,
            _count_statement(park_ids),
            _count_identity_statement(park_ids),
        ),
    )


def _safe_cell(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _csv_chunks(rows) -> Iterator[bytes]:
    try:
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
    finally:
        rows.close()


def _xlsx_cell(value):
    if isinstance(value, str):
        value = _safe_cell(_sanitize_xlsx_text(value))
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and len(str(abs(value))) > MAX_XLSX_SIGNIFICANT_DIGITS
    ):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _resolve_aliases(links: Mapping[int, int | None]) -> dict[int, int]:
    resolved: dict[int, int] = {}
    for start in links:
        if start in resolved:
            continue
        path: list[int] = []
        positions: set[int] = set()
        current = start
        while current not in resolved:
            if current in positions:
                raise ValueError("inventory_alias_cycle")
            positions.add(current)
            path.append(current)
            target = links.get(current)
            if target is None:
                resolved[current] = current
                break
            current = target
        canonical_id = resolved[current]
        for alias_id in reversed(path):
            resolved[alias_id] = canonical_id
    return resolved


def _load_aliases(db: Session) -> dict[int, int]:
    links: dict[int, int | None] = {}
    statement = (
        select(InventoryCatalogPart.id, InventoryCatalogPart.merged_into_part_id)
        .order_by(InventoryCatalogPart.id)
        .limit(MAX_ALIAS_ROWS + 1)
    )
    for part_id, target_id in db.execute(statement):
        links[part_id] = target_id
        if len(links) > MAX_ALIAS_ROWS:
            raise OverflowError("inventory_export_too_large")
    return _resolve_aliases(links)


def _begin_sqlite_snapshot(db: Session) -> None:
    if db.get_bind().dialect.name != "sqlite":
        return
    connection = db.connection()
    driver_connection = getattr(connection.connection, "driver_connection", connection.connection)
    if not driver_connection.in_transaction:
        cursor = driver_connection.cursor()
        try:
            cursor.execute("BEGIN")
        finally:
            cursor.close()


def _capture_export_snapshot(
    db: Session, queries: tuple[ExportQuery, ...], *, include_aliases: bool
) -> ExportSnapshot:
    inventory_catalog.acquire_alias_graph_read_lock(db)
    try:
        _begin_sqlite_snapshot(db)
        canonical_ids = _load_aliases(db) if include_aliases else {}
        identities: dict[str, list[tuple[int, ...]]] = {}
        exported_rows = 0
        for query in queries:
            remaining = MAX_EXPORT_ROWS - exported_rows
            rows = list(db.execute(query.identity_statement.limit(remaining + 1)).tuples())
            exported_rows += len(rows)
            if exported_rows > MAX_EXPORT_ROWS:
                raise OverflowError("inventory_export_too_large")
            identities[query.title] = [tuple(row) for row in rows]
        return ExportSnapshot(identities, canonical_ids, exported_rows)
    finally:
        # Releases the transaction-scoped advisory lock (or SQLite read snapshot)
        # after every exported identity and alias edge has been fixed in memory.
        db.rollback()


def _snapshot_filter(query: ExportQuery, identities: list[tuple[int, ...]]):
    if query.title == "Остатки":
        return tuple_(Park.id, InventoryCatalogPart.id).in_(identities)
    if query.title == "Движения":
        return InventoryMovement.id.in_(identity[0] for identity in identities)
    if query.title == "Поставки":
        line_ids = [line_id for _receipt_id, line_id in identities if line_id is not None]
        empty_receipt_ids = [receipt_id for receipt_id, line_id in identities if line_id is None]
        return or_(
            InventoryReceiptLine.id.in_(line_ids),
            and_(
                InventoryReceiptLine.id.is_(None),
                InventoryReceipt.id.in_(empty_receipt_ids),
            ),
        )
    line_ids = [line_id for _count_id, line_id in identities if line_id is not None]
    empty_count_ids = [count_id for count_id, line_id in identities if line_id is None]
    return or_(
        InventoryCountLine.id.in_(line_ids),
        and_(InventoryCountLine.id.is_(None), InventoryCount.id.in_(empty_count_ids)),
    )


def _snapshot_payload_rows(
    db: Session, query: ExportQuery, identities: list[tuple[int, ...]]
) -> Iterator:
    for offset in range(0, len(identities), SNAPSHOT_BATCH_SIZE):
        batch = identities[offset : offset + SNAPSHOT_BATCH_SIZE]
        result = db.execute(
            query.statement.where(_snapshot_filter(query, batch)).execution_options(yield_per=1_000)
        )
        try:
            yield from result
        finally:
            result.close()


def _sanitize_xlsx_text(value: str) -> str:
    escaped = []
    for char in value:
        codepoint = ord(char)
        if (
            char in "\t\n\r"
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        ):
            escaped.append(char)
        elif codepoint <= 0xFFFF:
            escaped.append(f"\\u{codepoint:04X}")
        else:
            escaped.append(f"\\U{codepoint:08X}")
    return "".join(escaped)


def _expanded_xlsx_rows(raw_row) -> Iterator[list]:
    values = [_sanitize_xlsx_text(value) if isinstance(value, str) else value for value in raw_row]
    part_counts = [
        max(1, (len(value) + XLSX_TEXT_CHUNK_SIZE - 1) // XLSX_TEXT_CHUNK_SIZE)
        if isinstance(value, str)
        else 1
        for value in values
    ]
    part_count = max(part_counts, default=1)
    for part_index in range(part_count):
        row = []
        for value, value_part_count in zip(values, part_counts, strict=True):
            if isinstance(value, str):
                offset = part_index * XLSX_TEXT_CHUNK_SIZE
                chunk = value[offset : offset + XLSX_TEXT_CHUNK_SIZE]
                row.append(_safe_cell(chunk) if part_index < value_part_count else None)
            else:
                row.append(_xlsx_cell(value) if part_index == 0 else None)
        row.extend((part_index + 1, part_count))
        yield row


def _build_xlsx(db: Session, queries: tuple[ExportQuery, ...], snapshot: ExportSnapshot):
    # Ownership is transferred to _file_chunks, which closes after streaming.
    spool = tempfile.SpooledTemporaryFile(  # noqa: SIM115
        max_size=XLSX_SPOOL_MEMORY_BYTES, mode="w+b"
    )
    book = Workbook(write_only=True)
    sheets = []
    for query in queries:
        sheet = book.create_sheet(query.title)
        sheet.append((*query.headers, *CONTINUATION_HEADERS))
        sheets.append(sheet)

    overflow = False
    actual_rows = 0
    for query, sheet in zip(queries, sheets, strict=True):
        sheet_rows = 0
        for raw_row in _snapshot_payload_rows(db, query, snapshot.identities[query.title]):
            row = list(raw_row)
            if query.title == "Движения" and row[3] is not None:
                row[4] = snapshot.canonical_ids.get(row[3], row[3])
            elif query.title == "Поставки" and row[13] is not None:
                row[14] = snapshot.canonical_ids.get(row[13], row[13])
            elif query.title == "Инвентаризации" and row[10] is not None:
                row[11] = snapshot.canonical_ids.get(row[10], row[10])
            for expanded in _expanded_xlsx_rows(row):
                actual_rows += 1
                if actual_rows > MAX_EXPORT_ROWS:
                    overflow = True
                    break
                sheet.append(expanded)
                sheet_rows += 1
            if overflow:
                break
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(query.headers) + len(CONTINUATION_HEADERS))}{sheet_rows + 1}"
        if overflow:
            break

    if overflow:
        # Saving finalizes write-only worksheet XML and prevents leaked generators.
        book.save(spool)
        spool.close()
        raise OverflowError("inventory_export_too_large")
    book.save(spool)
    spool.seek(0)
    return spool


def _file_chunks(file_obj) -> Iterator[bytes]:
    try:
        while chunk := file_obj.read(64 * 1024):
            yield chunk
    finally:
        file_obj.close()


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
    stock_query = _stock_query(park_ids)
    if format == "csv":
        queries = (stock_query,)
        snapshot = _capture_export_snapshot(db, queries, include_aliases=False)
    else:
        queries = _xlsx_queries(park_ids)
        snapshot = _capture_export_snapshot(db, queries, include_aliases=True)
        spool = _build_xlsx(db, queries, snapshot)
    suffix = "all" if all_parks else f"park-{park_ids[0]}"
    if format == "csv":
        return (
            "text/csv; charset=utf-8",
            f"Склад-{suffix}.csv",
            _csv_chunks(_snapshot_payload_rows(db, stock_query, snapshot.identities["Остатки"])),
        )
    return XLSX_MEDIA_TYPE, f"Склад-{suffix}.xlsx", _file_chunks(spool)
