from concurrent.futures import ThreadPoolExecutor
from threading import Lock, get_ident
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from robopark_api.db import get_db
from robopark_api.models import (
    AuditLog,
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryMovement,
    InventoryParkStock,
    InventoryReceipt,
    Park,
    Permission,
    User,
    UserPark,
    UserPermission,
)
from robopark_api.security import hash_password
from robopark_api.services import inventory_catalog, inventory_receipts


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


def _part(db, user, *, active=True, article="R-1"):
    component = InventoryCatalogComponent(
        name="Receipt component",
        normalized_name=f"receipt component {article}",
        is_active=True,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(component)
    db.flush()
    part = InventoryCatalogPart(
        component_id=component.id,
        name="Receipt part",
        normalized_name=f"receipt part {article}",
        article=article,
        normalized_article=article.casefold(),
        is_active=active,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(part)
    db.commit()
    return part


def _payload(part_id, **changes):
    payload = {
        "supplier": "Завод",
        "document_number": "UPD-42",
        "received_on": "2026-09-11",
        "comment": "Приемка",
        "lines": [{"catalog_part_id": part_id, "quantity": 5, "note": "Коробка"}],
    }
    payload.update(changes)
    return payload


def test_receipt_lifecycle_normalizes_lines_and_post_is_idempotent(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "receipt-mechanic", [seed_park_with_tracker])
    part = _part(db_session, mechanic)
    login_as(client, mechanic.username, "secret")

    created_response = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/receipts",
        json=_payload(
            part.id,
            lines=[
                {"catalog_part_id": part.id, "quantity": 2, "note": "first"},
                {"catalog_part_id": part.id, "quantity": 3, "note": "second"},
            ],
        ),
    )

    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["status"] == "draft"
    assert created["received_on"] == "2026-09-11"
    assert [(line["catalog_part_id"], line["quantity"]) for line in created["lines"]] == [
        (part.id, 5)
    ]
    assert created["lines"][0]["note"] == "first\nsecond"
    listed = client.get(f"/inventory/parks/{seed_park_with_tracker.id}/receipts")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["items"]] == [created["id"]]
    assert listed.json()["total"] == 1

    posted = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/receipts/{created['id']}/post"
    )
    retried = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/receipts/{created['id']}/post"
    )

    assert posted.status_code == retried.status_code == 200
    assert posted.json()["status"] == retried.json()["status"] == "posted"
    db_session.expire_all()
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 5
    movements = list(
        db_session.scalars(
            select(InventoryMovement).where(
                InventoryMovement.source_kind == "receipt",
                InventoryMovement.source_id == f"{created['id']}:{created['lines'][0]['id']}",
            )
        )
    )
    assert [(row.delta, row.kind) for row in movements] == [(5, "receipt")]


def test_receipt_duplicate_multiline_notes_are_deduplicated_as_whole_notes(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "receipt-multiline-note", [seed_park_with_tracker])
    part = _part(db_session, mechanic, article="MULTILINE-NOTE")
    login_as(client, mechanic.username, "secret")
    multiline_note = "outer box\ninner sleeve"

    response = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/receipts",
        json=_payload(
            part.id,
            lines=[
                {"catalog_part_id": part.id, "quantity": 2, "note": multiline_note},
                {"catalog_part_id": part.id, "quantity": 3, "note": multiline_note},
            ],
        ),
    )

    assert response.status_code == 201, response.text
    line = response.json()["lines"][0]
    assert line["quantity"] == 5
    assert line["note"] == multiline_note
    assert len(line["note"]) <= 500


def test_receipt_validates_lines_and_active_catalog_parts(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "receipt-validation", [seed_park_with_tracker])
    inactive = _part(db_session, mechanic, active=False, article="INACTIVE")
    login_as(client, mechanic.username, "secret")

    assert (
        client.post(
            f"/inventory/parks/{seed_park_with_tracker.id}/receipts",
            json=_payload(inactive.id, lines=[]),
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/inventory/parks/{seed_park_with_tracker.id}/receipts",
            json=_payload(inactive.id, lines=[{"catalog_part_id": inactive.id, "quantity": 0}]),
        ).status_code
        == 422
    )
    rejected = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/receipts",
        json=_payload(inactive.id),
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "inventory_receipt_part_invalid"


def test_receipt_access_and_document_post_capability_are_independent(
    client, db_session, seed_park_with_tracker
):
    foreign = Park(name="Foreign", tag="Foreign", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    mechanic = _user(db_session, "mechanic", "receipt-scope", [seed_park_with_tracker])
    part = _part(db_session, mechanic, article="SCOPE")
    login_as(client, mechanic.username, "secret")

    assert client.get(f"/inventory/parks/{foreign.id}/receipts").status_code == 403
    assert (
        client.post(f"/inventory/parks/{foreign.id}/receipts", json=_payload(part.id)).status_code
        == 403
    )

    created = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/receipts", json=_payload(part.id)
    ).json()
    permission_id = db_session.scalar(
        select(Permission.id).where(Permission.key == "inventory.documents.post")
    )
    db_session.add(UserPermission(user_id=mechanic.id, permission_id=permission_id, granted=False))
    db_session.commit()
    assert (
        client.post(
            f"/inventory/parks/{seed_park_with_tracker.id}/receipts/{created['id']}/post"
        ).status_code
        == 403
    )


def test_posted_receipt_cannot_be_edited_or_cancelled_but_draft_can(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "receipt-state", [seed_park_with_tracker])
    part = _part(db_session, mechanic, article="STATE")
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/receipts"
    posted = client.post(base, json=_payload(part.id)).json()
    draft = client.post(base, json=_payload(part.id, document_number="DRAFT")).json()

    updated = client.patch(f"{base}/{draft['id']}", json={"supplier": "  Новый завод  "})
    invalid_date = client.patch(f"{base}/{draft['id']}", json={"received_on": None})
    invalid_lines = client.patch(f"{base}/{draft['id']}", json={"lines": None})
    cancelled = client.post(f"{base}/{draft['id']}/cancel")
    client.post(f"{base}/{posted['id']}/post")

    assert updated.status_code == 200
    assert updated.json()["supplier"] == "Новый завод"
    assert invalid_date.status_code == 422
    assert invalid_lines.status_code == 422
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.patch(f"{base}/{posted['id']}", json={"comment": "changed"}).status_code == 409
    assert client.post(f"{base}/{posted['id']}/cancel").status_code == 409


def test_posted_receipt_reversal_requires_reason_and_preserves_original_history(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "receipt-reversal", [seed_park_with_tracker])
    part = _part(db_session, mechanic, article="REV")
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/receipts"
    receipt = client.post(base, json=_payload(part.id)).json()
    client.post(f"{base}/{receipt['id']}/post")
    original = db_session.scalar(
        select(InventoryMovement).where(InventoryMovement.source_kind == "receipt")
    )
    original_fields = (original.id, original.delta, original.note, original.source_id)

    assert client.post(f"{base}/{receipt['id']}/reverse", json={"reason": ""}).status_code == 422
    reversed_response = client.post(
        f"{base}/{receipt['id']}/reverse", json={"reason": "Ошибка приёмки"}
    )
    retried = client.post(f"{base}/{receipt['id']}/reverse", json={"reason": "Ошибка приёмки"})

    assert reversed_response.status_code == retried.status_code == 200
    assert reversed_response.json()["status"] == "posted"
    db_session.expire_all()
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 0
    original = db_session.get(InventoryMovement, original.id)
    assert (original.id, original.delta, original.note, original.source_id) == original_fields
    reversal = db_session.scalar(
        select(InventoryMovement).where(InventoryMovement.source_kind == "receipt_reversal")
    )
    assert reversal.delta == -5
    assert reversal.note == "Ошибка приёмки"
    assert (
        db_session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "inventory.receipt.reversed")
        )
        == 1
    )


def test_reverse_receipt_service_rejects_blank_reason(client, db_session, seed_park_with_tracker):
    mechanic = _user(db_session, "mechanic", "receipt-service-reversal", [seed_park_with_tracker])
    part = _part(db_session, mechanic, article="SERVICE-REV")
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/receipts"
    receipt = client.post(base, json=_payload(part.id)).json()
    client.post(f"{base}/{receipt['id']}/post")

    with pytest.raises(ValueError, match="inventory_receipt_reversal_reason_required"):
        inventory_receipts.reverse_receipt(
            db_session,
            mechanic,
            park_id=seed_park_with_tracker.id,
            receipt_id=receipt["id"],
            reason=" \t ",
        )

    assert db_session.scalar(select(InventoryParkStock.quantity)) == 5
    assert (
        db_session.scalar(
            select(func.count(InventoryMovement.id)).where(
                InventoryMovement.source_kind == "receipt_reversal"
            )
        )
        == 0
    )


def test_receipt_mutations_write_attributed_audit_in_the_same_transaction(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "receipt-audit", [seed_park_with_tracker])
    part = _part(db_session, mechanic, article="AUDIT")
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/receipts"
    receipt = client.post(base, json=_payload(part.id)).json()
    client.patch(f"{base}/{receipt['id']}", json={"comment": "updated"})
    client.post(f"{base}/{receipt['id']}/post")

    rows = list(
        db_session.scalars(select(AuditLog).where(AuditLog.target_type == "inventory_receipt"))
    )
    assert {row.action for row in rows} == {
        "inventory.receipt.created",
        "inventory.receipt.updated",
        "inventory.receipt.posted",
    }
    assert all(row.actor_user_id == mechanic.id for row in rows)
    assert all(row.actor_role == "mechanic" for row in rows)
    assert all(row.park_id == seed_park_with_tracker.id for row in rows)
    assert all('"changed_fields"' in row.detail for row in rows)


def test_receipt_post_rolls_back_all_lines_status_and_audit_on_failure(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import inventory_receipts

    mechanic = _user(db_session, "mechanic", "receipt-atomic", [seed_park_with_tracker])
    first = _part(db_session, mechanic, article="ATOMIC-1")
    second = _part(db_session, mechanic, article="ATOMIC-2")
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/receipts"
    receipt = client.post(
        base,
        json=_payload(
            first.id,
            lines=[
                {"catalog_part_id": first.id, "quantity": 2},
                {"catalog_part_id": second.id, "quantity": 3},
            ],
        ),
    ).json()
    real_apply = inventory_receipts.inventory_stock.apply_stock_delta
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced_receipt_failure")
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(inventory_receipts.inventory_stock, "apply_stock_delta", fail_second)
    response = client.post(f"{base}/{receipt['id']}/post")

    assert response.status_code == 502
    db_session.expire_all()
    assert list(db_session.scalars(select(InventoryParkStock))) == []
    assert list(db_session.scalars(select(InventoryMovement))) == []
    assert db_session.get(InventoryReceipt, receipt["id"]).status == "draft"
    assert (
        db_session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "inventory.receipt.posted")
        )
        == 0
    )


def test_receipt_line_source_identity_survives_merge_before_post_and_after_post(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "receipt-merge-admin")
    login_as(client, admin.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/receipts"

    source = _part(db_session, admin, article="MERGE-BEFORE-SOURCE")
    target = _part(db_session, admin, article="MERGE-BEFORE-TARGET")
    before = client.post(
        base,
        json=_payload(
            source.id,
            lines=[
                {"catalog_part_id": source.id, "quantity": 2},
                {"catalog_part_id": target.id, "quantity": 3},
            ],
        ),
    ).json()
    inventory_catalog.merge_parts(db_session, admin, source.id, target.id)
    assert client.post(f"{base}/{before['id']}/post").status_code == 200
    db_session.expire_all()
    stock = db_session.scalar(
        select(InventoryParkStock).where(
            InventoryParkStock.park_id == seed_park_with_tracker.id,
            InventoryParkStock.catalog_part_id == target.id,
        )
    )
    assert stock.quantity == 5
    assert {
        row.source_id
        for row in db_session.scalars(
            select(InventoryMovement).where(InventoryMovement.source_kind == "receipt")
        )
    } == {f"{before['id']}:{line['id']}" for line in before["lines"]}

    source = _part(db_session, admin, article="MERGE-AFTER-SOURCE")
    target = _part(db_session, admin, article="MERGE-AFTER-TARGET")
    after = client.post(
        base,
        json=_payload(
            source.id,
            lines=[
                {"catalog_part_id": source.id, "quantity": 2},
                {"catalog_part_id": target.id, "quantity": 3},
            ],
        ),
    ).json()
    assert client.post(f"{base}/{after['id']}/post").status_code == 200
    inventory_catalog.merge_parts(db_session, admin, source.id, target.id)
    assert (
        client.post(
            f"{base}/{after['id']}/reverse", json={"reason": "merged correction"}
        ).status_code
        == 200
    )
    db_session.expire_all()
    stock = db_session.scalar(
        select(InventoryParkStock).where(
            InventoryParkStock.park_id == seed_park_with_tracker.id,
            InventoryParkStock.catalog_part_id == target.id,
        )
    )
    assert stock.quantity == 0


def test_receipt_list_is_paginated_and_loads_lines_in_one_query(
    client, db_engine, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "receipt-list", [seed_park_with_tracker])
    part = _part(db_session, mechanic, article="LIST")
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/receipts"
    created = [
        client.post(base, json=_payload(part.id, document_number=f"DOC-{index}")).json()
        for index in range(3)
    ]
    line_queries = []

    def capture_line_queries(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "FROM inventory_receipt_lines" in statement:
            line_queries.append(statement)

    event.listen(db_engine, "before_cursor_execute", capture_line_queries)
    try:
        response = client.get(base, params={"limit": 2, "offset": 1})
    finally:
        event.remove(db_engine, "before_cursor_execute", capture_line_queries)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 3
    assert response.json()["limit"] == 2
    assert response.json()["offset"] == 1
    assert [row["id"] for row in response.json()["items"]] == [
        created[1]["id"],
        created[0]["id"],
    ]
    assert len(line_queries) == 1


def test_receipt_list_searches_supplier_and_document_number_case_insensitively(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "receipt-search", [seed_park_with_tracker])
    part = _part(db_session, mechanic, article="SEARCH")
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/receipts"
    supplier_match = client.post(
        base,
        json=_payload(
            part.id,
            supplier="Northern Factory",
            document_number="DOC-ONE",
        ),
    ).json()
    document_match = client.post(
        base,
        json=_payload(part.id, supplier="Other", document_number="factory-42"),
    ).json()
    client.post(
        base,
        json=_payload(part.id, supplier="Unrelated", document_number="NO-MATCH"),
    )
    unicode_match = client.post(
        base,
        json=_payload(part.id, supplier="Северный Завод", document_number="RU-1"),
    ).json()

    first_page = client.get(base, params={"q": " FaCtOrY ", "limit": 1})
    second_page = client.get(base, params={"q": "factory", "limit": 1, "offset": 1})

    assert first_page.status_code == second_page.status_code == 200
    assert first_page.json()["total"] == second_page.json()["total"] == 2
    assert [row["id"] for row in first_page.json()["items"]] == [document_match["id"]]
    assert [row["id"] for row in second_page.json()["items"]] == [supplier_match["id"]]
    unicode_result = client.get(base, params={"q": "завод"})
    assert unicode_result.status_code == 200
    assert unicode_result.json()["total"] == 1
    assert [row["id"] for row in unicode_result.json()["items"]] == [unicode_match["id"]]


def test_postgresql_receipt_search_normalizes_russian_without_locale_dependent_ilike(
    monkeypatch,
):
    class Bind:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

    class RecordingSession:
        statements = []

        def get_bind(self):
            return Bind()

        def scalar(self, statement):
            self.statements.append(statement)
            return 0

        def scalars(self, statement):
            self.statements.append(statement)
            return []

    monkeypatch.setattr(inventory_receipts.inventory_access, "require_park", lambda *_args: None)
    db = RecordingSession()

    inventory_receipts.list_receipts(
        db,
        object(),
        park_id=7,
        query="ЗАВОД",
        limit=10,
        offset=0,
    )

    compiled = "\n".join(
        str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for statement in db.statements
    )
    assert "translate(" in compiled
    assert "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ" in compiled
    assert "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" in compiled
    assert "%завод%" in compiled
    assert "ILIKE" not in compiled.upper()


@pytest.mark.parametrize(
    ("dialect_name", "expected_sql"),
    [("sqlite", None), ("postgresql", "pg_advisory_xact_lock_shared")],
)
def test_receipt_alias_stabilization_uses_shared_postgresql_advisory_lock(
    dialect_name, expected_sql
):
    class Bind:
        class Dialect:
            name = dialect_name

        dialect = Dialect()

    class RecordingSession:
        statements = []

        def get_bind(self):
            return Bind()

        def execute(self, statement):
            self.statements.append(str(statement))

    db = RecordingSession()
    inventory_receipts._stabilize_catalog_aliases(db)
    if expected_sql is None:
        assert db.statements == []
    else:
        assert len(db.statements) == 1
        assert expected_sql in db.statements[0]
        assert "inventory_catalog_parts" not in db.statements[0]


def test_postgresql_receipt_acquires_alias_lock_before_receipt_row_lock(monkeypatch):
    events = []

    class Bind:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

    class RecordingSession:
        def get_bind(self):
            return Bind()

        def execute(self, statement):
            events.append(str(statement))

        def commit(self):
            return None

        def refresh(self, _row):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(inventory_receipts, "_require_document_post", lambda *_args: None)

    def record_receipt_row_lock(*_args, **_kwargs):
        events.append("receipt-row-lock")
        return SimpleNamespace(status="posted")

    monkeypatch.setattr(inventory_receipts, "_receipt", record_receipt_row_lock)

    inventory_receipts.post_receipt(
        RecordingSession(),
        object(),
        park_id=7,
        receipt_id=11,
    )

    assert len(events) == 2
    assert "pg_advisory_xact_lock_shared" in events[0]
    assert events[1] == "receipt-row-lock"


def test_concurrent_alias_receipts_prelock_canonical_stocks_in_sorted_order(
    client, db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    admin = _user(db_session, "admin", "receipt-lock-order")
    login_as(client, admin.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/receipts"
    target_low = _part(db_session, admin, article="LOCK-TARGET-LOW")
    target_high = _part(db_session, admin, article="LOCK-TARGET-HIGH")
    sources = [_part(db_session, admin, article=f"LOCK-SOURCE-{index}") for index in range(4)]
    first = client.post(
        base,
        json=_payload(
            sources[0].id,
            lines=[
                {"catalog_part_id": sources[0].id, "quantity": 1},
                {"catalog_part_id": sources[1].id, "quantity": 1},
            ],
        ),
    ).json()
    second = client.post(
        base,
        json=_payload(
            sources[2].id,
            lines=[
                {"catalog_part_id": sources[2].id, "quantity": 1},
                {"catalog_part_id": sources[3].id, "quantity": 1},
            ],
        ),
    ).json()
    inventory_catalog.merge_parts(db_session, admin, sources[0].id, target_high.id)
    inventory_catalog.merge_parts(db_session, admin, sources[1].id, target_low.id)
    inventory_catalog.merge_parts(db_session, admin, sources[2].id, target_low.id)
    inventory_catalog.merge_parts(db_session, admin, sources[3].id, target_high.id)
    admin_id = admin.id
    park_id = seed_park_with_tracker.id
    calls: dict[int, list[int]] = {}
    calls_lock = Lock()
    real_ensure_stock = inventory_receipts.inventory_stock.ensure_stock

    def record_ensure_stock(db, *, park_id, catalog_part_id):
        with calls_lock:
            calls.setdefault(get_ident(), []).append(catalog_part_id)
        return real_ensure_stock(db, park_id=park_id, catalog_part_id=catalog_part_id)

    monkeypatch.setattr(inventory_receipts.inventory_stock, "ensure_stock", record_ensure_stock)

    def post(receipt_id):
        with Session(db_engine) as session:
            return inventory_receipts.post_receipt(
                session,
                session.get(User, admin_id),
                park_id=park_id,
                receipt_id=receipt_id,
            ).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(post, [first["id"], second["id"]]))

    assert statuses == ["posted", "posted"]
    expected_order = [target_low.id, target_high.id]
    assert len(calls) == 2
    assert all(part_ids[:2] == expected_order for part_ids in calls.values())
    db_session.expire_all()
    assert dict(
        db_session.execute(
            select(InventoryParkStock.catalog_part_id, InventoryParkStock.quantity).where(
                InventoryParkStock.catalog_part_id.in_(expected_order)
            )
        ).all()
    ) == {target_low.id: 2, target_high.id: 2}


def test_concurrent_receipt_post_retries_apply_each_line_once(
    client, db_engine, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "receipt-concurrent", [seed_park_with_tracker])
    part = _part(db_session, mechanic, article="CONCURRENT-RECEIPT")
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/receipts"
    receipt = client.post(base, json=_payload(part.id)).json()
    cookies = dict(client.cookies)

    def independent_db():
        with Session(db_engine) as session:
            yield session

    client.app.dependency_overrides[get_db] = independent_db

    def post_once(_):
        with TestClient(client.app, cookies=cookies) as parallel_client:
            response = parallel_client.post(f"{base}/{receipt['id']}/post")
            return response.status_code, response.json()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(post_once, range(2)))
    finally:
        client.app.dependency_overrides[get_db] = lambda: iter([db_session])

    assert [code for code, _ in results] == [200, 200]
    assert [body["status"] for _, body in results] == ["posted", "posted"]
    db_session.expire_all()
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 5
    assert (
        db_session.scalar(
            select(func.count(InventoryMovement.id)).where(
                InventoryMovement.source_kind == "receipt",
                InventoryMovement.source_id == f"{receipt['id']}:{receipt['lines'][0]['id']}",
            )
        )
        == 1
    )
    assert db_session.get(InventoryReceipt, receipt["id"]).status == "posted"
