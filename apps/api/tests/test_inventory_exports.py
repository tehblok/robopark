import csv
import io
from datetime import date, datetime

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from sqlalchemy import event
from sqlalchemy.orm import Session as OrmSession

from conftest import login_as, role_id_for
from robopark_api.models import (
    INVENTORY_INT64_MAX,
    INVENTORY_INT64_MIN,
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
    UserPark,
)
from robopark_api.security import hash_password
from robopark_api.services import inventory_exports


def _user(db, slug, username, parks=()):
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, slug),
        access_status="approved",
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add_all(UserPark(user_id=user.id, park_id=park.id) for park in parks)
    db.commit()
    return user


def test_mechanic_can_export_assigned_park_as_utf8_bom_csv(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "export-mechanic", [seed_park_with_tracker])
    component = InventoryCatalogComponent(
        name="=component",
        normalized_name="=component",
        created_by=mechanic.id,
        updated_by=mechanic.id,
    )
    db_session.add(component)
    db_session.flush()
    part = InventoryCatalogPart(
        component_id=component.id,
        name="+part",
        normalized_name="+part",
        article="=1+1",
        normalized_article="=1+1",
        created_by=mechanic.id,
        updated_by=mechanic.id,
    )
    db_session.add(part)
    db_session.flush()
    db_session.add(
        InventoryParkStock(
            park_id=seed_park_with_tracker.id,
            catalog_part_id=part.id,
            quantity=INVENTORY_INT64_MAX,
            minimum_quantity=2**40,
            location="@A-1",
            updated_by=mechanic.id,
        )
    )
    db_session.commit()
    login_as(client, mechanic.username, "secret")

    response = client.get(
        "/inventory/export",
        params={"park_id": seed_park_with_tracker.id, "format": "csv"},
    )

    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0] == [
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
    ]
    assert rows[1] == [
        str(seed_park_with_tracker.id),
        "Alpha",
        str(part.id),
        "'=component",
        "'+part",
        "'=1+1",
        str(INVENTORY_INT64_MAX),
        str(2**40),
        "'@A-1",
        "True",
    ]
    disposition = response.headers["content-disposition"]
    assert 'filename="inventory-park-' in disposition
    assert "filename*=UTF-8''%D0%A1%D0%BA%D0%BB%D0%B0%D0%B4-" in disposition


def test_export_role_scope_is_checked_before_inventory_queries(
    client, db_session, db_engine, seed_park_with_tracker
):
    foreign = Park(name="Foreign", tag="Foreign", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    mechanic = _user(db_session, "mechanic", "export-scope-mechanic", [seed_park_with_tracker])
    operator = _user(db_session, "operator", "export-operator")
    admin = _user(db_session, "admin", "export-admin")

    statements = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.casefold())

    event.listen(db_engine, "before_cursor_execute", record_statement)
    try:
        login_as(client, mechanic.username, "secret")
        statements.clear()
        all_response = client.get("/inventory/export", params={"scope": "all", "format": "csv"})
        assert all_response.status_code == 403
        assert not any(
            table in statement
            for statement in statements
            for table in (
                "inventory_catalog_parts",
                "inventory_park_stocks",
                "inventory_movements",
                "inventory_receipts",
                "inventory_counts",
            )
        )

        statements.clear()
        foreign_response = client.get(
            "/inventory/export", params={"park_id": foreign.id, "format": "csv"}
        )
        assert foreign_response.status_code == 403
        assert not any("inventory_catalog_parts" in statement for statement in statements)

        login_as(client, operator.username, "secret")
        assert (
            client.get(
                "/inventory/export", params={"park_id": foreign.id, "format": "csv"}
            ).status_code
            == 200
        )
        assert (
            client.get("/inventory/export", params={"scope": "all", "format": "csv"}).status_code
            == 403
        )

        login_as(client, admin.username, "secret")
        assert (
            client.get("/inventory/export", params={"scope": "all", "format": "csv"}).status_code
            == 200
        )
    finally:
        event.remove(db_engine, "before_cursor_execute", record_statement)


def test_royal_xlsx_exports_four_operational_sheets_and_preserves_alias_history(
    client, db_session, seed_park_with_tracker
):
    foreign = Park(name="Beta", tag="Beta", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    royal = _user(db_session, "royal", "export-royal")
    component = InventoryCatalogComponent(
        name="@Component",
        normalized_name="@component",
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add(component)
    db_session.flush()
    target = InventoryCatalogPart(
        component_id=component.id,
        name="-Target",
        normalized_name="-target",
        article="+TARGET",
        normalized_article="+target",
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add(target)
    db_session.flush()
    source = InventoryCatalogPart(
        component_id=component.id,
        name="Old source",
        normalized_name="old source",
        article="OLD",
        normalized_article="old",
        is_active=False,
        merged_into_part_id=target.id,
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add_all(
        [
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=target.id,
                quantity=INVENTORY_INT64_MAX,
                minimum_quantity=2**40,
                location="=A1",
                updated_by=royal.id,
            ),
            InventoryParkStock(
                park_id=foreign.id,
                catalog_part_id=target.id,
                quantity=7,
                minimum_quantity=3,
                location="B-4",
                updated_by=royal.id,
            ),
            InventoryMovement(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=source.id,
                actor_user_id=royal.id,
                kind="receipt",
                delta=INVENTORY_INT64_MAX,
                balance_before=0,
                balance_after=INVENTORY_INT64_MAX,
                source_kind="receipt",
                source_id="receipt-line-1",
                note="@historic",
            ),
        ]
    )
    receipt = InventoryReceipt(
        park_id=seed_park_with_tracker.id,
        supplier="=Factory",
        document_number="+UPD-1",
        receipt_date=date(2026, 9, 11),
        comment="@accepted",
        status="posted",
        created_by=royal.id,
        posted_by=royal.id,
    )
    count = InventoryCount(
        park_id=foreign.id,
        name="-September",
        status="posted",
        created_by=royal.id,
        posted_by=royal.id,
    )
    db_session.add_all([receipt, count])
    db_session.flush()
    db_session.add_all(
        [
            InventoryReceiptLine(
                receipt_id=receipt.id,
                catalog_part_id=target.id,
                quantity=2**40,
                note="=box",
            ),
            InventoryCountLine(
                count_id=count.id,
                catalog_part_id=target.id,
                expected_quantity=4,
                actual_quantity=7,
                difference=3,
                comment="+checked",
            ),
        ]
    )
    db_session.commit()
    login_as(client, royal.username, "secret")

    response = client.get("/inventory/export", params={"scope": "all", "format": "xlsx"})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    book = load_workbook(io.BytesIO(response.content), read_only=True, data_only=False)
    assert book.sheetnames == ["Остатки", "Движения", "Поставки", "Инвентаризации"]

    stock_rows = list(book["Остатки"].iter_rows(values_only=True))
    assert [row[0] for row in stock_rows[1:]] == [seed_park_with_tracker.id, foreign.id]
    assert {row[2] for row in stock_rows[1:]} == {target.id}
    assert stock_rows[1][3:6] == ("'@Component", "'-Target", "'+TARGET")
    assert stock_rows[1][6:9] == (str(INVENTORY_INT64_MAX), 2**40, "'=A1")

    movement_rows = list(book["Движения"].iter_rows(values_only=True))
    assert len(movement_rows) == 2
    assert movement_rows[1][3:5] == (source.id, target.id)
    assert movement_rows[1][9] == str(INVENTORY_INT64_MAX)
    assert movement_rows[1][13:16] == ("receipt-line-1", None, "'@historic")
    assert movement_rows[1][16] == royal.username

    receipt_rows = list(book["Поставки"].iter_rows(values_only=True))
    assert len(receipt_rows) == 2
    assert receipt_rows[1][4:8] == (
        "'=Factory",
        "'+UPD-1",
        datetime(2026, 9, 11),
        "'@accepted",
    )
    assert receipt_rows[1][8:10] == (royal.username, royal.username)
    assert receipt_rows[1][16:20] == ("'-Target", "'+TARGET", 2**40, "'=box")

    count_rows = list(book["Инвентаризации"].iter_rows(values_only=True))
    assert len(count_rows) == 2
    assert count_rows[1][3:7] == ("'-September", "posted", royal.username, royal.username)
    assert count_rows[1][13:19] == ("'-Target", "'+TARGET", 4, 7, 3, "'+checked")


@pytest.mark.parametrize("export_format", ["csv", "xlsx"])
def test_export_row_cap_returns_controlled_413(
    client, db_session, seed_park_with_tracker, monkeypatch, export_format
):
    royal = _user(db_session, "royal", f"cap-royal-{export_format}")
    component = InventoryCatalogComponent(
        name="Cap component",
        normalized_name="cap component",
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add(component)
    db_session.flush()
    db_session.add(
        InventoryCatalogPart(
            component_id=component.id,
            name="Cap part",
            normalized_name="cap part",
            article="CAP-1",
            normalized_article="cap-1",
            created_by=royal.id,
            updated_by=royal.id,
        )
    )
    db_session.commit()
    monkeypatch.setattr(inventory_exports, "MAX_EXPORT_ROWS", 0)
    login_as(client, royal.username, "secret")

    response = client.get(
        "/inventory/export",
        params={"park_id": seed_park_with_tracker.id, "format": export_format},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "inventory_export_too_large"}


def test_export_rejects_missing_ambiguous_and_invalid_parameters(
    client, db_session, seed_park_with_tracker
):
    royal = _user(db_session, "royal", "validation-royal")
    login_as(client, royal.username, "secret")

    missing = client.get("/inventory/export", params={"format": "csv"})
    ambiguous = client.get(
        "/inventory/export",
        params={"park_id": seed_park_with_tracker.id, "scope": "all", "format": "csv"},
    )
    invalid = client.get(
        "/inventory/export", params={"park_id": seed_park_with_tracker.id, "format": "pdf"}
    )
    invalid_scope = client.get(
        "/inventory/export",
        params={"park_id": seed_park_with_tracker.id, "scope": "selected", "format": "csv"},
    )

    assert missing.status_code == 400
    assert missing.json() == {"detail": "inventory_export_scope_required"}
    assert ambiguous.status_code == 400
    assert ambiguous.json() == {"detail": "inventory_export_scope_ambiguous"}
    assert invalid.status_code == 422
    assert invalid_scope.status_code == 422


def test_admin_and_royal_export_inactive_parks_while_operator_and_mechanic_cannot(
    client, db_session, db_engine, seed_park_with_tracker
):
    inactive = Park(name="Archived", tag="Archived", is_active=False)
    db_session.add(inactive)
    db_session.commit()
    royal = _user(db_session, "royal", "inactive-royal")
    admin = _user(db_session, "admin", "inactive-admin")
    operator = _user(db_session, "operator", "inactive-operator")
    mechanic = _user(db_session, "mechanic", "inactive-mechanic", [inactive])
    component = InventoryCatalogComponent(
        name="Inactive park component",
        normalized_name="inactive park component",
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add(component)
    db_session.flush()
    part = InventoryCatalogPart(
        component_id=component.id,
        name="Inactive park part",
        normalized_name="inactive park part",
        article="INACTIVE-PARK",
        normalized_article="inactive-park",
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add(part)
    db_session.flush()
    db_session.add_all(
        [
            InventoryParkStock(
                park_id=inactive.id,
                catalog_part_id=part.id,
                quantity=9,
                minimum_quantity=2,
                location="ARCHIVE",
                updated_by=royal.id,
            ),
            InventoryMovement(
                park_id=inactive.id,
                catalog_part_id=part.id,
                actor_user_id=royal.id,
                kind="receipt",
                delta=9,
                balance_before=0,
                balance_after=9,
            ),
        ]
    )
    db_session.commit()

    for privileged in (admin, royal):
        login_as(client, privileged.username, "secret")
        selected = client.get("/inventory/export", params={"park_id": inactive.id, "format": "csv"})
        assert selected.status_code == 200, selected.text
        assert list(csv.reader(io.StringIO(selected.content.decode("utf-8-sig"))))[1][0] == str(
            inactive.id
        )

    login_as(client, royal.username, "secret")
    all_result = client.get("/inventory/export", params={"scope": "all", "format": "xlsx"})
    assert all_result.status_code == 200, all_result.text
    book = load_workbook(io.BytesIO(all_result.content), read_only=False)
    assert inactive.id in [row[0] for row in list(book["Остатки"].values)[1:]]
    assert inactive.id in [row[1] for row in list(book["Движения"].values)[1:]]
    for sheet in book.worksheets:
        assert sheet.auto_filter.ref == (
            f"A1:{get_column_letter(sheet.max_column)}{max(sheet.max_row, 1)}"
        )

    domain_queries = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "inventory_" in statement.casefold():
            domain_queries.append(statement)

    event.listen(db_engine, "before_cursor_execute", record_statement)
    try:
        for restricted in (operator, mechanic):
            login_as(client, restricted.username, "secret")
            domain_queries.clear()
            denied = client.get(
                "/inventory/export", params={"park_id": inactive.id, "format": "xlsx"}
            )
            assert denied.status_code == 403
            assert domain_queries == []
    finally:
        event.remove(db_engine, "before_cursor_execute", record_statement)


def test_xlsx_stringifies_integers_beyond_excel_fifteen_digits(
    client, db_session, seed_park_with_tracker
):
    royal = _user(db_session, "royal", "precision-royal")
    component = InventoryCatalogComponent(
        name="Precision component",
        normalized_name="precision component",
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add(component)
    db_session.flush()
    part = InventoryCatalogPart(
        component_id=component.id,
        name="Precision part",
        normalized_name="precision part",
        article="PRECISION",
        normalized_article="precision",
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add(part)
    db_session.flush()
    db_session.add(
        InventoryParkStock(
            park_id=seed_park_with_tracker.id,
            catalog_part_id=part.id,
            quantity=1_000_000_000_000_001,
            minimum_quantity=999_999_999_999_999,
            updated_by=royal.id,
        )
    )
    db_session.add_all(
        [
            InventoryMovement(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=part.id,
                actor_user_id=royal.id,
                kind="adjustment",
                delta=INVENTORY_INT64_MAX,
                balance_before=0,
                balance_after=INVENTORY_INT64_MAX,
            ),
            InventoryMovement(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=part.id,
                actor_user_id=royal.id,
                kind="adjustment",
                delta=INVENTORY_INT64_MIN,
                balance_before=INVENTORY_INT64_MAX,
                balance_after=0,
            ),
        ]
    )
    db_session.commit()
    login_as(client, royal.username, "secret")

    response = client.get(
        "/inventory/export",
        params={"park_id": seed_park_with_tracker.id, "format": "xlsx"},
    )

    assert response.status_code == 200, response.text
    book = load_workbook(io.BytesIO(response.content), read_only=True)
    stock = list(book["Остатки"].values)[1]
    assert stock[6] == "1000000000000001"
    assert stock[7] == 999_999_999_999_999
    movements = list(book["Движения"].values)[1:]
    assert [row[9] for row in movements] == [str(INVENTORY_INT64_MAX), str(INVENTORY_INT64_MIN)]


def test_alias_resolution_is_bounded_linear_and_cycles_fail_controlled(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    class CountingLinks(dict):
        get_calls = 0

        def get(self, key, default=None):
            self.get_calls += 1
            return super().get(key, default)

    chain_length = 2_000
    links = CountingLinks({index: index + 1 for index in range(chain_length - 1)})
    links[chain_length - 1] = None
    resolved = inventory_exports._resolve_aliases(links)
    assert resolved[0] == chain_length - 1
    assert resolved[chain_length // 2] == chain_length - 1
    assert links.get_calls <= chain_length * 5
    with pytest.raises(ValueError, match="inventory_alias_cycle"):
        inventory_exports._resolve_aliases({1: 2, 2: 1})

    royal = _user(db_session, "royal", "alias-cap-royal")
    component = InventoryCatalogComponent(
        name="Alias cap component",
        normalized_name="alias cap component",
        is_active=False,
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add(component)
    db_session.flush()
    for index in range(3):
        db_session.add(
            InventoryCatalogPart(
                component_id=component.id,
                name=f"Alias {index}",
                normalized_name=f"alias {index}",
                article=f"ALIAS-{index}",
                normalized_article=f"alias-{index}",
                is_active=False,
                created_by=royal.id,
                updated_by=royal.id,
            )
        )
    db_session.commit()
    monkeypatch.setattr(inventory_exports, "MAX_ALIAS_ROWS", 2)
    login_as(client, royal.username, "secret")

    response = client.get(
        "/inventory/export",
        params={"park_id": seed_park_with_tracker.id, "format": "xlsx"},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "inventory_export_too_large"}


def test_xlsx_chunks_long_comments_without_duplicate_numeric_values_and_counts_them(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", "long-comment-royal")
    long_comment = "=" + "Я" * 32_765 + "+" + "Ю" * 32_765 + "@" + "Э" * 4_468
    db_session.add(
        InventoryReceipt(
            park_id=seed_park_with_tracker.id,
            supplier="Factory",
            receipt_date=date(2026, 9, 11),
            comment=long_comment,
            status="draft",
            created_by=royal.id,
        )
    )
    db_session.commit()
    login_as(client, royal.username, "secret")

    response = client.get(
        "/inventory/export",
        params={"park_id": seed_park_with_tracker.id, "format": "xlsx"},
    )

    assert response.status_code == 200, response.text
    book = load_workbook(io.BytesIO(response.content), read_only=True)
    rows = list(book["Поставки"].values)
    assert rows[0][-2:] == ("Часть текста", "Всего частей")
    assert len(rows) == 4
    assert [row[-2:] for row in rows[1:]] == [(1, 3), (2, 3), (3, 3)]
    comment_parts = [row[7] for row in rows[1:]]
    assert [part[:2] for part in comment_parts] == ["'=", "'+", "'@"]
    reconstructed = "".join(part[1:] for part in comment_parts)
    assert reconstructed == long_comment
    assert rows[1][0] is not None
    assert all(row[0] is None for row in rows[2:])

    monkeypatch.setattr(inventory_exports, "MAX_EXPORT_ROWS", 1)
    capped = client.get(
        "/inventory/export",
        params={"park_id": seed_park_with_tracker.id, "format": "xlsx"},
    )
    assert capped.status_code == 413
    assert capped.json() == {"detail": "inventory_export_too_large"}


def test_xlsx_captures_bounded_ids_without_counts_and_uses_closed_spooled_backing(
    client, db_session, db_engine, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", "spooled-royal")
    sql = []
    spools = []
    original_factory = inventory_exports.tempfile.SpooledTemporaryFile

    def tracked_spool(*args, **kwargs):
        spool = original_factory(*args, **kwargs)
        spools.append(spool)
        return spool

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "inventory_" in statement.casefold():
            sql.append(statement.casefold())

    monkeypatch.setattr(inventory_exports.tempfile, "SpooledTemporaryFile", tracked_spool)
    event.listen(db_engine, "before_cursor_execute", record_statement)
    try:
        login_as(client, royal.username, "secret")
        response = client.get(
            "/inventory/export",
            params={"park_id": seed_park_with_tracker.id, "format": "xlsx"},
        )
    finally:
        event.remove(db_engine, "before_cursor_execute", record_statement)

    assert response.status_code == 200, response.text
    assert not any("count(" in statement for statement in sql)
    assert len([statement for statement in sql[:5] if " limit " in statement]) == 5
    assert spools and all(spool.closed for spool in spools)


def test_xlsx_export_row_cap_is_independent_from_alias_working_set(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", "independent-cap-royal")
    active_component = InventoryCatalogComponent(
        name="Only exported component",
        normalized_name="only exported component",
        created_by=royal.id,
        updated_by=royal.id,
    )
    archived_component = InventoryCatalogComponent(
        name="Alias-only component",
        normalized_name="alias-only component",
        is_active=False,
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add_all([active_component, archived_component])
    db_session.flush()
    db_session.add(
        InventoryCatalogPart(
            component_id=active_component.id,
            name="Only exported part",
            normalized_name="only exported part",
            article="EXPORTED-ONE",
            normalized_article="exported-one",
            created_by=royal.id,
            updated_by=royal.id,
        )
    )
    for index in range(3):
        db_session.add(
            InventoryCatalogPart(
                component_id=archived_component.id,
                name=f"Working alias {index}",
                normalized_name=f"working alias {index}",
                article=f"WORKING-ALIAS-{index}",
                normalized_article=f"working-alias-{index}",
                is_active=False,
                created_by=royal.id,
                updated_by=royal.id,
            )
        )
    db_session.commit()
    monkeypatch.setattr(inventory_exports, "MAX_EXPORT_ROWS", 1)
    monkeypatch.setattr(inventory_exports, "MAX_ALIAS_ROWS", 10)
    login_as(client, royal.username, "secret")

    response = client.get(
        "/inventory/export",
        params={"park_id": seed_park_with_tracker.id, "format": "xlsx"},
    )

    assert response.status_code == 200, response.text
    book = load_workbook(io.BytesIO(response.content), read_only=True)
    assert len(list(book["Остатки"].values)) == 2


def test_snapshot_accepts_exactly_50001_export_rows_with_separate_alias_budget(monkeypatch):
    class Rows(list):
        def tuples(self):
            return self

    class SnapshotSession:
        def __init__(self):
            self.responses = iter(
                (
                    Rows((part_id, None) for part_id in range(100)),
                    Rows((1, part_id) for part_id in range(50_001)),
                )
            )
            self.rolled_back = False

        def get_bind(self):
            return type("Bind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})()

        def execute(self, _statement):
            return next(self.responses)

        def rollback(self):
            self.rolled_back = True

    session = SnapshotSession()
    monkeypatch.setattr(
        inventory_exports.inventory_catalog,
        "acquire_alias_graph_read_lock",
        lambda _db: None,
    )
    monkeypatch.setattr(inventory_exports, "MAX_EXPORT_ROWS", 50_001)
    monkeypatch.setattr(inventory_exports, "MAX_ALIAS_ROWS", 100)

    snapshot = inventory_exports._capture_export_snapshot(
        session,
        (inventory_exports._stock_query([1]),),
        include_aliases=True,
    )

    assert snapshot.exported_rows == 50_001
    assert len(snapshot.identities["Остатки"]) == 50_001
    assert len(snapshot.canonical_ids) == 100
    assert session.rolled_back is False


def test_sqlite_export_snapshot_explicitly_starts_a_consistent_read_transaction(monkeypatch):
    events = []

    class DriverConnection:
        in_transaction = False

        class Cursor:
            def execute(self, statement):
                events.append(statement)

            def close(self):
                return None

        def cursor(self):
            return self.Cursor()

    class Connection:
        connection = type("ConnectionFairy", (), {"driver_connection": DriverConnection()})()

    class Bind:
        dialect = type("Dialect", (), {"name": "sqlite"})()

    class Rows(list):
        def tuples(self):
            return self

    class SnapshotSession:
        def get_bind(self):
            return Bind()

        def connection(self):
            return Connection()

        def execute(self, _statement):
            events.append("snapshot-select")
            return Rows()

        def rollback(self):
            events.append("rollback")

    monkeypatch.setattr(
        inventory_exports.inventory_catalog,
        "acquire_alias_graph_read_lock",
        lambda _db: events.append("alias-lock"),
    )

    inventory_exports._capture_export_snapshot(
        SnapshotSession(),
        (inventory_exports._stock_query([1]),),
        include_aliases=False,
    )

    assert events == ["alias-lock", "BEGIN", "snapshot-select"]


def test_export_snapshot_holds_alias_lock_and_excludes_later_rows(
    db_session, seed_park_with_tracker, monkeypatch
):
    events = []
    monkeypatch.setattr(
        inventory_exports.inventory_catalog,
        "acquire_alias_graph_read_lock",
        lambda _db: events.append("alias-lock"),
    )
    queries = inventory_exports._xlsx_queries([seed_park_with_tracker.id])

    snapshot = inventory_exports._capture_export_snapshot(db_session, queries, include_aliases=True)
    db_session.rollback()
    events.append("snapshot-fixed")
    actor = _user(db_session, "royal", "snapshot-race-royal")
    late = InventoryMovement(
        park_id=seed_park_with_tracker.id,
        actor_user_id=actor.id,
        kind="adjustment",
        delta=0,
        balance_before=0,
        balance_after=0,
    )
    db_session.add(late)
    db_session.commit()

    movement_query = next(query for query in queries if query.title == "Движения")
    rows = list(
        inventory_exports._snapshot_payload_rows(
            db_session,
            movement_query,
            snapshot.identities["Движения"],
        )
    )

    assert events == ["alias-lock", "snapshot-fixed"]
    assert late.id not in [row[0] for row in rows]
    assert snapshot.exported_rows == sum(len(ids) for ids in snapshot.identities.values())


def test_xlsx_escapes_xml_invalid_controls_before_formula_safety_and_chunking(
    client, db_session, seed_park_with_tracker
):
    royal = _user(db_session, "royal", "xml-control-royal")
    comment = "=before\x00middle\x0bafter"
    db_session.add(
        InventoryReceipt(
            park_id=seed_park_with_tracker.id,
            supplier="Controls",
            receipt_date=date(2026, 9, 11),
            comment=comment,
            status="draft",
            created_by=royal.id,
        )
    )
    db_session.commit()
    login_as(client, royal.username, "secret")

    response = client.get(
        "/inventory/export",
        params={"park_id": seed_park_with_tracker.id, "format": "xlsx"},
    )

    assert response.status_code == 200, response.text
    book = load_workbook(io.BytesIO(response.content), read_only=True)
    exported = list(book["Поставки"].values)[1][7]
    assert exported == "'=before\\u0000middle\\u000Bafter"


def test_postgresql_export_session_uses_repeatable_read_and_closes_before_stream(monkeypatch):
    events = []

    class Connection:
        def execution_options(self, **options):
            events.append(("isolation", options))
            return self

        def close(self):
            events.append("connection-close")

    connection = Connection()

    class Engine:
        dialect = type("Dialect", (), {"name": "postgresql"})()

        def connect(self):
            events.append("connect")
            return connection

    class RequestDB:
        def get_bind(self):
            events.append("authorized-bind")
            return Engine()

    class SnapshotSession:
        def rollback(self):
            events.append("snapshot-rollback")

        def close(self):
            events.append("snapshot-close")

    monkeypatch.setattr(
        inventory_exports,
        "Session",
        lambda *, bind: events.append(("session", bind)) or SnapshotSession(),
    )

    with inventory_exports._export_session(RequestDB()) as snapshot_db:
        assert isinstance(snapshot_db, SnapshotSession)
        events.append("payload-spooled")

    assert events == [
        "authorized-bind",
        "connect",
        ("isolation", {"isolation_level": "REPEATABLE READ"}),
        ("session", connection),
        "payload-spooled",
        "snapshot-rollback",
        "snapshot-close",
        "connection-close",
    ]


def test_xlsx_receipt_snapshot_is_not_mixed_when_post_replaces_draft_line(
    client, db_session, db_engine, seed_park_with_tracker, monkeypatch
):
    with db_engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    royal = _user(db_session, "royal", "export-cutoff-royal")
    component = InventoryCatalogComponent(
        name="Cutoff component",
        normalized_name="cutoff component",
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add(component)
    db_session.flush()
    old_part = InventoryCatalogPart(
        component_id=component.id,
        name="Old draft part",
        normalized_name="old draft part",
        article="OLD-CUTOFF",
        normalized_article="old-cutoff",
        created_by=royal.id,
        updated_by=royal.id,
    )
    new_part = InventoryCatalogPart(
        component_id=component.id,
        name="New posted part",
        normalized_name="new posted part",
        article="NEW-CUTOFF",
        normalized_article="new-cutoff",
        created_by=royal.id,
        updated_by=royal.id,
    )
    db_session.add_all([old_part, new_part])
    db_session.flush()
    receipt = InventoryReceipt(
        park_id=seed_park_with_tracker.id,
        supplier="Snapshot supplier",
        receipt_date=date(2026, 9, 12),
        status="draft",
        created_by=royal.id,
    )
    db_session.add(receipt)
    db_session.flush()
    old_line = InventoryReceiptLine(
        receipt_id=receipt.id,
        catalog_part_id=old_part.id,
        quantity=3,
    )
    db_session.add(old_line)
    db_session.commit()
    old_line_id = old_line.id
    receipt_id = receipt.id
    new_part_id = new_part.id
    original_capture = inventory_exports._capture_export_snapshot

    def capture_then_replace(snapshot_db, queries, *, include_aliases):
        snapshot = original_capture(snapshot_db, queries, include_aliases=include_aliases)
        with OrmSession(bind=db_engine) as writer:
            writer_receipt = writer.get(InventoryReceipt, receipt_id)
            writer_receipt.status = "posted"
            writer_receipt.posted_by = royal.id
            writer_receipt.posted_at = datetime(2026, 9, 12, 12, 0)
            writer.delete(writer.get(InventoryReceiptLine, old_line_id))
            writer.add(
                InventoryReceiptLine(
                    receipt_id=receipt_id,
                    catalog_part_id=new_part_id,
                    quantity=7,
                )
            )
            writer.commit()
        return snapshot

    monkeypatch.setattr(inventory_exports, "_capture_export_snapshot", capture_then_replace)
    login_as(client, royal.username, "secret")

    response = client.get(
        "/inventory/export",
        params={"park_id": seed_park_with_tracker.id, "format": "xlsx"},
    )

    assert response.status_code == 200, response.text
    rows = list(load_workbook(io.BytesIO(response.content), read_only=True)["Поставки"].values)
    exported = [row for row in rows[1:] if row[0] == receipt_id]
    assert len(exported) == 1
    assert exported[0][3] == "draft"
    assert exported[0][12] == old_line_id
    assert exported[0][17] == "OLD-CUTOFF"
    assert exported[0][18] == 3
