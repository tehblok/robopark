from conftest import role_id_for
from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services.gateway_policy import eligible_gateway_owner_ids, requester_can_gateway


def test_corporate_owner_login_defaults_to_service_username():
    from robopark_api.services.gateway_policy import corporate_owner_login

    user = User(username="Tehblokdan", tracker_login=None)
    assert corporate_owner_login(user) == "tehblokdan"
    user.tracker_login = " explicit.owner "
    assert corporate_owner_login(user) == "explicit.owner"
    user.tracker_login = "invalid@example.com"
    assert corporate_owner_login(user) is None
    user.tracker_login = None
    user.username = "invalid@example.com"
    assert corporate_owner_login(user) is None


def _user(db, username: str, role: str, *, status: str = "approved", active: bool = True):
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, role),
        access_status=status,
        is_active=active,
    )
    db.add(user)
    db.flush()
    return user


def test_operator_admin_and_royal_can_only_use_their_own_gateway(db_session):
    operator = _user(db_session, "gateway-operator", "operator")
    other_operator = _user(db_session, "gateway-other-operator", "operator")
    admin = _user(db_session, "gateway-admin", "admin")
    royal = _user(db_session, "gateway-royal", "royal")
    park = Park(name="Gateway shared", tag="Gateway-shared", is_active=True)
    db_session.add(park)
    db_session.flush()
    for user in (operator, other_operator, admin, royal):
        db_session.add(UserPark(user_id=user.id, park_id=park.id))
    db_session.commit()

    assert eligible_gateway_owner_ids(db_session, operator, "emergency") == frozenset({operator.id})
    assert eligible_gateway_owner_ids(db_session, admin, "emergency") == frozenset({admin.id})
    assert eligible_gateway_owner_ids(db_session, royal, "emergency") == frozenset({royal.id})


def test_mechanic_uses_only_approved_active_operators_in_a_shared_active_park(db_session):
    mechanic = _user(db_session, "gateway-mechanic", "mechanic")
    shared = _user(db_session, "gateway-shared-owner", "operator")
    other_park = _user(db_session, "gateway-other-park", "operator")
    pending = _user(
        db_session,
        "gateway-pending-owner",
        "operator",
        status=AccessStatus.pending.value,
    )
    inactive = _user(db_session, "gateway-inactive-owner", "operator", active=False)
    first = Park(name="Gateway first", tag="Gateway-first", is_active=True)
    second = Park(name="Gateway second", tag="Gateway-second", is_active=True)
    disabled = Park(name="Gateway disabled", tag="Gateway-disabled", is_active=False)
    db_session.add_all([first, second, disabled])
    db_session.flush()
    db_session.add_all(
        [
            UserPark(user_id=mechanic.id, park_id=first.id),
            UserPark(user_id=shared.id, park_id=first.id),
            UserPark(user_id=other_park.id, park_id=second.id),
            UserPark(user_id=pending.id, park_id=first.id),
            UserPark(user_id=inactive.id, park_id=first.id),
            UserPark(user_id=mechanic.id, park_id=disabled.id),
            UserPark(user_id=other_park.id, park_id=disabled.id),
        ]
    )
    db_session.commit()

    assert eligible_gateway_owner_ids(db_session, mechanic, "emergency") == frozenset({shared.id})
    assert eligible_gateway_owner_ids(db_session, mechanic, "inventory") == frozenset({shared.id})

    db_session.query(UserPark).filter_by(user_id=shared.id, park_id=first.id).delete()
    db_session.add(UserPark(user_id=other_park.id, park_id=first.id))
    db_session.commit()

    assert eligible_gateway_owner_ids(db_session, mechanic, "emergency") == frozenset(
        {other_park.id}
    )
    assert eligible_gateway_owner_ids(db_session, mechanic, "inventory") == frozenset(
        {other_park.id}
    )


def test_rejected_or_inactive_requester_has_no_gateway_authority(db_session):
    rejected = _user(
        db_session,
        "gateway-rejected-requester",
        "operator",
        status=AccessStatus.rejected.value,
    )
    inactive = _user(db_session, "gateway-inactive-requester", "admin", active=False)
    db_session.commit()

    assert eligible_gateway_owner_ids(db_session, rejected, "emergency") == frozenset()
    assert eligible_gateway_owner_ids(db_session, inactive, "emergency") == frozenset()
    assert requester_can_gateway(db_session, rejected, "emergency") is False
    assert requester_can_gateway(db_session, inactive, "emergency") is False
