from conftest import role_id_for
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services.tracker_signatures import (
    build_signature_context,
    filter_mechanic_visible_comments,
    filter_platform_comments,
    format_signed_comment,
    is_platform_signed_comment,
    resolve_mechanic_login,
    resolve_operator_login,
    staff_tracker_logins,
)


def test_format_signed_comment():
    text = format_signed_comment(
        body="Не работает крышка",
        park_name="Next",
        mechanic_login="mech.startrek",
        operator_login="operator1",
    )
    assert text == "Не работает крышка\nNext / mech.startrek / operator1"


def test_build_signature_context_for_operator(db_session, seed_park_with_tracker):
    operator = User(
        username="operator1",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    issue = {
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
        "assignee": {"login": "mech.startrek", "display": "Mechanic One"},
    }
    ctx = build_signature_context(db_session, operator, issue)
    assert ctx.park_name == "Alpha"
    assert ctx.park_id == seed_park_with_tracker.id
    assert ctx.mechanic_login == "mech.startrek"
    assert ctx.operator_login == "operator1"


def test_resolve_mechanic_login_from_robopark_user(db_session, seed_mechanic):
    seed_mechanic.tracker_login = "mech.startrek"
    db_session.commit()

    issue = {"assignee": {"login": "mech.startrek", "display": "Mechanic One"}}
    assert resolve_mechanic_login(db_session, issue, seed_mechanic) == "mech.startrek"


def test_resolve_operator_login_from_park(db_session, seed_park_with_tracker, seed_mechanic):
    operator = User(
        username="park.operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    assert (
        resolve_operator_login(db_session, seed_park_with_tracker, seed_mechanic) == "park.operator"
    )


def test_is_platform_signed_comment():
    signed = "Не работает крышка\nNext / mech1 / operator1"
    assert is_platform_signed_comment(signed) is True
    assert is_platform_signed_comment("plain comment") is False
    assert is_platform_signed_comment("[28.08.2026 11:30] – Alpha / mech1: hello") is True
    assert is_platform_signed_comment("line\n\n—\nРобопарк: footer") is True


def test_filter_platform_comments():
    comments = [
        {"id": "1", "text": "ok\nAlpha / mech1 / operator1"},
        {"id": "2", "text": "random chatter"},
        {"id": "3", "text": "legacy\n\n—\nРобопарк: note"},
    ]
    filtered = filter_platform_comments(comments)
    assert [item["id"] for item in filtered] == ["1", "3"]


def test_filter_mechanic_visible_comments_includes_staff(db_session, seed_park_with_tracker):
    operator = User(
        username="operator.staff",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
    )
    db_session.add(operator)
    db_session.commit()

    comments = [
        {"id": "1", "text": "signed\nAlpha / mech1 / operator1", "author_login": "mech1"},
        {"id": "2", "text": "note from operator", "author_login": "operator.staff"},
        {"id": "3", "text": "noise", "author_login": "random.person"},
    ]
    filtered = filter_mechanic_visible_comments(db_session, comments)
    assert [item["id"] for item in filtered] == ["1", "2"]
    assert "operator.staff" in staff_tracker_logins(db_session)
