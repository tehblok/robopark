"""Normalization of the extended Tracker issue DTO.

The UI renders a Tracker-like issue card, so the DTO must carry description,
assignee, reporter, priority, type and dates — regardless of whether the
upstream payload is a REST dict or a Startrek client object.
"""

from types import SimpleNamespace

from robopark_api.services.tracker_client import issue_to_dict


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
