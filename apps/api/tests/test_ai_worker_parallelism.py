"""The local assistant can serve independent chats concurrently and stop safely."""

import asyncio
import json
import threading
import time

from sqlalchemy.orm import sessionmaker

from robopark_api.ai_models import AIConversation, AIJob, AIMessage
from robopark_api.services.ai import jobs


def _enable_host(settings, tmp_path):
    path = tmp_path / "ai-runtime-parallel.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "supported": True,
                "installed": True,
                "enabled": True,
                "ready": True,
                "reason": None,
                "model": "google/gemma-4-E4B-it-qat-q4_0-gguf",
                "backend": "cuda",
            }
        ),
        encoding="utf-8",
    )
    settings.ai_runtime_state_path = str(path)


def _queue_chat(factory, owner_id, park_id, key):
    with factory() as db:
        conversation = AIConversation(owner_id=owner_id, park_id=park_id)
        db.add(conversation)
        db.flush()
        db.add(AIMessage(conversation_id=conversation.id, role="user", content=f"Вопрос {key}"))
        job = AIJob(
            owner_id=owner_id,
            conversation_id=conversation.id,
            park_id=park_id,
            kind="chat",
            payload={"request": {"content": f"Вопрос {key}"}},
            idempotency_key=f"parallel-{key}",
        )
        db.add(job)
        db.commit()
        return job.id


def test_run_loop_processes_two_independent_chats_concurrently(
    client,
    db_engine,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    _enable_host(test_settings, tmp_path)
    test_settings.ai_chat_workers = 2
    factory = sessionmaker(bind=db_engine, future=True)
    job_ids = [
        _queue_chat(factory, seed_admin.id, seed_park_with_tracker.id, "one"),
        _queue_chat(factory, seed_admin.id, seed_park_with_tracker.id, "two"),
    ]
    entered = threading.Barrier(2, timeout=3)
    calls = []

    def delayed_complete(_settings, messages):
        calls.append(messages[-1]["content"])
        entered.wait()
        return "Готово"

    monkeypatch.setattr(jobs.runtime, "complete", delayed_complete)
    monkeypatch.setattr(jobs, "host_maintenance_active", lambda _settings: False)

    async def scenario():
        stop = asyncio.Event()
        worker = asyncio.create_task(jobs.run_loop(factory, stop, test_settings))
        deadline = asyncio.get_running_loop().time() + 5
        while True:
            with factory() as db:
                if all(db.get(AIJob, job_id).state == "succeeded" for job_id in job_ids):
                    break
            if asyncio.get_running_loop().time() >= deadline:
                with factory() as db:
                    states = [
                        (db.get(AIJob, job_id).state, db.get(AIJob, job_id).error)
                        for job_id in job_ids
                    ]
                raise AssertionError(f"parallel jobs did not finish: {states}")
            await asyncio.sleep(0.02)
        stop.set()
        await asyncio.wait_for(worker, timeout=3)

    asyncio.run(scenario())

    assert sorted(calls) == ["Вопрос one", "Вопрос two"]


def test_concurrent_process_job_calls_claim_one_job_once(
    client,
    db_engine,
    seed_admin,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
    monkeypatch,
):
    _enable_host(test_settings, tmp_path)
    factory = sessionmaker(bind=db_engine, future=True)
    job_id = _queue_chat(factory, seed_admin.id, seed_park_with_tracker.id, "single")
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def delayed_complete(_settings, _messages):
        calls.append("runtime")
        entered.set()
        assert release.wait(3)
        return "Готово"

    monkeypatch.setattr(jobs.runtime, "complete", delayed_complete)
    first = threading.Thread(target=jobs.process_job, args=(factory, test_settings))
    second = threading.Thread(target=jobs.process_job, args=(factory, test_settings))
    first.start()
    if not entered.wait(3):
        with factory() as db:
            row = db.get(AIJob, job_id)
            raise AssertionError(f"runtime not entered: {(row.state, row.error)}")
    second.start()
    second.join(3)
    release.set()
    first.join(3)

    assert not first.is_alive() and not second.is_alive()
    assert calls == ["runtime"]
    with factory() as db:
        assert db.get(AIJob, job_id).state == "succeeded"


def test_run_loop_waits_for_inflight_thread_after_stop(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def delayed_process(_factory, _settings):
        entered.set()
        assert release.wait(3)
        return False

    monkeypatch.setattr(jobs, "process_job", delayed_process)
    monkeypatch.setattr(jobs, "_recover_workers", lambda *_args: None)
    monkeypatch.setattr(jobs, "tick", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(jobs.policy, "host_status", lambda _settings: {"supported": True})
    settings = type("Settings", (), {"ai_chat_workers": 1})()

    async def scenario():
        stop = asyncio.Event()
        worker = asyncio.create_task(jobs.run_loop(object(), stop, settings))
        assert await asyncio.to_thread(entered.wait, 3)
        stop.set()
        await asyncio.sleep(0.05)
        assert not worker.done()
        release.set()
        await asyncio.wait_for(worker, timeout=3)

    asyncio.run(scenario())


def test_run_loop_does_not_poll_jobs_while_host_is_unsupported(monkeypatch):
    recovered = threading.Event()
    processed = threading.Event()

    monkeypatch.setattr(jobs, "_recover_workers", lambda *_args: recovered.set())
    monkeypatch.setattr(jobs.policy, "host_status", lambda _settings: {"supported": False})
    monkeypatch.setattr(jobs, "process_job", lambda *_args: processed.set())
    settings = type("Settings", (), {"ai_chat_workers": 1})()

    async def scenario():
        stop = asyncio.Event()
        worker = asyncio.create_task(jobs.run_loop(object(), stop, settings))
        assert await asyncio.to_thread(recovered.wait, 1)
        await asyncio.sleep(0.05)
        assert not processed.is_set()
        stop.set()
        await asyncio.wait_for(worker, timeout=3)

    asyncio.run(scenario())


def test_chat_workers_start_when_maintenance_fails(monkeypatch):
    recovered = threading.Event()
    processed = threading.Event()

    def recovery(_factory):
        recovered.set()

    def maintenance(*_args, **_kwargs):
        raise RuntimeError("maintenance failed")

    def chat(_factory, _settings):
        assert recovered.is_set()
        processed.set()
        return False

    monkeypatch.setattr(jobs, "_recover_workers", recovery, raising=False)
    monkeypatch.setattr(jobs, "tick", maintenance)
    monkeypatch.setattr(jobs, "process_job", chat)
    monkeypatch.setattr(jobs.policy, "host_status", lambda _settings: {"supported": True})
    settings = type("Settings", (), {"ai_chat_workers": 1})()

    async def scenario():
        stop = asyncio.Event()
        worker = asyncio.create_task(jobs.run_loop(object(), stop, settings))
        try:
            assert await asyncio.to_thread(processed.wait, 1)
        finally:
            stop.set()
            await asyncio.wait_for(worker, timeout=3)

    asyncio.run(scenario())


def test_tick_stages_automation_events_without_creating_knowledge(
    db_session, db_engine, seed_park_with_tracker, test_settings, tmp_path
):
    from robopark_api.ai_models import AIDocument, AIEvent

    _enable_host(test_settings, tmp_path)
    db_session.add(
        AIEvent(
            key="automation-only",
            park_id=seed_park_with_tracker.id,
            occurred_at=time.time(),
            payload={"event_key": "automation-only", "comment": "verified"},
        )
    )
    db_session.commit()

    assert jobs.tick(sessionmaker(bind=db_engine, future=True), test_settings)

    db_session.expire_all()
    assert db_session.get(AIEvent, "automation-only").processed is True
    assert db_session.query(AIDocument).count() == 0
