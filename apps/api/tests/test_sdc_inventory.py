from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest

from conftest import login_as
from robopark_api.routers import sdc_inventory as inventory_router
from robopark_api.services import (
    corporate_gateway_client,
    corporate_gateway_contract,
    emergency_scope,
    inventory_server_circuit,
    platform_settings,
    sdc_inventory,
)


@pytest.fixture(autouse=True)
def _isolated_inventory_server_circuit(monkeypatch):
    circuit = inventory_server_circuit.InventoryServerCircuit()
    monkeypatch.setattr(inventory_server_circuit, "inventory_server_circuit", circuit)
    return circuit


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
    with pytest.raises(sdc_inventory.InventoryError, match="sdc_inventory_request_rejected"):
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


@pytest.mark.parametrize(
    "status,body,expected_error,availability",
    [
        (400, b"{}", "sdc_inventory_request_rejected", False),
        (409, b"{}", "sdc_inventory_request_rejected", False),
        (422, b"{}", "sdc_inventory_request_rejected", False),
        (429, b"{}", "sdc_inventory_rate_limited", False),
        (500, b"{}", "sdc_inventory_upstream_error", True),
        (200, b"not-json", "sdc_inventory_response_invalid", False),
    ],
)
def test_inventory_client_classifies_only_transport_and_5xx_as_availability(
    monkeypatch, status, body, expected_error, availability
):
    real_client = httpx.Client

    def factory(**kwargs):
        return real_client(
            **kwargs,
            transport=httpx.MockTransport(lambda _request: httpx.Response(status, content=body)),
        )

    monkeypatch.setattr(sdc_inventory.httpx, "Client", factory)
    with pytest.raises(sdc_inventory.InventoryError, match=expected_error) as caught:
        sdc_inventory.fetch_card("secret", "a664")
    assert isinstance(caught.value, sdc_inventory.InventoryAvailabilityError) is availability


def test_inventory_client_classifies_transport_timeout_as_availability(monkeypatch):
    real_client = httpx.Client

    def factory(**kwargs):
        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)

        return real_client(**kwargs, transport=httpx.MockTransport(timeout))

    monkeypatch.setattr(sdc_inventory.httpx, "Client", factory)
    with pytest.raises(sdc_inventory.InventoryAvailabilityError, match="sdc_inventory_unavailable"):
        sdc_inventory.fetch_card("secret", "a664")


def test_inventory_probe_uses_three_second_budget_and_healthy_reads_keep_ten(monkeypatch):
    timeouts = []
    real_client = httpx.Client

    def factory(**kwargs):
        timeouts.append(kwargs["timeout"])
        return real_client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"name": "a664"})
            ),
        )

    monkeypatch.setattr(sdc_inventory.httpx, "Client", factory)

    sdc_inventory.fetch_card("secret", "a664", timeout=3)
    sdc_inventory.fetch_card("secret", "a664")

    assert timeouts == [3, 10]


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


def test_test_gateway_uses_owner_mac_when_server_token_is_missing(
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
        lambda *_args: None,
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


def test_healthy_server_inventory_is_primary_and_does_not_call_mac(
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
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected Mac fallback")),
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


def test_server_only_parallel_request_waits_for_healthy_cold_probe(
    db_session, test_settings, monkeypatch
):
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = False
    test_settings.mac_corporate_gateway_enabled = False
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: "server-token")
    probe_started = Event()
    release_probe = Event()
    calls = []

    def healthy(_token, _name, *, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            probe_started.set()
            assert release_probe.wait(timeout=1)
        return {"robot": {"storage": "Server path"}}

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(
            inventory_router._server_read,
            healthy,
            db_session,
            "a664",
            endpoint="rover_card",
            wait_for_probe=True,
        )
        assert probe_started.wait(timeout=1)
        second = workers.submit(
            inventory_router._server_read,
            healthy,
            db_session,
            "a664",
            endpoint="rover_card",
            wait_for_probe=True,
        )
        release_probe.set()
        outcomes = [first.result(timeout=2), second.result(timeout=2)]

    assert [result[0]["robot"]["storage"] for result in outcomes] == [
        "Server path",
        "Server path",
    ]
    assert sorted(calls) == [3, 10]


def test_gateway_enabled_parallel_request_waits_for_healthy_cold_probe(
    db_session, seed_admin, test_settings, monkeypatch
):
    monkeypatch.setattr(inventory_router, "_access", lambda *_args, **_kwargs: "a664")
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: "server-token")
    monkeypatch.setattr(corporate_gateway_client, "enabled", lambda _settings: True)
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected Mac fallback")),
    )
    probe_started = Event()
    release_probe = Event()
    second_started = Event()

    def healthy(_token, _name, *, timeout):
        if timeout == 3:
            probe_started.set()
            assert release_probe.wait(timeout=1)
        return {"robot": {"storage": "Server path"}}

    monkeypatch.setattr(sdc_inventory, "fetch_card", healthy)

    def request():
        return inventory_router.robot_card(
            robot="a664",
            park_id=None,
            user=seed_admin,
            db=db_session,
            settings=test_settings,
        )

    def second_request():
        second_started.set()
        return request()

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(request)
        assert probe_started.wait(timeout=1)
        second = workers.submit(second_request)
        assert second_started.wait(timeout=1)
        assert not second.done()
        release_probe.set()
        outcomes = [first.result(timeout=2), second.result(timeout=2)]

    assert [result["robot"]["storage"] for result in outcomes] == [
        "Server path",
        "Server path",
    ]


def test_missing_server_token_returns_mac_availability_failure(
    client, seed_admin, test_settings, monkeypatch
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    test_settings.mac_corporate_server_fallback_enabled = False
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: None)
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **_kwargs: (_ for _ in ()).throw(
            corporate_gateway_client.CorporateGatewayError(503, "corporate_unavailable")
        ),
    )
    response = client.get("/sdc-inventory/robots/a664")
    assert response.status_code == 503
    assert response.json()["detail"] == "corporate_unavailable"


def test_server_inventory_availability_failure_uses_owner_mac(
    client, seed_admin, test_settings, monkeypatch
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: "server-token")
    monkeypatch.setattr(
        sdc_inventory,
        "fetch_card",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sdc_inventory.InventoryAvailabilityError("sdc_inventory_unavailable")
        ),
    )
    mac_calls = []
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **kwargs: (
            mac_calls.append(kwargs)
            or corporate_gateway_contract.CorporateReadResponse(
                200, {}, {"name": "a664", "storage": "Owner Mac"}
            )
        ),
    )

    response = client.get("/sdc-inventory/robots/a664")

    assert response.status_code == 200
    assert response.json()["robot"]["storage"] == "Owner Mac"
    assert len(mac_calls) == 1


def test_repeated_server_inventory_failure_uses_fast_owner_mac_fallback(
    client, seed_admin, test_settings, monkeypatch
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: "cooldown-token")
    server_calls = []
    mac_calls = []

    def unavailable(*_args, **_kwargs):
        server_calls.append(True)
        raise sdc_inventory.InventoryAvailabilityError("sdc_inventory_unavailable")

    monkeypatch.setattr(sdc_inventory, "fetch_card", unavailable)
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **kwargs: (
            mac_calls.append(kwargs)
            or corporate_gateway_contract.CorporateReadResponse(
                200, {}, {"name": "a664", "storage": "Owner Mac"}
            )
        ),
    )

    first = client.get("/sdc-inventory/robots/a664")
    second = client.get("/sdc-inventory/robots/a664")

    assert first.status_code == second.status_code == 200
    assert len(server_calls) == 1
    assert len(mac_calls) == 2


def test_inventory_server_recovery_is_probed_once_after_cooldown(
    client, seed_admin, test_settings, monkeypatch
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    now = [100.0]
    circuit = inventory_server_circuit.InventoryServerCircuit(
        cooldown_seconds=10, now=lambda: now[0]
    )
    monkeypatch.setattr(inventory_server_circuit, "inventory_server_circuit", circuit)
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: "recovery-token")
    server_calls = []

    def fetch(*_args, **_kwargs):
        server_calls.append(True)
        if len(server_calls) == 1:
            raise sdc_inventory.InventoryAvailabilityError("sdc_inventory_unavailable")
        return {
            "source": "SDC Inventory",
            "robot_name": "a664",
            "checked_at": "2026-10-16T00:00:00+00:00",
            "physical_inspection": "not_performed",
            "robot": {"name": "a664", "storage": "Recovered server"},
        }

    mac_calls = []
    monkeypatch.setattr(sdc_inventory, "fetch_card", fetch)
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **kwargs: (
            mac_calls.append(kwargs)
            or corporate_gateway_contract.CorporateReadResponse(
                200, {}, {"name": "a664", "storage": "Owner Mac"}
            )
        ),
    )

    assert client.get("/sdc-inventory/robots/a664").json()["robot"]["storage"] == "Owner Mac"
    assert client.get("/sdc-inventory/robots/a664").json()["robot"]["storage"] == "Owner Mac"
    now[0] += 10
    recovered = client.get("/sdc-inventory/robots/a664")
    healthy = client.get("/sdc-inventory/robots/a664")

    assert recovered.json()["robot"]["storage"] == "Recovered server"
    assert healthy.json()["robot"]["storage"] == "Recovered server"
    assert len(server_calls) == 3
    assert len(mac_calls) == 2


def test_inventory_half_open_auth_refusal_is_not_bypassed_through_mac(
    client, seed_admin, test_settings, monkeypatch
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    now = [100.0]
    circuit = inventory_server_circuit.InventoryServerCircuit(
        cooldown_seconds=10, now=lambda: now[0]
    )
    monkeypatch.setattr(inventory_server_circuit, "inventory_server_circuit", circuit)
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: "auth-token")
    server_calls = []

    def fetch(*_args, **_kwargs):
        server_calls.append(True)
        if len(server_calls) == 1:
            raise sdc_inventory.InventoryAvailabilityError("sdc_inventory_unavailable")
        if len(server_calls) == 2:
            raise sdc_inventory.InventoryError("sdc_inventory_access_denied")
        return {
            "source": "SDC Inventory",
            "robot_name": "a664",
            "checked_at": "2026-10-16T00:00:00+00:00",
            "physical_inspection": "not_performed",
            "robot": {"name": "a664", "storage": "Server path"},
        }

    mac_calls = []
    monkeypatch.setattr(sdc_inventory, "fetch_card", fetch)
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **kwargs: (
            mac_calls.append(kwargs)
            or corporate_gateway_contract.CorporateReadResponse(
                200, {}, {"name": "a664", "storage": "Owner Mac"}
            )
        ),
    )

    assert client.get("/sdc-inventory/robots/a664").status_code == 200
    now[0] += 10
    refused = client.get("/sdc-inventory/robots/a664")
    healthy = client.get("/sdc-inventory/robots/a664")

    assert refused.status_code == 403
    assert refused.json()["detail"] == "sdc_inventory_access_denied"
    assert healthy.status_code == 200
    assert healthy.json()["robot"]["storage"] == "Server path"
    assert len(mac_calls) == 1


def test_inventory_cooldown_isolated_by_endpoint(client, seed_admin, test_settings, monkeypatch):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: "endpoint-token")
    card_calls = []
    installation_calls = []

    def unavailable_card(*_args, **_kwargs):
        card_calls.append(True)
        raise sdc_inventory.InventoryAvailabilityError("sdc_inventory_unavailable")

    def server_installations(*_args, **_kwargs):
        installation_calls.append(True)
        return {"items": [], "complete": True}

    monkeypatch.setattr(sdc_inventory, "fetch_card", unavailable_card)
    monkeypatch.setattr(sdc_inventory, "fetch_installations", server_installations)
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **_kwargs: corporate_gateway_contract.CorporateReadResponse(
            200, {}, {"name": "a664", "storage": "Owner Mac"}
        ),
    )

    assert client.get("/sdc-inventory/robots/a664").status_code == 200
    installations = client.get("/sdc-inventory/robots/a664/installations")

    assert installations.status_code == 200, installations.text
    assert card_calls == [True]
    assert installation_calls == [True]


def test_inventory_cooldown_does_not_hide_mac_unavailability(
    client, seed_admin, test_settings, monkeypatch
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: "unavailable-token")
    server_calls = []

    def unavailable(*_args, **_kwargs):
        server_calls.append(True)
        raise sdc_inventory.InventoryAvailabilityError("sdc_inventory_unavailable")

    monkeypatch.setattr(sdc_inventory, "fetch_card", unavailable)
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **_kwargs: (_ for _ in ()).throw(
            corporate_gateway_client.CorporateGatewayError(503, "corporate_unavailable")
        ),
    )

    first = client.get("/sdc-inventory/robots/a664")
    second = client.get("/sdc-inventory/robots/a664")

    assert first.status_code == second.status_code == 503
    assert first.json()["detail"] == second.json()["detail"] == "corporate_unavailable"
    assert server_calls == [True]


@pytest.mark.parametrize(
    "status,body,expected_status,uses_mac",
    [
        (401, b"{}", 403, False),
        (403, b"{}", 403, False),
        (404, b"{}", 404, False),
        (429, b"{}", 429, False),
        (400, b"{}", 502, False),
        (200, b"not-json", 502, False),
        (503, b"{}", 200, True),
    ],
)
def test_router_falls_back_only_for_real_server_availability(
    client,
    seed_admin,
    test_settings,
    monkeypatch,
    status,
    body,
    expected_status,
    uses_mac,
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: "server-token")
    real_client = httpx.Client

    def factory(**kwargs):
        return real_client(
            **kwargs,
            transport=httpx.MockTransport(lambda _request: httpx.Response(status, content=body)),
        )

    mac_calls = []
    monkeypatch.setattr(sdc_inventory.httpx, "Client", factory)
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **kwargs: (
            mac_calls.append(kwargs)
            or corporate_gateway_contract.CorporateReadResponse(
                200, {}, {"name": "a664", "storage": "Owner Mac"}
            )
        ),
    )

    response = client.get("/sdc-inventory/robots/a664")

    assert response.status_code == expected_status, response.text
    assert len(mac_calls) == int(uses_mac)


@pytest.mark.parametrize(
    "server_error,expected_status",
    [
        ("sdc_inventory_access_denied", 403),
        ("sdc_inventory_not_found", 404),
        ("sdc_inventory_response_invalid", 502),
        ("sdc_inventory_response_too_large", 502),
    ],
)
def test_test_gateway_does_not_bypass_server_denial_not_found_or_invalid_response(
    client, seed_admin, test_settings, monkeypatch, server_error, expected_status
):
    login_as(client, seed_admin.username, "secret")
    test_settings.robopark_environment = "test"
    test_settings.emergency_bridge_enabled = True
    test_settings.mac_corporate_gateway_enabled = True
    test_settings.mac_corporate_server_fallback_enabled = True
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda *_args: "server-token")
    monkeypatch.setattr(
        sdc_inventory,
        "fetch_card",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sdc_inventory.InventoryError(server_error)
        ),
    )
    monkeypatch.setattr(
        corporate_gateway_client,
        "read",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("permission bypass")),
    )

    response = client.get("/sdc-inventory/robots/a664")

    assert response.status_code == expected_status
    assert response.json()["detail"] == server_error


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
