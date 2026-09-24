import json
from datetime import UTC, datetime

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
from robopark_api.task_workflow_models import ReliableAction, TaskMessage


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


def test_task_writeoff_requires_owner_and_queues_business_tracker_comment(
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
    assert comments == []
    action = db_session.scalar(
        select(ReliableAction).where(
            ReliableAction.resource_id == "RP-42", ReliableAction.action == "comment"
        )
    )
    assert action is not None and action.state == "pending"
    assert "TY-001" in json.loads(action.payload_json)["text"]
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


def test_task_writeoff_requires_active_claim_before_persisting_stock_effects(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    mechanic = _user(db_session, "mechanic", "pending-writeoff", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    from robopark_api.services.tracker_claims import claim_issue

    claim = claim_issue(
        db_session,
        actor=mechanic,
        owner=mechanic,
        issue_key="RP-PENDING-WRITEOFF",
        park_id=seed_park_with_tracker.id,
        state="pending",
    )
    db_session.commit()
    monkeypatch.setattr(inventory_svc.platform_settings, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        inventory_svc.tracker_cache,
        "get_issue",
        lambda **kwargs: {
            "key": "RP-PENDING-WRITEOFF",
            "tags": [seed_park_with_tracker.tag],
        },
    )
    comments = []
    monkeypatch.setattr(
        inventory_svc.tracker_client, "add_comment", lambda **kwargs: comments.append(kwargs)
    )
    payload = {"part_id": part["id"], "quantity": 2, "idempotency_key": "pending-writeoff"}

    pending = client.post("/inventory/tasks/RP-PENDING-WRITEOFF/writeoff", json=payload)

    assert pending.status_code == 409
    assert pending.json()["detail"]["code"] == "tracker_issue_claim_not_active"
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 5
    assert db_session.query(InventoryMovement).filter_by(kind="task_writeoff").count() == 0
    assert comments == []

    claim.state = "active"
    db_session.commit()
    active = client.post("/inventory/tasks/RP-PENDING-WRITEOFF/writeoff", json=payload)
    assert active.status_code == 201, active.text
    assert active.json()["balance_after"] == 3


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
    assert comments == []
    assert (
        db_session.scalar(
            select(func.count(ReliableAction.id)).where(
                ReliableAction.resource_id == "RP-IDEMPOTENT", ReliableAction.action == "comment"
            )
        )
        == 1
    )
    assert any(
        '"issue_key": "RP-IDEMPOTENT"' in (row.detail or "")
        for row in db_session.scalars(
            select(AuditLog).where(AuditLog.action == "inventory.stock.moved")
        )
    )

    mismatch = client.post(
        "/inventory/tasks/RP-IDEMPOTENT/writeoff",
        json={"part_id": part["id"], "quantity": 1, "idempotency_key": "retry-42"},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "inventory_idempotency_conflict"
    assert comments == []


@pytest.mark.parametrize("action_state", ["pending", "succeeded"])
def test_writeoff_race_replays_movement_after_stock_lock_without_duplicate_events(
    client, db_session, seed_park_with_tracker, monkeypatch, action_state
):
    mechanic = _user(db_session, "mechanic", "race-writeoff", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=mechanic,
        owner=mechanic,
        issue_key="RP-RACE-WRITEOFF",
        park_id=seed_park_with_tracker.id,
    )
    monkeypatch.setattr(inventory_svc.platform_settings, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        inventory_svc.tracker_cache,
        "get_issue",
        lambda **_kwargs: {"key": "RP-RACE-WRITEOFF", "tags": [seed_park_with_tracker.tag]},
    )
    payload = {"part_id": part["id"], "quantity": 1, "idempotency_key": "race-receipt-51"}
    first = client.post("/inventory/tasks/RP-RACE-WRITEOFF/writeoff", json=payload)
    assert first.status_code == 201
    action = db_session.scalar(
        select(ReliableAction).where(ReliableAction.resource_id == "RP-RACE-WRITEOFF")
    )
    if action_state == "succeeded":
        action.state = "succeeded"
        action.result_json = '{"external_id":"remote-1"}'
        db_session.commit()
    original_scalar = db_session.scalar
    skipped_initial_lookup = False

    def stale_initial_lookup(statement, *args, **kwargs):
        nonlocal skipped_initial_lookup
        if not skipped_initial_lookup and "inventory_movements.idempotency_key" in str(statement):
            skipped_initial_lookup = True
            return None
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", stale_initial_lookup)
    second = client.post("/inventory/tasks/RP-RACE-WRITEOFF/writeoff", json=payload)
    monkeypatch.setattr(db_session, "scalar", original_scalar)

    assert skipped_initial_lookup
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 4
    assert db_session.query(InventoryMovement).filter_by(issue_key="RP-RACE-WRITEOFF").count() == 1
    assert db_session.query(ReliableAction).filter_by(resource_id="RP-RACE-WRITEOFF").count() == 1
    assert db_session.query(TaskMessage).filter_by(issue_key="RP-RACE-WRITEOFF").count() == 1
    assert (
        sum(
            '"issue_key": "RP-RACE-WRITEOFF"' in (row.detail or "")
            for row in db_session.scalars(
                select(AuditLog).where(AuditLog.action == "inventory.stock.moved")
            )
        )
        == 1
    )


def test_task_writeoff_commits_stock_receipt_and_outbox_comment_before_delivery(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    mechanic = _user(db_session, "mechanic", "durable-writeoff", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=mechanic,
        owner=mechanic,
        issue_key="RP-DURABLE",
        park_id=seed_park_with_tracker.id,
    )
    monkeypatch.setattr(inventory_svc.platform_settings, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        inventory_svc.tracker_cache,
        "get_issue",
        lambda **_kwargs: {"key": "RP-DURABLE", "tags": [seed_park_with_tracker.tag]},
    )
    monkeypatch.setattr(
        inventory_svc.tracker_client,
        "add_comment",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("request sent Tracker comment")),
    )

    response = client.post(
        "/inventory/tasks/RP-DURABLE/writeoff",
        json={"part_id": part["id"], "quantity": 2, "idempotency_key": "durable-42"},
    )

    assert response.status_code == 201
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 3
    movement = db_session.scalar(
        select(InventoryMovement).where(InventoryMovement.idempotency_key == "durable-42")
    )
    action = db_session.scalar(
        select(ReliableAction).where(
            ReliableAction.resource_id == "RP-DURABLE", ReliableAction.action == "comment"
        )
    )
    assert movement is not None
    assert action is not None and action.state == "pending"
    assert action.idempotency_key == f"inventory-writeoff:{movement.id}"
    message = db_session.scalar(select(TaskMessage).where(TaskMessage.action_id == action.id))
    assert message is not None and "2" in message.text


def test_writeoff_outbox_reconciles_remote_comment_after_response_loss(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    mechanic = _user(db_session, "mechanic", "writeoff-replay", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    from robopark_api.services import tracker_outbox
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=mechanic,
        owner=mechanic,
        issue_key="RP-REPLAY",
        park_id=seed_park_with_tracker.id,
    )
    monkeypatch.setattr(inventory_svc.platform_settings, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        inventory_svc.tracker_cache,
        "get_issue",
        lambda **_kwargs: {"key": "RP-REPLAY", "tags": [seed_park_with_tracker.tag]},
    )
    response = client.post(
        "/inventory/tasks/RP-REPLAY/writeoff",
        json={"part_id": part["id"], "quantity": 1, "idempotency_key": "replay-42"},
    )
    assert response.status_code == 201
    action = db_session.scalar(
        select(ReliableAction).where(
            ReliableAction.resource_id == "RP-REPLAY", ReliableAction.action == "comment"
        )
    )
    remote_comments = []
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda _db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **_kwargs: {"key": "RP-REPLAY", "tags": [seed_park_with_tracker.tag]},
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client, "list_comments", lambda **_kwargs: remote_comments
    )

    def accept_comment(**kwargs):
        assert "surp-action:" not in kwargs["text"]
        assert action.id not in kwargs["text"]
        remote_comments.append(
            {
                "id": "remote-1",
                "text": kwargs["text"],
                "created_at": datetime.fromtimestamp(action.created_at + 1, UTC).isoformat(),
            }
        )
        raise tracker_outbox.tracker_client.TrackerError("connection lost after acceptance")

    monkeypatch.setattr(tracker_outbox.tracker_client, "add_comment", accept_comment)
    with pytest.raises(tracker_outbox.DeliveryError) as uncertain:
        tracker_outbox._deliver_action(db_session, action)
    accepted = remote_comments.pop()
    action.error_code = "tracker_comment_unconfirmed"
    with pytest.raises(tracker_outbox.DeliveryError) as still_uncertain:
        tracker_outbox._deliver_action(db_session, action)
    assert still_uncertain.value.code == "tracker_comment_unconfirmed"
    assert json.loads(action.payload_json)["tracker_text"] == accepted["text"]
    remote_comments.append(accepted)
    remote_comments.append({**accepted, "id": "remote-2"})
    with pytest.raises(tracker_outbox.DeliveryError) as ambiguous:
        tracker_outbox._deliver_action(db_session, action)
    assert ambiguous.value.code == "duplicate_remote_action"
    remote_comments.pop()
    second = tracker_outbox._deliver_action(db_session, action)

    assert uncertain.value.code == "tracker_comment_unconfirmed"
    assert second == {"external_id": "remote-1"}
    assert len(remote_comments) == 1


def test_task_writeoff_replay_does_not_depend_on_tracker_delivery(
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
    assert comments == []
    assert (
        db_session.scalar(
            select(func.count(ReliableAction.id)).where(
                ReliableAction.resource_id == "RP-AUDIT-FAIL", ReliableAction.action == "comment"
            )
        )
        == 1
    )


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


@pytest.mark.parametrize("staging_fails", [False, True], ids=["success", "outbox-failure"])
def test_task_writeoff_stock_receipt_and_outbox_commit_together(
    client, db_session, db_engine, seed_park_with_tracker, monkeypatch, staging_fails
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
    begin_action = inventory_svc.begin_action

    def stage_action(*args, **kwargs):
        assert db_session.connection().execute(stock_state).one() == (0, 2)
        assert db_session.connection().execute(task_movement).one() == (
            part["catalog_part_id"],
            -1,
            0,
        )
        with db_engine.connect() as reader:
            assert reader.execute(stock_state).one() == (1, 1)
            assert reader.execute(task_movement).all() == []
            assert list(reader.scalars(select(AuditLog.id))) == audit_ids
        if staging_fails:
            raise ValueError("outbox_stage_failed")
        return begin_action(*args, **kwargs)

    monkeypatch.setattr(inventory_svc, "begin_action", stage_action)
    monkeypatch.setattr(
        inventory_svc.tracker_client,
        "add_comment",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("request sent Tracker comment")),
    )

    response = client.post(
        "/inventory/tasks/RP-TRANSACTION/writeoff",
        json={
            "part_id": part["id"],
            "quantity": 1,
            "idempotency_key": f"transaction-{staging_fails}",
        },
    )

    assert response.status_code == (400 if staging_fails else 201), response.text
    db_session.expire_all()
    assert (stock.quantity, stock.version) == ((1, 1) if staging_fails else (0, 2))
    with db_engine.connect() as reader:
        assert reader.execute(stock_state).one() == ((1, 1) if staging_fails else (0, 2))
        if staging_fails:
            assert response.json()["detail"] == "outbox_stage_failed"
            assert list(reader.scalars(select(InventoryMovement.id))) == movement_ids
            assert list(reader.scalars(select(AuditLog.id))) == audit_ids
            assert (
                reader.execute(
                    select(ReliableAction.id).where(ReliableAction.resource_id == "RP-TRANSACTION")
                ).all()
                == []
            )
        else:
            assert reader.execute(task_movement).one() == (part["catalog_part_id"], -1, 0)
            assert reader.execute(
                select(ReliableAction.action, ReliableAction.state).where(
                    ReliableAction.resource_id == "RP-TRANSACTION"
                )
            ).one() == ("comment", "pending")


def test_legacy_movement_requires_park_when_global_part_has_multiple_accessible_stocks(
    client, db_session, seed_park_with_tracker
):
    other = Park(name="Other", tag="Other", is_active=True)
    db_session.add(other)
    db_session.commit()
    mechanic = _user(db_session, "mechanic", "ambiguous-mechanic", [seed_park_with_tracker, other])
    login_as(client, mechanic.username, "secret")
    _, part = _seed_part(client, seed_park_with_tracker.id)
    db_session.add(
        InventoryParkStock(
            park_id=other.id,
            catalog_part_id=part["catalog_part_id"],
            quantity=9,
            updated_by=mechanic.id,
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
    mechanic = _user(db_session, "mechanic", "collision-mechanic", [seed_park_with_tracker])
    legacy_component = InventoryComponent(
        id=50,
        park_id=seed_park_with_tracker.id,
        name="Legacy component",
    )
    catalog_component = InventoryCatalogComponent(
        id=60,
        name="Catalog component",
        normalized_name="catalog component",
        created_by=mechanic.id,
        updated_by=mechanic.id,
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
        created_by=mechanic.id,
        updated_by=mechanic.id,
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
        created_by=mechanic.id,
        updated_by=mechanic.id,
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
                updated_by=mechanic.id,
            ),
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=migrated_catalog.id,
                quantity=7,
                updated_by=mechanic.id,
            ),
        ]
    )
    db_session.commit()
    login_as(client, mechanic.username, "secret")
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
