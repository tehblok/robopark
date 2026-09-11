import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
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
from robopark_api.services import inventory_stock


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
    assert all(
        row.catalog_part_id == target.id for row in db_session.scalars(select(InventoryMovement))
    )
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
