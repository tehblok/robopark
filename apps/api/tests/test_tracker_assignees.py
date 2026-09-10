"""Tests for assignee suggestions and attachments."""

from conftest import role_id_for
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services.tracker_assignees import list_assignee_candidates
from robopark_api.services.tracker_client import issue_to_dict


def test_issue_to_dict_parses_attachments():
    issue = issue_to_dict(
        {
            "key": "ROBOPARK-9",
            "summary": "with files",
            "attachment": [
                {
                    "id": "1",
                    "name": "photo.jpg",
                    "size": 2048,
                    "self": "https://st-api.yandex-team.ru/v2/issues/ROBOPARK-9/attachments/1",
                    "mimetype": "image/jpeg",
                }
            ],
        }
    )
    assert len(issue["attachments"]) == 1
    assert issue["attachments"][0]["name"] == "photo.jpg"
    assert issue["attachments"][0]["size"] == 2048


def test_list_assignee_candidates_filters_by_park(db_session, seed_park_with_tracker):
    mechanic = User(
        username="mech-assignee",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status=AccessStatus.approved.value,
        tracker_login="mech.startrek",
        is_active=True,
    )
    db_session.add(mechanic)
    db_session.flush()
    db_session.add(UserPark(user_id=mechanic.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    operator = User(
        username="op-assignee",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    found = list_assignee_candidates(db_session, operator, "mech")
    assert len(found) == 1
    assert found[0]["login"] == "mech-assignee"


def test_list_assignee_candidates_does_not_require_tracker_login(
    db_session, seed_park_with_tracker
):
    mechanic = User(
        username="local-mechanic",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status=AccessStatus.approved.value,
        tracker_login=None,
        is_active=True,
    )
    db_session.add(mechanic)
    db_session.flush()
    db_session.add(UserPark(user_id=mechanic.id, park_id=seed_park_with_tracker.id))
    operator = User(
        username="local-operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    assert list_assignee_candidates(db_session, operator, "local-mech") == [
        {"login": "local-mechanic", "display": "local-mechanic", "source": "park"}
    ]


def test_tracker_users_endpoint(client, db_session, seed_park_with_tracker):
    from conftest import login_as

    mechanic = User(
        username="mech-api",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status=AccessStatus.approved.value,
        tracker_login="api.login",
        is_active=True,
    )
    db_session.add(mechanic)
    db_session.flush()
    db_session.add(UserPark(user_id=mechanic.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    operator = User(
        username="op-api",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    login_as(client, "op-api", "secret")
    response = client.get("/tracker/users?q=api")
    assert response.status_code == 200
    body = response.json()
    assert any(item["login"] == "mech-api" for item in body)


def test_change_password_clears_flag(client, db_session, seed_mechanic):
    from conftest import VALID_PASSWORD, login_as

    seed_mechanic.must_change_password = True
    db_session.commit()

    login_as(client, "mech1", "secret")
    assert client.get("/auth/me").json()["must_change_password"] is True

    response = client.post(
        "/auth/change-password",
        json={"current_password": "secret", "new_password": VALID_PASSWORD},
    )
    assert response.status_code == 204
    assert client.get("/auth/me").json()["must_change_password"] is False
