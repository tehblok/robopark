import json

from robopark_api.models import (
    EmergencyReading,
    EmergencySection,
    EmergencySectionRole,
)
from robopark_api.services.emergency_readings import render_readings


def add_section(db, section_id: str, *, role: str = "mechanic", enabled: bool = True):
    db.add(
        EmergencySection(
            id=section_id,
            title=section_id,
            sort_order=0,
            is_enabled=enabled,
        )
    )
    db.add(EmergencySectionRole(section_id=section_id, role=role))
    db.flush()


def add_reading(db, **overrides):
    values = {
        "section_id": "status",
        "path": "sensors.0.value",
        "label": "Distance",
        "display_kind": "distance",
        "unit": "m",
        "precision": 2,
        "enabled_path": None,
        "no_data_json": "[]",
        "warning_below": None,
        "warning_above": 4.0,
        "critical_below": None,
        "critical_above": 8.0,
        "view": "front",
        "x": 0.25,
        "y": 0.75,
        "label_direction": "left",
        "is_enabled": True,
        "sort_order": 0,
    }
    values.update(overrides)
    reading = EmergencyReading(**values)
    db.add(reading)
    db.flush()
    return reading


def test_render_readings_looks_up_lists_formats_zero_and_applies_thresholds(db_session):
    add_section(db_session, "status")
    zero = add_reading(db_session, path="speed", label="Speed", display_kind="number", unit=None)
    distance = add_reading(db_session, sort_order=1)
    percent = add_reading(
        db_session,
        path="battery.charge",
        label="Charge",
        display_kind="percent",
        unit=None,
        precision=0,
        warning_below=30,
        warning_above=None,
        critical_below=10,
        critical_above=None,
        sort_order=2,
    )
    db_session.commit()

    rendered = render_readings(
        db_session,
        {"speed": 0, "sensors": [{"value": 9.125}], "battery": {"charge": 20}},
        "mechanic",
    )

    assert [item.id for item in rendered] == [zero.id, distance.id, percent.id]
    assert [(item.display, item.state) for item in rendered] == [
        ("0.00", "normal"),
        ("9.12 m", "critical"),
        ("20 %", "warning"),
    ]
    assert rendered[1].model_dump(exclude={"id", "display", "state"}) == {
        "section_id": "status",
        "label": "Distance",
        "view": "front",
        "x": 0.25,
        "y": 0.75,
        "label_direction": "left",
    }


def test_render_readings_treats_sensor_disabled_sentinel_and_invalid_config_as_unavailable(
    db_session,
):
    add_section(db_session, "status")
    disabled = add_reading(
        db_session,
        path="parktronics.lt",
        enabled_path="parktronics.ltEnabled",
        no_data_json=json.dumps([2147483647], separators=(",", ":")),
    )
    sentinel = add_reading(
        db_session,
        path="parktronics.rt",
        enabled_path=None,
        no_data_json="[2147483647]",
        sort_order=1,
    )
    invalid = add_reading(
        db_session,
        path="bad..path",
        no_data_json="not-json",
        sort_order=2,
    )
    healthy = add_reading(
        db_session,
        path="healthy",
        display_kind="state",
        unit=None,
        precision=0,
        sort_order=3,
    )
    db_session.commit()

    rendered = render_readings(
        db_session,
        {
            "parktronics": {
                "lt": 0,
                "ltEnabled": False,
                "rt": 2147483647,
            },
            "healthy": True,
        },
        "mechanic",
    )

    assert [item.id for item in rendered] == [disabled.id, sentinel.id, invalid.id, healthy.id]
    assert [(item.display, item.state) for item in rendered] == [
        ("Отключён", "unavailable"),
        ("Нет показания", "unavailable"),
        ("Нет показания", "unavailable"),
        ("Да", "normal"),
    ]


def test_render_readings_filters_disabled_unassigned_and_inactive_items(db_session):
    add_section(db_session, "visible", role="mechanic")
    add_section(db_session, "other-role", role="admin")
    add_section(db_session, "disabled-section", role="mechanic", enabled=False)
    visible = add_reading(db_session, section_id="visible", path="value", sort_order=3)
    add_reading(db_session, section_id="other-role", path="value", sort_order=0)
    add_reading(db_session, section_id="disabled-section", path="value", sort_order=1)
    add_reading(
        db_session,
        section_id="visible",
        path="inactive",
        is_enabled=False,
        sort_order=2,
    )
    db_session.commit()

    rendered = render_readings(db_session, {"value": 1}, "mechanic")

    assert [item.id for item in rendered] == [visible.id]
