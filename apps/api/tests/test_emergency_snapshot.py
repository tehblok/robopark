from robopark_api.services.emergency_snapshot import parse_emergency_snapshot
from robopark_api.services.emergency_vin import short_robot_number


def test_short_robot_number_strips_leading_zeros():
    assert short_robot_number("YASADR00000000447") == "447"


def test_parse_snapshot_happy_path():
    snap = parse_emergency_snapshot(
        {
            "isOnline": True,
            "velocity": 1.25,
            "autoMode": True,
            "icp": {"ok": True, "status": "ICP"},
            "lte": "LTE",
            "disk": {"usedPercents": 53},
            "batteriesStatus": {
                "chargePercents": 64,
                "battery1": {"chargePercents": 97},
                "battery2": {"chargePercents": 96},
            },
            "position": {"lat": 55.7, "lon": 37.6, "yaw": 90},
            "wheelsBroken": [0, 5],
            "errors": ["[+0.5s] /RoverChassis/Systems/Control: Offline"],
        },
        vin="YASADR00000000447",
    )
    assert snap["short_number"] == "447"
    assert snap["online"] is True
    assert snap["speed"] == 1.25
    assert snap["charge_percent"] == 64
    assert snap["battery1_percent"] == 97
    assert snap["battery2_percent"] == 96
    assert snap["disk_percent"] == 53
    assert snap["mode"] == "AUTO"
    assert snap["icp_label"] == "ICP"
    assert snap["icp_ok"] is True
    assert snap["lte_label"] == "LTE"
    assert snap["error_banner"] == "ERROR: [+0.5s] /RoverChassis/Systems/Control: Offline"
    assert snap["lat"] == 55.7
    assert snap["lon"] == 37.6
    assert snap["heading_deg"] == 90
    assert snap["wheels_fault"] == ["fl", "rr"]


def test_parse_snapshot_nested_velocity_and_missing_fields():
    snap = parse_emergency_snapshot({"velocity": {"value": 2}}, vin="YASADR00000000001")
    assert snap["speed"] == 2.0
    assert snap["online"] is None
    assert snap["lat"] is None
    assert snap["mode"] is None
    assert snap["wheels_fault"] == []


def test_parse_snapshot_opaque_wheels_uses_body():
    snap = parse_emergency_snapshot({"wheelsBroken": "unknown"}, vin="YASADR00000000001")
    assert snap["wheels_fault"] == ["body"]


def test_parse_snapshot_manual_mode_and_offline_link():
    snap = parse_emergency_snapshot(
        {"autoMode": False, "lte": False, "icp": "fail"},
        vin="YASADR00000000001",
    )
    assert snap["mode"] == "MANUAL"
    assert snap["lte_ok"] is False
    assert snap["icp_ok"] is False
