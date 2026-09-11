import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from robopark_api.models import (
    AuditLog,
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryMovement,
    InventoryParkStock,
    Park,
    User,
    UserPark,
)
from robopark_api.security import hash_password
from robopark_api.services import inventory_catalog, inventory_stock


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
    for park in parks:
        db.add(UserPark(user_id=user.id, park_id=park.id))
    db.commit()
    return user


def _catalog(db, actor, *, name="Тяга", article="ABC-01"):
    component = InventoryCatalogComponent(
        name="Подвязка",
        normalized_name="подвязка",
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(component)
    db.flush()
    part = InventoryCatalogPart(
        component_id=component.id,
        name=name,
        normalized_name=" ".join(name.strip().casefold().split()),
        article=article,
        normalized_article=" ".join(article.strip().casefold().split()),
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(part)
    db.commit()
    return component, part


def test_catalog_search_exposes_exact_contract_zero_stock_and_mechanic_scope(
    client, db_session, seed_park_with_tracker
):
    foreign = Park(name="Foreign", tag="Foreign", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    mechanic = _user(db_session, "mechanic", "catalog-mechanic", [seed_park_with_tracker])
    component, part = _catalog(db_session, mechanic, name="ABC тяга", article=" AbC-01 ")
    login_as(client, mechanic.username, "secret")

    response = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "q": "abc"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {
                "id": part.id,
                "component_id": component.id,
                "component_name": "Подвязка",
                "name": "ABC тяга",
                "article": " AbC-01 ",
                "is_active": True,
                "has_photo": False,
                "quantity": 0,
                "minimum_quantity": 0,
                "location": None,
                "stock_is_active": False,
            }
        ],
        "limit": 50,
        "offset": 0,
        "total": 1,
    }
    assert (
        client.get("/inventory/catalog/search", params={"park_id": foreign.id}).status_code == 403
    )


def test_catalog_search_filters_and_sorts_by_normalized_values(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "filter-mechanic", [seed_park_with_tracker])
    component, second = _catalog(db_session, mechanic, name="  яблоко", article="z-2")
    first = InventoryCatalogPart(
        component_id=component.id,
        name="Абрикос",
        normalized_name="абрикос",
        article="A-1",
        normalized_article="a-1",
        created_by=mechanic.id,
        updated_by=mechanic.id,
    )
    db_session.add(first)
    db_session.flush()
    db_session.add(
        InventoryParkStock(
            park_id=seed_park_with_tracker.id,
            catalog_part_id=first.id,
            quantity=1,
            minimum_quantity=2,
            location=None,
            updated_by=mechanic.id,
        )
    )
    db_session.commit()
    login_as(client, mechanic.username, "secret")

    all_rows = client.get(
        "/inventory/catalog/search", params={"park_id": seed_park_with_tracker.id}
    ).json()
    assert [row["id"] for row in all_rows["items"]] == [first.id, second.id]
    filtered = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "stock_filter": "below_minimum"},
    ).json()
    assert [row["id"] for row in filtered["items"]] == [first.id]


def test_mechanic_can_create_missing_catalog_but_only_admin_can_patch_existing(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "create-mechanic", [seed_park_with_tracker])
    admin = _user(db_session, "admin", "catalog-admin")
    login_as(client, mechanic.username, "secret")
    component_response = client.post(
        "/inventory/catalog/components",
        json={"park_id": seed_park_with_tracker.id, "name": "  Колесо  "},
    )
    assert component_response.status_code == 201, component_response.text
    assert component_response.json() == {
        "id": component_response.json()["id"],
        "name": "Колесо",
        "is_active": True,
        "has_photo": False,
    }
    part_response = client.post(
        "/inventory/catalog/parts",
        json={
            "park_id": seed_park_with_tracker.id,
            "component_id": component_response.json()["id"],
            "name": "Диск",
            "article": " WH-01 ",
        },
    )
    assert part_response.status_code == 201, part_response.text
    assert part_response.json() == {
        "id": part_response.json()["id"],
        "component_id": component_response.json()["id"],
        "name": "Диск",
        "article": "WH-01",
        "is_active": True,
        "has_photo": False,
    }
    part_id = part_response.json()["id"]
    assert (
        client.patch(f"/inventory/catalog/parts/{part_id}", json={"name": "Нет"}).status_code == 403
    )

    login_as(client, admin.username, "secret")
    patched = client.patch(
        f"/inventory/catalog/parts/{part_id}", json={"name": "  Исправлено  ", "is_active": False}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Исправлено"
    assert patched.json()["is_active"] is False

    component_id = component_response.json()["id"]
    component_patched = client.patch(
        f"/inventory/catalog/components/{component_id}",
        json={"name": "  Колесо в сборе  ", "is_active": False},
    )
    assert component_patched.status_code == 200, component_patched.text
    assert component_patched.json() == {
        "id": component_id,
        "name": "Колесо в сборе",
        "is_active": False,
        "has_photo": False,
    }


def test_duplicate_active_article_returns_existing_part(client, db_session, seed_park_with_tracker):
    mechanic = _user(db_session, "mechanic", "duplicate-mechanic", [seed_park_with_tracker])
    component, existing = _catalog(db_session, mechanic, article=" AbC-01 ")
    login_as(client, mechanic.username, "secret")

    response = client.post(
        "/inventory/catalog/parts",
        json={
            "park_id": seed_park_with_tracker.id,
            "component_id": component.id,
            "name": "Дубль",
            "article": "  abc-01  ",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "inventory_article_exists", "existing_part_id": existing.id}
    }


def test_stock_settings_reject_mismatched_park_part_and_never_accept_quantity(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "stock-mechanic", [seed_park_with_tracker])
    _, part = _catalog(db_session, mechanic)
    foreign = Park(name="Foreign", tag="Foreign", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    login_as(client, mechanic.username, "secret")

    unknown = client.put(
        f"/inventory/parks/{seed_park_with_tracker.id}/stocks/{part.id + 999}",
        json={"minimum_quantity": 2, "location": "A-1", "is_active": True},
    )
    assert unknown.status_code == 404
    assert (
        client.put(
            f"/inventory/parks/{foreign.id}/stocks/{part.id}",
            json={"minimum_quantity": 2, "location": "A-1", "is_active": True},
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/inventory/parks/{seed_park_with_tracker.id}/stocks/{part.id}",
            json={"quantity": 100},
        ).status_code
        == 422
    )
    updated = client.put(
        f"/inventory/parks/{seed_park_with_tracker.id}/stocks/{part.id}",
        json={"minimum_quantity": 2, "location": "  A-1  ", "is_active": True},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json() == {
        "park_id": seed_park_with_tracker.id,
        "catalog_part_id": part.id,
        "quantity": 0,
        "minimum_quantity": 2,
        "location": "A-1",
        "is_active": True,
        "version": 2,
    }


def test_archived_component_hides_parts_and_conflicting_restores_return_409(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "restore-admin")
    component, active = _catalog(db_session, admin, article="DUP-1")
    archived_component = InventoryCatalogComponent(
        name="  Подвязка  ",
        normalized_name="подвязка",
        is_active=False,
        created_by=admin.id,
        updated_by=admin.id,
    )
    archived_part = InventoryCatalogPart(
        component_id=component.id,
        name="Archived",
        normalized_name="archived",
        article=" dup-1 ",
        normalized_article="dup-1",
        is_active=False,
        created_by=admin.id,
        updated_by=admin.id,
    )
    hidden_component = InventoryCatalogComponent(
        name="Hidden component",
        normalized_name="hidden component",
        is_active=False,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db_session.add(hidden_component)
    db_session.flush()
    hidden_part = InventoryCatalogPart(
        component_id=hidden_component.id,
        name="Hidden part",
        normalized_name="hidden part",
        article="HIDDEN",
        normalized_article="hidden",
        created_by=admin.id,
        updated_by=admin.id,
    )
    db_session.add_all([archived_component, archived_part, hidden_part])
    db_session.commit()
    login_as(client, admin.username, "secret")

    searched = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "q": "HIDDEN"},
    )
    assert searched.status_code == 200
    assert searched.json()["items"] == []

    component_restore = client.patch(
        f"/inventory/catalog/components/{archived_component.id}", json={"is_active": True}
    )
    assert component_restore.status_code == 409
    assert component_restore.json()["detail"] == {
        "code": "inventory_component_exists",
        "existing_component_id": component.id,
    }
    part_restore = client.patch(
        f"/inventory/catalog/parts/{archived_part.id}", json={"is_active": True}
    )
    assert part_restore.status_code == 409
    assert part_restore.json()["detail"] == {
        "code": "inventory_article_exists",
        "existing_part_id": active.id,
    }


def test_merge_transfers_stock_and_movement_history_and_archives_source(
    client, db_session, seed_park_with_tracker
):
    other = Park(name="Other", tag="Other", is_active=True)
    db_session.add(other)
    db_session.commit()
    admin = _user(db_session, "admin", "merge-admin")
    component, target = _catalog(db_session, admin, name="Target", article="TARGET")
    source = InventoryCatalogPart(
        component_id=component.id,
        name="Source",
        normalized_name="source",
        article="SOURCE",
        normalized_article="source",
        created_by=admin.id,
        updated_by=admin.id,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add_all(
        [
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=target.id,
                quantity=2,
                updated_by=admin.id,
            ),
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=source.id,
                quantity=3,
                updated_by=admin.id,
            ),
            InventoryParkStock(
                park_id=other.id,
                catalog_part_id=source.id,
                quantity=4,
                minimum_quantity=2,
                location="B",
                updated_by=admin.id,
            ),
            InventoryMovement(
                catalog_part_id=source.id,
                park_id=seed_park_with_tracker.id,
                actor_user_id=admin.id,
                kind="receipt",
                delta=3,
                balance_before=0,
                balance_after=3,
            ),
        ]
    )
    db_session.commit()
    login_as(client, admin.username, "secret")

    response = client.post(
        f"/inventory/catalog/parts/{source.id}/merge",
        json={"target_part_id": target.id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == target.id
    db_session.refresh(source)
    assert source.is_active is False
    stocks = {
        (row.park_id, row.catalog_part_id): (row.quantity, row.minimum_quantity, row.location)
        for row in db_session.scalars(select(InventoryParkStock))
    }
    assert stocks[(seed_park_with_tracker.id, target.id)][0] == 5
    assert stocks[(other.id, target.id)] == (4, 2, "B")
    historical_receipt = db_session.scalar(
        select(InventoryMovement).where(InventoryMovement.kind == "receipt")
    )
    assert historical_receipt.catalog_part_id == source.id
    merge_audits = list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == "inventory.catalog.part.merged")
        )
    )
    assert {row.park_id for row in merge_audits} == {seed_park_with_tracker.id, other.id}
    assert all(row.actor_user_id == admin.id and row.actor_role == "admin" for row in merge_audits)
    assert all('"changed_fields"' in row.detail for row in merge_audits)


def test_inventory_mutations_write_attributed_audit_entries(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "audit-admin")
    _, part = _catalog(db_session, admin)
    login_as(client, admin.username, "secret")

    assert (
        client.patch(f"/inventory/catalog/parts/{part.id}", json={"name": "Changed"}).status_code
        == 200
    )
    assert (
        client.put(
            f"/inventory/parks/{seed_park_with_tracker.id}/stocks/{part.id}",
            json={"minimum_quantity": 2, "location": "A", "is_active": True},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/inventory/parts/{part.id}/movements",
            json={
                "park_id": seed_park_with_tracker.id,
                "kind": "receipt",
                "quantity": 1,
                "note": "delivery",
            },
        ).status_code
        == 201
    )
    assert (
        client.patch(f"/inventory/catalog/parts/{part.id}", json={"is_active": False}).status_code
        == 200
    )

    rows = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.actor_user_id == admin.id,
                AuditLog.action.like("inventory.%"),
            )
        )
    )
    assert {row.action for row in rows} >= {
        "inventory.catalog.part.updated",
        "inventory.stock.configured",
        "inventory.stock.moved",
    }
    assert all(row.actor_role == "admin" for row in rows)
    assert all('"changed_fields"' in (row.detail or "") for row in rows)
    stock_rows = [row for row in rows if row.action != "inventory.catalog.part.updated"]
    assert all(row.park_id == seed_park_with_tracker.id for row in stock_rows)
    movement_row = next(row for row in rows if row.action == "inventory.stock.moved")
    assert json.loads(movement_row.detail)["changed_fields"] == [
        "kind",
        "note",
        "quantity",
    ]
    assert any(
        json.loads(row.detail)["changed_fields"] == ["is_active"]
        for row in rows
        if row.action == "inventory.catalog.part.updated"
    )


def test_concurrent_first_stock_deltas_are_not_lost(db_engine, db_session, seed_park_with_tracker):
    mechanic = _user(db_session, "mechanic", "concurrent-mechanic", [seed_park_with_tracker])
    _, part = _catalog(db_session, mechanic, article="CONCURRENT")
    mechanic_id = mechanic.id
    park_id = seed_park_with_tracker.id
    part_id = part.id

    def add_one(source_id):
        with Session(db_engine) as session:
            user = session.get(User, mechanic_id)
            inventory_stock.apply_stock_delta(
                session,
                user=user,
                park_id=park_id,
                catalog_part_id=part_id,
                delta=1,
                kind="adjustment",
                source_kind="manual",
                source_id=source_id,
                note=None,
            )
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(add_one, ["one", "two"]))

    db_session.expire_all()
    stock = db_session.scalar(select(InventoryParkStock))
    assert stock.quantity == 2
    movements = list(db_session.scalars(select(InventoryMovement)))
    assert sorted((row.balance_before, row.balance_after) for row in movements) == [
        (0, 1),
        (1, 2),
    ]


def test_merge_preserves_colliding_source_history_and_document_identity(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "merge-source-admin")
    component, target = _catalog(db_session, admin, name="Target", article="MERGE-TARGET")
    source = InventoryCatalogPart(
        component_id=component.id,
        name="Source",
        normalized_name="source",
        article="MERGE-SOURCE",
        normalized_article="merge-source",
        created_by=admin.id,
        updated_by=admin.id,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add_all(
        [
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=target.id,
                quantity=2,
                updated_by=admin.id,
            ),
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=source.id,
                quantity=3,
                updated_by=admin.id,
            ),
            InventoryMovement(
                catalog_part_id=target.id,
                park_id=seed_park_with_tracker.id,
                actor_user_id=admin.id,
                kind="receipt",
                delta=2,
                balance_before=0,
                balance_after=2,
                source_kind="receipt",
                source_id="receipt-line-7",
            ),
            InventoryMovement(
                catalog_part_id=source.id,
                park_id=seed_park_with_tracker.id,
                actor_user_id=admin.id,
                kind="receipt",
                delta=3,
                balance_before=0,
                balance_after=3,
                source_kind="receipt",
                source_id="receipt-line-7",
                note="x" * 490,
            ),
        ]
    )
    db_session.commit()
    historical = list(
        db_session.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.kind == "receipt")
            .order_by(InventoryMovement.id)
        )
    )
    before = {
        row.id: (
            row.catalog_part_id,
            row.source_kind,
            row.source_id,
            row.note,
            row.balance_before,
            row.balance_after,
        )
        for row in historical
    }
    login_as(client, admin.username, "secret")

    response = client.post(
        f"/inventory/catalog/parts/{source.id}/merge",
        json={"target_part_id": target.id},
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    stock = db_session.scalar(
        select(InventoryParkStock).where(InventoryParkStock.catalog_part_id == target.id)
    )
    assert stock.quantity == 5
    after = {
        row.id: (
            row.catalog_part_id,
            row.source_kind,
            row.source_id,
            row.note,
            row.balance_before,
            row.balance_after,
        )
        for row in db_session.scalars(
            select(InventoryMovement).where(InventoryMovement.id.in_(before))
        )
    }
    assert after == before
    document_movement = db_session.scalar(
        select(InventoryMovement).where(
            InventoryMovement.catalog_part_id == source.id,
            InventoryMovement.park_id == seed_park_with_tracker.id,
            InventoryMovement.source_kind == "receipt",
            InventoryMovement.source_id == "receipt-line-7",
        )
    )
    assert document_movement is not None
    history_response = client.get(
        "/inventory/movements", params={"park_id": seed_park_with_tracker.id}
    )
    assert history_response.status_code == 200, history_response.text
    assert any(
        row["id"] == document_movement.id
        and row["catalog_part_id"] == source.id
        and row["part_id"] == source.id
        for row in history_response.json()
    )
    history = list(
        db_session.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.catalog_part_id.in_([source.id, target.id]))
            .order_by(InventoryMovement.id)
        )
    )
    assert len(history) == 4
    assert sum(row.delta for row in history if row.kind == "receipt") == 5
    assert all(row.balance_after - row.balance_before == row.delta for row in history)
    assert (
        sum(row.source_kind == "receipt" and row.source_id == "receipt-line-7" for row in history)
        == 2
    )


@pytest.mark.parametrize("kind", ["component", "part"])
def test_catalog_create_stale_uniqueness_precheck_returns_existing_id(
    db_session, seed_park_with_tracker, monkeypatch, kind
):
    admin = _user(db_session, "admin", f"race-{kind}-admin")
    component, part = _catalog(db_session, admin, article="RACE-1")
    original_scalar = db_session.scalar
    skipped = False

    def stale_precheck(statement, *args, **kwargs):
        nonlocal skipped
        sql = str(statement)
        marker = (
            "inventory_catalog_components.normalized_name"
            if kind == "component"
            else "inventory_catalog_parts.normalized_article"
        )
        if not skipped and marker in sql and "is_active" in sql:
            skipped = True
            return None
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", stale_precheck)
    with pytest.raises(inventory_stock.InventoryConflict) as raised:
        if kind == "component":
            inventory_catalog.create_component(
                db_session,
                admin,
                park_id=seed_park_with_tracker.id,
                name="  Подвязка ",
            )
        else:
            inventory_catalog.create_part(
                db_session,
                admin,
                park_id=seed_park_with_tracker.id,
                component_id=component.id,
                name="Duplicate",
                article=" race-1 ",
            )

    assert raised.value.code == (
        "inventory_component_exists" if kind == "component" else "inventory_article_exists"
    )
    assert (
        raised.value.existing_component_id if kind == "component" else raised.value.existing_part_id
    ) == (component.id if kind == "component" else part.id)


@pytest.mark.parametrize(
    ("kind", "operation"),
    [
        ("component", "rename"),
        ("component", "restore"),
        ("part", "rename"),
        ("part", "restore"),
    ],
)
def test_catalog_update_stale_uniqueness_precheck_returns_409_with_existing_id(
    client, db_session, seed_park_with_tracker, monkeypatch, kind, operation
):
    admin = _user(db_session, "admin", f"update-race-{kind}-{operation}")
    component, part = _catalog(db_session, admin, article="RACE-1")
    if kind == "component":
        conflicting = InventoryCatalogComponent(
            name="Source component",
            normalized_name=("source component" if operation == "rename" else "подвязка"),
            is_active=operation == "rename",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db_session.add(conflicting)
        db_session.commit()
        path = f"/inventory/catalog/components/{conflicting.id}"
        payload = {"name": "  Подвязка "} if operation == "rename" else {"is_active": True}
        marker = "inventory_catalog_components.normalized_name"
        expected_detail = {
            "code": "inventory_component_exists",
            "existing_component_id": component.id,
        }
    else:
        conflicting = InventoryCatalogPart(
            component_id=component.id,
            name="Source part",
            normalized_name="source part",
            article="RACE-2" if operation == "rename" else "race-1",
            normalized_article="race-2" if operation == "rename" else "race-1",
            is_active=operation == "rename",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db_session.add(conflicting)
        db_session.commit()
        path = f"/inventory/catalog/parts/{conflicting.id}"
        payload = {"article": " race-1 "} if operation == "rename" else {"is_active": True}
        marker = "inventory_catalog_parts.normalized_article"
        expected_detail = {
            "code": "inventory_article_exists",
            "existing_part_id": part.id,
        }

    login_as(client, admin.username, "secret")
    original_scalar = db_session.scalar
    skipped = False

    def stale_precheck(statement, *args, **kwargs):
        nonlocal skipped
        sql = str(statement)
        if not skipped and marker in sql and "is_active" in sql and "!=" in sql:
            skipped = True
            return None
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", stale_precheck)

    response = client.patch(path, json=payload)

    assert skipped is True
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == expected_detail


def test_postgresql_first_stock_creation_uses_conflict_safe_insert():
    statement = inventory_stock.stock_insert_if_missing_statement(
        "postgresql", park_id=7, catalog_part_id=11
    )

    compiled = str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert compiled == (
        "INSERT INTO inventory_park_stocks (park_id, catalog_part_id, quantity, "
        "minimum_quantity, is_active, version) VALUES (7, 11, 0, 0, true, 1) "
        "ON CONFLICT (park_id, catalog_part_id) DO NOTHING RETURNING "
        "inventory_park_stocks.id"
    )
