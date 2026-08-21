import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.main import create_app
from robopark_api.models import Base
from robopark_api.models import User
from robopark_api.security import hash_password


@pytest.fixture
def sqlite_database_url(tmp_path):
    return f"sqlite:///{tmp_path / 'alembic.db'}"


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(db_session):
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seed_royal(db_session):
    user = User(
        username="royal",
        password_hash=hash_password("secret"),
        role="royal",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user
