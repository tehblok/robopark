from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.models import (
    InventoryCatalogPart,
    InventoryMovement,
    InventoryParkStock,
    Park,
    User,
    UserPark,
)
from robopark_api.security import hash_password
from robopark_api.services import inventory as inventory_svc


def _user(db, slug, username, parks=()):
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, slug),
        access_status="approved",
        is_active=True,
        tracker_login=username,
    )
    db.add(user)
    db.flush()
    for park in parks:
        db.add(UserPark(user_id=user.id, park_id=park.id))
    db.commit()
    return user


def _seed_part(client, park_id):
    component = client.post("/inventory/components", data={"park_id": park_id, "name": "Подвязка"})
    assert component.status_code == 201, component.text
    part = client.post(
        "/inventory/parts",
        data={
            "park_id": park_id,
            "component_id": component.json()["id"],
            "name": "Тяга",
            "article": "TY-001",
            "quantity": 5,
            "minimum_quantity": 2,
            "location": "Стеллаж A / полка 2",
        },
    )
    assert part.status_code == 201, part.text
    return component.json(), part.json()


def test_mechanic_maintains_own_park_inventory_and_cannot_overdraw(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "store-mechanic", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    component, part = _seed_part(client, seed_park_with_tracker.id)

    overview = client.get(f"/inventory?park_id={seed_park_with_tracker.id}").json()
    assert overview["components"][0]["id"] == component["id"]
    assert overview["components"][0]["parts"][0]["quantity"] == 5
    assert overview["low_stock_count"] == 0

    receipt = client.post(
        f"/inventory/parts/{part['id']}/movements",
        json={"kind": "receipt", "quantity": 3, "note": "Поставка"},
    )
    assert receipt.status_code == 201
    assert receipt.json()["balance_after"] == 8
    adjustment = client.post(
        f"/inventory/parts/{part['id']}/movements",
        json={"kind": "adjustment", "quantity": -2, "note": "Сверка"},
    )
    assert adjustment.status_code == 201
    assert adjustment.json()["delta"] == -2
    assert adjustment.json()["balance_after"] == 6
    rejected = client.post(
        f"/inventory/parts/{part['id']}/movements", json={"kind": "writeoff", "quantity": 9}
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == {
        "code": "inventory_out_of_stock",
        "current_quantity": 6,
    }
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 6
    listed = client.get(
        "/inventory/movements", params={"park_id": seed_park_with_tracker.id}
    )
    assert listed.status_code == 200, listed.text
    assert [row["delta"] for row in listed.json()] == [-2, 3, 5]


def test_inventory_component_photo_is_stored_outside_database(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "photo-mechanic", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    image = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    created = client.post(
        "/inventory/components",
        data={"park_id": seed_park_with_tracker.id, "name": "Колесо"},
        files={"photo": ("wheel.png", image, "image/png")},
    )
    assert created.status_code == 201, created.text
    assert created.json()["has_photo"] is True
    downloaded = client.get(f"/inventory/components/{created.json()['id']}/photo")
    assert downloaded.status_code == 200
    assert downloaded.content == image


def test_operator_and_royal_can_open_every_park_inventory(
    client, db_session, seed_park_with_tracker
):
    other = Park(name="Other", tag="Other", tracker_queue="ROBOPARK", is_active=True)
    db_session.add(other)
    db_session.commit()
    operator = _user(db_session, "operator", "store-operator", [seed_park_with_tracker])
    royal = _user(db_session, "royal", "store-royal")
    login_as(client, operator.username, "secret")
    assert client.get(f"/inventory?park_id={other.id}").status_code == 200
    login_as(client, royal.username, "secret")
    assert client.get(f"/inventory?park_id={other.id}").status_code == 200


def test_task_writeoff_requires_owner_and_writes_technical_tracker_comment(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    mechanic = _user(db_session, "mechanic", "tracker-mechanic", [seed_park_with_tracker])
    monkeypatch.setattr(inventory_svc.platform_settings, "get_tracker_token", lambda db: "token")
    issue = {
        "key": "RP-42",
        "tags": [seed_park_with_tracker.tag],
        "assignee": {"login": mechanic.tracker_login},
    }
    monkeypatch.setattr(inventory_svc.tracker_cache, "get_issue", lambda **kwargs: issue)
    comments = []
    monkeypatch.setattr(
        inventory_svc.tracker_client, "add_comment", lambda **kwargs: comments.append(kwargs)
    )
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)

    from robopark_api.services.tracker_claims import claim_issue, release_claim

    claim_issue(
        db_session,
        actor=mechanic,
        owner=mechanic,
        issue_key="RP-42",
        park_id=seed_park_with_tracker.id,
    )

    response = client.post(
        "/inventory/tasks/RP-42/writeoff", json={"part_id": part["id"], "quantity": 2}
    )
    assert response.status_code == 201, response.text
    assert response.json()["balance_after"] == 3
    assert comments[0]["key"] == "RP-42"
    assert "TY-001" in comments[0]["text"]
    assert "tracker-mechanic" in comments[0]["text"]
    assert "Время:" in comments[0]["text"]
    assert "Инициатор: tracker-mechanic" in comments[0]["text"]
    movement = db_session.scalar(
        select(InventoryMovement).where(InventoryMovement.kind == "task_writeoff")
    )
    assert movement.issue_key == "RP-42"
    release_claim(db_session, "RP-42")
    rejected = client.post(
        "/inventory/tasks/RP-42/writeoff", json={"part_id": part["id"], "quantity": 1}
    )
    assert rejected.status_code == 403
    catalog_part = db_session.get(InventoryCatalogPart, part["id"])
    assert catalog_part is not None
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 3


def test_task_writeoff_does_not_comment_when_stock_is_insufficient(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    mechanic = _user(db_session, "mechanic", "empty-stock-mechanic", [seed_park_with_tracker])
    monkeypatch.setattr(inventory_svc.platform_settings, "get_tracker_token", lambda db: "token")
    issue = {
        "key": "RP-43",
        "tags": [seed_park_with_tracker.tag],
        "assignee": {"login": mechanic.tracker_login},
    }
    monkeypatch.setattr(inventory_svc.tracker_cache, "get_issue", lambda **kwargs: issue)
    comments = []
    monkeypatch.setattr(
        inventory_svc.tracker_client, "add_comment", lambda **kwargs: comments.append(kwargs)
    )
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=mechanic,
        owner=mechanic,
        issue_key="RP-43",
        park_id=seed_park_with_tracker.id,
    )

    response = client.post(
        "/inventory/tasks/RP-43/writeoff", json={"part_id": part["id"], "quantity": 6}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["current_quantity"] == 5
    assert comments == []
