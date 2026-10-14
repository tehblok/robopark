from __future__ import annotations

import json
import logging

from requests import Response
from yandex_tracker_client import TrackerClient, collections
from yandex_tracker_client.connection import decode_response
from yandex_tracker_client.objects import Reference, Resource
from yandex_tracker_client.uriutils import Matcher

from robopark_api.services import tracker_client
from robopark_api.services.tracker_sdk_compat import ensure_offices_collection


def _response(document: object) -> Response:
    response = Response()
    response.status_code = 200
    response._content = json.dumps(document).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def test_office_reference_decodes_without_http_and_preserves_nested_values(monkeypatch):
    ensure_offices_collection()
    sdk = TrackerClient(token="test", org_id="test")

    def unexpected_http(*_args, **_kwargs):
        raise AssertionError("unexpected HTTP")

    monkeypatch.setattr(
        sdk._connection.session,
        "request",
        unexpected_http,
    )
    document = {
        "self": "https://st-api.yandex-team.ru/v2/users/robot",
        "id": "robot",
        "display": "Robot Operator",
        "office": {
            "self": "https://st-api.yandex-team.ru/v2/offices/42",
            "id": 42,
            "display": "Moscow",
        },
    }

    user = decode_response(_response(document), sdk._connection)

    assert isinstance(user, Resource)
    assert user.id == "robot"
    assert user.display == "Robot Operator"
    assert isinstance(user.office, Reference)
    assert user.office.id == 42
    assert user.office.display == "Moscow"
    assert user.office._collection.fields == {
        "self": None,
        "id": None,
        "display": None,
    }


def test_repeated_client_construction_does_not_duplicate_office_registration():
    first = ensure_offices_collection()
    before = len(collections.CollectionMeta.matcher._patterns)

    first_client = tracker_client._import_startrek()(token="one", org_id="test")
    second_client = tracker_client._import_startrek()(token="two", org_id="test")
    first_client._connection.session.close()
    second_client._connection.session.close()

    assert ensure_offices_collection() is first
    assert len(collections.CollectionMeta.matcher._patterns) == before


def test_native_office_collection_is_not_replaced(monkeypatch):
    matcher = Matcher()
    monkeypatch.setattr(collections.CollectionMeta, "matcher", matcher)

    class NativeOffices(collections.Collection):
        path = "/{api_version}/offices/{id}"
        fields = {"native": None}

    assert ensure_offices_collection() is NativeOffices
    assert matcher.match("/v2/offices/42") is NativeOffices
    assert len(matcher._patterns) == 1


def test_unknown_collection_still_warns(caplog):
    ensure_offices_collection()

    with caplog.at_level(logging.WARNING, logger="yandex_tracker_client.collections"):
        matched = collections.match_collection("/v2/robopark-unknown/42")

    assert matched is collections.Unknown
    assert "Unrecognized collection: /v2/robopark-unknown/42" in caplog.text
