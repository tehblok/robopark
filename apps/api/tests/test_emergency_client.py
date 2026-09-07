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


def test_cookie_header_is_normalized_and_request_looks_like_a_browser():
    response = MagicMock(status_code=200, headers={"content-type": "application/json"})
    response.json.return_value = {"vin": "YASADR00000000447"}
    http_client = MagicMock()
    http_client.__enter__.return_value = http_client
    http_client.get.return_value = response

    with patch("robopark_api.services.emergency_client.httpx.Client", return_value=http_client):
        emergency_client.fetch_robot_payload(
            cookie="Cookie: Session_id=one; sessionid2=two\n",
            vin="YASADR00000000447",
        )

    headers = http_client.get.call_args.kwargs["headers"]
    assert headers["Cookie"] == "Session_id=one; sessionid2=two"
    assert headers["Accept"] == "application/json"
    assert "Mozilla/5.0" in headers["User-Agent"]


def test_unexpected_html_is_not_misreported_as_an_expired_cookie():
    response = MagicMock(status_code=200, headers={"content-type": "text/html"})
    response.text = "<html><body>temporary proxy error</body></html>"
    http_client = MagicMock()
    http_client.__enter__.return_value = http_client
    http_client.get.return_value = response

    with (
        patch("robopark_api.services.emergency_client.httpx.Client", return_value=http_client),
        pytest.raises(emergency_client.EmergencyError, match="unexpected html response"),
    ):
        emergency_client.fetch_robot_payload(cookie="Session_id=one", vin="YASADR00000000447")
