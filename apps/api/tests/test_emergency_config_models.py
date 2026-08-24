from pathlib import Path

from sqlalchemy import select

from robopark_api.models import EmergencyField, EmergencySection, EmergencySectionRole
from robopark_api.services.emergency_config import seed_emergency_config

DEFAULT_JSON_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "emergency_sections.json"
)


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
    db_session.add(
        EmergencyField(section_id="status", path="vin", label="VIN", sort_order=0)
    )
    db_session.add(EmergencySectionRole(section_id="status", role="mechanic"))
    db_session.commit()
    assert db_session.get(EmergencySection, "status").title == "Статус"
    assert db_session.scalar(select(EmergencyField).limit(1)) is not None


def test_seed_service_raw_has_no_mechanic_role(db_session):
    seed_emergency_config(db_session, DEFAULT_JSON_PATH)
    roles = db_session.scalars(
        select(EmergencySectionRole.role).where(
            EmergencySectionRole.section_id == "service_raw"
        )
    ).all()
    assert "mechanic" not in roles
    assert set(roles) == {"admin", "royal"}
