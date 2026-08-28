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
    assert snap["connection"] == "lte"
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


def test_parse_snapshot_compact_wheel_codes():
    snap = parse_emergency_snapshot({"wheelsBroken": ["fl", "rr"]}, vin="YASADR00000000001")
    assert snap["wheels_fault"] == ["fl", "rr"]


def test_parse_snapshot_online_coercion():
    online = parse_emergency_snapshot({"isOnline": "true"}, vin="YASADR00000000001")
    offline = parse_emergency_snapshot({"isOnline": 0}, vin="YASADR00000000001")
    unknown = parse_emergency_snapshot({"isOnline": "maybe"}, vin="YASADR00000000001")
    assert online["online"] is True
    assert offline["online"] is False
    assert unknown["online"] is None


def test_parse_snapshot_manual_mode_and_offline_link():
    snap = parse_emergency_snapshot(
        {"autoMode": False, "lte": False, "icp": "fail"},
        vin="YASADR00000000001",
    )
    assert snap["mode"] == "MANUAL"
    assert snap["lte_ok"] is False
    assert snap["icp_ok"] is False
    assert snap["connection"] is None


def test_parse_snapshot_live_field_shapes():
    snap = parse_emergency_snapshot(
        {
            "isOnline": True,
            "velocity": 0.0,
            "disk": {},
            "diskUsage": 17,
            "batteriesStatus": {
                "battery1": {"chargePercentage": 73, "isConnected": True},
                "battery2": {"chargePercentage": 74, "isConnected": True},
            },
            "lte": {"lte24": 7300, "lte50": 8900},
            "wheelsBroken": {
                "lf": False,
                "lm": False,
                "lr": False,
                "rf": False,
                "rm": False,
                "rr": False,
            },
            "position": {"yaw": 80},
        },
        vin="YASADR00000001975",
    )
    assert snap["battery1_percent"] == 73
    assert snap["battery2_percent"] == 74
    assert snap["disk_percent"] == 17
    assert snap["connection"] == "lte"
    assert snap["wheels_fault"] == []
    assert snap["heading_deg"] == 80


def test_parse_snapshot_disconnected_battery_wheel_dict_and_wire():
    snap = parse_emergency_snapshot(
        {
            "isOnline": True,
            "batteriesStatus": {
                "battery1": {"chargePercentage": 50, "isConnected": True},
                "battery2": {"chargePercentage": 0, "isConnected": False},
            },
            "wheelsBroken": {"lf": True, "lm": False, "rr": True},
            "lte": {"lte24": 0, "lte50": 0},
        },
        vin="YASADR00000000001",
    )
    assert snap["battery1_percent"] == 50
    assert snap["battery2_percent"] is None
    assert snap["wheels_fault"] == ["fl", "rr"]
    assert snap["connection"] == "wire"


def test_parse_snapshot_explicit_wire_beats_lte():
    snap = parse_emergency_snapshot(
        {"isOnline": True, "isWired": True, "lte": {"lte24": 8000}},
        vin="YASADR00000000001",
    )
    assert snap["connection"] == "wire"
