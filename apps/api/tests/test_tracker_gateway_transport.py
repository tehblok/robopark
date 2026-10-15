import json

import pytest

from robopark_api.services.tracker_gateway_transport import GatewayCredential, typed_request


def test_gateway_context_is_not_a_corporate_token():
    assert GatewayCredential(7).requester_user_id == 7
    with pytest.raises(ValueError):
        GatewayCredential(True)


def test_sdk_search_becomes_a_typed_read():
    assert typed_request(
        "POST",
        "https://st-api.yandex-team.ru/v2/issues/_search",
        params={"perPage": 50, "page": 2},
        data=json.dumps(
            {
                "query": "Queue: TEST",
                "filter": None,
                "filterId": None,
                "keys": None,
                "queue": None,
                "order": ["createdAt"],
            }
        ),
    ) == (
        "issues_search",
        "search",
        {"per_page": 50, "page": 2, "query": "Queue: TEST", "order": ["createdAt"]},
    )


@pytest.mark.parametrize(
    "suffix,action",
    [
        ("", "issue_read"),
        ("/comments", "comments_read"),
        ("/transitions", "transitions_read"),
        ("/changelog", "history_read"),
    ],
)
def test_sdk_issue_subresources(suffix, action):
    assert typed_request("GET", "https://st-api.yandex-team.ru/v2/issues/TEST-1" + suffix) == (
        action,
        "TEST-1",
        {},
    )


def test_sdk_user_reference_becomes_a_typed_read():
    assert typed_request("GET", "https://st-api.yandex-team.ru/v2/users/11291906") == (
        "user_read",
        "11291906",
        {},
    )


@pytest.mark.parametrize(
    "method,url,options",
    [
        ("GET", "https://attacker.example/v2/issues/TEST-1", {}),
        ("GET", "http://st-api.yandex-team.ru/v2/issues/TEST-1", {}),
        ("GET", "https://st-api.yandex-team.ru:444/v2/issues/TEST-1", {}),
        ("GET", "https://u:p@st-api.yandex-team.ru/v2/issues/TEST-1", {}),
        ("GET", "https://st-api.yandex-team.ru/v2/issues/../myself", {}),
        ("GET", "https://st-api.yandex-team.ru/v2/issues/TEST-1?token=secret", {}),
        ("GET", "https://st-api.yandex-team.ru/v2/issues/TEST-1?page=1", {"params": {"page": 2}}),
        ("PATCH", "https://st-api.yandex-team.ru/v2/issues/TEST-1", {"data": "{}"}),
        ("POST", "https://st-api.yandex-team.ru/v2/issues/TEST-1/comments", {"data": "{}"}),
        ("GET", "https://st-api.yandex-team.ru/v2/attachments/1", {}),
    ],
)
def test_native_gateway_cannot_become_an_arbitrary_proxy(method, url, options):
    with pytest.raises(ValueError):
        typed_request(method, url, **options)


def test_personal_read_never_uses_cached_service_account_data(monkeypatch):
    from robopark_api.services import tracker_cache, tracker_client

    tracker_cache.clear_all_for_tests()
    calls = []

    def get_issue(*, token, key):
        calls.append(token)
        return {
            "key": key,
            "summary": "owner" if isinstance(token, GatewayCredential) else "service",
        }

    monkeypatch.setattr(tracker_client, "get_issue", get_issue)
    assert tracker_cache.get_issue(token="service-token", key="TEST-1")["summary"] == "service"
    assert tracker_cache.get_issue(token=GatewayCredential(7), key="TEST-1")["summary"] == "owner"
    assert len(calls) == 2


def test_personal_blockers_ignore_cache_only_options(monkeypatch):
    from robopark_api.services import tracker_cache, tracker_client

    context = GatewayCredential(7)

    def fetch(*, token, queue, park_tag):
        assert token is context
        return [{"key": "TEST-1"}]

    monkeypatch.setattr(tracker_client, "fetch_park_blockers", fetch)
    assert tracker_cache.fetch_park_blockers(
        token=context, queue="TEST", park_tag="park", max_age_seconds=0, allow_stale=False
    ) == [{"key": "TEST-1"}]


def test_personal_metrics_never_consume_or_publish_service_cache(monkeypatch):
    from robopark_api.services import tracker_cache, tracker_metrics

    tracker_cache.clear_all_for_tests()
    calls = []

    def collect(*, token, **kwargs):
        calls.append(token)
        return {"total": 1 if isinstance(token, GatewayCredential) else 99}

    monkeypatch.setattr(tracker_metrics, "collect_park_metrics", collect)
    scope = {"queue": "TEST", "tag": "park"}
    assert tracker_cache.collect_park_metrics(token="service", **scope) == {"total": 99}
    assert tracker_cache.collect_park_metrics(token=GatewayCredential(7), **scope) == {"total": 1}
    assert tracker_cache.collect_park_metrics(token="service", **scope) == {"total": 99}
    assert len(calls) == 2


def test_sdk_uses_native_context_for_search_and_resources(monkeypatch):
    from robopark_api.services import corporate_gateway_client, tracker_client
    from robopark_api.services.corporate_gateway_contract import CorporateReadResponse

    calls = []

    def native_read(**kwargs):
        calls.append(kwargs)
        row = {
            "self": "https://st-api.yandex-team.ru/v2/issues/TEST-1",
            "key": "TEST-1",
            "summary": "owned",
        }
        return CorporateReadResponse(200, {}, [row] if kwargs["action"] == "issues_search" else row)

    monkeypatch.setattr(corporate_gateway_client, "read", native_read)
    tracker_client.clear_tracker_clients()
    client = tracker_client._client(GatewayCredential(7))
    assert client.issues["TEST-1"].summary == "owned"
    assert (
        list(client.issues.find("Queue: TEST", per_page=50, order=["createdAt"]))[0].key == "TEST-1"
    )
    assert [call["action"] for call in calls] == ["issue_read", "issues_search"]
    assert all(call["requester_user_id"] == 7 for call in calls)
    assert calls[1]["params"] == {"query": "Queue: TEST", "order": ["createdAt"], "per_page": 50}
    tracker_client.clear_tracker_clients()


def test_sdk_issue_projection_resolves_lazy_user_through_typed_read(monkeypatch):
    from robopark_api.services import corporate_gateway_client, tracker_client
    from robopark_api.services.corporate_gateway_contract import CorporateReadResponse

    calls = []

    def native_read(**kwargs):
        calls.append(kwargs)
        if kwargs["action"] == "issue_read":
            payload = {
                "self": "https://st-api.yandex-team.ru/v2/issues/TEST-1",
                "key": "TEST-1",
                "summary": "Owned issue",
                "createdBy": {
                    "self": "https://st-api.yandex-team.ru/v2/users/11291906",
                    "id": "11291906",
                    "display": "Owner Name",
                },
            }
        else:
            assert kwargs["action"] == "user_read"
            payload = {
                "self": "https://st-api.yandex-team.ru/v2/users/11291906",
                "id": "11291906",
                "display": "Owner Name",
                "login": "owner.login",
            }
        return CorporateReadResponse(200, {}, payload)

    monkeypatch.setattr(corporate_gateway_client, "read", native_read)
    tracker_client.clear_tracker_clients()
    issue = tracker_client._client(GatewayCredential(7)).issues["TEST-1"]

    projected = tracker_client.issue_to_dict(issue)

    assert projected["reporter"] == {"display": "Owner Name", "login": "owner.login"}
    assert [(call["action"], call["resource"]) for call in calls] == [
        ("issue_read", "TEST-1"),
        ("user_read", "11291906"),
    ]
    assert all(call["requester_user_id"] == 7 for call in calls)
    tracker_client.clear_tracker_clients()


@pytest.mark.parametrize(
    "status,code,falls_back",
    [
        (503, "corporate_unavailable", True),
        (503, "corporate_gateway_disabled", True),
        (401, "corporate_authentication", False),
        (403, "corporate_access_denied", False),
        (404, "corporate_not_found", False),
        (429, "corporate_rate_limited", False),
        (503, "corporate_invalid_response", False),
    ],
)
def test_read_fallback_is_only_for_availability(monkeypatch, status, code, falls_back):
    from requests import Response, Session

    from robopark_api.services import corporate_gateway_client, tracker_gateway_transport

    calls = []

    def unavailable(**kwargs):
        raise corporate_gateway_client.CorporateGatewayError(status, code)

    def server_request(self, **kwargs):
        calls.append((dict(self.headers), kwargs))
        response = Response()
        response.status_code = 200
        response._content = b'{"key":"TEST-1"}'
        return response

    monkeypatch.setattr(corporate_gateway_client, "read", unavailable)
    monkeypatch.setattr(Session, "request", server_request)
    context = GatewayCredential(7, server_token="server-test-token")
    conn = tracker_gateway_transport.connection(context)
    response = conn.session.request(
        method="GET", url="https://st-api.yandex-team.ru/v2/issues/TEST-1"
    )
    assert response.status_code == (200 if falls_back else status)
    assert len(calls) == int(falls_back)
    if falls_back:
        headers, request = calls[0]
        assert headers["Authorization"] == "OAuth server-test-token"
        assert request["allow_redirects"] is False
        assert request["timeout"] <= 10


def test_no_server_credential_never_attempts_anonymous_fallback(monkeypatch):
    from requests import Session

    from robopark_api.services import corporate_gateway_client, tracker_gateway_transport

    def unavailable(**kwargs):
        raise corporate_gateway_client.CorporateGatewayError(503, "corporate_unavailable")

    monkeypatch.setattr(corporate_gateway_client, "read", unavailable)
    monkeypatch.setattr(Session, "request", lambda *a, **kw: pytest.fail("unexpected server HTTP"))
    conn = tracker_gateway_transport.connection(GatewayCredential(7))
    response = conn.session.request(
        method="GET", url="https://st-api.yandex-team.ru/v2/issues/TEST-1"
    )
    assert response.status_code == 503


def test_native_client_does_not_keep_replaced_server_credential():
    from robopark_api.services import tracker_client

    first = tracker_client._client(GatewayCredential(7, server_token="first-test-token"))
    second = tracker_client._client(GatewayCredential(7, server_token="second-test-token"))
    assert first is not second
    assert "first-test-token" not in str(GatewayCredential(7, server_token="first-test-token"))


@pytest.mark.parametrize(
    "suffix,action",
    [
        ("comments/", "comments_read"),
        ("transitions/", "transitions_read"),
        ("changelog/", "history_read"),
    ],
)
def test_sdk_collections_allow_the_canonical_trailing_slash(suffix, action):
    assert typed_request("GET", "https://st-api.yandex-team.ru/v2/issues/TEST-1/" + suffix) == (
        action,
        "TEST-1",
        {},
    )


def test_sdk_default_search_does_not_send_null_order_to_native_contract():
    from robopark_api.services.corporate_gateway_contract import validate_request

    action, resource, params = typed_request(
        "POST",
        "https://st-api.yandex-team.ru/v2/issues/_search",
        data=json.dumps({"query": "Queue: TEST", "order": None, "filter": None}),
    )
    assert validate_request("tracker", action, resource, params) == {"query": "Queue: TEST"}


def test_owner_mutations_use_typed_pinned_commands_not_server_http(monkeypatch):
    from types import SimpleNamespace

    from robopark_api.services import tracker_client, tracker_gateway_transport
    from robopark_api.services.tracker_gateway_transport import GatewayWriteCredential

    calls = []
    token = GatewayWriteCredential(
        7,
        command_id="42f045af-e203-4cb6-851d-3f7c91e2cf30",
        resource="TEST-1",
        server_token="opaque-server-token",
    )

    def write(context, **kwargs):
        assert context is token
        calls.append(kwargs)
        return {"id": "created-1"}

    monkeypatch.setattr(tracker_gateway_transport, "write", write)
    monkeypatch.setattr(tracker_client, "_run_mutation", lambda fn: fn())
    monkeypatch.setattr(
        tracker_client, "_client", lambda _token: pytest.fail("unexpected direct SDK")
    )
    monkeypatch.setattr(
        tracker_client.httpx, "Client", lambda *a, **kw: pytest.fail("unexpected direct HTTP")
    )
    assert (
        tracker_client.add_comment(
            token=token, key="TEST-1", text="signed", attachment_ids=["photo-1"]
        )["id"]
        == "created-1"
    )
    assert (
        tracker_client.upload_temp_attachment(
            token=token, filename="photo.png", content=b"image", content_type="image/png"
        )
        == "created-1"
    )
    tracker_client.set_repair_fields(
        token=token,
        key="TEST-1",
        component_ids=["part"],
        defect_code="wear",
        solution_method="replace",
        issue_resource=SimpleNamespace(_version=12),
    )
    tracker_client.transition_issue(
        token=token, key="TEST-1", transition="start", resolution="fixed"
    )
    assert [c["action"] for c in calls] == [
        "comment_create",
        "attachment_upload",
        "issue_patch",
        "transition_execute",
    ]
    assert calls[0]["params"] == {"text": "signed", "attachment_ids": ["photo-1"]}
    assert calls[2]["params"]["version"] == "12"
    assert all("opaque-server-token" not in str(call) for call in calls)


@pytest.mark.parametrize(
    "call",
    [
        lambda client, token: client.add_comment(
            token=token, key="TEST-1", text="hello", attachment_ids=["1"]
        ),
        lambda client, token: client.upload_temp_attachment(
            token=token, filename="photo.png", content=b"image", content_type="image/png"
        ),
        lambda client, token: client.add_attachment(
            token=token,
            key="TEST-1",
            filename="photo.png",
            content=b"image",
            content_type="image/png",
        ),
        lambda client, token: client.assign_issue(token=token, key="TEST-1", assignee="owner"),
        lambda client, token: client.transition_issue(
            token=token, key="TEST-1", transition="start"
        ),
    ],
)
def test_read_context_cannot_escape_as_an_oauth_token(monkeypatch, call):
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client.httpx, "Client", lambda *a, **kw: pytest.fail("unexpected corporate HTTP")
    )
    with pytest.raises(tracker_client.TrackerError, match="corporate_write_context_required"):
        call(tracker_client, GatewayCredential(7, server_token="server-credential"))


@pytest.mark.parametrize(
    "code,status,expected",
    [
        ("corporate_write_pinned_unavailable", 503, "network"),
        ("corporate_write_busy", 503, "network"),
        ("corporate_write_outcome_unknown", 409, "corporate_write_outcome_unknown"),
        ("corporate_access_denied", 403, "corporate_access_denied"),
    ],
)
def test_native_mutation_keeps_typed_error_for_safe_outbox_retry(
    monkeypatch, code, status, expected
):
    from robopark_api.services import (
        corporate_write_client,
        tracker_client,
        tracker_gateway_transport,
        tracker_outbox,
    )

    def unavailable(**kwargs):
        raise corporate_write_client.CorporateWriteError(status, code)

    monkeypatch.setattr(corporate_write_client, "execute", unavailable)
    context = tracker_gateway_transport.GatewayWriteCredential(
        7, command_id="7f1343ea-80b2-4f0c-912d-a9593d17ae31", resource="TEST-1"
    )
    with pytest.raises(tracker_client.TrackerError) as failure:
        tracker_client.add_comment(token=context, key="TEST-1", text="once")
    assert failure.value.gateway_code == code
    assert failure.value.status_code == status
    assert tracker_outbox._tracker_error_code(failure.value) == expected
