"""Normalization of the extended Tracker issue DTO.

The UI renders a Tracker-like issue card, so the DTO must carry description,
assignee, reporter, priority, type and dates — regardless of whether the
upstream payload is a REST dict or a Startrek client object.
"""

from types import SimpleNamespace

from robopark_api.services.tracker_client import issue_to_dict


def test_search_reuses_user_lookups_and_does_not_hydrate_display_references(monkeypatch):
    from yandex_tracker_client import TrackerClient
    from yandex_tracker_client.objects import Reference, Resource

    from robopark_api.services import tracker_client

    sdk = TrackerClient(token="test", org_id="test")
    calls = []

    def get(*, path, **_kwargs):
        calls.append(path)
        if path == "/v2/users/123":
            return Resource(sdk._connection, path, {"id": "123", "login": "mechanic"})
        if path == "/v2/fields/":
            return []
        if path == "/v2/components/1":
            return Resource(sdk._connection, path, {"id": "1", "name": "Шасси"})
        raise AssertionError(f"Unexpected hydration: {path}")

    monkeypatch.setattr(sdk._connection, "get", get)

    def issues(*_args, **_kwargs):
        return [
            Resource(
                sdk._connection,
                f"/v2/issues/ROBOPARK-{i}",
                {
                    "key": f"ROBOPARK-{i}",
                    "summary": "[447] repair",
                    "assignee": Reference(
                        sdk._connection, "/v2/users/123", {"id": "123", "display": "Механик"}
                    ),
                    "createdBy": Reference(
                        sdk._connection, "/v2/users/123", {"id": "123", "display": "Механик"}
                    ),
                    "components": [
                        Reference(
                            sdk._connection, "/v2/components/1", {"id": "1", "display": "Шасси"}
                        )
                    ],
                },
            )
            for i in range(50)
        ]

    monkeypatch.setattr(sdk.issues, "find", issues)
    monkeypatch.setattr(tracker_client, "_client", lambda _: sdk)
    monkeypatch.setattr(tracker_client, "call_with_retry", lambda fn, **_: fn())

    rows = tracker_client.search_issues(token="test", query="Queue: ROBOPARK")

    assert len(rows) == 50
    assert all(row["assignee"] == {"display": "Механик", "login": "mechanic"} for row in rows)
    assert all(row["reporter"]["login"] == "mechanic" for row in rows)
    assert all(row["components"] == ["Шасси"] for row in rows)
    assert calls == ["/v2/users/123"]

    calls.clear()
    tracker_client.search_issues(token="other-token", query="Queue: ROBOPARK")
    assert calls == ["/v2/users/123"]  # The memo belongs to one search, not another token.


def test_loaded_sdk_fields_match_rest_payload_without_metadata_requests(monkeypatch):
    from yandex_tracker_client import TrackerClient
    from yandex_tracker_client.objects import Reference, Resource

    sdk = TrackerClient(token="test", org_id="test")

    def unexpected_request(**_kwargs):
        raise AssertionError("Loaded fields must not require another HTTP request")

    monkeypatch.setattr(sdk._connection, "get", unexpected_request)
    payload = {
        "key": "ROBOPARK-1",
        "summary": "[447] repair",
        "description": "Описание",
        "resolvedAt": "2026-09-05T10:00:00.000+0000",
        "status": Reference(
            sdk._connection, "/v2/statuses/1", {"key": "closed", "display": "Закрыт"}
        ),
        "resolution": Reference(
            sdk._connection, "/v2/resolutions/1", {"key": "fixed", "display": "Исправлен"}
        ),
        "queue": Reference(sdk._connection, "/v2/queues/ROBOPARK", {"key": "ROBOPARK"}),
        "priority": Reference(
            sdk._connection, "/v2/priorities/1", {"key": "blocker", "display": "Блокер"}
        ),
        "type": Reference(
            sdk._connection, "/v2/issuetypes/1", {"key": "repair", "display": "Ремонт"}
        ),
        "attachment": [
            Resource(
                sdk._connection,
                "/v2/attachments/1",
                {
                    "id": "1",
                    "name": "photo.png",
                    "size": 512,
                    "mimetype": "image/png",
                    "content": "https://tracker.example.invalid/attachments/1",
                },
            )
        ],
    }
    resource = Resource(sdk._connection, "/v2/issues/ROBOPARK-1", payload)
    result = issue_to_dict(resource)
    assert result == issue_to_dict(resource.as_dict())
    assert result["resolved"] == "2026-09-05T10:00:00.000+0000"
    assert result["status_key"] == "closed"
    assert result["type_key"] == "repair"
    assert result["resolution"] == "fixed"
    assert result["attachments"][0]["name"] == "photo.png"
    assert result["attachments"][0]["size"] == 512


def test_rest_dict_payload_is_fully_normalized():
    issue = issue_to_dict(
        {
            "key": "ROBOPARK-1",
            "summary": "[a447] blocker",
            "description": "Робот не заводится",
            "status": {"key": "open", "display": "Открыт"},
            "priority": {"key": "blocker", "display": "Блокер"},
            "type": {"key": "repair", "display": "Ремонт"},
            "queue": {"key": "ROBOPARK"},
            "assignee": {"display": "Иван Петров", "login": "ipetrov"},
            "createdBy": {"display": "Анна Смирнова", "login": "asmirnova"},
            "tags": ["Alpha"],
            "components": [{"display": "Шасси"}],
            "createdAt": "2026-01-01T10:00:00.000+0000",
            "updatedAt": "2026-01-02T10:00:00.000+0000",
        }
    )

    assert issue["key"] == "ROBOPARK-1"
    assert issue["description"] == "Робот не заводится"
    assert issue["status"] == "Открыт"
    assert issue["priority"] == "Блокер"
    assert issue["type"] == "Ремонт"
    assert issue["type_key"] == "repair"
    assert issue["assignee"] == {"display": "Иван Петров", "login": "ipetrov"}
    assert issue["reporter"] == {"display": "Анна Смирнова", "login": "asmirnova"}
    assert issue["components"] == ["Шасси"]
    assert issue["updated"] == "2026-01-02T10:00:00.000+0000"
    assert issue["robot"] == "a447"


def test_startrek_object_payload_is_fully_normalized():
    issue = issue_to_dict(
        SimpleNamespace(
            key="ROBOPARK-2",
            summary="[448] service",
            description="Плановое ТО",
            status=SimpleNamespace(key="queued", display="В очереди"),
            priority=SimpleNamespace(key="normal", display="Обычный"),
            type=SimpleNamespace(key="service", display="Обслуживание"),
            queue=SimpleNamespace(key="ROBOPARK"),
            assignee=SimpleNamespace(display="Иван Петров", login="ipetrov"),
            createdBy=SimpleNamespace(display="Анна", login="asmirnova"),
            tags=["Beta"],
            components=[],
            createdAt="2026-01-01T10:00:00.000+0000",
            updatedAt="",
            resolution=None,
        )
    )

    assert issue["description"] == "Плановое ТО"
    assert issue["priority"] == "Обычный"
    assert issue["type_key"] == "service"
    assert issue["assignee"]["login"] == "ipetrov"
    assert issue["tags"] == ["Beta"]


def test_missing_optional_fields_are_safe():
    """A sparse payload must not raise and must not invent values."""
    issue = issue_to_dict({"key": "ROBOPARK-3", "summary": "no extras"})

    assert issue["description"] == ""
    assert issue["assignee"] is None
    assert issue["reporter"] is None
    assert issue["priority"] == ""
    assert issue["type"] == ""
    assert issue["type_key"] == ""
    assert issue["components"] == []
    assert issue["updated"] == ""


def test_unassigned_issue_has_no_assignee():
    issue = issue_to_dict({"key": "ROBOPARK-4", "summary": "x", "assignee": None})
    assert issue["assignee"] is None


def test_person_accepts_plain_login_string():
    issue = issue_to_dict({"key": "ROBOPARK-5", "summary": "x", "assignee": "ipetrov"})
    assert issue["assignee"] == {"display": "ipetrov", "login": "ipetrov"}


def test_issue_endpoint_exposes_new_fields(client, db_session, seed_royal, monkeypatch):
    from conftest import login_as
    from robopark_api.services import platform_settings, tracker_client

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "t")
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_k: issue_to_dict(
            {
                "key": "ROBOPARK-1",
                "summary": "[a447] blocker",
                "description": "Описание",
                "status": {"key": "open", "display": "Открыт"},
                "priority": {"display": "Блокер"},
                "queue": {"key": "ROBOPARK"},
                "assignee": {"display": "Иван", "login": "ivan"},
                "createdAt": "2026-01-01T10:00:00.000+0000",
            }
        ),
    )

    login_as(client, "royal", "secret")
    body = client.get("/tracker/issues/ROBOPARK-1").json()

    assert body["description"] == "Описание"
    assert body["assignee"]["display"] == "Иван"
    assert body["priority"] == "Блокер"
    assert body["url"].endswith("/ROBOPARK-1")
