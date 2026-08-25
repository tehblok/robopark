import pytest
from fastapi import HTTPException

from robopark_api.deps import get_operator_parks, require_operator_park
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password


def _approved_operator(db, username: str) -> User:
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_get_operator_parks_ordered(db_session):
    op = _approved_operator(db_session, "op-deps-1")
    p_b = Park(name="B", tag="B", is_active=True)
    p_a = Park(name="A", tag="A", is_active=True)
    db_session.add_all([p_b, p_a])
    db_session.flush()
    # assign in reverse id order; helper must return by Park.id ascending
    db_session.add_all(
        [
            UserPark(user_id=op.id, park_id=p_b.id),
            UserPark(user_id=op.id, park_id=p_a.id),
        ]
    )
    db_session.commit()

    parks = get_operator_parks(db_session, op)
    assert [p.id for p in parks] == sorted(p.id for p in parks)
    assert {p.tag for p in parks} == {"A", "B"}


def test_require_operator_park_forbidden_when_not_assigned(db_session):
    op = _approved_operator(db_session, "op-deps-2")
    other = Park(name="X", tag="X", is_active=True)
    db_session.add(other)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        require_operator_park(other.id, db_session, op)
    assert exc.value.status_code == 403


def test_require_operator_park_not_found(db_session):
    op = _approved_operator(db_session, "op-deps-3")
    with pytest.raises(HTTPException) as exc:
        require_operator_park(99999, db_session, op)
    assert exc.value.status_code == 404


def test_require_operator_park_ok(db_session):
    op = _approved_operator(db_session, "op-deps-4")
    park = Park(name="Y", tag="Y", is_active=True)
    db_session.add(park)
    db_session.flush()
    db_session.add(UserPark(user_id=op.id, park_id=park.id))
    db_session.commit()
    assert require_operator_park(park.id, db_session, op).id == park.id
