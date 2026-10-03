import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

from robopark_api.models import EmergencyField, EmergencySection, EmergencySectionRole
from robopark_api.services.emergency_config import seed_emergency_config

DEFAULT_JSON_PATH = Path(__file__).resolve().parents[1] / "data" / "emergency_sections.json"


def test_initial_emergency_migration_requires_seed_before_creating_tables(tmp_path, monkeypatch):
    source = (
        Path(__file__).resolve().parents[1] / "alembic/versions/0004_phase6_emergency_config.py"
    )
    spec = importlib.util.spec_from_file_location("emergency_seed_migration_test", source)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(
        migration, "__file__", str(tmp_path / "alembic/versions/0004_phase6_emergency_config.py")
    )
    create_table = Mock()
    monkeypatch.setattr(migration.op, "create_table", create_table)

    with pytest.raises(FileNotFoundError, match="emergency_sections_seed_missing"):
        migration.upgrade()

    create_table.assert_not_called()


@pytest.mark.parametrize(
    "seed",
    [
        '{"sections":{}}',
        '{"sections":{"status":null}}',
        '{"sections":{"status":{"fields":[null]}}}',
    ],
)
def test_initial_emergency_migration_rejects_invalid_seed_before_creating_tables(
    tmp_path, monkeypatch, seed
):
    source = (
        Path(__file__).resolve().parents[1] / "alembic/versions/0004_phase6_emergency_config.py"
    )
    spec = importlib.util.spec_from_file_location("emergency_empty_seed_migration_test", source)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(
        migration, "__file__", str(tmp_path / "alembic/versions/0004_phase6_emergency_config.py")
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data/emergency_sections.json").write_text(seed)
    create_table = Mock()
    monkeypatch.setattr(migration.op, "create_table", create_table)

    with pytest.raises(ValueError, match="invalid emergency sections seed"):
        migration.upgrade()

    create_table.assert_not_called()


def test_initial_emergency_migration_populates_catalog(sqlite_database_url, monkeypatch):
    api_dir = Path(__file__).resolve().parents[1]
    expected = json.loads(DEFAULT_JSON_PATH.read_text(encoding="utf-8"))["sections"]
    config = Config(api_dir / "alembic.ini")
    config.set_main_option("script_location", str(api_dir / "alembic"))
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)

    command.upgrade(config, "0004")

    engine = create_engine(sqlite_database_url, future=True)
    with engine.connect() as connection:
        section_ids = set(connection.scalars(text("SELECT id FROM emergency_sections")))
        field_count = connection.scalar(text("SELECT count(*) FROM emergency_fields"))
        role_count = connection.scalar(text("SELECT count(*) FROM emergency_section_roles"))
    assert section_ids == set(expected)
    assert field_count == sum(len(section.get("fields", [])) for section in expected.values())
    assert role_count >= len(expected)


def test_emergency_section_tables_exist(db_session):
    section = EmergencySection(
        id="status",
        title="Статус",
        sort_order=0,
        is_enabled=True,
        formatter=None,
        meta_json=None,
    )
    db_session.add(section)
    db_session.add(EmergencyField(section_id="status", path="vin", label="VIN", sort_order=0))
    db_session.add(EmergencySectionRole(section_id="status", role="mechanic"))
    db_session.commit()
    assert db_session.get(EmergencySection, "status").title == "Статус"
    assert db_session.scalar(select(EmergencyField).limit(1)) is not None


def _seeded_roles(db_session, section_id: str) -> set[str]:
    return set(
        db_session.scalars(
            select(EmergencySectionRole.role).where(EmergencySectionRole.section_id == section_id)
        ).all()
    )


def test_seed_section_role_defaults(db_session):
    seed_emergency_config(db_session, DEFAULT_JSON_PATH)

    assert _seeded_roles(db_session, "service_raw") == {"admin", "royal"}
    assert _seeded_roles(db_session, "position_route") == {
        "operator",
        "admin",
        "royal",
    }
    assert _seeded_roles(db_session, "metadata") == {"operator", "admin", "royal"}
    assert _seeded_roles(db_session, "status") == {
        "mechanic",
        "operator",
        "admin",
        "royal",
        "driver",
    }
