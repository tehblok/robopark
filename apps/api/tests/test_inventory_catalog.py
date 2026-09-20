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
    Permission,
    Role,
    User,
    UserPark,
)
from robopark_api.security import hash_password
from robopark_api.services import inventory_catalog, inventory_stock
from robopark_api.services.rbac import has_permission


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


_INVENTORY_ACTION_PERMISSION = {
    "stock-settings": "inventory.stock.manage",
    "receive-and-inventory": "inventory.documents.post",
    "labels-and-export": "inventory.export",
    "catalog-delete-or-merge": "inventory.catalog.manage",
}


@pytest.mark.parametrize(
    ("action", "slug", "allowed"),
    [
        pytest.param("stock-settings", "royal", True, id="stock-settings-royal-allow"),
        pytest.param("stock-settings", "admin", True, id="stock-settings-admin-allow"),
        pytest.param("stock-settings", "operator", False, id="stock-settings-operator-deny"),
        pytest.param("stock-settings", "mechanic", True, id="stock-settings-mechanic-allow"),
        pytest.param("stock-settings", "driver", False, id="stock-settings-driver-deny"),
        pytest.param("stock-settings", "restricted", True, id="stock-settings-restricted-allow"),
        pytest.param("receive-and-inventory", "royal", True, id="receive-and-inventory-royal-allow"),
        pytest.param("receive-and-inventory", "admin", True, id="receive-and-inventory-admin-allow"),
        pytest.param("receive-and-inventory", "operator", False, id="receive-and-inventory-operator-deny"),
        pytest.param("receive-and-inventory", "mechanic", True, id="receive-and-inventory-mechanic-allow"),
        pytest.param("receive-and-inventory", "driver", False, id="receive-and-inventory-driver-deny"),
        pytest.param("receive-and-inventory", "restricted", True, id="receive-and-inventory-restricted-allow"),
        pytest.param("labels-and-export", "royal", True, id="labels-and-export-royal-allow"),
        pytest.param("labels-and-export", "admin", True, id="labels-and-export-admin-allow"),
        pytest.param("labels-and-export", "operator", False, id="labels-and-export-operator-deny"),
        pytest.param("labels-and-export", "mechanic", True, id="labels-and-export-mechanic-allow"),
        pytest.param("labels-and-export", "driver", False, id="labels-and-export-driver-deny"),
        pytest.param("labels-and-export", "restricted", True, id="labels-and-export-restricted-allow"),
        pytest.param("catalog-delete-or-merge", "royal", True, id="catalog-delete-or-merge-royal-allow"),
        pytest.param("catalog-delete-or-merge", "admin", True, id="catalog-delete-or-merge-admin-allow"),
        pytest.param("catalog-delete-or-merge", "operator", False, id="catalog-delete-or-merge-operator-deny"),
        pytest.param("catalog-delete-or-merge", "mechanic", False, id="catalog-delete-or-merge-mechanic-deny"),
        pytest.param("catalog-delete-or-merge", "driver", False, id="catalog-delete-or-merge-driver-deny"),
        pytest.param("catalog-delete-or-merge", "restricted", True, id="catalog-delete-or-merge-restricted-allow"),
    ],
)
def test_inventory_permission_role_matrix(db_session, action, slug, allowed):
    permission_key = _INVENTORY_ACTION_PERMISSION[action]
    if slug == "restricted":
        permission = db_session.scalar(select(Permission).where(Permission.key == permission_key))
        role = Role(slug=f"matrix-{action}", name=f"Matrix {action}", permissions=[permission])
        db_session.add(role)
        db_session.flush()
        actor = User(
            username=f"matrix-{action}", password_hash=hash_password("secret"), role_id=role.id,
            access_status="approved", is_active=True,
        )
        db_session.add(actor)
        db_session.commit()
    else:
        actor = _user(db_session, slug, f"matrix-permission-{action}-{slug}")
    assert has_permission(db_session, actor, permission_key) is allowed


@pytest.mark.parametrize(
    ("action", "slug", "expected"),
    [
        pytest.param("stock-settings", "royal", 200, id="stock-settings-royal-allow"),
        pytest.param("stock-settings", "admin", 200, id="stock-settings-admin-allow"),
        pytest.param("stock-settings", "operator", 403, id="stock-settings-operator-deny"),
        pytest.param("stock-settings", "mechanic", 200, id="stock-settings-mechanic-allow"),
        pytest.param("stock-settings", "driver", 403, id="stock-settings-driver-deny"),
        pytest.param("stock-settings", "restricted", 200, id="stock-settings-restricted-allow"),
        pytest.param("catalog-delete-or-merge", "royal", 200, id="catalog-delete-or-merge-royal-allow"),
        pytest.param("catalog-delete-or-merge", "admin", 200, id="catalog-delete-or-merge-admin-allow"),
        pytest.param("catalog-delete-or-merge", "operator", 403, id="catalog-delete-or-merge-operator-deny"),
        pytest.param("catalog-delete-or-merge", "mechanic", 403, id="catalog-delete-or-merge-mechanic-deny"),
        pytest.param("catalog-delete-or-merge", "driver", 403, id="catalog-delete-or-merge-driver-deny"),
        pytest.param("catalog-delete-or-merge", "restricted", 200, id="catalog-delete-or-merge-restricted-allow"),
    ],
)
def test_inventory_action_role_matrix(
    client, db_session, seed_park_with_tracker, action, slug, expected
):
    if slug == "restricted":
        permission_keys = {
            "nav.inventory",
            "inventory.stock.manage" if action == "stock-settings" else "inventory.catalog.manage",
        }
        role = Role(
            slug=f"restricted_{action}",
            name=f"Restricted {action}",
            permissions=list(
                db_session.scalars(select(Permission).where(Permission.key.in_(permission_keys)))
            ),
        )
        db_session.add(role)
        db_session.flush()
        actor = User(
            username=f"matrix-{action}-restricted",
            password_hash=hash_password("secret"),
            role_id=role.id,
            access_status="approved",
            is_active=True,
        )
        db_session.add(actor)
        db_session.flush()
        db_session.add(UserPark(user_id=actor.id, park_id=seed_park_with_tracker.id))
        db_session.commit()
    else:
        actor = _user(
            db_session,
            slug,
            f"matrix-{action}-{slug}",
            [seed_park_with_tracker],
        )
    _, part = _catalog(db_session, actor, article=f"MATRIX-{action}-{slug}")
    login_as(client, actor.username, "secret")

    if action == "stock-settings":
        response = client.put(
            f"/inventory/parks/{seed_park_with_tracker.id}/stocks/{part.id}",
            json={"minimum_quantity": 2, "location": "A-1", "is_active": True},
        )
    else:
        response = client.patch(
            f"/inventory/catalog/parts/{part.id}", json={"is_active": False}
        )

    assert response.status_code == expected, response.text


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


def test_catalog_archived_mode_is_admin_only_and_supports_restore(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "archive-browser-admin")
    mechanic = _user(db_session, "mechanic", "archive-browser-mechanic", [seed_park_with_tracker])
    component, part = _catalog(db_session, admin, article="ARCHIVE-FIND")
    login_as(client, admin.username, "secret")
    assert (
        client.patch(f"/inventory/catalog/parts/{part.id}", json={"is_active": False}).status_code
        == 200
    )

    active = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "q": "ARCHIVE-FIND"},
    )
    archived = client.get(
        "/inventory/catalog/search",
        params={
            "park_id": seed_park_with_tracker.id,
            "q": "ARCHIVE-FIND",
            "mode": "archived",
        },
    )
    all_rows = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "mode": "all"},
    )
    assert active.json()["items"] == []
    assert [row["id"] for row in archived.json()["items"]] == [part.id]
    assert part.id in [row["id"] for row in all_rows.json()["items"]]

    assert (
        client.patch(
            f"/inventory/catalog/components/{component.id}", json={"is_active": False}
        ).status_code
        == 200
    )
    assert [
        row["id"]
        for row in client.get(
            "/inventory/catalog/search",
            params={"park_id": seed_park_with_tracker.id, "mode": "archived"},
        ).json()["items"]
    ] == [part.id]

    login_as(client, mechanic.username, "secret")
    assert (
        client.get(
            "/inventory/catalog/search",
            params={"park_id": seed_park_with_tracker.id, "mode": "archived"},
        ).status_code
        == 403
    )

    login_as(client, admin.username, "secret")
    assert (
        client.patch(
            f"/inventory/catalog/components/{component.id}", json={"is_active": True}
        ).status_code
        == 200
    )
    restored = client.patch(f"/inventory/catalog/parts/{part.id}", json={"is_active": True})
    assert restored.status_code == 200, restored.text
    assert (
        client.get(
            "/inventory/catalog/search",
            params={"park_id": seed_park_with_tracker.id, "q": "ARCHIVE-FIND"},
        ).json()["items"][0]["id"]
        == part.id
    )


def test_catalog_component_metadata_and_exact_part_are_scoped_and_independent_of_search_page(
    client, db_session, seed_park_with_tracker
):
    foreign = Park(name="Foreign metadata", tag="Foreign-metadata", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    mechanic = _user(db_session, "mechanic", "metadata-mechanic", [seed_park_with_tracker])
    component, part = _catalog(db_session, mechanic)
    db_session.add(
        InventoryParkStock(
            park_id=seed_park_with_tracker.id,
            catalog_part_id=part.id,
            quantity=7,
            minimum_quantity=2,
            location="A-7",
            updated_by=mechanic.id,
        )
    )
    db_session.commit()
    login_as(client, mechanic.username, "secret")

    components = client.get(
        "/inventory/catalog/components",
        params={"park_id": seed_park_with_tracker.id, "limit": 1, "offset": 0},
    )
    assert components.status_code == 200, components.text
    assert components.json() == {
        "items": [{"id": component.id, "name": "Подвязка", "is_active": True, "has_photo": False}],
        "limit": 1,
        "offset": 0,
        "total": 1,
    }

    exact = client.get(
        f"/inventory/catalog/parts/{part.id}",
        params={"park_id": seed_park_with_tracker.id},
    )
    assert exact.status_code == 200, exact.text
    assert exact.json() == {
        "id": part.id,
        "component_id": component.id,
        "component_name": "Подвязка",
        "name": "Тяга",
        "article": "ABC-01",
        "is_active": True,
        "has_photo": False,
        "quantity": 7,
        "minimum_quantity": 2,
        "location": "A-7",
        "stock_is_active": True,
    }
    assert (
        client.get(
            f"/inventory/catalog/parts/{part.id}", params={"park_id": foreign.id}
        ).status_code
        == 403
    )


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


def test_catalog_api_accepts_max_length_values_whose_casefold_expands(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "expanding-casefold", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    expanding = "ß" * 128
    component = client.post(
        "/inventory/catalog/components",
        json={"park_id": seed_park_with_tracker.id, "name": expanding},
    )
    assert component.status_code == 201, component.text
    part = client.post(
        "/inventory/catalog/parts",
        json={
            "park_id": seed_park_with_tracker.id,
            "component_id": component.json()["id"],
            "name": expanding,
            "article": expanding,
        },
    )
    assert part.status_code == 201, part.text
    db_session.expire_all()
    stored = db_session.get(InventoryCatalogPart, part.json()["id"])
    assert stored.normalized_name == "ss" * 128
    assert stored.normalized_article == "ss" * 128
    found = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "q": "SS" * 128},
    )
    assert [item["id"] for item in found.json()["items"]] == [part.json()["id"]]


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
                minimum_quantity=1,
                location=None,
                is_active=False,
                updated_by=admin.id,
            ),
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=source.id,
                quantity=3,
                minimum_quantity=7,
                location="Source shelf",
                is_active=True,
                updated_by=admin.id,
            ),
            InventoryParkStock(
                park_id=other.id,
                catalog_part_id=target.id,
                quantity=0,
                minimum_quantity=9,
                location="Target B",
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
    assert stocks[(seed_park_with_tracker.id, target.id)] == (5, 7, "Source shelf")
    merged_target_stock = db_session.scalar(
        select(InventoryParkStock).where(
            InventoryParkStock.park_id == seed_park_with_tracker.id,
            InventoryParkStock.catalog_part_id == target.id,
        )
    )
    assert merged_target_stock.is_active is True
    assert stocks[(other.id, target.id)] == (4, 9, "Target B")
    archived = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "mode": "archived"},
    )
    assert source.id not in [row["id"] for row in archived.json()["items"]]
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


def test_postgresql_merge_acquires_exclusive_alias_lock_before_catalog_rows(monkeypatch):
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

    monkeypatch.setattr(
        inventory_catalog.inventory_access,
        "require_catalog_manage",
        lambda *_args: None,
    )

    def record_catalog_row_lock(_db, _part_ids):
        events.append("catalog-row-lock")
        return {}

    monkeypatch.setattr(inventory_catalog, "lock_catalog_parts", record_catalog_row_lock)

    with pytest.raises(LookupError, match="inventory_part_not_found"):
        inventory_catalog.merge_parts(RecordingSession(), object(), 10, 20)

    assert len(events) == 2
    assert "pg_advisory_xact_lock" in events[0]
    assert "pg_advisory_xact_lock_shared" not in events[0]
    assert events[1] == "catalog-row-lock"


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
        and row["part_id"] == -target.id
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
