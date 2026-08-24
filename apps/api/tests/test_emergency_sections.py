import pytest

from robopark_api.models import (
    EmergencyField,
    EmergencySection,
    EmergencySectionRole,
)
from robopark_api.services import emergency_config
from robopark_api.services.emergency_sections import list_sections, render_section
from robopark_api.services.emergency_vin import normalize_robot_id


@pytest.fixture(autouse=True)
def clear_config_cache():
    emergency_config.invalidate_config_cache()
    yield
    emergency_config.invalidate_config_cache()


def _seed_section(
    db_session,
    *,
    section_id: str,
    title: str,
    formatter: str | None = None,
    fields: list[tuple[str, str]] | None = None,
    roles: list[str] | None = None,
    is_enabled: bool = True,
) -> None:
    db_session.add(
        EmergencySection(
            id=section_id,
            title=title,
            sort_order=0,
            formatter=formatter,
            is_enabled=is_enabled,
        )
    )
    for sort_order, (path, label) in enumerate(fields or []):
        db_session.add(
            EmergencyField(
                section_id=section_id,
                path=path,
                label=label,
                sort_order=sort_order,
            )
        )
    for role in roles or []:
        db_session.add(EmergencySectionRole(section_id=section_id, role=role))
    db_session.commit()


def test_normalize_robot_id():
    assert normalize_robot_id("447") == "YASADR00000000447"


def test_list_sections_uses_db_config_and_role(db_session):
    _seed_section(
        db_session,
        section_id="status",
        title="Статус из БД",
        roles=["mechanic"],
    )

    assert list_sections(db_session, "mechanic") == [("status", "Статус из БД")]
    assert list_sections(db_session, "admin") == []


def test_render_status_section_uses_db_config(db_session):
    _seed_section(
        db_session,
        section_id="status",
        title="Статус из БД",
        fields=[("name", "Название"), ("isOnline", "Онлайн")],
        roles=["admin"],
    )
    payload = {"name": "Robot", "isOnline": True}

    assert render_section(db_session, payload, "status", role="admin") == {
        "id": "status",
        "title": "Статус из БД",
        "fields": [
            {"label": "Название", "lines": ["Robot"]},
            {"label": "Онлайн", "lines": ["да"]},
        ],
    }


def test_render_section_rejects_disabled_config(db_session):
    _seed_section(
        db_session,
        section_id="disabled",
        title="Disabled",
        roles=["admin"],
        is_enabled=False,
    )

    with pytest.raises(KeyError, match="role cannot view"):
        render_section(db_session, {}, "disabled", role="admin")


def test_service_raw_renders_fields_and_top_level_leftovers(db_session):
    _seed_section(
        db_session,
        section_id="service_raw",
        title="Служебные данные",
        formatter="fields_and_top_level_leftovers",
        fields=[("sdcOptions", "SDC options"), ("nested.value", "Nested value")],
        roles=["admin"],
    )
    payload = {
        "sdcOptions": {"mode": "auto"},
        "nested": {"value": 42, "other": "covered with top-level key"},
        "unexpected": {"flag": True},
    }

    rendered = render_section(db_session, payload, "service_raw", role="admin")

    assert rendered["fields"] == [
        {"label": "SDC options", "lines": ["mode: auto"]},
        {"label": "Nested value", "lines": ["42"]},
        {"label": "unexpected", "lines": ["flag: да"]},
    ]
