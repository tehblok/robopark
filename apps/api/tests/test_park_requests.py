from fastapi.testclient import TestClient

from conftest import login_as
from robopark_api.models import Park, ParkRequest, User, UserPark
from robopark_api.security import hash_password


def add_operator(db_session, username: str, *, access_status: str = "approved") -> User:
    operator = User(
        username=username,
        password_hash=hash_password("secret"),
        role="operator",
        access_status=access_status,
        is_active=True,
    )
    db_session.add(operator)
    db_session.commit()
    db_session.refresh(operator)
    return operator


def add_park(db_session, tag: str, *, is_active: bool = True) -> Park:
    park = Park(name=f"Park {tag}", tag=tag, is_active=is_active)
    db_session.add(park)
    db_session.commit()
    db_session.refresh(park)
    return park


def test_operator_creates_and_lists_own_request(
    client: TestClient, db_session
):
    operator = add_operator(db_session, "operator-one")
    park = add_park(db_session, "one")
    login_as(client, operator.username, "secret")

    created = client.post("/operator/park-requests", json={"park_id": park.id})

    assert created.status_code == 201
    assert created.json()["user_id"] == operator.id
    assert created.json()["park_id"] == park.id
    assert created.json()["status"] == "pending"
    assert client.get("/operator/park-requests").json() == [created.json()]


def test_duplicate_pending_request_returns_conflict(
    client: TestClient, db_session
):
    operator = add_operator(db_session, "operator-two")
    park = add_park(db_session, "two")
    db_session.add(ParkRequest(user_id=operator.id, park_id=park.id, status="pending"))
    db_session.commit()
    login_as(client, operator.username, "secret")

    response = client.post("/operator/park-requests", json={"park_id": park.id})

    assert response.status_code == 409


def test_existing_park_member_cannot_request_it(client: TestClient, db_session):
    operator = add_operator(db_session, "operator-three")
    park = add_park(db_session, "three")
    db_session.add(UserPark(user_id=operator.id, park_id=park.id))
    db_session.commit()
    login_as(client, operator.username, "secret")

    response = client.post("/operator/park-requests", json={"park_id": park.id})

    assert response.status_code == 400
    assert [item["id"] for item in client.get("/operator/parks").json()] == [park.id]


def test_operator_lists_only_available_parks(client: TestClient, db_session):
    operator = add_operator(db_session, "operator-available")
    available = add_park(db_session, "available")
    assigned = add_park(db_session, "assigned")
    pending = add_park(db_session, "pending")
    add_park(db_session, "inactive", is_active=False)
    db_session.add(UserPark(user_id=operator.id, park_id=assigned.id))
    db_session.add(
        ParkRequest(user_id=operator.id, park_id=pending.id, status="pending")
    )
    db_session.commit()
    login_as(client, operator.username, "secret")

    response = client.get("/operator/available-parks")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [available.id]


def test_admin_approves_request_and_assigns_park(
    client: TestClient, db_session, seed_royal
):
    operator = add_operator(db_session, "operator-four")
    park = add_park(db_session, "four")
    request = ParkRequest(user_id=operator.id, park_id=park.id, status="pending")
    db_session.add(request)
    db_session.commit()
    db_session.refresh(request)
    login_as(client, "royal", "secret")

    inbox = client.get("/admin/park-requests")
    response = client.post(f"/admin/park-requests/{request.id}/approve")

    assert inbox.status_code == 200
    assert [item["id"] for item in inbox.json()] == [request.id]
    assert response.status_code == 204
    db_session.refresh(request)
    assert request.status == "approved"
    assert request.resolved_by == seed_royal.id
    assert request.resolved_at is not None
    assert db_session.get(UserPark, (operator.id, park.id)) is not None


def test_admin_rejects_request_without_assigning_park(
    client: TestClient, db_session, seed_royal
):
    operator = add_operator(db_session, "operator-five")
    park = add_park(db_session, "five")
    request = ParkRequest(user_id=operator.id, park_id=park.id, status="pending")
    db_session.add(request)
    db_session.commit()
    db_session.refresh(request)
    login_as(client, "royal", "secret")

    response = client.post(f"/admin/park-requests/{request.id}/reject")

    assert response.status_code == 204
    db_session.refresh(request)
    assert request.status == "rejected"
    assert request.resolved_by == seed_royal.id
    assert request.resolved_at is not None
    assert db_session.get(UserPark, (operator.id, park.id)) is None


def test_pending_operator_cannot_create_request(client: TestClient, db_session):
    operator = add_operator(db_session, "operator-six", access_status="pending")
    park = add_park(db_session, "six")
    login_as(client, operator.username, "secret")

    response = client.post("/operator/park-requests", json={"park_id": park.id})

    assert response.status_code == 403
