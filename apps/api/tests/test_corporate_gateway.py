import json

import pytest

from robopark_api.config import Settings
from robopark_api.services import corporate_gateway_client, corporate_gateway_contract


def test_contract_allows_only_typed_actions_resources_and_flat_params():
    assert (
        corporate_gateway_contract.validate_request(
            "inventory",
            "rover_installations",
            "a664",
            {"offset": 0, "limit": 25, "slot": "/boards/dr_killswitch"},
        )["limit"]
        == 25
    )
    assert corporate_gateway_contract.validate_request(
        "tracker",
        "issues_search",
        "search",
        {"query": "Queue: ROBOPARK", "page": 1, "per_page": 50, "order": ["-createdAt"]},
    )["order"] == ["-createdAt"]
    assert corporate_gateway_contract.validate_request("tracker", "user_read", "11291906", {}) == {}

    for value in (
        ("inventory", "rover_card", "https://inventory/secret", {}),
        ("inventory", "delete", "a664", {}),
        ("tracker", "issue_read", "ROBOPARK-1", {"url": "https://other"}),
        ("tracker", "user_read", "../issues/ROBOPARK-1", {}),
        ("tracker", "user_read", "11291906", {"expand": "all"}),
        ("tracker", "issues_search", "search", {"query": "Queue: X", "order": ["bad field"]}),
    ):
        with pytest.raises(corporate_gateway_contract.ContractError):
            corporate_gateway_contract.validate_request(*value)


def test_result_rejects_owner_resource_identity_headers_and_oversize():
    expected = {
        "service": "inventory",
        "action": "rover_card",
        "resource": "a664",
        "owner_user_id": 7,
        "identity_login": "owner.login",
        "status_code": 200,
        "headers": {"X-Total-Count": "1"},
        "payload": {"name": "a664"},
    }
    response = corporate_gateway_contract.validate_result(
        expected,
        service="inventory",
        action="rover_card",
        resource="a664",
        owner_user_id=7,
        owner_tracker_login="owner.login",
    )
    assert response.headers == {"x-total-count": "1"}
    for changed in (
        {**expected, "owner_user_id": 8},
        {**expected, "resource": "a665"},
        {**expected, "identity_login": "other.login"},
        {**expected, "headers": {"authorization": "secret"}},
        {**expected, "payload": {"value": "x" * (1024 * 1024)}},
    ):
        with pytest.raises(corporate_gateway_contract.ContractError):
            corporate_gateway_contract.validate_result(
                changed,
                service="inventory",
                action="rover_card",
                resource="a664",
                owner_user_id=7,
                owner_tracker_login="owner.login",
            )


def test_client_is_test_only_and_maps_only_safe_errors(monkeypatch):
    production = Settings(
        _env_file=None,
        secret_key="secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
        mac_corporate_gateway_enabled=True,
        robopark_environment="production",
    )
    assert corporate_gateway_client.enabled(production) is False
    test = production.model_copy(update={"robopark_environment": "test"})

    class Response:
        status_code = 500

        def json(self):
            return {"detail": "upstream leaked secret"}

    monkeypatch.setattr(
        corporate_gateway_client, "_signed_post", lambda *_args, **_kwargs: Response()
    )
    with pytest.raises(corporate_gateway_client.CorporateGatewayError) as captured:
        corporate_gateway_client.read(
            service="inventory",
            action="rover_card",
            resource="a664",
            params={},
            requester_user_id=1,
            settings=test,
        )
    assert captured.value.status_code == 503
    assert captured.value.code == "corporate_unavailable"
    assert "secret" not in str(captured.value)
    assert len(json.dumps({"error": captured.value.code})) < 100


def test_client_preserves_authentication_denial_as_non_availability(monkeypatch):
    settings = Settings(
        _env_file=None,
        secret_key="secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
        mac_corporate_gateway_enabled=True,
        robopark_environment="test",
    )

    class Response:
        status_code = 401

        def json(self):
            return {"detail": "corporate_authentication"}

    monkeypatch.setattr(
        corporate_gateway_client, "_signed_post", lambda *_args, **_kwargs: Response()
    )
    with pytest.raises(corporate_gateway_client.CorporateGatewayError) as captured:
        corporate_gateway_client.read(
            service="tracker",
            action="issue_read",
            resource="ROBOPARK-1",
            params={},
            requester_user_id=1,
            settings=settings,
        )
    assert (captured.value.status_code, captured.value.code) == (
        401,
        "corporate_authentication",
    )
