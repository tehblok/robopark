import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.collaboration_models import TrackerClaim
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.schedule_models import ScheduleEntry
from robopark_api.security import hash_password
from robopark_api.services import platform_settings
from robopark_api.task_workflow_models import ReliableAction, TaskMessage, TaskReview

ISSUE_KEY = "ROBOPARK-71"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _issue(**overrides):
    return {
        "key": ISSUE_KEY,
        "summary": "Не работает Лидар",
        "status": "В очереди",
        "status_key": "queued",
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
        "components": [],
        "component_ids": [],
        "defect_code": None,
        "solution_method": None,
        **overrides,
    }


def _operator(db_session, park):
    user = User(
        username="repair-operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=park.id))
    now = datetime.now(UTC)
    db_session.add(
        ScheduleEntry(
            owner_user_id=user.id,
            park_id=park.id,
            kind="shift",
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
    )
    db_session.commit()
    return user


def _prepare(db_session, monkeypatch, issue):
    from robopark_api.services import tracker_client

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: dict(issue))
    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: [
            {"id": "lidar", "label": "Лидар"},
            {"id": "camera", "label": "Камера"},
        ],
        raising=False,
    )


def test_repair_options_return_queue_catalog_snapshot_and_exact_title_suggestion(
    client, db_session, seed_mechanic, monkeypatch
):
    issue = _issue()
    _prepare(db_session, monkeypatch, issue)
    login_as(client, seed_mechanic.username, "secret")

    response = client.get(f"/tracker/issues/{ISSUE_KEY}/repair-options")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "issue_key": ISSUE_KEY,
        "components": [
            {
                "id": "lidar",
                "label": "Лидар",
                "tracker_name": "Лидар",
                "aliases": [],
                "defect_codes": [],
                "solution_methods": [],
            },
            {
                "id": "camera",
                "label": "Камера",
                "tracker_name": "Камера",
                "aliases": [],
                "defect_codes": [],
                "solution_methods": [],
            },
        ],
        "selected_component_ids": [],
        "suggested_component_ids": ["lidar"],
        "suggestion_reason": "exact_title_match",
        "defect_code": None,
        "solution_method": None,
        "solution_methods": [
            {"code": "CHANGE", "label": "Заменил"},
            {"code": "REPAIR", "label": "Отремонтировал"},
            {"code": "MAINTENANCE", "label": "Обслужил"},
            {"code": "DIAG", "label": "Провёл диагностику"},
            {"code": "CONFIG", "label": "Настроил"},
            {"code": "HARD RESET", "label": "Сбросил настройки"},
            {"code": "INSTALL", "label": "Установил"},
            {"code": "RESTART", "label": "Перезапустил"},
        ],
        "defect_method_suggestions": {
            "BD-06": ["REPAIR", "MAINTENANCE"],
            "CH-01": ["MAINTENANCE", "REPAIR"],
            "CH-03": ["CHANGE", "REPAIR"],
            "EL-01": ["CONFIG", "DIAG"],
            "EL-02": ["DIAG", "REPAIR", "CHANGE"],
            "EL-03": ["CONFIG", "RESTART", "DIAG"],
            "EL-04": ["DIAG", "REPAIR"],
            "EL-06": ["CONFIG", "DIAG"],
            "EL-10": ["DIAG", "CHANGE", "REPAIR"],
            "WH-03": ["REPAIR", "INSTALL"],
            "WH-05": ["REPAIR", "CHANGE"],
            "PP-01": ["CHANGE", "INSTALL"],
            "PP-02": ["INSTALL", "CHANGE"],
            "PP-03": ["DIAG", "CONFIG"],
        },
        "field_snapshot": {
            "component_ids": [],
            "defect_code": None,
            "solution_method": None,
        },
    }


def test_claim_without_component_queues_temporary_name_without_catalog_lookup(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _operator(db_session, seed_park_with_tracker)
    issue = _issue(summary="Неисправность без названия компоненты")
    _prepare(db_session, monkeypatch, issue)
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Claim must not load the component catalog")
        ),
    )
    login_as(client, seed_mechanic.username, "secret")

    response = client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "repair-claim-missing"},
    )
    assert response.status_code == 200, response.text
    action = db_session.scalar(
        select(ReliableAction).where(
            ReliableAction.resource_id == ISSUE_KEY,
            ReliableAction.action == "ensure_components",
        )
    )
    assert json.loads(action.payload_json) == {
        "policy": "temporary_component",
        "name": "ROBOT_UNSORTED",
        "depends_on_action_ids": json.loads(action.payload_json)["depends_on_action_ids"],
    }
    assert "ROBOT_SUSPENSION" not in action.payload_json


def test_repair_options_hide_temporary_component_but_keep_it_in_snapshot(
    client, db_session, seed_mechanic, monkeypatch
):
    issue = _issue(components=["ROBOT_UNSORTED"], component_ids=["162206"])
    _prepare(db_session, monkeypatch, issue)
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: [
            {"id": "162206", "label": "ROBOT_UNSORTED"},
            {"id": "lidar", "label": "Лидар"},
        ],
    )
    login_as(client, seed_mechanic.username, "secret")

    response = client.get(f"/tracker/issues/{ISSUE_KEY}/repair-options")

    assert response.status_code == 200
    assert response.json()["components"] == [
        {
            "id": "lidar",
            "label": "Лидар",
            "tracker_name": "Лидар",
            "aliases": [],
            "defect_codes": [],
            "solution_methods": [],
        }
    ]
    assert response.json()["selected_component_ids"] == []
    assert response.json()["field_snapshot"]["component_ids"] == ["162206"]


def test_repair_options_match_full_decorated_label_without_asserting_completed_work(
    client, db_session, seed_mechanic, monkeypatch
):
    issue = _issue(summary="Проверить кабель камеры")
    _prepare(db_session, monkeypatch, issue)
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: [{"id": "42", "label": "ROBOT_SENSORS_CAMERA_WIRE"}],
    )
    login_as(client, seed_mechanic.username, "secret")

    response = client.get(f"/tracker/issues/{ISSUE_KEY}/repair-options")

    assert response.status_code == 200
    assert response.json()["components"][0]["label"] == "Кабель камеры"
    assert response.json()["components"][0]["tracker_name"] == "ROBOT_SENSORS_CAMERA_WIRE"
    assert response.json()["suggested_component_ids"] == ["42"]
    assert "solution_method" not in response.json()["components"][0]


@pytest.mark.parametrize("summary", ["Переднее левое колесо", "Проблема с АКБ"])
def test_repair_options_do_not_autoselect_from_broad_aliases(
    client, db_session, seed_mechanic, monkeypatch, summary
):
    issue = _issue(summary=summary)
    _prepare(db_session, monkeypatch, issue)
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: [
            {"id": "wheel", "label": "ROBOT_SUSPENSION_WHEEL"},
            {
                "id": "front-left",
                "label": "ROBOT_SUSPENSION_WHEELS_FRONT_LEFT",
            },
            {"id": "battery", "label": "ROBOT_BATTERY"},
        ],
    )
    login_as(client, seed_mechanic.username, "secret")

    response = client.get(f"/tracker/issues/{ISSUE_KEY}/repair-options")

    assert response.status_code == 200
    assert response.json()["suggested_component_ids"] == []


def test_structured_submit_needs_no_typed_comment_and_queues_one_field_action(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _operator(db_session, seed_park_with_tracker)
    issue = _issue(components=["Лидар"], component_ids=["lidar"])
    _prepare(db_session, monkeypatch, issue)
    login_as(client, seed_mechanic.username, "secret")
    claimed = client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "repair-claim-existing"},
    )
    assert claimed.status_code == 200, claimed.text
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    claim.state = "active"
    db_session.commit()
    fields = {
        "component_ids": ["lidar"],
        "solution_method": "REPAIR",
        "expected": {
            "component_ids": ["lidar"],
            "defect_code": None,
            "solution_method": None,
        },
    }

    stale = client.post(
        f"/tracker/issues/{ISSUE_KEY}/submit-review",
        headers={"Idempotency-Key": "repair-review-stale"},
        data={
            "defect_code": "EL-02",
            "repair_fields": json.dumps(
                {
                    **fields,
                    "expected": {
                        "component_ids": [],
                        "defect_code": None,
                        "solution_method": None,
                    },
                }
            ),
        },
        files=[("photo", ("robot.png", PNG, "image/png"))],
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "repair_fields_conflict"
    assert (
        db_session.scalar(
            select(ReliableAction.id).where(
                ReliableAction.resource_id == ISSUE_KEY,
                ReliableAction.action == "set_repair_fields",
            )
        )
        is None
    )

    response = client.post(
        f"/tracker/issues/{ISSUE_KEY}/submit-review",
        headers={"Idempotency-Key": "repair-review-structured"},
        data={"defect_code": "EL-02", "repair_fields": json.dumps(fields)},
        files=[("photo", ("robot.png", PNG, "image/png"))],
    )

    assert response.status_code == 200, response.text
    actions = db_session.scalars(
        select(ReliableAction).where(ReliableAction.resource_id == ISSUE_KEY)
    ).all()
    structured = [item for item in actions if item.action == "set_repair_fields"]
    assert len(structured) == 1
    assert json.loads(structured[0].payload_json) == {
        "component_ids": ["lidar"],
        "defect_code": "EL-02",
        "solution_method": "REPAIR",
        "expected": fields["expected"],
    }
    assert not any(item.action == "set_field" for item in actions)
    report = next(item for item in actions if item.action == "comment")
    assert json.loads(report.payload_json)["text"] == (
        "Выполненные работы\nКомпоненты: Лидар\nНеисправность: EL-02\nДействие: Отремонтировал"
    )


def test_temporary_component_requires_structured_real_component_before_review(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _operator(db_session, seed_park_with_tracker)
    issue = _issue(components=["ROBOT_UNSORTED"], component_ids=["162206"])
    _prepare(db_session, monkeypatch, issue)
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: [{"id": "lidar", "label": "Лидар"}],
    )
    login_as(client, seed_mechanic.username, "secret")
    assert (
        client.post(
            f"/tracker/issues/{ISSUE_KEY}/claim",
            headers={"Idempotency-Key": "temporary-claim"},
        ).status_code
        == 200
    )
    db_session.get(TrackerClaim, ISSUE_KEY).state = "active"
    db_session.commit()

    legacy = client.post(
        f"/tracker/issues/{ISSUE_KEY}/submit-review",
        headers={"Idempotency-Key": "temporary-legacy-review"},
        data={"defect_code": "EL-02", "comment": "Готово"},
        files=[("photo", ("robot.png", PNG, "image/png"))],
    )
    assert legacy.status_code == 409
    assert legacy.json()["detail"] == "repair_fields_required"

    temporary_target = client.post(
        f"/tracker/issues/{ISSUE_KEY}/submit-review",
        headers={"Idempotency-Key": "temporary-target-review"},
        data={
            "defect_code": "EL-02",
            "repair_fields": json.dumps(
                {
                    "component_ids": ["162206"],
                    "solution_method": "REPAIR",
                    "expected": {
                        "component_ids": ["162206"],
                        "defect_code": None,
                        "solution_method": None,
                    },
                }
            ),
        },
        files=[("photo", ("robot.png", PNG, "image/png"))],
    )
    assert temporary_target.status_code == 422
    assert temporary_target.json()["detail"] == "task_component_invalid"
    assert (
        db_session.scalar(
            select(ReliableAction.id).where(ReliableAction.action == "set_repair_fields")
        )
        is None
    )

    completed = client.post(
        f"/tracker/issues/{ISSUE_KEY}/submit-review",
        headers={"Idempotency-Key": "temporary-real-review"},
        data={
            "defect_code": "EL-02",
            "repair_fields": json.dumps(
                {
                    "component_ids": ["lidar"],
                    "solution_method": "REPAIR",
                    "expected": {
                        "component_ids": ["162206"],
                        "defect_code": None,
                        "solution_method": None,
                    },
                }
            ),
        },
        files=[("photo", ("robot.png", PNG, "image/png"))],
    )
    assert completed.status_code == 200, completed.text


def test_outbox_resolves_temporary_component_name_to_current_catalog_id(
    db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    payload = {
        "policy": "temporary_component",
        "name": "ROBOT_UNSORTED",
        "depends_on_action_ids": [],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    action = ReliableAction(
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id=ISSUE_KEY,
        action="ensure_components",
        idempotency_key="temporary-component-delivery",
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        payload_json=encoded,
        state="pending",
        next_attempt_at=0,
        created_at=1,
        updated_at=1,
    )
    db_session.add(action)
    db_session.commit()
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda _db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **_kwargs: {"key": ISSUE_KEY, "queue": "ROBOPARK", "components": []},
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "list_queue_components",
        lambda **_kwargs: [{"id": "162206", "label": "ROBOT_UNSORTED"}],
    )
    writes = []
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "set_issue_components",
        lambda **kwargs: writes.append(kwargs["components"]),
    )

    assert tracker_outbox._deliver_action(db_session, action) == {"components": ["162206"]}
    assert writes == [["162206"]]


def test_outbox_temporary_component_preserves_concurrent_real_component(
    db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    payload = {"policy": "temporary_component", "name": "ROBOT_UNSORTED"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    action = ReliableAction(
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id=ISSUE_KEY,
        action="ensure_components",
        idempotency_key="temporary-component-preserve-real",
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        payload_json=encoded,
        state="pending",
        next_attempt_at=0,
        created_at=1,
        updated_at=1,
    )
    db_session.add(action)
    db_session.commit()
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda _db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **_kwargs: {
            "key": ISSUE_KEY,
            "queue": "ROBOPARK",
            "components": ["Лидар"],
            "component_ids": ["lidar"],
        },
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "list_queue_components",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Existing real component must not require catalog")
        ),
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "set_issue_components",
        lambda **_kwargs: pytest.fail("Existing real component must not be overwritten"),
    )

    assert tracker_outbox._deliver_action(db_session, action) == {"already_applied": True}


def test_outbox_temporary_component_cas_conflict_retries_then_preserves_real_component(
    db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import reliable_actions, tracker_client, tracker_outbox

    payload = {"policy": "temporary_component", "name": "ROBOT_UNSORTED"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    action = ReliableAction(
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id=ISSUE_KEY,
        action="ensure_components",
        idempotency_key="temporary-component-cas-race",
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        payload_json=encoded,
        state="pending",
        next_attempt_at=0,
        created_at=1,
        updated_at=1,
    )
    db_session.add(action)
    db_session.commit()
    issue = {
        "key": ISSUE_KEY,
        "queue": "ROBOPARK",
        "components": [],
        "component_ids": [],
        "_tracker_resource": object(),
    }
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda _db: "token")
    monkeypatch.setattr(tracker_outbox.tracker_client, "get_issue", lambda **_kwargs: dict(issue))
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "list_queue_components",
        lambda **_kwargs: [{"id": "162206", "label": "ROBOT_UNSORTED"}],
    )
    writes = []

    class PreconditionError(Exception):
        response = SimpleNamespace(status_code=412)

    def race(**kwargs):
        writes.append(kwargs)
        try:
            raise PreconditionError
        except PreconditionError as cause:
            raise tracker_client.TrackerError("precondition failed") from cause

    monkeypatch.setattr(tracker_outbox.tracker_client, "set_issue_components", race)

    with pytest.raises(tracker_client.TrackerError) as caught:
        tracker_outbox._deliver_action(db_session, action)
    assert str(caught.value) == "temporary_component_version_conflict"
    reliable_actions.schedule_retry(
        db_session,
        action,
        error_code=tracker_outbox._tracker_error_code(caught.value),
    )
    assert action.state == "retry_wait"
    issue.update(components=["Лидар"], component_ids=["lidar"])

    assert tracker_outbox._deliver_action(db_session, action) == {"already_applied": True}
    assert len(writes) == 1
    assert writes[0]["issue_resource"] is not None


@pytest.mark.parametrize(
    "catalog",
    [[], [{"id": "1", "label": "ROBOT_UNSORTED"}, {"id": "2", "label": "ROBOT_UNSORTED"}]],
)
def test_outbox_requires_exactly_one_active_temporary_component(
    db_session, seed_mechanic, monkeypatch, catalog
):
    from robopark_api.services import tracker_outbox

    payload = {"policy": "temporary_component", "name": "ROBOT_UNSORTED"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    action = ReliableAction(
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id=ISSUE_KEY,
        action="ensure_components",
        idempotency_key=f"temporary-component-invalid-{len(catalog)}",
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        payload_json=encoded,
        state="pending",
        next_attempt_at=0,
        created_at=1,
        updated_at=1,
    )
    db_session.add(action)
    db_session.commit()
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda _db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **_kwargs: {"key": ISSUE_KEY, "queue": "ROBOPARK", "components": []},
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client, "list_queue_components", lambda **_kwargs: catalog
    )

    with pytest.raises(tracker_outbox.DeliveryError) as caught:
        tracker_outbox._deliver_action(db_session, action)
    assert caught.value.code == "temporary_component_unavailable"


def test_outbox_structured_fields_detects_conflict_and_accepts_retry_after_timeout(
    db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    payload = {
        "component_ids": ["lidar"],
        "defect_code": "EL-02",
        "solution_method": "REPAIR",
        "expected": {
            "component_ids": [],
            "defect_code": None,
            "solution_method": None,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    action = ReliableAction(
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id=ISSUE_KEY,
        action="set_repair_fields",
        idempotency_key="repair-fields-delivery",
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        payload_json=encoded,
        state="pending",
        next_attempt_at=0,
        created_at=1,
        updated_at=1,
    )
    db_session.add(action)
    db_session.commit()
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda _db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "list_queue_components",
        lambda **_kwargs: [{"id": "lidar", "label": "Лидар"}],
        raising=False,
    )
    writes = []
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "set_repair_fields",
        lambda **kwargs: writes.append(kwargs),
        raising=False,
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **_kwargs: _issue(
            components=["Камера"],
            component_ids=["camera"],
            defect_code=None,
            solution_method=None,
        ),
    )

    try:
        tracker_outbox._deliver_action(db_session, action)
    except tracker_outbox.DeliveryError as exc:
        assert exc.code == "repair_fields_conflict"
    else:
        raise AssertionError("A stale field snapshot must not overwrite Tracker")
    assert writes == []
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **_kwargs: _issue(
            components=["Лидар"],
            component_ids=["lidar"],
            defect_code="EL-02",
            solution_method="REPAIR",
        ),
    )
    assert tracker_outbox._deliver_action(db_session, action) == {"already_applied": True}
    assert writes == []


def test_outbox_structured_fields_treats_component_order_as_equivalent(
    db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    payload = {
        "component_ids": ["lidar", "camera"],
        "defect_code": "EL-02",
        "solution_method": "REPAIR",
        "expected": {
            "component_ids": [],
            "defect_code": None,
            "solution_method": None,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    action = ReliableAction(
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id=ISSUE_KEY,
        action="set_repair_fields",
        idempotency_key="repair-fields-reordered-readback",
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        payload_json=encoded,
        state="pending",
        next_attempt_at=0,
        created_at=1,
        updated_at=1,
    )
    db_session.add(action)
    db_session.commit()
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda _db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **_kwargs: _issue(
            component_ids=["camera", "lidar"],
            defect_code="EL-02",
            solution_method="REPAIR",
        ),
    )
    writes = []
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "set_repair_fields",
        lambda **kwargs: writes.append(kwargs),
    )

    assert tracker_outbox._deliver_action(db_session, action) == {"already_applied": True}
    assert writes == []


@pytest.mark.parametrize("status_code", [412, 428])
def test_outbox_treats_sdk_precondition_status_as_repair_conflict(
    db_session, seed_mechanic, monkeypatch, status_code
):
    from robopark_api.services import tracker_client, tracker_outbox

    payload = {
        "component_ids": ["lidar"],
        "defect_code": "EL-02",
        "solution_method": "REPAIR",
        "expected": {
            "component_ids": [],
            "defect_code": None,
            "solution_method": None,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    action = ReliableAction(
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id=ISSUE_KEY,
        action="set_repair_fields",
        idempotency_key=f"precondition-{status_code}",
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        payload_json=encoded,
        state="pending",
        next_attempt_at=0,
        created_at=1,
        updated_at=1,
    )
    db_session.add(action)
    db_session.commit()
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda _db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **_kwargs: _issue(),
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "list_queue_components",
        lambda **_kwargs: [{"id": "lidar", "label": "Лидар"}],
    )

    class PreconditionError(Exception):
        def __init__(self):
            self.response = SimpleNamespace(status_code=status_code)

    def fail_update(**_kwargs):
        try:
            raise PreconditionError
        except PreconditionError as cause:
            raise tracker_client.TrackerError("precondition failed") from cause

    monkeypatch.setattr(tracker_outbox.tracker_client, "set_repair_fields", fail_update)

    with pytest.raises(tracker_outbox.DeliveryError) as caught:
        tracker_outbox._deliver_action(db_session, action)
    assert caught.value.code == "repair_fields_conflict"


def test_tracker_catalog_excludes_archived_and_structured_update_uses_sdk_version(
    monkeypatch,
):
    from yandex_tracker_client import TrackerClient
    from yandex_tracker_client.objects import Resource

    from robopark_api.services import tracker_client

    queue = SimpleNamespace(
        components=[
            {"id": "active", "display": "Активная"},
            {"id": "retired", "display": "Архивная", "archived": True},
        ]
    )

    class Queues:
        def __getitem__(self, key):
            assert key == "ROBOPARK"
            return queue

    catalog_client = SimpleNamespace(queues=Queues())
    monkeypatch.setattr(tracker_client, "_client", lambda _token: catalog_client)
    monkeypatch.setattr(tracker_client, "_run_tracked", lambda fn, **_kwargs: fn())
    assert tracker_client.list_queue_components(token="token", queue="ROBOPARK") == [
        {"id": "active", "label": "Активная"}
    ]

    sdk = TrackerClient(token="test", org_id="test")
    resource = Resource(
        sdk._connection,
        f"/v2/issues/{ISSUE_KEY}",
        {"key": ISSUE_KEY, "version": 17},
    )
    patches = []

    def patch(*, path, data, version, **_kwargs):
        patches.append({"path": path, "data": data, "version": version})
        return Resource(
            sdk._connection,
            f"/v2/issues/{ISSUE_KEY}",
            {"key": ISSUE_KEY, "version": 18, **data},
        )

    monkeypatch.setattr(sdk._connection, "patch", patch)
    monkeypatch.setattr(tracker_client, "_client", lambda _token: sdk)
    monkeypatch.setattr(tracker_client, "_run_mutation", lambda fn: fn())

    tracker_client.set_issue_components(
        token="token",
        key=ISSUE_KEY,
        components=["temporary"],
        issue_resource=resource,
    )
    assert patches == [
        {
            "path": f"/v2/issues/{ISSUE_KEY}",
            "version": 17,
            "data": {"components": ["temporary"]},
        }
    ]
    patches.clear()
    resource = Resource(
        sdk._connection,
        f"/v2/issues/{ISSUE_KEY}",
        {"key": ISSUE_KEY, "version": 17},
    )

    tracker_client.set_repair_fields(
        token="token",
        key=ISSUE_KEY,
        component_ids=["active"],
        defect_code="EL-02",
        solution_method="REPAIR",
        issue_resource=resource,
    )

    assert patches == [
        {
            "path": f"/v2/issues/{ISSUE_KEY}",
            "version": 17,
            "data": {
                "components": ["active"],
                "60df26695151a36df681d67b--theDefectCode": "EL-02",
                "solutionMethod": "REPAIR",
            },
        }
    ]


def test_tracker_catalog_stops_consuming_lazy_result_at_bound(monkeypatch):
    from robopark_api.services import repair_fields, tracker_client

    consumed = []

    def lazy_components():
        for index in range(repair_fields.MAX_COMPONENTS + 25):
            consumed.append(index)
            yield {"id": str(index), "display": f"Компонента {index}"}

    queue = SimpleNamespace(components=lazy_components())

    class Queues:
        def __getitem__(self, _key):
            return queue

    monkeypatch.setattr(tracker_client, "_client", lambda _token: SimpleNamespace(queues=Queues()))
    monkeypatch.setattr(tracker_client, "_run_tracked", lambda fn, **_kwargs: fn())

    result = tracker_client.list_queue_components(token="token", queue="ROBOPARK")

    assert len(result) == repair_fields.MAX_COMPONENTS
    assert len(consumed) == repair_fields.MAX_COMPONENTS


@pytest.mark.parametrize("failure_code", ["repair_fields_conflict", "repair_component_invalid"])
def test_permanent_repair_failure_returns_review_and_next_submission_skips_failed_transition(
    db_session, seed_mechanic, seed_park_with_tracker, failure_code
):
    from robopark_api.services import task_lifecycle, tracker_outbox

    payload = {
        "component_ids": ["lidar"],
        "defect_code": "EL-02",
        "solution_method": "REPAIR",
        "expected": {
            "component_ids": [],
            "defect_code": None,
            "solution_method": None,
        },
    }

    def action(name, encoded_payload, created_at):
        encoded = json.dumps(
            encoded_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        row = ReliableAction(
            actor_user_id=seed_mechanic.id,
            resource_type="tracker_issue",
            resource_id=ISSUE_KEY,
            action=name,
            idempotency_key="conflicted-review-key",
            payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
            payload_json=encoded,
            state="pending",
            next_attempt_at=0,
            created_at=created_at,
            updated_at=created_at,
        )
        db_session.add(row)
        db_session.flush()
        return row

    fields = action("set_repair_fields", payload, 1)
    primary = action("review", {"depends_on_actions": ["set_repair_fields"]}, 2)
    db_session.add(
        TaskReview(
            id="conflicted-local-review",
            issue_key=ISSUE_KEY,
            state="pending",
            actor_user_id=seed_mechanic.id,
            reviewer_user_id=seed_mechanic.id,
            created_at=2,
            updated_at=2,
        )
    )
    db_session.add(
        TrackerClaim(
            issue_key=ISSUE_KEY,
            park_id=seed_park_with_tracker.id,
            owner_user_id=seed_mechanic.id,
            updated_by_user_id=seed_mechanic.id,
            state="active",
            operator_user_id=seed_mechanic.id,
            updated_at=2,
        )
    )
    db_session.commit()
    fields.state = "needs_attention"
    fields.error_code = failure_code
    tracker_outbox._return_repair_failure(db_session, fields, error_code=failure_code)
    db_session.commit()

    review = db_session.get(TaskReview, "conflicted-local-review")
    assert (review.state, review.return_reason) == ("returned", failure_code)
    db_session.refresh(primary)
    assert (primary.state, primary.error_code) == ("needs_attention", failure_code)
    failed_workflow = task_lifecycle.workflow(
        db_session,
        issue_key=ISSUE_KEY,
        viewer=seed_mechanic,
        issue=_issue(),
    )
    assert failed_workflow["sync_error_code"] == failure_code

    next_mechanic = User(
        username=f"next-repair-mechanic-{failure_code}",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(next_mechanic)
    db_session.flush()
    db_session.add(UserPark(user_id=next_mechanic.id, park_id=seed_park_with_tracker.id))
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    claim.owner_user_id = next_mechanic.id
    claim.updated_by_user_id = next_mechanic.id
    db_session.commit()

    task_lifecycle.submit_review(
        db_session,
        actor=next_mechanic,
        issue_key=ISSUE_KEY,
        defect_code="EL-02",
        filename="robot.png",
        content=PNG,
        content_type="image/png",
        comment=None,
        repair_fields_payload=payload,
        component_options=[{"id": "lidar", "label": "Лидар"}],
        current_issue=_issue(),
        reviewer=seed_mechanic,
        idempotency_key="repaired-review-key",
    )
    retry = db_session.scalar(
        select(ReliableAction).where(
            ReliableAction.action == "review",
            ReliableAction.idempotency_key == "repaired-review-key",
        )
    )
    assert retry is not None
    assert "depends_on_action_ids" not in json.loads(retry.payload_json)
    assert (fields.state, fields.error_code) == (
        "needs_attention",
        "repair_report_superseded",
    )
    assert (primary.state, primary.error_code) == (
        "needs_attention",
        "repair_report_superseded",
    )
    from robopark_api.services import task_timeline

    superseded_message = TaskMessage(sync_state="saved")
    assert task_timeline._sync_state(superseded_message, primary) == "saved"
    db_session.refresh(review)
    assert (review.state, review.return_reason) == ("pending", None)
    tracker_outbox._return_repair_failure(db_session, fields, error_code=failure_code)
    assert review.state == "pending"
    assert (
        task_lifecycle.workflow(
            db_session,
            issue_key=ISSUE_KEY,
            viewer=seed_mechanic,
            issue=_issue(),
        )["sync_error_code"]
        is None
    )

    from robopark_api.services.reliable_actions import retry_needs_attention

    assert retry_needs_attention(db_session, resource_id=ISSUE_KEY) == 0
