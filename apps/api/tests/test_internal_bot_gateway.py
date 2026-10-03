from __future__ import annotations

import json
from pathlib import Path

from robopark_api.models import Park
from robopark_api.services import platform_settings, tracker_client

BRIDGE_KEY = "b" * 48
BRIDGE_HEADERS = {"X-Robopark-Bot-Key": BRIDGE_KEY}


class _Resource:
    def __init__(self, payload: dict):
        self._value = payload


class _Links:
    def get_all(self):
        return [
            _Resource(
                {
                    "type": _Resource({"id": "subtask"}),
                    "direction": "outward",
                    "object": _Resource(
                        {"key": "PARK-7", "display": "Park task", "privateField": "secret"}
                    ),
                    "createdBy": {"login": "private-user"},
                }
            )
        ]


class _Changelog:
    def get_all(self, **kwargs):
        assert kwargs == {"field": "status"}
        return [
            _Resource(
                {
                    "updatedAt": "2026-09-28T09:00:00Z",
                    "updatedBy": {"login": "private-user"},
                    "fields": [
                        _Resource(
                            {
                                "field": _Resource({"id": "status"}),
                                "from": _Resource({"key": "new"}),
                                "to": _Resource({"key": "queued"}),
                            }
                        )
                    ],
                }
            )
        ]


class _Issue(_Resource):
    links = _Links()
    changelog = _Changelog()


class _Issues:
    def __init__(self):
        self.find_calls: list[tuple[str, dict]] = []
        self.issue = _Issue(
            {
                "key": "SDCFLEETOPS-1",
                "summary": "Repair robot",
                "status": _Resource({"key": "queued", "display": "В очереди"}),
                "type": _Resource({"key": "repair", "display": "Repair"}),
                "tags": ["Next"],
                "followers": [{"login": "private-user"}],
                "createdBy": {"login": "private-user"},
                "self": "https://tracker.invalid/issue/1",
            }
        )

    def find(self, query: str, **kwargs):
        self.find_calls.append((query, kwargs))
        return [self.issue, self.issue, self.issue]

    def __getitem__(self, key: str):
        if key == "MISSING-404":
            raise type("NotFound", (Exception,), {})()
        return self.issue


class _Client:
    def __init__(self):
        self.issues = _Issues()


def _configure_bridge(monkeypatch, tmp_path: Path) -> Path:
    secret_path = tmp_path / "bot-bridge-key"
    secret_path.write_text(f"{BRIDGE_KEY}\n", encoding="utf-8")
    secret_path.chmod(0o600)
    monkeypatch.setenv("ROBOPARK_BOT_BRIDGE_KEY_FILE", str(secret_path))
    return secret_path


def _configure_tracker(db_session) -> None:
    platform_settings.set_setting(
        db_session,
        platform_settings.TRACKER_TOKEN_KEY,
        "tracker-test-token",
    )


def _configure_active_queue(db_session, queue: str = "SDCFLEETOPS") -> Park:
    park = Park(
        name=f"Park {queue}",
        tag=f"park-{queue.lower()}",
        is_active=True,
        tracker_queue=queue,
    )
    db_session.add(park)
    db_session.commit()
    return park


def _configure_auxiliary_queues(test_settings, tmp_path: Path, queues: list[str]) -> Path:
    host_data = tmp_path / "host-data"
    data = host_data / "telegram-bot" / "data"
    data.mkdir(parents=True)
    config = data / "robopark-settings.json"
    config.write_text(
        json.dumps({"version": 1, "auxiliary_tracker_queues": queues}),
        encoding="utf-8",
    )
    config.chmod(0o600)
    test_settings.host_data_path = str(host_data)
    return config


def test_gateway_fails_closed_without_a_safe_server_key(client, monkeypatch, tmp_path):
    missing = tmp_path / "missing-key"
    monkeypatch.setenv("ROBOPARK_BOT_BRIDGE_KEY_FILE", str(missing))

    response = client.post(
        "/internal/bot/tracker/search",
        headers=BRIDGE_HEADERS,
        json={"query": "Queue: SDCFLEETOPS"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "bot_bridge_unavailable"}


def test_gateway_rejects_missing_or_wrong_bot_key(client, monkeypatch, tmp_path):
    _configure_bridge(monkeypatch, tmp_path)

    missing = client.post(
        "/internal/bot/tracker/search",
        json={"query": "Queue: SDCFLEETOPS"},
    )
    wrong = client.post(
        "/internal/bot/tracker/search",
        headers={"X-Robopark-Bot-Key": "wrong"},
        json={"query": "Queue: SDCFLEETOPS"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json() == {"detail": "invalid_bot_bridge_key"}
    assert wrong.json() == {"detail": "invalid_bot_bridge_key"}


def test_gateway_rejects_symlinked_key_file(client, monkeypatch, tmp_path):
    target = tmp_path / "bridge-target"
    target.write_text(BRIDGE_KEY, encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "bridge-link"
    link.symlink_to(target)
    monkeypatch.setenv("ROBOPARK_BOT_BRIDGE_KEY_FILE", str(link))

    response = client.get(
        "/internal/bot/tracker/issue/SDCFLEETOPS-1",
        headers=BRIDGE_HEADERS,
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "bot_bridge_unavailable"}


def test_search_uses_platform_token_and_preserves_tracker_shape(
    client, db_session, monkeypatch, tmp_path
):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)
    _configure_active_queue(db_session)
    fake = _Client()
    tokens: list[str] = []

    def fake_client(token: str):
        tokens.append(token)
        return fake

    monkeypatch.setattr(tracker_client, "_client", fake_client)

    response = client.post(
        "/internal/bot/tracker/search",
        headers=BRIDGE_HEADERS,
        json={
            "query": "Queue: SDCFLEETOPS",
            "order": "+created",
            "per_page": 1,
            "max_pages": 1,
        },
    )

    assert response.status_code == 200
    assert tokens == ["tracker-test-token"]
    assert response.json() == [
        {
            "key": "SDCFLEETOPS-1",
            "summary": "Repair robot",
            "status": {"key": "queued", "display": "В очереди"},
            "type": {"key": "repair", "display": "Repair"},
            "tags": ["Next"],
        }
    ]
    assert fake.issues.find_calls == [
        (
            "(Queue: SDCFLEETOPS) AND (Queue: SDCFLEETOPS)",
            {"per_page": 1, "order": ["+created"]},
        )
    ]


def test_issue_links_and_status_changelog_keep_nested_tracker_objects(
    client, db_session, monkeypatch, tmp_path
):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)
    _configure_active_queue(db_session)
    fake = _Client()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: fake)

    issue = client.get(
        "/internal/bot/tracker/issue/SDCFLEETOPS-1",
        headers=BRIDGE_HEADERS,
    )
    links = client.get(
        "/internal/bot/tracker/issue/SDCFLEETOPS-1/links",
        headers=BRIDGE_HEADERS,
    )
    changelog = client.get(
        "/internal/bot/tracker/issue/SDCFLEETOPS-1/status-changelog",
        headers=BRIDGE_HEADERS,
    )

    assert issue.status_code == 200
    assert issue.json()["status"] == {"key": "queued", "display": "В очереди"}
    assert "followers" not in issue.json()
    assert "createdBy" not in issue.json()
    assert links.status_code == 200
    assert links.json() == [
        {
            "type": {"id": "subtask"},
            "direction": "outward",
            "object": {"key": "PARK-7", "display": "Park task"},
        }
    ]
    assert changelog.status_code == 200
    assert changelog.json()[0]["fields"][0]["to"] == {"key": "queued"}
    assert "updatedBy" not in changelog.json()[0]


def test_gateway_returns_non_200_for_missing_token_and_tracker_failure(
    client, db_session, monkeypatch, tmp_path
):
    _configure_bridge(monkeypatch, tmp_path)

    no_token = client.get(
        "/internal/bot/tracker/issue/SDCFLEETOPS-1",
        headers=BRIDGE_HEADERS,
    )
    assert no_token.status_code == 503
    assert no_token.json() == {"detail": "tracker_not_configured"}

    _configure_tracker(db_session)
    _configure_active_queue(db_session)
    monkeypatch.setattr(
        tracker_client,
        "_client",
        lambda _token: (_ for _ in ()).throw(tracker_client.TrackerError("upstream failed")),
    )
    failed = client.get(
        "/internal/bot/tracker/issue/SDCFLEETOPS-1/status-changelog",
        headers=BRIDGE_HEADERS,
    )

    assert failed.status_code == 502
    assert failed.json() == {"detail": "tracker_upstream_error"}


def test_gateway_rejects_unbounded_search_and_invalid_issue_key(
    client, db_session, monkeypatch, tmp_path
):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)

    search = client.post(
        "/internal/bot/tracker/search",
        headers=BRIDGE_HEADERS,
        json={"query": "Q" * 2001, "per_page": 51, "max_pages": 41},
    )
    issue = client.get(
        "/internal/bot/tracker/issue/not-a-key",
        headers=BRIDGE_HEADERS,
    )

    assert search.status_code == 422
    assert issue.status_code == 422


def test_gateway_search_requires_an_explicit_queue_scope(client, db_session, monkeypatch, tmp_path):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)
    fake = _Client()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: fake)

    response = client.post(
        "/internal/bot/tracker/search",
        headers=BRIDGE_HEADERS,
        json={"query": 'Summary: "robot"'},
    )

    assert response.status_code == 422
    assert fake.issues.find_calls == []


def test_gateway_rejects_oversized_link_collection_instead_of_truncating(
    client, db_session, monkeypatch, tmp_path
):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)
    _configure_active_queue(db_session)

    class TooManyLinks:
        def get_all(self):
            return [{"object": {"key": f"PARK-{index}"}} for index in range(2_001)]

    fake = _Client()
    fake.issues.issue.links = TooManyLinks()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: fake)

    response = client.get(
        "/internal/bot/tracker/issue/SDCFLEETOPS-1/links",
        headers=BRIDGE_HEADERS,
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "tracker_response_too_large"}


def test_gateway_returns_404_for_missing_issue(client, db_session, monkeypatch, tmp_path):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)
    _configure_active_queue(db_session, "MISSING")
    fake = _Client()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: fake)

    response = client.get(
        "/internal/bot/tracker/issue/MISSING-404",
        headers=BRIDGE_HEADERS,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "tracker_issue_not_found"}


def test_gateway_fails_closed_when_no_active_park_has_a_tracker_queue(
    client, db_session, monkeypatch, tmp_path
):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)
    inactive = _configure_active_queue(db_session)
    inactive.is_active = False
    db_session.commit()
    fake = _Client()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: fake)

    response = client.post(
        "/internal/bot/tracker/search",
        headers=BRIDGE_HEADERS,
        json={"query": "Queue: SDCFLEETOPS"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "tracker_queue_not_authorized"}
    assert fake.issues.find_calls == []


def test_gateway_scopes_search_to_all_active_configured_queues(
    client, db_session, monkeypatch, tmp_path
):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)
    _configure_active_queue(db_session, "SDCFLEETOPS")
    _configure_active_queue(db_session, "SECOND")
    inactive = _configure_active_queue(db_session, "INACTIVE")
    inactive.is_active = False
    db_session.commit()
    fake = _Client()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: fake)

    response = client.post(
        "/internal/bot/tracker/search",
        headers=BRIDGE_HEADERS,
        json={"query": "Queue: SDCFLEETOPS OR Assignee: me", "per_page": 1, "max_pages": 1},
    )

    assert response.status_code == 200
    assert fake.issues.find_calls == [
        (
            "(Queue: SDCFLEETOPS OR Assignee: me) AND (Queue: SDCFLEETOPS OR Queue: SECOND)",
            {"per_page": 1, "order": ["+created"]},
        )
    ]


def test_gateway_allows_royal_configured_auxiliary_tracker_queue(
    client, db_session, test_settings, monkeypatch, tmp_path
):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)
    _configure_auxiliary_queues(test_settings, tmp_path, ["ROBOMAINT"])
    fake = _Client()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: fake)

    response = client.post(
        "/internal/bot/tracker/search",
        headers=BRIDGE_HEADERS,
        json={"query": "Queue: ROBOMAINT", "per_page": 1, "max_pages": 1},
    )

    assert response.status_code == 200
    assert fake.issues.find_calls == [
        (
            "(Queue: ROBOMAINT) AND (Queue: ROBOMAINT)",
            {"per_page": 1, "order": ["+created"]},
        )
    ]


def test_gateway_fails_closed_on_unsafe_auxiliary_queue_config(
    client, db_session, test_settings, monkeypatch, tmp_path
):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)
    _configure_active_queue(db_session)
    config = _configure_auxiliary_queues(test_settings, tmp_path, ["ROBOMAINT"])
    target = tmp_path / "settings-target"
    config.replace(target)
    config.symlink_to(target)
    fake = _Client()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: fake)

    response = client.post(
        "/internal/bot/tracker/search",
        headers=BRIDGE_HEADERS,
        json={"query": "Queue: SDCFLEETOPS"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "bot_config_unavailable"}
    assert fake.issues.find_calls == []


def test_gateway_rejects_an_explicit_search_for_an_unconfigured_queue(
    client, db_session, monkeypatch, tmp_path
):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)
    _configure_active_queue(db_session, "SDCFLEETOPS")
    fake = _Client()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: fake)

    response = client.post(
        "/internal/bot/tracker/search",
        headers=BRIDGE_HEADERS,
        json={"query": "Queue: FOREIGN AND Resolution: empty()"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "tracker_queue_not_authorized"}
    assert fake.issues.find_calls == []


def test_gateway_rejects_issue_and_subresources_outside_active_park_queues(
    client, db_session, monkeypatch, tmp_path
):
    _configure_bridge(monkeypatch, tmp_path)
    _configure_tracker(db_session)
    _configure_active_queue(db_session, "SDCFLEETOPS")
    fake = _Client()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: fake)

    responses = [
        client.get("/internal/bot/tracker/issue/FOREIGN-1", headers=BRIDGE_HEADERS),
        client.get("/internal/bot/tracker/issue/FOREIGN-1/links", headers=BRIDGE_HEADERS),
        client.get(
            "/internal/bot/tracker/issue/FOREIGN-1/status-changelog",
            headers=BRIDGE_HEADERS,
        ),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert all(
        response.json() == {"detail": "tracker_queue_not_authorized"} for response in responses
    )
