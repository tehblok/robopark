from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from robopark_api.models import AuthSession, Base, User


def test_metadata_has_users_and_sessions():
    assert set(Base.metadata.tables) == {"users", "sessions"}


def test_models_match_required_schema():
    assert set(User.__table__.columns.keys()) == {
        "id",
        "username",
        "password_hash",
        "role",
        "is_active",
        "created_at",
    }
    assert set(AuthSession.__table__.columns.keys()) == {
        "id",
        "user_id",
        "token_hash",
        "expires_at",
        "created_at",
    }
    assert AuthSession.__tablename__ == "sessions"
    assert {foreign_key.target_fullname for foreign_key in AuthSession.__table__.foreign_keys} == {
        "users.id"
    }


def test_create_all_builds_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "metadata.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from robopark_api.config import Settings
    from sqlalchemy import create_engine

    engine = create_engine(Settings().database_url, future=True)
    Base.metadata.create_all(engine)

    assert set(inspect(engine).get_table_names()) >= {"users", "sessions"}


def test_alembic_upgrade_builds_schema(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]
    config = Config(api_dir / "alembic.ini")

    command.upgrade(config, "head")

    from sqlalchemy import create_engine

    engine = create_engine(sqlite_database_url, future=True)
    assert set(inspect(engine).get_table_names()) >= {
        "alembic_version",
        "users",
        "sessions",
    }
