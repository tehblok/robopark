from sqlalchemy import select

from conftest import role_id_for
from robopark_api.config import Settings
from robopark_api.dev_seed import DEV_ACCOUNTS, DEV_PARK_TAG, ensure_dev_seed
from robopark_api.models import Park, User, UserPark
from robopark_api.security import verify_password


def test_dev_seed_creates_demo_fixtures(db_session):
    ensure_dev_seed(db_session, Settings(_env_file=None, dev_seed=True))
    ensure_dev_seed(db_session, Settings(_env_file=None, dev_seed=True))

    users = {user.username: user for user in db_session.scalars(select(User)).all()}
    assert set(users) == {account.username for account in DEV_ACCOUNTS}

    royal = users["royal"]
    assert royal.role == "royal"
    assert verify_password("RoboparkRoyal!1", royal.password_hash)

    mechanic = users["mechanic"]
    assert mechanic.tracker_login == "mechanic.dev"
    assert mechanic.role == "mechanic"

    pending = users["operator_pending"]
    assert pending.access_status == "pending"

    park = db_session.scalar(select(Park).where(Park.tag == DEV_PARK_TAG))
    assert park is not None
    assert park.tracker_queue == "ROBOPARK"

    operator_links = db_session.scalars(
        select(UserPark).where(UserPark.user_id == users["operator"].id)
    ).all()
    assert len(operator_links) == 1
    assert operator_links[0].park_id == park.id


def test_dev_seed_skipped_when_disabled(db_session):
    ensure_dev_seed(db_session, Settings(_env_file=None, dev_seed=False))
    assert db_session.scalars(select(User)).all() == []


def test_dev_seed_does_not_overwrite_existing_user(db_session):
    existing = User(
        username="royal",
        password_hash="unchanged",
        role_id=role_id_for(db_session, "royal"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(existing)
    db_session.commit()

    ensure_dev_seed(db_session, Settings(_env_file=None, dev_seed=True))

    royal = db_session.scalar(select(User).where(User.username == "royal"))
    assert royal is not None
    assert royal.password_hash == "unchanged"
