import httpx
import pytest

from conftest import login_as
from robopark_api.services import (
    corporate_gateway_client,
    corporate_gateway_contract,
    emergency_scope,
    platform_settings,
    sdc_inventory,
)


@pytest.mark.parametrize(
    "raw,expected", [("664", "a664"), ("А00664", "a00664"), ("a1170", "a1170")]
)
def test_robot_names_preserve_significant_zeroes(raw, expected):
    assert sdc_inventory.robot_name(raw) == expected


@pytest.mark.parametrize("raw", ["../../settings", "a1/other", "robot 3 secret", "http://host", ""])
def test_robot_name_rejects_paths_and_ambiguous_numbers(raw):
    with pytest.raises(ValueError):
        sdc_inventory.robot_name(raw)


def test_client_never_follows_redirect_or_uses_ambient_proxy(monkeypatch):
    options = {}
    real_client = httpx.Client

    def factory(**kwargs):
        options.update(kwargs)
        return real_client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    302, headers={"location": "https://untrusted.example/"}
                )
            ),
        )

    monkeypatch.setattr(sdc_inventory.httpx, "Client", factory)
    with pytest.raises(sdc_inventory.InventoryError, match="sdc_inventory_upstream_error"):
        sdc_inventory.fetch_card("secret", "a664")
    assert options["follow_redirects"] is False
    assert options["trust_env"] is False


def test_inventory_scope_checked_before_network(client, seed_mechanic, monkeypatch):
    login_as(client, seed_mechanic.username, "secret")
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_args: False)
    monkeypatch.setattr(
        sdc_inventory, "fetch_card", lambda *_args: pytest.fail("out of scope request")
    )
    response = client.get("/sdc-inventory/robots/a664")
    assert response.status_code == 403


def test_card_returns_projected_facts_and_source(client, db_session, seed_admin, monkeypatch):
    login_as(client, seed_admin.username, "secret")
    platform_settings.set_setting(db_session, "tracker_token", "test-token")
    monkeypatch.setattr(
        sdc_inventory,
        "_get",
        lambda *_args, **_kwargs: {
            "name": "a664",
            "port": "Moscow",
            "storage": "Garage",
            "fleet": "production",
            "token": "never-return",
            "password": "never-return",
            "private_note": "never-return",
        },
    )
    response = client.get("/sdc-inventory/robots/a664")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "SDC Inventory"
    assert body["robot"]["storage"] == "Garage"
    assert body["checked_at"]
    assert "never-return" not in response.text
    assert body["physical_inspection"] == "not_performed"


def test_test_gateway_uses_owner_mac_and_never_falls_back_to_server_token(
    client, seed_admin, test_settings, monkeypatch
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    calls = []

    monkeypatch.setattr(
        platform_settings,
        "get_tracker_token",
        lambda *_args: pytest.fail("interactive gateway must not read the service token"),
    )
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **kwargs: (
            calls.append(kwargs)
            or corporate_gateway_contract.CorporateReadResponse(
                200,
                {},
                {"name": "a664", "storage": "Owner Mac"},
            )
        ),
    )

    response = client.get("/sdc-inventory/robots/a664")
    assert response.status_code == 200, response.text
    assert response.json()["robot"]["storage"] == "Owner Mac"
    assert calls[0]["action"] == "rover_card"
    assert calls[0]["requester_user_id"] == seed_admin.id


def test_test_gateway_unavailable_falls_back_to_configured_server_token(
    client, seed_admin, test_settings, monkeypatch
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    test_settings.mac_corporate_server_fallback_enabled = True
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **_kwargs: (_ for _ in ()).throw(
            corporate_gateway_client.CorporateGatewayError(503, "corporate_unavailable")
        ),
    )
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: "server-token")
    monkeypatch.setattr(
        sdc_inventory,
        "_get",
        lambda token, *_args, **_kwargs: {
            "name": "a664",
            "storage": "Server path" if token == "server-token" else "wrong",
        },
    )

    response = client.get("/sdc-inventory/robots/a664")

    assert response.status_code == 200, response.text
    assert response.json()["robot"]["storage"] == "Server path"


def test_test_gateway_fallback_requires_flag_and_configured_server_token(
    client, seed_admin, test_settings, monkeypatch
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    test_settings.mac_corporate_server_fallback_enabled = False
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **_kwargs: (_ for _ in ()).throw(
            corporate_gateway_client.CorporateGatewayError(503, "corporate_unavailable")
        ),
    )
    monkeypatch.setattr(
        platform_settings,
        "get_tracker_token",
        lambda *_args: pytest.fail("disabled fallback must not read the server token"),
    )
    response = client.get("/sdc-inventory/robots/a664")
    assert response.status_code == 503
    assert response.json()["detail"] == "corporate_unavailable"

    test_settings.mac_corporate_server_fallback_enabled = True
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: None)
    response = client.get("/sdc-inventory/robots/a664")
    assert response.status_code == 503
    assert response.json()["detail"] == "sdc_inventory_token_not_configured"


@pytest.mark.parametrize(
    "error",
    [
        corporate_gateway_client.CorporateGatewayError(403, "corporate_access_denied"),
        corporate_gateway_client.CorporateGatewayError(404, "corporate_not_found"),
        corporate_gateway_client.CorporateGatewayError(429, "corporate_rate_limited"),
        corporate_gateway_client.CorporateGatewayError(503, "corporate_invalid_response"),
    ],
)
def test_test_gateway_does_not_fallback_for_denial_not_found_rate_limit_or_invalid(
    client, seed_admin, test_settings, monkeypatch, error
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    test_settings.mac_corporate_server_fallback_enabled = True
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **_kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        platform_settings,
        "get_tracker_token",
        lambda *_args: pytest.fail("non-availability failure must not fall back"),
    )

    response = client.get("/sdc-inventory/robots/a664")

    assert response.status_code == error.status_code
    assert response.json()["detail"] == error.code


def test_installations_keep_pagination_and_filter_response_scope(monkeypatch):
    monkeypatch.setattr(
        sdc_inventory,
        "_get",
        lambda *_args, **_kwargs: {
            "count": 250,
            "next": "https://untrusted.example/?offset=50",
            "results": [
                {
                    "id": 1,
                    "rover": "a664",
                    "slot": "/boards/dr_killswitch",
                    "hardware_unit": {
                        "model": {"id": 1806, "name": "dr_killswitch_r1_0"},
                        "serial_number": "123",
                        "secret": "never-return",
                    },
                },
                {"id": 2, "rover": "a900", "slot": "/boards/dr_killswitch"},
                {"id": 3, "slot": "/boards/dr_killswitch"},
            ],
        },
    )
    result = sdc_inventory.fetch_installations("token", "a664", offset=0, limit=50)
    assert [item["id"] for item in result["items"]] == [1]
    assert result["next_offset"] == 3
    assert result["complete"] is False
    assert result["excluded_items"] == 2
    assert "secret" not in str(result)
    assert "untrusted" not in str(result)


def test_full_page_at_exact_upstream_count_is_complete(monkeypatch):
    monkeypatch.setattr(
        sdc_inventory,
        "_get",
        lambda *_args, **_kwargs: {
            "count": 2,
            "next": None,
            "results": [
                {"id": 1, "rover": "a664"},
                {"id": 2, "rover": "a664"},
            ],
        },
    )
    result = sdc_inventory.fetch_installations("token", "a664", limit=2)
    assert result["next_offset"] is None
    assert result["complete"] is True
    assert result["page_complete"] is True


def test_last_page_is_exhausted_without_claiming_it_contains_prior_pages(monkeypatch):
    monkeypatch.setattr(
        sdc_inventory,
        "_get",
        lambda *_args, **_kwargs: {
            "count": 4,
            "next": None,
            "results": [
                {"id": 3, "rover": "a664"},
                {"id": 4, "rover": "a664"},
            ],
        },
    )
    result = sdc_inventory.fetch_installations("token", "a664", limit=2, offset=2)
    assert result["next_offset"] is None
    assert result["complete"] is False
    assert result["page_complete"] is True


def test_non_json_or_large_upstream_is_rejected(monkeypatch):
    real_client = httpx.Client
    monkeypatch.setattr(
        sdc_inventory.httpx,
        "Client",
        lambda **kwargs: real_client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=b"x" * (sdc_inventory.MAX_RESPONSE_BYTES + 1),
                )
            ),
        ),
    )
    with pytest.raises(sdc_inventory.InventoryError):
        sdc_inventory.fetch_card("token", "a664")
