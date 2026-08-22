import json
from pathlib import Path
from unittest.mock import patch

from conftest import login_as

FIXTURES = Path(__file__).parent / "fixtures"


def test_emergency_resolve(client, seed_mechanic, seed_royal):
    login_as(client, "royal", "secret")
    client.put("/admin/settings/emergency-cookie", json={"cookie": "Session_id=test"})
    login_as(client, "mech1", "secret")

    payload = json.loads((FIXTURES / "emergency_robot.json").read_text(encoding="utf-8"))
    with patch(
        "robopark_api.services.emergency_client.fetch_robot_payload",
        return_value=payload,
    ):
        response = client.post("/mechanic/emergency/resolve", json={"robot_number": "447"})

    assert response.status_code == 200
    body = response.json()
    assert body["vin"] == "YASADR00000000447"
    assert body["sections"]


def test_emergency_invalid_cookie(client, seed_mechanic, seed_royal):
    login_as(client, "royal", "secret")
    client.put("/admin/settings/emergency-cookie", json={"cookie": "bad"})
    login_as(client, "mech1", "secret")

    from robopark_api.services.emergency_client import EmergencyAuthError

    with patch(
        "robopark_api.services.emergency_client.fetch_robot_payload",
        side_effect=EmergencyAuthError("invalid"),
    ):
        response = client.post("/mechanic/emergency/resolve", json={"robot_number": "447"})

    assert response.status_code == 401
    assert response.json()["detail"] == "emergency_cookie_invalid"
