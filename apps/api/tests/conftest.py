import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from robopark_api import main
from robopark_api.config import Settings, get_settings, reset_settings_cache
from robopark_api.db import get_db
from robopark_api.models import AccessStatus, Base, Park, User, UserPark
from robopark_api.security import hash_password

#: Password satisfying the default policy (length + character classes).
#: Fixtures hash passwords directly, so only tests going through the API need it.
VALID_PASSWORD = "Str0ng-Pass!2026"


def login_as(client: TestClient, username: str, password: str):
    return client.post("/auth/login", json={"username": username, "password": password})


@pytest.fixture(autouse=True)
def ignore_local_env_file(monkeypatch):
    """A developer's apps/api/.env must never change a test outcome.

    Production `Settings` still reads it; only the suite opts out, so that
    `monkeypatch.delenv` and explicit keyword arguments stay authoritative.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """`get_settings()` is process-wide cached; isolate it between tests."""
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def sqlite_database_url(tmp_path):
    return f"sqlite:///{tmp_path / 'alembic.db'}"


@pytest.fixture
def db_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(db_engine):
    with Session(db_engine) as session:
        yield session


@pytest.fixture
def test_settings(db_engine):
    return Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        seed_username=None,
        seed_password=None,
    )


@pytest.fixture
def client(db_engine, db_session, test_settings, monkeypatch):
    # create_app's lifespan seeds through main.SessionLocal, which is bound to
    # the real DATABASE_URL. Redirect it at the tmp database before building
    # the app so entering TestClient cannot touch a developer's database.
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)

    app = main.create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: test_settings

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seed_royal(db_session):
    user = User(
        username="royal",
        password_hash=hash_password("secret"),
        role="royal",
        access_status="approved",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def seed_pending_operator(db_session):
    user = User(
        username="operator",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="pending",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def seed_park_with_tracker(db_session):
    park = Park(
        name="Alpha",
        tag="Alpha",
        is_active=True,
        tracker_queue="ROBOPARK",
        feature_blockers=True,
    )
    db_session.add(park)
    db_session.commit()
    db_session.refresh(park)
    return park


@pytest.fixture
def seed_mechanic(db_session, seed_park_with_tracker):
    user = User(
        username="mech1",
        password_hash=hash_password("secret"),
        role="mechanic",
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    db_session.refresh(user)
    return user
