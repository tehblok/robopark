"""Unit tests for the Emergency VIN park-scope ACL."""

from unittest.mock import patch

from conftest import role_id_for
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.emergency_scope import robot_query_from_vin, vin_allowed_for_user


def _operator(db_session, *, parks: list[Park]) -> User:
    user = User(
        username="op-scope",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    for park in parks:
        db_session.add(UserPark(user_id=user.id, park_id=park.id))
    db_session.commit()
    db_session.refresh(user)
    return user


def test_robot_query_strips_yasadr_and_leading_zeros():
    assert robot_query_from_vin("YASADR00000000447") == "447"
    assert robot_query_from_vin("yasadr00000000001") == "1"
    assert robot_query_from_vin("447") == "447"
    # Fall back to a stable non-empty value: an all-zero VIN must not become "".
    assert robot_query_from_vin("YASADR00000000000") == "0"


def test_admin_vin_always_allowed(db_session, seed_royal):
    assert vin_allowed_for_user(db_session, seed_royal, "YASADR00000000447") is True


def test_operator_without_parks_denied(db_session):
    user = _operator(db_session, parks=[])
    assert vin_allowed_for_user(db_session, user, "YASADR00000000447") is False


def test_operator_without_tracker_token_denied(db_session, seed_park_with_tracker):
    user = _operator(db_session, parks=[seed_park_with_tracker])
    assert vin_allowed_for_user(db_session, user, "YASADR00000000447") is False


def test_operator_matching_park_ticket_allowed(db_session, seed_park_with_tracker):
    user = _operator(db_session, parks=[seed_park_with_tracker])
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "tok")
    issue = {
        "key": "R-1",
        "queue": "ROBOPARK",
        "summary": "[447] brake",
        "tags": [seed_park_with_tracker.tag],
    }
    with patch(
        "robopark_api.services.tracker_client.search_robot_tickets",
        return_value=[issue],
    ):
        assert vin_allowed_for_user(db_session, user, "YASADR00000000447") is True


def test_operator_foreign_park_ticket_denied(db_session, seed_park_with_tracker):
    other = Park(name="Other", tag="OtherPark", is_active=True, tracker_queue="ROBOPARK")
    db_session.add(other)
    db_session.commit()

    user = _operator(db_session, parks=[seed_park_with_tracker])
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "tok")
    issue = {
        "key": "R-9",
        "queue": "ROBOPARK",
        "summary": "[447] other",
        "tags": ["OtherPark"],
    }
    with patch(
        "robopark_api.services.tracker_client.search_robot_tickets",
        return_value=[issue],
    ):
        assert vin_allowed_for_user(db_session, user, "YASADR00000000447") is False


def test_operator_no_matching_ticket_denied(db_session, seed_park_with_tracker):
    user = _operator(db_session, parks=[seed_park_with_tracker])
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "tok")
    with patch(
        "robopark_api.services.tracker_client.search_robot_tickets",
        return_value=[],
    ):
        assert vin_allowed_for_user(db_session, user, "YASADR00000000447") is False
