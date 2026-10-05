"""AI jobs revalidate mutable controls at claim and publication boundaries."""

import asyncio
import json
import threading
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

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
                "model": "google/gemma-4-E4B-it-qat-q4_0-gguf",
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


def _queued_issue_chat(client, settings, tmp_path, park):
    _enable_host(settings, tmp_path, ready=True)
    login_as(client, "admin", "secret")
    conversation = client.post(
        "/ai/conversations", json={"park_id": park.id, "issue_key": "ROBOPARK-1"}
    )
    assert conversation.status_code == 201
    response = client.post(
        f"/ai/conversations/{conversation.json()['id']}/messages",
        json={"content": "How do I repair it?", "idempotency_key": "issue-message"},
    )
    assert response.status_code == 202
    return response.json()


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


def test_tracker_fetch_never_runs_under_job_control_locks(
    client,
    db_engine,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    controls_held = threading.Event()
    original_lock = jobs.database_idempotency_lock

    @contextmanager
    def observed_lock(db, key):
        with original_lock(db, key):
            if key == "ai-controls":
                controls_held.set()
            try:
                yield
            finally:
                if key == "ai-controls":
                    controls_held.clear()

    def fetch_issue(**_kwargs):
        assert not controls_held.is_set(), "Tracker fetch ran under ai-controls"
        return {
            "key": "ROBOPARK-1",
            "queue": "ROBOPARK",
            "tags": ["Alpha"],
            "description": "camera",
        }

    monkeypatch.setattr(jobs, "database_idempotency_lock", observed_lock)
    monkeypatch.setattr(jobs.issue_context.platform_settings, "get_tracker_token", lambda db: "x")
    monkeypatch.setattr(jobs.issue_context.tracker_cache, "get_issue", fetch_issue)
    monkeypatch.setattr(jobs.runtime, "context_tokens", lambda *_args: (10, 8192))
    monkeypatch.setattr(jobs.runtime, "complete", lambda *_args: "Safe answer")
    job = _queued_issue_chat(client, test_settings, tmp_path, seed_park_with_tracker)

    assert jobs.process_job(sessionmaker(bind=db_engine), test_settings)
    with sessionmaker(bind=db_engine)() as db:
        assert db.get(AIJob, job["id"]).state == "succeeded"


def test_disable_during_final_tracker_refresh_cancels_without_lock_busy(
    client,
    db_engine,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    inference_finished = threading.Event()
    final_fetch_started = threading.Event()
    release_final_fetch = threading.Event()
    errors = []

    def fetch_issue(**_kwargs):
        if inference_finished.is_set() and threading.current_thread().name == "process-ai-job":
            final_fetch_started.set()
            if not release_final_fetch.wait(3):
                raise TimeoutError("test did not release Tracker fetch")
        return {
            "key": "ROBOPARK-1",
            "queue": "ROBOPARK",
            "tags": ["Alpha"],
            "description": "camera",
        }

    monkeypatch.setattr(jobs.issue_context.platform_settings, "get_tracker_token", lambda db: "x")
    monkeypatch.setattr(jobs.issue_context.tracker_cache, "get_issue", fetch_issue)
    monkeypatch.setattr(jobs.runtime, "context_tokens", lambda *_args: (10, 8192))

    def complete(*_args):
        inference_finished.set()
        return "Never publish"

    monkeypatch.setattr(jobs.runtime, "complete", complete)
    job = _queued_issue_chat(client, test_settings, tmp_path, seed_park_with_tracker)
    factory = sessionmaker(bind=db_engine, future=True)

    def process():
        try:
            jobs.process_job(factory, test_settings)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=process, name="process-ai-job")
    thread.start()
    assert final_fetch_started.wait(3)
    monkeypatch.setattr(database_locks, "LOCK_WAIT_SECONDS", 0.05)
    try:
        response = client.patch("/ai/config", json={"revision": 1, "enabled": False})
        assert response.status_code == 200
    finally:
        release_final_fetch.set()
        thread.join(5)

    assert not thread.is_alive()
    assert not errors
    with factory() as db:
        final = db.get(AIJob, job["id"])
        assert (final.state, final.error, final.result) == ("cancelled", "ai_disabled", None)


def test_remote_issue_moved_during_inference_discards_answer(
    client,
    db_engine,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    moved = threading.Event()

    def fetch_issue(**_kwargs):
        return {
            "key": "ROBOPARK-1",
            "queue": "ROBOPARK",
            "tags": ["Foreign" if moved.is_set() else "Alpha"],
            "description": "camera",
        }

    monkeypatch.setattr(jobs.issue_context.platform_settings, "get_tracker_token", lambda db: "x")
    monkeypatch.setattr(jobs.issue_context.tracker_cache, "get_issue", fetch_issue)
    monkeypatch.setattr(jobs.runtime, "context_tokens", lambda *_args: (10, 8192))

    def complete(*_args):
        moved.set()
        return "Outdated answer"

    monkeypatch.setattr(jobs.runtime, "complete", complete)
    job = _queued_issue_chat(client, test_settings, tmp_path, seed_park_with_tracker)
    factory = sessionmaker(bind=db_engine, future=True)

    assert jobs.process_job(factory, test_settings)
    with factory() as db:
        final = db.get(AIJob, job["id"])
        assert (final.state, final.error, final.result) == (
            "cancelled",
            "ai_issue_park_mismatch",
            None,
        )


def test_worker_recovers_actual_final_commit_failure_without_reexecuting(
    client, db_engine, seed_admin, test_settings, tmp_path, monkeypatch
):
    script, job = _queued_script_test(client, test_settings, tmp_path)
    ordinary_factory = sessionmaker(bind=db_engine, future=True)
    with ordinary_factory() as db:
        db.get(AIJob, job["id"]).state = "held-for-next-tick"
        db.commit()

    stop = asyncio.Event()
    recovery_flags = []
    inference_calls = []
    failure = {"armed": True}
    real_tick = jobs.tick

    class FailFinalCommitSession(Session):
        def commit(self):
            if failure["armed"] and any(
                isinstance(row, AIJob) and row.state == "succeeded" for row in self.dirty
            ):
                failure["armed"] = False
                raise OperationalError("COMMIT", {}, RuntimeError("connection lost"))
            return super().commit()

    factory = sessionmaker(bind=db_engine, future=True, class_=FailFinalCommitSession)
    monkeypatch.setattr(
        jobs.runtime,
        "broker",
        lambda *_args, **_kwargs: (
            inference_calls.append("sandbox") or {"output": {"a": 1}, "stdout": ""}
        ),
    )

    def controlled_tick(_factory, settings, *, first):
        recovery_flags.append(first)
        result = real_tick(_factory, settings, first=first)
        if len(recovery_flags) == 1:
            with ordinary_factory() as db:
                db.get(AIJob, job["id"]).state = "queued"
                db.commit()
        if len(recovery_flags) == 3:
            stop.set()
        return result

    async def no_wait(awaitable, *, timeout):
        del timeout
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(jobs, "tick", controlled_tick)
    monkeypatch.setattr(jobs, "host_maintenance_active", lambda _settings: False)
    monkeypatch.setattr(jobs.asyncio, "wait_for", no_wait)

    asyncio.run(jobs.run_loop(factory, stop, test_settings))

    assert recovery_flags == [True, False, True]
    assert inference_calls == ["sandbox"]
    assert failure["armed"] is False
    with ordinary_factory() as db:
        final = db.get(AIJob, job["id"])
        assert (final.state, final.error, final.result) == (
            "failed",
            "ai_worker_interrupted",
            None,
        )
        assert db.get(AIScript, script["id"]).tested_revision is None


def test_disable_completed_during_prepare_prevents_execution(
    client, db_engine, seed_admin, test_settings, tmp_path, monkeypatch
):
    _, job = _queued_script_test(client, test_settings, tmp_path)
    original = jobs._prepare

    def disable_after_prepare(db, settings, row, issue_snapshot):
        prepared = original(db, settings, row, issue_snapshot)
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

    def delete_after_prepare(db, settings, row, issue_snapshot):
        prepared = original(db, settings, row, issue_snapshot)
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


def test_temporary_finalization_lock_contention_preserves_completed_result(
    client, db_engine, seed_admin, test_settings, tmp_path, monkeypatch
):
    script, job = _queued_script_test(client, test_settings, tmp_path)
    factory = sessionmaker(bind=db_engine, future=True)
    lock_held = threading.Event()
    release_lock = threading.Event()
    first_timeout = threading.Event()
    errors = []
    inference_calls = []
    original_lock = jobs.database_idempotency_lock

    def hold_controls():
        try:
            with factory() as db, database_locks.database_idempotency_lock(db, "ai-controls"):
                lock_held.set()
                assert release_lock.wait(3)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    holder = threading.Thread(target=hold_controls, name="hold-final-ai-controls")

    @contextmanager
    def release_after_first_timeout(db, key):
        try:
            with original_lock(db, key):
                yield
        except HTTPException as exc:
            if exc.detail == "idempotency_lock_busy" and not first_timeout.is_set():
                first_timeout.set()
                release_lock.set()
            raise

    def execute(*_args, **_kwargs):
        inference_calls.append("sandbox")
        holder.start()
        assert lock_held.wait(3)
        return {"output": {"a": 1}, "stdout": ""}

    monkeypatch.setattr(database_locks, "LOCK_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(jobs, "database_idempotency_lock", release_after_first_timeout)
    monkeypatch.setattr(jobs.runtime, "broker", execute)

    assert jobs.process_job(factory, test_settings)
    holder.join(3)
    assert not holder.is_alive()
    assert not errors
    assert first_timeout.is_set()
    assert inference_calls == ["sandbox"]
    with factory() as db:
        assert db.get(AIJob, job["id"]).state == "succeeded"
        assert db.get(AIScript, script["id"]).tested_revision == script["revision"]


def test_persistent_finalization_lock_contention_fails_without_reexecution(
    client, db_engine, seed_admin, test_settings, tmp_path, monkeypatch
):
    script, job = _queued_script_test(client, test_settings, tmp_path)
    factory = sessionmaker(bind=db_engine, future=True)
    lock_held = threading.Event()
    release_lock = threading.Event()
    errors = []
    inference_calls = []

    def hold_controls():
        try:
            with factory() as db, database_locks.database_idempotency_lock(db, "ai-controls"):
                lock_held.set()
                assert release_lock.wait(3)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    holder = threading.Thread(target=hold_controls, name="hold-persistent-ai-controls")

    def execute(*_args, **_kwargs):
        inference_calls.append("sandbox")
        holder.start()
        assert lock_held.wait(3)
        return {"output": {"a": 1}, "stdout": ""}

    monkeypatch.setattr(database_locks, "LOCK_WAIT_SECONDS", 0.03)
    monkeypatch.setattr(jobs, "FINALIZE_LOCK_ATTEMPTS", 2, raising=False)
    monkeypatch.setattr(jobs.runtime, "broker", execute)

    try:
        assert jobs.process_job(factory, test_settings)
    finally:
        release_lock.set()
        holder.join(3)

    assert not holder.is_alive()
    assert not errors
    assert inference_calls == ["sandbox"]
    with factory() as db:
        final = db.get(AIJob, job["id"])
        assert (final.state, final.error, final.result) == (
            "failed",
            "ai_finalize_busy",
            None,
        )
        assert db.get(AIScript, script["id"]).tested_revision is None


def test_script_deleted_while_finalization_waits_discards_completed_result(
    client, db_engine, seed_admin, test_settings, tmp_path, monkeypatch
):
    script, job = _queued_script_test(client, test_settings, tmp_path)
    factory = sessionmaker(bind=db_engine, future=True)
    script_deleted = threading.Event()
    release_admin = threading.Event()
    first_timeout = threading.Event()
    errors = []
    inference_calls = []
    original_lock = jobs.database_idempotency_lock

    def delete_script_under_controls():
        try:
            with factory() as db, original_lock(db, "ai-controls"):
                row = db.get(AIScript, script["id"])
                db.delete(row)
                db.commit()
                script_deleted.set()
                assert release_admin.wait(3)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    admin = threading.Thread(target=delete_script_under_controls, name="delete-ai-script")

    @contextmanager
    def release_after_first_timeout(db, key):
        try:
            with original_lock(db, key):
                yield
        except HTTPException as exc:
            if exc.detail == "idempotency_lock_busy" and not first_timeout.is_set():
                first_timeout.set()
                release_admin.set()
            raise

    def execute(*_args, **_kwargs):
        inference_calls.append("sandbox")
        admin.start()
        assert script_deleted.wait(3)
        return {"output": {"a": 1}, "stdout": ""}

    monkeypatch.setattr(database_locks, "LOCK_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(jobs, "database_idempotency_lock", release_after_first_timeout)
    monkeypatch.setattr(jobs.runtime, "broker", execute)

    try:
        assert jobs.process_job(factory, test_settings)
    finally:
        release_admin.set()
        admin.join(3)

    assert not admin.is_alive()
    assert not errors
    assert first_timeout.is_set()
    assert inference_calls == ["sandbox"]
    with factory() as db:
        final = db.get(AIJob, job["id"])
        assert (final.state, final.error, final.result) == (
            "cancelled",
            "ai_not_found",
            None,
        )
        assert db.get(AIScript, script["id"]) is None
