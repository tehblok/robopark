import json
from unittest.mock import MagicMock, patch

import pytest

from robopark_api.services import emergency_client


def test_malformed_success_response_raises_emergency_error():
    response = MagicMock(status_code=200, headers={"content-type": "application/json"})
    response.json.side_effect = json.JSONDecodeError("invalid JSON", "not-json", 0)
    http_client = MagicMock()
    http_client.__enter__.return_value = http_client
    http_client.get.return_value = response

    with (
        patch("robopark_api.services.emergency_client.httpx.Client", return_value=http_client),
        pytest.raises(emergency_client.EmergencyError, match="invalid emergency response"),
    ):
        emergency_client.fetch_robot_payload(cookie="candidate", vin="YASADR00000000447")
