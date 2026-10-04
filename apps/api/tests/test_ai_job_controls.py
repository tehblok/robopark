"""AI jobs revalidate mutable controls at claim and publication boundaries."""

import json
import threading

import pytest
from sqlalchemy.orm import sessionmaker

from conftest import login_as
from robopark_api.ai_models import AIJob, AIScript
from robopark_api.services import database_locks
from robopark_api.services.ai import jobs


def _enable_host(settings, tmp_path, *, ready=False):
    path = tmp_path / "ai-runtime-job-controls.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "supported": True,
                "installed": True,
                "enabled": True,
                "ready": ready,
                "reason": None if ready else "model_stopped",
                "model": "bonsai",
                "backend": "cuda",
            }
        )
    )
    settings.ai_runtime_state_path = str(path)


def _queued_script_test(client, settings, tmp_path):
    _enable_host(settings, tmp_path, ready=False)
    login_as(client, "admin", "secret")
    script = client.post(
        "/ai/scripts",
        json={"name": "Mapping", "source": "def main(data):\n    return data"},
    ).json()
    response = client.post(f"/ai/scripts/{script['id']}/test", json={"input": {"a": 1}})
    assert response.status_code == 202
    return script, response.json()


def test_script_test_succeeds_while_llm_is_not_ready(
    client, db_engine, seed_admin, test_settings, tmp_path, monkeypatch
):
    script, job = _queued_script_test(client, test_settings, tmp_path)
    monkeypatch.setattr(
        jobs.runtime,
        "broker",
        lambda *_args, **_kwargs: {"output": {"a": 1}, "stdout": ""},
    )

    assert jobs.process_job(sessionmaker(bind=db_engine), test_settings)

    with sessionmaker(bind=db_engine)() as db:
        assert db.get(AIJob, job["id"]).state == "succeeded"
        assert db.get(AIScript, script["id"]).tested_revision == script["revision"]


def test_disable_completed_during_prepare_prevents_execution(
    client, db_engine, seed_admin, test_settings, tmp_path, monkeypatch
):
    _, job = _queued_script_test(client, test_settings, tmp_path)
    original = jobs._prepare

    def disable_after_prepare(db, settings, row):
        prepared = original(db, settings, row)
        response = client.patch("/ai/config", json={"revision": 1, "enabled": False})
        assert response.status_code == 200
        return prepared

    monkeypatch.setattr(jobs, "_prepare", disable_after_prepare)
    monkeypatch.setattr(
        jobs.runtime,
        "broker",
        lambda *_args, **_kwargs: pytest.fail("disabled job reached sandbox"),
    )

    assert jobs.process_job(sessionmaker(bind=db_engine), test_settings)
    final = client.get(f"/ai/jobs/{job['id']}").json()
    assert (final["state"], final["error"], final["result"]) == (
        "cancelled",
        "ai_disabled",
        None,
    )


def test_script_deleted_during_prepare_prevents_execution(
    client, db_engine, seed_admin, test_settings, tmp_path, monkeypatch
):
    script, job = _queued_script_test(client, test_settings, tmp_path)
    original = jobs._prepare

    def delete_after_prepare(db, settings, row):
        prepared = original(db, settings, row)
        assert client.delete(f"/ai/scripts/{script['id']}").status_code == 200
        return prepared

    monkeypatch.setattr(jobs, "_prepare", delete_after_prepare)
    monkeypatch.setattr(
        jobs.runtime,
        "broker",
        lambda *_args, **_kwargs: pytest.fail("deleted script reached sandbox"),
    )

    assert jobs.process_job(sessionmaker(bind=db_engine), test_settings)
    final = client.get(f"/ai/jobs/{job['id']}")
    assert final.status_code == 200
    assert final.json()["state"] == "cancelled"
    assert final.json()["error"] == "ai_not_found"


def test_disable_while_running_discards_script_test_result(
    client, db_engine, seed_admin, test_settings, tmp_path, monkeypatch
):
    script, job = _queued_script_test(client, test_settings, tmp_path)

    def execute_then_disable(*_args, **_kwargs):
        response = client.patch("/ai/config", json={"revision": 1, "enabled": False})
        assert response.status_code == 200
        return {"output": {"a": 1}, "stdout": ""}

    monkeypatch.setattr(jobs.runtime, "broker", execute_then_disable)

    assert jobs.process_job(sessionmaker(bind=db_engine), test_settings)
    with sessionmaker(bind=db_engine)() as db:
        final = db.get(AIJob, job["id"])
        assert (final.state, final.error, final.result) == (
            "cancelled",
            "ai_disabled",
            None,
        )
        assert db.get(AIScript, script["id"]).tested_revision is None


def test_claim_lock_contention_keeps_job_queued_for_next_tick(
    client, db_engine, seed_admin, test_settings, tmp_path, monkeypatch
):
    _, job = _queued_script_test(client, test_settings, tmp_path)
    factory = sessionmaker(bind=db_engine, future=True)
    lock_held = threading.Event()
    release_lock = threading.Event()
    errors = []

    def hold_controls():
        try:
            with factory() as db, database_locks.database_idempotency_lock(db, "ai-controls"):
                lock_held.set()
                assert release_lock.wait(3)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    holder = threading.Thread(target=hold_controls, name="hold-ai-controls")
    holder.start()
    assert lock_held.wait(3)
    monkeypatch.setattr(database_locks, "LOCK_WAIT_SECONDS", 0.05)
    calls = []
    monkeypatch.setattr(
        jobs.runtime,
        "broker",
        lambda *_args, **_kwargs: calls.append("sandbox") or {"output": {"a": 1}, "stdout": ""},
    )
    try:
        assert jobs.process_job(factory, test_settings)
        with factory() as db:
            waiting = db.get(AIJob, job["id"])
            assert (waiting.state, waiting.error, waiting.result) == ("queued", None, None)
        assert calls == []
    finally:
        release_lock.set()
        holder.join(3)

    assert not holder.is_alive()
    assert not errors
    assert jobs.process_job(factory, test_settings)
    with factory() as db:
        assert db.get(AIJob, job["id"]).state == "succeeded"
    assert calls == ["sandbox"]
