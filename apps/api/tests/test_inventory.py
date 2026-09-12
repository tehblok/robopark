import pytest
from sqlalchemy import func, select

from conftest import login_as, role_id_for
from robopark_api.models import (
    AuditLog,
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryComponent,
    InventoryMovement,
    InventoryParkStock,
    InventoryPart,
    Park,
    User,
    UserPark,
)
from robopark_api.security import hash_password
from robopark_api.services import inventory as inventory_svc
from robopark_api.services import inventory_stock


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


def test_legacy_numeric_inputs_enforce_shared_int64_bounds_before_side_effects(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    mechanic = _user(db_session, "mechanic", "legacy-int64", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    component = client.post(
        "/inventory/components",
        data={"park_id": seed_park_with_tracker.id, "name": "Int64 legacy"},
    ).json()
    too_large = 2**63

    for field in ("quantity", "minimum_quantity"):
        payload = {
            "park_id": seed_park_with_tracker.id,
            "component_id": component["id"],
            "name": f"Overflow {field}",
            "article": f"OVERFLOW-{field}",
            "quantity": 0,
            "minimum_quantity": 0,
            "location": "A",
            field: too_large,
        }
        response = client.post("/inventory/parts", data=payload)
        assert response.status_code == 422

    with pytest.raises(inventory_stock.InventoryValidation, match="inventory_quantity_overflow"):
        inventory_svc.create_part(
            db_session,
            mechanic,
            park_id=seed_park_with_tracker.id,
            component_id=component["id"],
            name="Direct overflow",
            article="DIRECT-OVERFLOW",
            quantity=0,
            minimum_quantity=too_large,
            location="A",
            photo=None,
        )
    assert (
        db_session.scalar(
            select(InventoryCatalogPart.id).where(
                InventoryCatalogPart.normalized_article == "direct-overflow"
            )
        )
        is None
    )

    valid = client.post(
        "/inventory/parts",
        data={
            "park_id": seed_park_with_tracker.id,
            "component_id": component["id"],
            "name": "Valid",
            "article": "VALID-INT64",
            "quantity": 0,
            "minimum_quantity": 1,
            "location": "A",
        },
    ).json()
    stock_id = db_session.scalar(
        select(InventoryParkStock.id).where(
            InventoryParkStock.catalog_part_id == valid["catalog_part_id"]
        )
    )
    with pytest.raises(inventory_stock.InventoryValidation, match="inventory_quantity_overflow"):
        inventory_svc.update_part(
            db_session,
            mechanic,
            valid["id"],
            {"minimum_quantity": too_large},
        )
    db_session.expire_all()
    assert db_session.get(InventoryParkStock, stock_id).minimum_quantity == 1

    monkeypatch.setattr(
        inventory_svc.platform_settings,
        "get_tracker_token",
        lambda _db: pytest.fail("invalid quantity reached Tracker access"),
    )
    with pytest.raises(inventory_stock.InventoryValidation, match="inventory_quantity_overflow"):
        inventory_svc.task_writeoff(
            db_session,
            mechanic,
            "RP-INT64",
            valid["id"],
            too_large,
            idempotency_key="direct-overflow",
        )


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
    listed = client.get("/inventory/movements", params={"park_id": seed_park_with_tracker.id})
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
        "/inventory/tasks/RP-42/writeoff",
        json={"part_id": part["id"], "quantity": 2, "idempotency_key": "writeoff-rp42"},
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
        "/inventory/tasks/RP-42/writeoff",
        json={"part_id": part["id"], "quantity": 1, "idempotency_key": "writeoff-rejected"},
    )
    assert rejected.status_code == 403
    catalog_part = db_session.get(InventoryCatalogPart, part["catalog_part_id"])
    assert catalog_part is not None
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 3


def test_task_writeoff_idempotency_key_has_one_movement_decrement_comment_and_audit(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    mechanic = _user(db_session, "mechanic", "idempotent-writeoff", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=mechanic,
        owner=mechanic,
        issue_key="RP-IDEMPOTENT",
        park_id=seed_park_with_tracker.id,
    )
    monkeypatch.setattr(inventory_svc.platform_settings, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        inventory_svc.tracker_cache,
        "get_issue",
        lambda **kwargs: {
            "key": "RP-IDEMPOTENT",
            "tags": [seed_park_with_tracker.tag],
            "assignee": {"login": mechanic.tracker_login},
        },
    )
    comments = []
    monkeypatch.setattr(
        inventory_svc.tracker_client, "add_comment", lambda **kwargs: comments.append(kwargs)
    )
    payload = {"part_id": part["id"], "quantity": 2, "idempotency_key": "retry-42"}

    first = client.post("/inventory/tasks/RP-IDEMPOTENT/writeoff", json=payload)
    retried = client.post("/inventory/tasks/RP-IDEMPOTENT/writeoff", json=payload)

    assert first.status_code == retried.status_code == 201
    assert first.json()["id"] == retried.json()["id"]
    db_session.expire_all()
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 3
    assert (
        db_session.scalar(
            select(func.count(InventoryMovement.id)).where(
                InventoryMovement.kind == "task_writeoff"
            )
        )
        == 1
    )
    assert len(comments) == 1
    assert (
        db_session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "tracker.comment", AuditLog.target_id == "RP-IDEMPOTENT"
            )
        )
        == 1
    )

    mismatch = client.post(
        "/inventory/tasks/RP-IDEMPOTENT/writeoff",
        json={"part_id": part["id"], "quantity": 1, "idempotency_key": "retry-42"},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "inventory_idempotency_conflict"
    assert len(comments) == 1


def test_task_writeoff_audit_failure_does_not_make_committed_operation_retryable(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    mechanic = _user(db_session, "mechanic", "writeoff-audit-failure", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=mechanic,
        owner=mechanic,
        issue_key="RP-AUDIT-FAIL",
        park_id=seed_park_with_tracker.id,
    )
    monkeypatch.setattr(inventory_svc.platform_settings, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        inventory_svc.tracker_cache,
        "get_issue",
        lambda **kwargs: {
            "key": "RP-AUDIT-FAIL",
            "tags": [seed_park_with_tracker.tag],
            "assignee": {"login": mechanic.tracker_login},
        },
    )
    comments = []
    monkeypatch.setattr(
        inventory_svc.tracker_client, "add_comment", lambda **kwargs: comments.append(kwargs)
    )
    monkeypatch.setattr(
        inventory_svc.audit,
        "record",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit")),
    )
    payload = {"part_id": part["id"], "quantity": 2, "idempotency_key": "audit-failure"}

    first = client.post("/inventory/tasks/RP-AUDIT-FAIL/writeoff", json=payload)
    retried = client.post("/inventory/tasks/RP-AUDIT-FAIL/writeoff", json=payload)

    assert first.status_code == retried.status_code == 201
    assert first.json()["id"] == retried.json()["id"]
    db_session.expire_all()
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 3
    assert len(comments) == 1


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
        "/inventory/tasks/RP-43/writeoff",
        json={"part_id": part["id"], "quantity": 6, "idempotency_key": "insufficient"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["current_quantity"] == 5
    assert comments == []


def test_task_writeoff_version_overflow_rejects_before_tracker_comment(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    mechanic = _user(db_session, "mechanic", "overflow-writeoff", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=mechanic,
        owner=mechanic,
        issue_key="RP-OVERFLOW",
        park_id=seed_park_with_tracker.id,
    )
    stock = db_session.scalar(select(InventoryParkStock))
    stock.quantity = 1
    stock.version = 2**63 - 1
    db_session.commit()
    movement_ids = list(db_session.scalars(select(InventoryMovement.id)))
    audit_ids = list(db_session.scalars(select(AuditLog.id)))
    monkeypatch.setattr(inventory_svc.platform_settings, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        inventory_svc.tracker_cache,
        "get_issue",
        lambda **kwargs: {
            "key": "RP-OVERFLOW",
            "tags": [seed_park_with_tracker.tag],
            "assignee": {"login": mechanic.tracker_login},
        },
    )
    comments = []
    monkeypatch.setattr(
        inventory_svc.tracker_client, "add_comment", lambda **kwargs: comments.append(kwargs)
    )

    response = client.post(
        "/inventory/tasks/RP-OVERFLOW/writeoff",
        json={"part_id": part["id"], "quantity": 1, "idempotency_key": "overflow"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "inventory_version_overflow"
    db_session.expire_all()
    assert (stock.quantity, stock.version) == (1, 2**63 - 1)
    assert list(db_session.scalars(select(InventoryMovement.id))) == movement_ids
    assert list(db_session.scalars(select(AuditLog.id))) == audit_ids
    assert comments == []


@pytest.mark.parametrize("tracker_fails", [False, True], ids=["success", "tracker-failure"])
def test_task_writeoff_flushes_before_tracker_and_commits_only_after_success(
    client, db_session, db_engine, seed_park_with_tracker, monkeypatch, tracker_fails
):
    mechanic = _user(db_session, "mechanic", "transaction-writeoff", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=mechanic,
        owner=mechanic,
        issue_key="RP-TRANSACTION",
        park_id=seed_park_with_tracker.id,
    )
    stock = db_session.scalar(select(InventoryParkStock))
    stock.quantity = 1
    stock.version = 1
    db_session.commit()
    movement_ids = list(db_session.scalars(select(InventoryMovement.id)))
    audit_ids = list(db_session.scalars(select(AuditLog.id)))
    stock_state = select(InventoryParkStock.quantity, InventoryParkStock.version)
    task_movement = select(
        InventoryMovement.catalog_part_id, InventoryMovement.delta, InventoryMovement.balance_after
    ).where(InventoryMovement.issue_key == "RP-TRANSACTION")
    monkeypatch.setattr(
        inventory_svc.platform_settings, "get_tracker_token", lambda db: "bot-token"
    )
    monkeypatch.setattr(
        inventory_svc.tracker_cache,
        "get_issue",
        lambda **kwargs: {
            "key": "RP-TRANSACTION",
            "tags": [seed_park_with_tracker.tag],
            "assignee": {"login": mechanic.tracker_login},
        },
    )
    comments = []

    def add_comment(**kwargs):
        comments.append(kwargs)
        # Raw connection reads cannot autoflush pending ORM changes for the service.
        assert db_session.connection().execute(stock_state).one() == (0, 2)
        assert db_session.connection().execute(task_movement).one() == (
            part["catalog_part_id"],
            -1,
            0,
        )
        # A separate reader must still see the old committed inventory until Tracker succeeds.
        with db_engine.connect() as reader:
            assert reader.execute(stock_state).one() == (1, 1)
            assert reader.execute(task_movement).all() == []
            assert list(reader.scalars(select(AuditLog.id))) == audit_ids
        if tracker_fails:
            raise inventory_svc.tracker_client.TrackerError("tracker unavailable")

    monkeypatch.setattr(inventory_svc.tracker_client, "add_comment", add_comment)

    response = client.post(
        "/inventory/tasks/RP-TRANSACTION/writeoff",
        json={
            "part_id": part["id"],
            "quantity": 1,
            "idempotency_key": f"transaction-{tracker_fails}",
        },
    )

    assert response.status_code == (502 if tracker_fails else 201), response.text
    assert len(comments) == 1
    assert comments[0]["token"] == "bot-token"
    assert comments[0]["key"] == "RP-TRANSACTION"
    assert "Техническое сообщение · Склад" in comments[0]["text"]
    assert "Инициатор: transaction-writeoff" in comments[0]["text"]
    db_session.expire_all()
    assert (stock.quantity, stock.version) == ((1, 1) if tracker_fails else (0, 2))
    with db_engine.connect() as reader:
        assert reader.execute(stock_state).one() == ((1, 1) if tracker_fails else (0, 2))
        if tracker_fails:
            assert response.json()["detail"] == "tracker_upstream_error"
            assert list(reader.scalars(select(InventoryMovement.id))) == movement_ids
            assert list(reader.scalars(select(AuditLog.id))) == audit_ids
        else:
            assert reader.execute(task_movement).one() == (part["catalog_part_id"], -1, 0)
            assert reader.execute(
                select(AuditLog.action, AuditLog.actor_user_id, AuditLog.park_id).where(
                    AuditLog.target_id == "RP-TRANSACTION",
                    AuditLog.action == "tracker.comment",
                )
            ).one() == ("tracker.comment", mechanic.id, seed_park_with_tracker.id)


def test_legacy_movement_requires_park_when_global_part_has_multiple_accessible_stocks(
    client, db_session, seed_park_with_tracker
):
    other = Park(name="Other", tag="Other", is_active=True)
    db_session.add(other)
    db_session.commit()
    operator = _user(db_session, "operator", "ambiguous-operator")
    login_as(client, operator.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    db_session.add(
        InventoryParkStock(
            park_id=other.id,
            catalog_part_id=part["catalog_part_id"],
            quantity=9,
            updated_by=operator.id,
        )
    )
    db_session.commit()

    ambiguous_update = client.patch(f"/inventory/parts/{part['id']}", json={"location": "C"})
    assert ambiguous_update.status_code == 409
    explicit_update = client.patch(
        f"/inventory/parts/{part['id']}",
        json={"park_id": seed_park_with_tracker.id, "location": "C"},
    )
    assert explicit_update.status_code == 200, explicit_update.text
    assert explicit_update.json()["park_id"] == seed_park_with_tracker.id
    assert explicit_update.json()["location"] == "C"

    ambiguous = client.post(
        f"/inventory/parts/{part['id']}/movements",
        json={"kind": "receipt", "quantity": 1},
    )
    assert ambiguous.status_code == 409
    assert ambiguous.json()["detail"] == {
        "code": "inventory_park_required",
        "park_ids": [seed_park_with_tracker.id, other.id],
    }

    explicit = client.post(
        f"/inventory/parts/{part['id']}/movements",
        json={"park_id": other.id, "kind": "receipt", "quantity": 1},
    )
    assert explicit.status_code == 201, explicit.text
    quantities = dict(
        db_session.execute(
            select(InventoryParkStock.park_id, InventoryParkStock.quantity).where(
                InventoryParkStock.catalog_part_id == part["catalog_part_id"]
            )
        ).all()
    )
    assert quantities == {seed_park_with_tracker.id: 5, other.id: 10}


def test_task_writeoff_derives_claim_park_for_shared_global_part(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    other = Park(name="Other", tag="Other", is_active=True)
    db_session.add(other)
    db_session.commit()
    mechanic = _user(db_session, "mechanic", "two-park-mechanic", [seed_park_with_tracker, other])
    monkeypatch.setattr(inventory_svc.platform_settings, "get_tracker_token", lambda db: "token")
    issue = {"key": "RP-44", "tags": [other.tag]}
    monkeypatch.setattr(inventory_svc.tracker_cache, "get_issue", lambda **kwargs: issue)
    monkeypatch.setattr(inventory_svc.tracker_client, "add_comment", lambda **kwargs: None)
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    db_session.add(
        InventoryParkStock(
            park_id=other.id,
            catalog_part_id=part["catalog_part_id"],
            quantity=7,
            updated_by=mechanic.id,
        )
    )
    db_session.commit()
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=mechanic,
        owner=mechanic,
        issue_key="RP-44",
        park_id=other.id,
    )

    response = client.post(
        "/inventory/tasks/RP-44/writeoff",
        json={"part_id": part["id"], "quantity": 2, "idempotency_key": "claim-park"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["park_id"] == other.id
    quantities = dict(
        db_session.execute(
            select(InventoryParkStock.park_id, InventoryParkStock.quantity).where(
                InventoryParkStock.catalog_part_id == part["catalog_part_id"]
            )
        ).all()
    )
    assert quantities == {seed_park_with_tracker.id: 5, other.id: 5}


def test_legacy_create_part_rolls_back_catalog_and_photo_on_late_failure(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    mechanic = _user(db_session, "mechanic", "rollback-mechanic", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    component = client.post(
        "/inventory/components", data={"park_id": seed_park_with_tracker.id, "name": "Wheel"}
    ).json()
    monkeypatch.setattr(
        inventory_svc.inventory_stock,
        "apply_stock_delta",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("late_failure")),
    )
    image = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    existing_files = set(inventory_svc.photos_root().iterdir())

    response = client.post(
        "/inventory/parts",
        data={
            "park_id": seed_park_with_tracker.id,
            "component_id": component["id"],
            "name": "Disk",
            "article": "ROLLBACK-1",
            "quantity": 1,
            "minimum_quantity": 0,
            "location": "A",
        },
        files={"photo": ("disk.png", image, "image/png")},
    )

    assert response.status_code == 502
    assert (
        db_session.scalar(
            select(InventoryCatalogPart).where(
                InventoryCatalogPart.normalized_article == "rollback-1"
            )
        )
        is None
    )
    assert set(inventory_svc.photos_root().iterdir()) == existing_files


def test_legacy_overview_keeps_component_without_parts(client, db_session, seed_park_with_tracker):
    mechanic = _user(db_session, "mechanic", "empty-component-mechanic", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    component = client.post(
        "/inventory/components",
        data={"park_id": seed_park_with_tracker.id, "name": "Empty"},
    ).json()

    overview = client.get(f"/inventory?park_id={seed_park_with_tracker.id}")

    assert overview.status_code == 200
    assert overview.json()["components"] == [
        {
            "id": component["id"],
            "park_id": seed_park_with_tracker.id,
            "name": "Empty",
            "has_photo": False,
            "parts": [],
        }
    ]


def test_legacy_adapter_disambiguates_catalog_id_from_colliding_legacy_id(
    client, db_session, seed_park_with_tracker, monkeypatch, tmp_path
):
    operator = _user(db_session, "operator", "collision-operator")
    legacy_component = InventoryComponent(
        id=50,
        park_id=seed_park_with_tracker.id,
        name="Legacy component",
    )
    catalog_component = InventoryCatalogComponent(
        id=60,
        name="Catalog component",
        normalized_name="catalog component",
        created_by=operator.id,
        updated_by=operator.id,
    )
    db_session.add_all([legacy_component, catalog_component])
    db_session.flush()
    direct_catalog = InventoryCatalogPart(
        id=1,
        component_id=catalog_component.id,
        name="Global one",
        normalized_name="global one",
        article="GLOBAL-1",
        normalized_article="global-1",
        photo_storage_key="global-photo",
        photo_content_type="image/png",
        created_by=operator.id,
        updated_by=operator.id,
    )
    migrated_catalog = InventoryCatalogPart(
        id=2,
        component_id=catalog_component.id,
        name="Migrated legacy",
        normalized_name="migrated legacy",
        article="LEGACY-1",
        normalized_article="legacy-1",
        photo_storage_key="legacy-photo",
        photo_content_type="image/jpeg",
        created_by=operator.id,
        updated_by=operator.id,
    )
    legacy_part = InventoryPart(
        id=1,
        park_id=seed_park_with_tracker.id,
        component_id=legacy_component.id,
        name="Migrated legacy",
        article="LEGACY-1",
        quantity=7,
        minimum_quantity=0,
        location="L",
    )
    db_session.add_all([direct_catalog, migrated_catalog, legacy_part])
    db_session.flush()
    db_session.add_all(
        [
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=direct_catalog.id,
                quantity=5,
                updated_by=operator.id,
            ),
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=migrated_catalog.id,
                quantity=7,
                updated_by=operator.id,
            ),
        ]
    )
    db_session.commit()
    login_as(client, operator.username, "secret")
    global_photo = tmp_path / "global.png"
    global_photo.write_bytes(b"global")
    legacy_photo = tmp_path / "legacy.jpg"
    legacy_photo.write_bytes(b"legacy")
    monkeypatch.setattr(
        inventory_svc,
        "photo_path",
        lambda key: {"global-photo": global_photo, "legacy-photo": legacy_photo}[key],
    )

    overview = client.get(f"/inventory?park_id={seed_park_with_tracker.id}")
    assert overview.status_code == 200
    represented = {
        part["article"]: (part["id"], part["catalog_part_id"])
        for component in overview.json()["components"]
        for part in component["parts"]
    }
    assert represented == {"GLOBAL-1": (-1, 1), "LEGACY-1": (-2, 2)}
    assert len({adapter_id for adapter_id, _ in represented.values()}) == 2

    global_move = client.post(
        "/inventory/parts/-1/movements",
        json={
            "park_id": seed_park_with_tracker.id,
            "kind": "receipt",
            "quantity": 1,
        },
    )
    assert global_move.status_code == 201, global_move.text
    assert global_move.json()["part_id"] == -direct_catalog.id
    assert global_move.json()["catalog_part_id"] == direct_catalog.id

    migrated_update = client.patch(
        "/inventory/parts/1",
        json={"park_id": seed_park_with_tracker.id, "location": "M"},
    )
    assert migrated_update.status_code == 200, migrated_update.text
    assert migrated_update.json()["id"] == -migrated_catalog.id
    assert migrated_update.json()["catalog_part_id"] == migrated_catalog.id

    assert client.get("/inventory/parts/-1/photo").content == b"global"
    assert client.get("/inventory/parts/1/photo").content == b"legacy"
    legacy_photo_response = client.get("/inventory/parts/1/photo")
    assert legacy_photo_response.status_code == 200
    assert legacy_photo_response.content == b"legacy"

    legacy_move = client.post(
        "/inventory/parts/1/movements",
        json={"kind": "receipt", "quantity": 1},
    )
    assert legacy_move.status_code == 201, legacy_move.text
    assert legacy_move.json()["part_id"] == -migrated_catalog.id
    assert legacy_move.json()["catalog_part_id"] == migrated_catalog.id
    quantities = dict(
        db_session.execute(
            select(InventoryParkStock.catalog_part_id, InventoryParkStock.quantity)
        ).all()
    )
    assert quantities == {direct_catalog.id: 6, migrated_catalog.id: 8}


def test_legacy_mixed_patch_rolls_back_catalog_when_park_fields_are_invalid(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "mixed-patch-admin")
    login_as(client, admin.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    audit_count = len(
        list(db_session.scalars(select(AuditLog).where(AuditLog.action.like("inventory.%"))))
    )

    response = client.patch(
        f"/inventory/parts/{part['id']}",
        json={"name": "Must roll back", "location": "   "},
    )

    assert response.status_code == 400
    db_session.expire_all()
    catalog = db_session.get(InventoryCatalogPart, part["catalog_part_id"])
    stock = db_session.scalar(select(InventoryParkStock))
    assert catalog.name == "Тяга"
    assert stock.location == "Стеллаж A / полка 2"
    assert (
        len(list(db_session.scalars(select(AuditLog).where(AuditLog.action.like("inventory.%")))))
        == audit_count
    )
