from robopark_api.services.emergency_snapshot import parse_emergency_snapshot
from robopark_api.services.emergency_vin import short_robot_number


def test_short_robot_number_strips_leading_zeros():
    assert short_robot_number("YASADR00000000447") == "447"


def test_parse_snapshot_happy_path():
    snap = parse_emergency_snapshot(
        {
            "isOnline": True,
            "velocity": 1.25,
            "batteriesStatus": {"chargePercents": 64},
            "position": {"lat": 55.7, "lon": 37.6, "yaw": 90},
            "wheelsBroken": [0, 5],
        },
        vin="YASADR00000000447",
    )
    assert snap["short_number"] == "447"
    assert snap["online"] is True
    assert snap["speed"] == 1.25
    assert snap["charge_percent"] == 64
    assert snap["lat"] == 55.7
    assert snap["lon"] == 37.6
    assert snap["heading_deg"] == 90
    assert snap["wheels_fault"] == ["fl", "rr"]


def test_parse_snapshot_nested_velocity_and_missing_fields():
    snap = parse_emergency_snapshot({"velocity": {"value": 2}}, vin="YASADR00000000001")
    assert snap["speed"] == 2.0
    assert snap["online"] is None
    assert snap["lat"] is None
    assert snap["wheels_fault"] == []


def test_parse_snapshot_opaque_wheels_uses_body():
    snap = parse_emergency_snapshot({"wheelsBroken": "unknown"}, vin="YASADR00000000001")
    assert snap["wheels_fault"] == ["body"]
