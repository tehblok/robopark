import json

import pytest

from robopark_api.models import EmergencyField, EmergencySection, EmergencySectionRole
from robopark_api.services import emergency_config


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
    sort_order: int,
    is_enabled: bool = True,
    formatter: str | None = None,
    meta: dict | None = None,
    fields: list[tuple[str, str]] | None = None,
    roles: list[str] | None = None,
) -> None:
    db_session.add(
        EmergencySection(
            id=section_id,
            title=title,
            sort_order=sort_order,
            is_enabled=is_enabled,
            formatter=formatter,
            meta_json=json.dumps(meta, ensure_ascii=False) if meta else None,
        )
    )
    for field_order, (path, label) in enumerate(fields or []):
        db_session.add(
            EmergencyField(
                section_id=section_id,
                path=path,
                label=label,
                sort_order=field_order,
            )
        )
    for role in roles or []:
        db_session.add(EmergencySectionRole(section_id=section_id, role=role))
    db_session.commit()


def test_list_sections_filters_by_role(db_session):
    _seed_section(
        db_session,
        section_id="status",
        title="Статус",
        sort_order=0,
        roles=["mechanic", "admin"],
    )
    _seed_section(
        db_session,
        section_id="service_raw",
        title="Service raw",
        sort_order=1,
        roles=["admin"],
    )

    assert [s[0] for s in emergency_config.list_sections_for_role(db_session, "mechanic")] == [
        "status"
    ]
    assert "service_raw" in [
        s[0] for s in emergency_config.list_sections_for_role(db_session, "admin")
    ]


def test_disabled_section_hidden(db_session):
    _seed_section(
        db_session,
        section_id="status",
        title="Статус",
        sort_order=0,
        is_enabled=False,
        roles=["mechanic"],
    )

    assert emergency_config.list_sections_for_role(db_session, "mechanic") == []


def test_get_section_config(db_session):
    _seed_section(
        db_session,
        section_id="status",
        title="Статус",
        sort_order=0,
        formatter="errors_classify",
        meta={"covered_top_level": True},
        fields=[("vin", "VIN"), ("state", "State")],
        roles=["mechanic", "admin"],
    )

    config = emergency_config.get_section_config(db_session, "status")

    assert config == {
        "id": "status",
        "title": "Статус",
        "formatter": "errors_classify",
        "meta": {"covered_top_level": True},
        "fields": [{"path": "vin", "label": "VIN"}, {"path": "state", "label": "State"}],
        "roles": ["admin", "mechanic"],
    }


def test_get_section_config_missing(db_session):
    assert emergency_config.get_section_config(db_session, "missing") is None


def test_role_can_view_section(db_session):
    _seed_section(
        db_session,
        section_id="status",
        title="Статус",
        sort_order=0,
        roles=["mechanic"],
    )
    _seed_section(
        db_session,
        section_id="service_raw",
        title="Service raw",
        sort_order=1,
        roles=["admin"],
    )

    assert emergency_config.role_can_view_section(db_session, "mechanic", "status") is True
    assert emergency_config.role_can_view_section(db_session, "mechanic", "service_raw") is False
    assert emergency_config.role_can_view_section(db_session, "admin", "service_raw") is True


def test_role_can_view_disabled_section(db_session):
    _seed_section(
        db_session,
        section_id="status",
        title="Статус",
        sort_order=0,
        is_enabled=False,
        roles=["mechanic"],
    )

    assert emergency_config.role_can_view_section(db_session, "mechanic", "status") is False


def test_config_cache_invalidated_on_seed(db_session, tmp_path):
    json_path = tmp_path / "sections.json"
    json_path.write_text(
        json.dumps(
            {
                "sections": {
                    "status": {
                        "title": "Статус",
                        "fields": [{"path": "vin", "label": "VIN"}],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    emergency_config.seed_emergency_config(db_session, json_path)
    first = emergency_config.list_sections_for_role(db_session, "mechanic")

    db_session.query(EmergencySection).delete()
    db_session.commit()
    emergency_config.invalidate_config_cache()

    json_path.write_text(
        json.dumps(
            {
                "sections": {
                    "batteries": {
                        "title": "Батареи",
                        "fields": [{"path": "battery", "label": "Battery"}],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    emergency_config.seed_emergency_config(db_session, json_path)
    second = emergency_config.list_sections_for_role(db_session, "mechanic")

    assert [s[0] for s in first] == ["status"]
    assert [s[0] for s in second] == ["batteries"]
