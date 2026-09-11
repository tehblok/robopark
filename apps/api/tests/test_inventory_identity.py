import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import login_as
from robopark_api.models import (
    InventoryCatalogPart,
    InventoryComponent,
    InventoryMovement,
    InventoryParkStock,
    InventoryPart,
    Park,
)
from robopark_api.services import inventory as inventory_svc
from robopark_api.services import inventory_catalog, inventory_stock
from test_inventory_catalog import _catalog, _user


def _legacy(db, park, catalog):
    component = InventoryComponent(park_id=park.id, name="Legacy component")
    db.add(component)
    db.flush()
    part = InventoryPart(
        park_id=park.id,
        component_id=component.id,
        name=catalog.name,
        article=catalog.article,
        location="old",
    )
    db.add(part)
    db.commit()
    return part


def test_global_adapter_is_park_independent_and_positive_legacy_stays_park_bound(
    client, db_session, seed_park_with_tracker, monkeypatch, tmp_path
):
    park = seed_park_with_tracker
    other = Park(name="Other", tag="other", is_active=True)
    db_session.add(other)
    db_session.commit()
    admin = _user(db_session, "admin", "adapter-admin")
    component, part = _catalog(db_session, admin)
    legacy = _legacy(db_session, park, part)
    part.photo_storage_key = "part.png"
    db_session.commit()
    (tmp_path / "part.png").write_bytes(b"shared-photo")
    monkeypatch.setattr(inventory_svc, "photos_root", lambda: tmp_path)
    login_as(client, admin.username, "secret")
    for current in (park, other):
        overview = client.get("/inventory", params={"park_id": current.id}).json()
        represented = overview["components"][0]["parts"][0]
        assert represented["id"] == -part.id
        assert represented["catalog_part_id"] == part.id
        move = client.post(
            f"/inventory/parts/{represented['id']}/movements",
            json={"park_id": current.id, "kind": "receipt", "quantity": 2},
        )
        assert move.status_code == 201, move.text
        assert move.json()["part_id"] == -part.id
        patch = client.patch(
            f"/inventory/parts/{represented['id']}",
            json={"park_id": current.id, "location": current.name},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["catalog_part_id"] == part.id
    mechanic = _user(db_session, "mechanic", "other-mechanic", [other])
    login_as(client, mechanic.username, "secret")
    assert client.get(f"/inventory/parts/{-part.id}/photo").content == b"shared-photo"
    assert client.get(f"/inventory/parts/{legacy.id}/photo").status_code == 403
    rejected = client.post(
        f"/inventory/parts/{legacy.id}/movements",
        json={"park_id": other.id, "kind": "receipt", "quantity": 20},
    )
    assert rejected.status_code in (403, 409)
    assert list(db_session.scalars(select(InventoryParkStock.quantity))) == [2, 2]


def test_reused_article_legacy_lookup_never_selects_archived_row(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "article-admin")
    component, archived = _catalog(db_session, admin)
    legacy = _legacy(db_session, seed_park_with_tracker, archived)
    archived.is_active = False
    db_session.commit()
    active = InventoryCatalogPart(
        component_id=component.id,
        name="Replacement",
        normalized_name="replacement",
        article=archived.article,
        normalized_article=archived.normalized_article,
    )
    db_session.add(active)
    db_session.commit()
    login_as(client, admin.username, "secret")
    response = client.post(
        f"/inventory/parts/{legacy.id}/movements",
        json={"kind": "receipt", "quantity": 3},
    )
    assert response.status_code == 201, response.text
    assert response.json()["catalog_part_id"] == active.id
    assert db_session.scalar(select(InventoryParkStock.catalog_part_id)) == active.id


@pytest.mark.parametrize(
    "source_quantity,target_stock", [(None, False), (0, False), (0, True), (3, False), (3, True)]
)
def test_merge_alias_survives_zero_stock_and_routes_every_source_reference(
    client, db_session, seed_park_with_tracker, source_quantity, target_stock, monkeypatch, tmp_path
):
    park = seed_park_with_tracker
    admin = _user(db_session, "admin", "alias-admin")
    component, source = _catalog(db_session, admin, article="SOURCE")
    legacy = _legacy(db_session, park, source)
    target = InventoryCatalogPart(
        component_id=component.id,
        name="Target",
        normalized_name="target",
        article="TARGET",
        normalized_article="target",
        photo_storage_key="target.png",
    )
    db_session.add(target)
    db_session.flush()
    if source_quantity is not None:
        db_session.add(
            InventoryParkStock(park_id=park.id, catalog_part_id=source.id, quantity=source_quantity)
        )
    if target_stock:
        db_session.add(InventoryParkStock(park_id=park.id, catalog_part_id=target.id, quantity=2))
    historical = InventoryMovement(
        catalog_part_id=source.id,
        part_id=legacy.id,
        park_id=park.id,
        actor_user_id=admin.id,
        kind="receipt",
        delta=1,
        balance_before=0,
        balance_after=1,
        source_kind="receipt",
        source_id="historic",
        note="original",
    )
    db_session.add(historical)
    db_session.commit()
    original = {
        column.name: getattr(historical, column.name)
        for column in InventoryMovement.__table__.columns
    }
    (tmp_path / "target.png").write_bytes(b"target-photo")
    monkeypatch.setattr(inventory_svc, "photos_root", lambda: tmp_path)
    login_as(client, admin.username, "secret")
    response = client.post(
        f"/inventory/catalog/parts/{source.id}/merge", json={"target_part_id": target.id}
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert getattr(source, "merged_into_part_id", None) == target.id
    assert {
        column.name: getattr(historical, column.name)
        for column in InventoryMovement.__table__.columns
    } == original
    history = client.get("/inventory/movements", params={"park_id": park.id}).json()
    represented = next(row for row in history if row["id"] == historical.id)
    assert represented["part_id"] == -target.id
    assert represented["catalog_part_id"] == source.id
    # Reusing and then renaming the old article must not steal a migrated legacy reference.
    replacement = client.post(
        "/inventory/catalog/parts",
        json={
            "park_id": park.id,
            "component_id": component.id,
            "name": "Unrelated",
            "article": "SOURCE",
        },
    )
    assert replacement.status_code == 201, replacement.text
    for path_id, extra in (
        (-source.id, {}),
        (legacy.id, {}),
        (-source.id, {"catalog_part_id": source.id}),
    ):
        move = client.post(
            f"/inventory/parts/{path_id}/movements",
            json={
                "park_id": park.id,
                "kind": "receipt",
                "quantity": 1,
                **extra,
            },
        )
        assert move.status_code == 201, move.text
        assert move.json()["catalog_part_id"] == target.id
        assert client.get(f"/inventory/parts/{path_id}/photo").content == b"target-photo"
        patch = client.patch(
            f"/inventory/parts/{path_id}", json={"park_id": park.id, "location": "new"}
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["catalog_part_id"] == target.id
    configured = client.put(
        f"/inventory/parks/{park.id}/stocks/{source.id}",
        json={"location": "target location", "minimum_quantity": 0, "is_active": True},
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["catalog_part_id"] == target.id
    movement = inventory_stock.apply_stock_delta(
        db_session,
        user=admin,
        park_id=park.id,
        catalog_part_id=source.id,
        delta=1,
        kind="receipt",
        source_kind="manual",
        source_id="post-merge",
        note=None,
    )
    db_session.commit()
    assert movement.catalog_part_id == target.id
    db_session.expire_all()
    stocks = {
        row.catalog_part_id: row.quantity for row in db_session.scalars(select(InventoryParkStock))
    }
    assert stocks.get(source.id, 0) == 0
    assert stocks[target.id] == (source_quantity or 0) + (2 if target_stock else 0) + 4
    assert replacement.json()["id"] not in stocks
    assert source.is_active is False


def test_stale_merge_cannot_replace_an_existing_alias(
    client, db_engine, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "stale-alias-admin")
    component, source = _catalog(db_session, admin)
    target = InventoryCatalogPart(
        component_id=component.id,
        name="Target",
        normalized_name="target",
        article="T",
        normalized_article="t",
    )
    unrelated = InventoryCatalogPart(
        component_id=component.id,
        name="Other",
        normalized_name="other",
        article="O",
        normalized_article="o",
    )
    db_session.add_all([target, unrelated])
    db_session.commit()
    login_as(client, admin.username, "secret")
    with Session(db_engine) as stale:
        cached = stale.get(InventoryCatalogPart, source.id)
        response = client.post(
            f"/inventory/catalog/parts/{source.id}/merge", json={"target_part_id": target.id}
        )
        assert response.status_code == 200, response.text
        assert cached.is_active is True
        with pytest.raises(
            inventory_stock.InventoryConflict, match="inventory_merge_inactive_part"
        ):
            inventory_catalog.merge_parts(stale, admin, source.id, unrelated.id)
    db_session.expire_all()
    assert source.merged_into_part_id == target.id


def test_stale_catalog_patch_follows_the_committed_merge_alias(
    client, db_engine, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "stale-patch-admin")
    component, source = _catalog(db_session, admin)
    target = InventoryCatalogPart(
        component_id=component.id,
        name="Target",
        normalized_name="target",
        article="T",
        normalized_article="t",
    )
    db_session.add(target)
    db_session.commit()
    login_as(client, admin.username, "secret")
    with Session(db_engine) as stale:
        cached = stale.get(InventoryCatalogPart, source.id)
        response = client.post(
            f"/inventory/catalog/parts/{source.id}/merge", json={"target_part_id": target.id}
        )
        assert response.status_code == 200, response.text
        assert cached.is_active is True
        result = inventory_catalog.update_part(stale, admin, source.id, {"name": "Corrected"})
        assert result.id == target.id
    db_session.expire_all()
    assert target.name == "Corrected"
    assert source.is_active is False


def test_merge_refreshes_locked_stock_before_transferring(
    client, db_engine, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "stale-stock-admin")
    component, source = _catalog(db_session, admin)
    target = InventoryCatalogPart(
        component_id=component.id,
        name="Target",
        normalized_name="target",
        article="T",
        normalized_article="t",
    )
    db_session.add(target)
    db_session.flush()
    db_session.add_all(
        [
            InventoryParkStock(
                park_id=seed_park_with_tracker.id, catalog_part_id=source.id, quantity=3
            ),
            InventoryParkStock(
                park_id=seed_park_with_tracker.id, catalog_part_id=target.id, quantity=0
            ),
        ]
    )
    db_session.commit()
    login_as(client, admin.username, "secret")
    with Session(db_engine) as stale:
        cached_stock = stale.scalar(
            select(InventoryParkStock).where(InventoryParkStock.catalog_part_id == source.id)
        )
        response = client.post(
            f"/inventory/parts/{-source.id}/movements",
            json={"park_id": seed_park_with_tracker.id, "kind": "receipt", "quantity": 2},
        )
        assert response.status_code == 201, response.text
        assert cached_stock.quantity == 3
        inventory_catalog.merge_parts(stale, admin, source.id, target.id)
    db_session.expire_all()
    quantities = dict(
        db_session.execute(
            select(InventoryParkStock.catalog_part_id, InventoryParkStock.quantity)
        ).all()
    )
    assert quantities == {source.id: 0, target.id: 5}
