"""Durable tool effects and human confirmation, with only the model mocked."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from conftest import login_as
from robopark_api.ai_models import AIAction, AIJob, AIScript
from robopark_api.services.ai import jobs, runtime
from test_ai import enable_host


def call(name, arguments, call_id="call_1"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def queue(client, settings, tmp_path, park, monkeypatch, responses):
    enable_host(settings, tmp_path)
    login_as(client, "admin", "secret")
    monkeypatch.setattr(runtime, "context_tokens", lambda *args, **kwargs: (100, 8192))
    turns = iter(responses)
    monkeypatch.setattr(runtime, "complete_turn", lambda *args: next(turns))
    conversation = client.post("/ai/conversations", json={"park_id": park.id}).json()
    response = client.post(
        f"/ai/conversations/{conversation['id']}/messages",
        json={
            "content": "Выполни указанное действие",
            "use_tools": True,
            "idempotency_key": "tool-message-1",
        },
    )
    assert response.status_code == 202, response.text
    return conversation, response.json()


def step(engine, settings):
    assert jobs.process_job(sessionmaker(bind=engine), settings)


def action(client, job):
    response = client.get(f"/ai/jobs/{job['id']}")
    assert response.status_code == 200, response.text
    return response.json()["actions"][-1]


def script(db):
    row = AIScript(name="Example", source="def main(data):\n    return data", enabled=False)
    db.add(row)
    db.commit()
    return row.id, row.revision


def test_create_script_once_and_show_durable_effect(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    conversation, job = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [
            call(
                "script_create", {"name": "Mapping", "source": "def main(data):\n    return data"}
            ),
            {"role": "assistant", "content": "Создан черновик."},
        ],
    )
    step(db_engine, test_settings)
    step(db_engine, test_settings)
    step(db_engine, test_settings)
    assert [row.name for row in db_session.scalars(select(AIScript))] == ["Mapping"]
    receipt = action(client, job)
    assert receipt["state"] == "succeeded"
    assert receipt["tool"] == "script_create"
    replay = client.post(
        f"/ai/conversations/{conversation['id']}/messages",
        json={
            "content": "Выполни указанное действие",
            "use_tools": True,
            "idempotency_key": "tool-message-1",
        },
    )
    assert replay.json()["id"] == job["id"]
    assert not jobs.process_job(sessionmaker(bind=db_engine), test_settings)


def test_delete_requires_bound_confirmation_and_never_replays(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    script_id, revision = script(db_session)
    _, job = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [
            call("script_delete", {"script_id": script_id, "revision": revision}),
            {"role": "assistant", "content": "Скрипт удалён."},
        ],
    )
    step(db_engine, test_settings)
    receipt = action(client, job)
    assert receipt["state"] == "waiting"
    assert db_session.get(AIScript, script_id) is not None
    url = f"/ai/actions/{receipt['id']}/confirm"
    assert client.post(url, json={"digest": "0" * 64}).status_code == 409
    response = client.post(url, json={"digest": receipt["digest"]})
    assert response.status_code == 200, response.text
    assert client.post(url, json={"digest": receipt["digest"]}).status_code == 200
    step(db_engine, test_settings)
    step(db_engine, test_settings)
    db_session.expire_all()
    assert db_session.get(AIScript, script_id) is None
    assert action(client, job)["state"] == "succeeded"
    assert client.post(url, json={"digest": receipt["digest"]}).status_code == 200
    assert not jobs.process_job(sessionmaker(bind=db_engine), test_settings)


def test_changed_revision_blocks_previously_confirmed_delete(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    script_id, revision = script(db_session)
    _, job = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [call("script_delete", {"script_id": script_id, "revision": revision})],
    )
    step(db_engine, test_settings)
    receipt = action(client, job)
    assert (
        client.post(
            f"/ai/actions/{receipt['id']}/confirm", json={"digest": receipt["digest"]}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/ai/scripts/{script_id}", json={"revision": revision, "name": "Changed"}
        ).status_code
        == 200
    )
    step(db_engine, test_settings)
    db_session.expire_all()
    assert db_session.get(AIScript, script_id).name == "Changed"
    assert action(client, job)["state"] in {"failed", "uncertain"}


def test_role_revocation_before_action_prevents_write(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    _, job = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [
            call(
                "script_create",
                {"name": "Should not exist", "source": "def main(data):\n    return data"},
            )
        ],
    )
    step(db_engine, test_settings)
    seed_admin.is_active = False
    db_session.commit()
    step(db_engine, test_settings)
    db_session.expire_all()
    assert list(db_session.scalars(select(AIScript))) == []
    assert db_session.get(AIJob, job["id"]).state in {"cancelled", "failed"}


def test_delete_conversation_cancels_waiting_action_and_keeps_receipt(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    script_id, revision = script(db_session)
    conversation, job = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [call("script_delete", {"script_id": script_id, "revision": revision})],
    )
    step(db_engine, test_settings)
    receipt = action(client, job)
    assert client.delete(f"/ai/conversations/{conversation['id']}").status_code == 200
    assert (
        client.post(
            f"/ai/actions/{receipt['id']}/confirm", json={"digest": receipt["digest"]}
        ).status_code
        == 404
    )
    db_session.expire_all()
    assert db_session.get(AIScript, script_id) is not None
    assert db_session.get(AIAction, receipt["id"]).state == "cancelled"


def test_recovery_never_requeues_inflight_action(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    _, job = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [call("script_create", {"name": "Example", "source": "def main(data):\n    return data"})],
    )
    step(db_engine, test_settings)
    receipt = db_session.scalar(select(AIAction).where(AIAction.job_id == job["id"]))
    receipt.state = "running"
    db_session.get(AIJob, job["id"]).state = "running"
    db_session.commit()
    assert client.post(f"/ai/jobs/{job['id']}/cancel").status_code == 409
    jobs.recover(db_session)
    db_session.expire_all()
    assert receipt.state == "uncertain"
    assert db_session.get(AIJob, job["id"]).state == "failed"
    assert not jobs.process_job(sessionmaker(bind=db_engine), test_settings)


def test_repeated_model_write_is_deduplicated_and_bounded(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    arguments = {"name": "Only once", "source": "def main(data):\n    return data"}
    _, job = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [call("script_create", arguments, f"call_{index}") for index in range(7)],
    )
    for _ in range(8):
        step(db_engine, test_settings)
    db_session.expire_all()
    assert [row.name for row in db_session.scalars(select(AIScript))] == ["Only once"]
    assert len(list(db_session.scalars(select(AIAction).where(AIAction.job_id == job["id"])))) == 1
    row = db_session.get(AIJob, job["id"])
    assert row.state == "failed"
    assert row.error == "ai_tool_limit"


def test_expired_confirmation_does_not_allow_deletion(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    import time

    script_id, revision = script(db_session)
    _, job = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [call("script_delete", {"script_id": script_id, "revision": revision})],
    )
    step(db_engine, test_settings)
    receipt = action(client, job)
    db_session.get(AIAction, receipt["id"]).expires_at = time.time() - 1
    db_session.commit()
    response = client.post(
        f"/ai/actions/{receipt['id']}/confirm", json={"digest": receipt["digest"]}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "ai_confirmation_expired"
    assert db_session.get(AIScript, script_id) is not None


def test_sandbox_result_redaction_preserves_json_and_hides_credentials(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    script_id, revision = script(db_session)
    row = db_session.get(AIScript, script_id)
    row.enabled, row.tested_revision = True, revision
    db_session.commit()
    _, job = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [
            call("script_run", {"script_id": script_id, "revision": revision, "input": {}}),
            {"role": "assistant", "content": "Готово."},
        ],
    )
    monkeypatch.setattr(
        runtime,
        "broker",
        lambda *args, **kwargs: {
            "output": {
                "link": "https://example.test/?token=private-value",
                "password": "another-private-value",
            }
        },
    )
    step(db_engine, test_settings)
    step(db_engine, test_settings)
    receipt = action(client, job)
    assert receipt["state"] == "succeeded"
    text = json.dumps(receipt["result"], ensure_ascii=False)
    assert "private-value" not in text
    assert "[скрыто]" in text


def test_lost_script_role_hides_saved_answer_and_receipt(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    from robopark_api.models import UserPark
    from robopark_api.services.rbac import get_role_by_slug

    conversation, _ = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [
            call(
                "script_create",
                {"name": "Private script", "source": "def main(data):\n    return data"},
            ),
            {"role": "assistant", "content": "PRIVATE_TEST_INFO"},
        ],
    )
    for _ in range(3):
        step(db_engine, test_settings)
    seed_admin.role_id = get_role_by_slug(db_session, "operator").id
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    response = client.get(f"/ai/conversations/{conversation['id']}")
    assert response.status_code == 200, response.text
    assert "PRIVATE_TEST_INFO" not in response.text
    assert "Private script" not in response.text
    assert "Ответ скрыт" in response.text


@pytest.mark.parametrize("gate", ["unsupported", "maintenance"])
def test_restart_recovers_inflight_receipt_even_when_runtime_is_blocked(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
    gate,
):
    _, job = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [call("script_create", {"name": "Example", "source": "def main(data):\n    return data"})],
    )
    step(db_engine, test_settings)
    receipt = db_session.scalar(select(AIAction).where(AIAction.job_id == job["id"]))
    receipt.state = "running"
    db_session.get(AIJob, job["id"]).state = "running"
    db_session.commit()
    monkeypatch.setattr(jobs, "host_maintenance_active", lambda settings: gate == "maintenance")
    monkeypatch.setattr(
        jobs.policy, "host_status", lambda settings: {"supported": gate != "unsupported"}
    )
    jobs.tick(sessionmaker(bind=db_engine), test_settings, first=True)
    db_session.expire_all()
    assert receipt.state == "uncertain"
    assert db_session.get(AIJob, job["id"]).state == "failed"


def test_read_after_write_gets_fresh_data_instead_of_reusing_prior_result(
    client,
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    _, job = queue(
        client,
        test_settings,
        tmp_path,
        seed_park_with_tracker,
        monkeypatch,
        [
            call("script_list", {}, "before"),
            call(
                "script_create",
                {"name": "New script", "source": "def main(data):\n    return data"},
                "create",
            ),
            call("script_list", {}, "after"),
            {"role": "assistant", "content": "Список обновлён."},
        ],
    )
    for _ in range(7):
        step(db_engine, test_settings)
    receipts = list(
        db_session.scalars(
            select(AIAction)
            .where(AIAction.job_id == job["id"], AIAction.tool == "script_list")
            .order_by(AIAction.ordinal)
        )
    )
    assert len(receipts) == 2
    assert receipts[0].result["items"] == []
    assert receipts[1].result["items"][0]["name"] == "New script"
