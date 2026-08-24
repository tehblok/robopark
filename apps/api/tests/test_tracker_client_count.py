from unittest.mock import MagicMock, patch

import httpx
import pytest

from robopark_api.services.tracker_client import TrackerError, count_issues, issue_to_dict


def test_issue_to_dict_includes_queue():
    item = issue_to_dict(
        {
            "key": "ROB-1",
            "summary": "test",
            "queue": {"key": "ROBOPARK", "display": "Robopark"},
            "status": {"key": "queued", "display": "В очереди"},
        }
    )
    assert item["queue"] == "ROBOPARK"


@pytest.mark.parametrize(
    "payload",
    [
        "not-a-number",
        {"count": "bad"},
        {"total": None},
        {"value": {"nested": 1}},
        ["list"],
        None,
    ],
)
def test_count_issues_bad_payload_raises_tracker_error(payload):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = payload

    with patch("robopark_api.services.tracker_client.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = response
        with pytest.raises(TrackerError, match="unexpected tracker count response"):
            count_issues(token="t", query="Queue: ROBOPARK")


def test_count_issues_invalid_json_raises_tracker_error():
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.side_effect = ValueError("bad json")

    with patch("robopark_api.services.tracker_client.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = response
        with pytest.raises(TrackerError, match="unexpected tracker count response"):
            count_issues(token="t", query="Queue: ROBOPARK")


def test_count_issues_http_error_raises_tracker_error():
    with patch("robopark_api.services.tracker_client.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.side_effect = httpx.HTTPError(
            "network"
        )
        with pytest.raises(TrackerError, match="network"):
            count_issues(token="t", query="Queue: ROBOPARK")


def test_count_issues_parses_int_and_dict():
    for payload, expected in [(7, 7), ({"count": 3}, 3), ({"total": "4"}, 4)]:
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = payload

        with patch("robopark_api.services.tracker_client.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value.post.return_value = response
            assert count_issues(token="t", query="q") == expected
