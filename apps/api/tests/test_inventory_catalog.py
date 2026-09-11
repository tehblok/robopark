from conftest import login_as, role_id_for
from robopark_api.models import (
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryParkStock,
    Park,
    User,
    UserPark,
)
from robopark_api.security import hash_password


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
        client.get("/inventory/catalog/search", params={"park_id": foreign.id}).status_code
        == 403
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
        client.patch(f"/inventory/catalog/parts/{part_id}", json={"name": "Нет"}).status_code
        == 403
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
    assert client.put(
        f"/inventory/parks/{foreign.id}/stocks/{part.id}",
        json={"minimum_quantity": 2, "location": "A-1", "is_active": True},
    ).status_code == 403
    assert client.put(
        f"/inventory/parks/{seed_park_with_tracker.id}/stocks/{part.id}",
        json={"quantity": 100},
    ).status_code == 422
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
