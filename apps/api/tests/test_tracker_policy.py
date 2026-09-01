from conftest import role_id_for
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings, rbac
from robopark_api.services.tracker_policy import (
    allowed_queues_for_user,
    can_view_untagged,
    can_write_tracker,
)


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


def test_operator_denied_tracker_write_cannot_write(db_session, seed_park_with_tracker):
    operator = User(
        username="op-nowrite",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    operator = rbac.load_user_with_role(db_session, operator.id)
    assert operator is not None
    denied = sorted(
        rbac.role_permission_keys(db_session, operator) - {rbac.PERMISSION_TRACKER_WRITE}
    )
    rbac.set_user_effective_permissions(db_session, operator, denied)
    db_session.commit()

    operator = rbac.load_user_with_role(db_session, operator.id)
    assert operator is not None
    assert can_write_tracker(db_session, operator) is False


def test_default_operator_can_write_tracker(db_session, seed_park_with_tracker):
    operator = User(
        username="op-write",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    operator = rbac.load_user_with_role(db_session, operator.id)
    assert operator is not None
    assert can_write_tracker(db_session, operator) is True


def test_mechanic_with_tracker_write_blocked_by_flag(db_session, seed_mechanic):
    mechanic = rbac.load_user_with_role(db_session, seed_mechanic.id)
    assert mechanic is not None
    granted = sorted(
        rbac.role_permission_keys(db_session, mechanic) | {rbac.PERMISSION_TRACKER_WRITE}
    )
    rbac.set_user_effective_permissions(db_session, mechanic, granted)
    platform_settings.set_bool_setting(
        db_session,
        platform_settings.TRACKER_MECHANIC_WRITE_KEY,
        False,
    )
    db_session.commit()

    mechanic = rbac.load_user_with_role(db_session, mechanic.id)
    assert mechanic is not None
    assert can_write_tracker(db_session, mechanic) is False
