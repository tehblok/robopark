from conftest import login_as
from robopark_api.models import Park
from robopark_api.services import platform_settings, tracker_cache
from robopark_api.task_workflow_models import HiddenTask


def _related_issue(key, *, robot="a447", rover=None, tags=None, type_key="service"):
    return {
        "key": key,
        "queue": "ROBOPARK",
        "summary": f"[{robot}] service",
        "robot": robot,
        "rover": rover or [],
        "status_key": "new",
        "status": "New",
        "resolution": "",
        "resolution_key": "",
        "type_key": type_key,
        "hours_created": "1",
        "tags": ["SC"] if tags is None else tags,
    }


def test_related_service_is_readable_but_not_writable(
    client, db_session, seed_mechanic, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    target = _related_issue("ROBOPARK-2")
    anchor = _related_issue("ROBOPARK-1", tags=["Alpha"])
    monkeypatch.setattr(tracker_cache, "get_issue", lambda **kw: target)
    monkeypatch.setattr(tracker_cache, "search_issues", lambda **kw: [anchor, target])
    monkeypatch.setattr(tracker_cache, "list_comments", lambda **kw: [])
    monkeypatch.setattr(
        "robopark_api.routers.tracker_read._work_issue_sla", lambda **kw: kw["issue"]
    )
    login_as(client, "mech1", "secret")
    response = client.get("/tracker/issues/ROBOPARK-2")
    assert response.status_code == 200
    assert response.json()["capabilities"]["comment"] is False
    assert response.json()["capabilities"]["attach"] is False
    assert client.get("/tracker/issues/ROBOPARK-2/timeline").status_code == 200
    assert client.get("/tracker/issues/ROBOPARK-2/comments").status_code == 200
    # Scope for state-changing operations is not widened by read access.
    from robopark_api.services.tracker_policy import is_issue_in_scope

    assert is_issue_in_scope(db_session, seed_mechanic, target) is False
    anchor["tags"] = ["SC"]
    assert client.get("/tracker/issues/ROBOPARK-2").status_code == 403


def test_hidden_task_cannot_prove_related_read_scope(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    target = _related_issue("ROBOPARK-2")
    anchor = _related_issue("ROBOPARK-1", tags=["Alpha"], type_key="repair")
    db_session.add(
        HiddenTask(
            id="hidden-related-anchor",
            issue_key=anchor["key"],
            park_id=seed_park_with_tracker.id,
            reason="hidden anchor",
            actor_user_id=seed_mechanic.id,
            created_at=1,
            updated_at=1,
        )
    )
    db_session.commit()
    monkeypatch.setattr(tracker_cache, "get_issue", lambda **_kwargs: target)
    monkeypatch.setattr(tracker_cache, "search_issues", lambda **_kwargs: [anchor, target])
    login_as(client, "mech1", "secret")

    assert client.get("/tracker/issues/ROBOPARK-2").status_code == 403


def test_multi_identity_related_read_uses_one_bulk_proof_and_accepts_later_match(
    client, db_session, seed_mechanic, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    target = _related_issue("ROBOPARK-2", robot="", rover=["a447", "a448"])
    target["summary"] = "service campaign"
    anchor = _related_issue(
        "ROBOPARK-1", robot="", rover=["YASADR00000000448"], tags=["Alpha"], type_key="repair"
    )
    anchor["summary"] = "Repair campaign"
    calls = []
    monkeypatch.setattr(tracker_cache, "get_issue", lambda **_kwargs: target)
    monkeypatch.setattr(
        tracker_cache, "search_issues", lambda **kwargs: calls.append(kwargs) or [target, anchor]
    )
    monkeypatch.setattr(
        "robopark_api.routers.tracker_read._work_issue_sla", lambda **kwargs: kwargs["issue"]
    )
    login_as(client, "mech1", "secret")

    response = client.get("/tracker/issues/ROBOPARK-2")

    assert response.status_code == 200
    assert len(calls) == 1
    assert 'Summary: "447"' in calls[0]["query"]
    assert 'Summary: "448"' in calls[0]["query"]
    assert 'rover: "YASADR00000000448"' in calls[0]["query"]


def test_excessive_robot_identities_and_foreign_park_deny_without_search(
    client, db_session, seed_mechanic, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    calls = []
    target = _related_issue(
        "ROBOPARK-2", robot="", rover=[f"a{number}" for number in range(100, 165)]
    )
    target["summary"] = "service campaign"
    monkeypatch.setattr(tracker_cache, "get_issue", lambda **_kwargs: target)
    monkeypatch.setattr(tracker_cache, "search_issues", lambda **kwargs: calls.append(kwargs) or [])
    login_as(client, "mech1", "secret")

    assert client.get("/tracker/issues/ROBOPARK-2").status_code == 403
    assert calls == []

    db_session.add(Park(name="Foreign", tag="Foreign", tracker_queue="ROBOPARK"))
    db_session.commit()
    target["rover"] = ["a447"]
    target["tags"] = ["Foreign"]
    assert client.get("/tracker/issues/ROBOPARK-2").status_code == 403
    assert calls == []
