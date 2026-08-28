from conftest import role_id_for
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings
from robopark_api.services.tracker_policy import allowed_queues_for_user, can_view_untagged


def test_operator_queues_and_untagged_toggle(db_session, seed_park_with_tracker):
    operator = User(
        username="op1",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    assert allowed_queues_for_user(db_session, operator) == ["ROBOPARK"]
    assert can_view_untagged(db_session, operator) is True

    platform_settings.set_bool_setting(
        db_session,
        platform_settings.TRACKER_OPERATOR_UNTAGGED_KEY,
        False,
    )
    assert can_view_untagged(db_session, operator) is False
