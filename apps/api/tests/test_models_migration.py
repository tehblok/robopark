from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from robopark_api.models import AuthSession, Base, User


def test_metadata_has_required_tables():
    assert set(Base.metadata.tables) == {
        "users",
        "sessions",
        "parks",
        "user_parks",
        "park_requests",
        "platform_settings",
        "emergency_sections",
        "emergency_fields",
        "emergency_section_roles",
        "park_blocker_history",
        "reports",
        "report_attachments",
        "audit_log",
        "permissions",
        "roles",
        "role_permissions",
        "user_permissions",
    }


def test_alembic_head_is_history_definition():
    api_dir = Path(__file__).parents[1]
    script = ScriptDirectory.from_config(Config(api_dir / "alembic.ini"))
    assert script.get_heads() == ["0016_history_definition"]


def test_history_migration_preserves_legacy_definition(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0015_report_attachments")
    engine = create_engine(sqlite_database_url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO parks (id, name, tag, is_active) VALUES (1, 'Test', 'test', 1)")
        )
        connection.execute(
            text(
                "INSERT INTO park_blocker_history (park_id, bucket_start, arrived_count, departed_count) VALUES (1, '2026-09-01 00:00:00', 4, 9)"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT definition_version, arrived_count, departed_count FROM park_blocker_history"
            )
        ).one()
    assert tuple(row) == (1, 4, 9)


def test_migrated_report_attachments_contract(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]
    command.upgrade(Config(api_dir / "alembic.ini"), "head")
    inspector = inspect(create_engine(sqlite_database_url, future=True))

    assert {column["name"] for column in inspector.get_columns("report_attachments")} == {
        "id",
        "report_id",
        "kind",
        "filename",
        "content_type",
        "size_bytes",
        "storage_key",
        "created_at",
    }
    foreign_keys = inspector.get_foreign_keys("report_attachments")
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["report_id"]
    assert foreign_keys[0]["referred_table"] == "reports"
    assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"
    assert any(
        constraint["column_names"] == ["report_id", "kind"]
        for constraint in inspector.get_unique_constraints("report_attachments")
    )
    assert any(
        index["column_names"] == ["report_id"]
        for index in inspector.get_indexes("report_attachments")
    )


def test_models_match_required_schema():
    assert set(User.__table__.columns.keys()) == {
        "id",
        "username",
        "password_hash",
        "role_id",
        "access_status",
        "tracker_login",
        "must_change_password",
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

    engine = create_engine(Settings().database_url, future=True)
    Base.metadata.create_all(engine)

    assert set(inspect(engine).get_table_names()) >= {"users", "sessions"}


def test_alembic_upgrade_with_percent_in_database_url(tmp_path, monkeypatch):
    db_path = tmp_path / "user%40data.db"
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    api_dir = Path(__file__).parents[1]
    config = Config(api_dir / "alembic.ini")

    command.upgrade(config, "head")

    engine = create_engine(database_url, future=True)
    assert set(inspect(engine).get_table_names()) >= {
        "alembic_version",
        "users",
        "sessions",
    }


def test_alembic_upgrade_builds_schema(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]
    config = Config(api_dir / "alembic.ini")

    command.upgrade(config, "head")

    engine = create_engine(sqlite_database_url, future=True)
    assert set(inspect(engine).get_table_names()) >= {
        "alembic_version",
        "users",
        "sessions",
    }


def test_migrated_schema_matches_models(sqlite_database_url, monkeypatch):
    """A model change without a matching migration must fail here, not on deploy."""
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]

    command.upgrade(Config(api_dir / "alembic.ini"), "head")

    engine = create_engine(sqlite_database_url, future=True)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        difference = compare_metadata(context, Base.metadata)

    assert difference == []


def test_migrated_indexes_and_foreign_keys_match_models(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]

    command.upgrade(Config(api_dir / "alembic.ini"), "head")

    inspector = inspect(create_engine(sqlite_database_url, future=True))

    unique_indexes = {
        (table, index["name"])
        for table in ("users", "sessions")
        for index in inspector.get_indexes(table)
        if index["unique"]
    }
    assert unique_indexes == {
        ("users", "ix_users_username"),
        ("sessions", "ix_sessions_token_hash"),
    }

    foreign_keys = inspector.get_foreign_keys("sessions")
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["user_id"]
    assert foreign_keys[0]["referred_table"] == "users"
    assert foreign_keys[0]["referred_columns"] == ["id"]
    assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"


def test_migrated_parks_have_tracker_columns_and_history_table(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]

    command.upgrade(Config(api_dir / "alembic.ini"), "head")

    inspector = inspect(create_engine(sqlite_database_url, future=True))
    park_columns = {column["name"] for column in inspector.get_columns("parks")}
    assert {"tracker_priority", "tracker_type"} <= park_columns
    assert "park_blocker_history" in inspector.get_table_names()

    history_columns = {column["name"] for column in inspector.get_columns("park_blocker_history")}
    assert history_columns == {
        "id",
        "park_id",
        "bucket_start",
        "arrived_count",
        "departed_count",
        "scanned_at",
        "definition_version",
    }

    unique_indexes = {
        index["name"] for index in inspector.get_indexes("park_blocker_history") if index["unique"]
    }
    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("park_blocker_history")
    }
    assert (
        "uq_park_blocker_history_park_bucket" in unique_indexes
        or "uq_park_blocker_history_park_bucket" in unique_constraints
    )
