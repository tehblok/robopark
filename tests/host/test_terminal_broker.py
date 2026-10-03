import asyncio
import importlib.util
from dataclasses import replace
from uuid import uuid4

import pytest
from test_terminal_protocol import descriptor


def build(tmp_path):
    assert importlib.util.find_spec("robopark_host.terminal_broker"), (
        "terminal broker missing"
    )
    import os

    from robopark_host.paths import HostPaths
    from robopark_host.terminal_broker import TerminalBroker
    from robopark_host.terminal_protocol import TerminalCreate, capability_revision

    os.environ["ROBOPARK_TESTING"] = "1"
    paths = HostPaths.from_root(tmp_path)
    boot, epoch = str(uuid4()), str(uuid4())

    class Runtime:
        def __init__(self):
            self.live = set()
            self.starts = 0
            self.held = False

        def hold(self):
            self.held = True

        def release(self):
            self.held = False

        async def start(self, request):
            self.live.add(request.session_id)
            self.starts += 1

        async def stop(self, session_id, profile):
            self.live.discard(session_id)

        async def stop_all(self):
            self.live.clear()

        async def worker_pid(self, session_id, profile):
            return 777

    runtime = Runtime()
    broker = TerminalBroker(paths, runtime, boot_id=boot, epoch=epoch)
    req = TerminalCreate.from_dict(
        descriptor(
            boot_id=boot,
            broker_epoch=epoch,
            capability_revision=capability_revision(boot, epoch),
        )
    )
    return broker, req, runtime


def test_create_replay_and_terminate_release_process_and_lock(tmp_path):
    async def scenario():
        broker, req, rt = build(tmp_path)
        a = await broker.create(req)
        b = await broker.create(req)
        assert a == b and rt.starts == 1 and rt.held
        await broker.terminate(req.session_id, reason="closed")
        assert rt.live == set() and not rt.held
        assert (await broker.create(req))["state"] == "ended"
        assert rt.starts == 1

    asyncio.run(scenario())


def test_wrong_epoch_never_creates_process(tmp_path):
    async def scenario():
        broker, req, rt = build(tmp_path)
        with pytest.raises(ValueError):
            await broker.create(replace(req, broker_epoch=str(uuid4())))
        assert rt.starts == 0

    asyncio.run(scenario())


def test_fake_worker_cannot_attach_with_normal_uid_or_wrong_pid(tmp_path):
    async def scenario():
        broker, req, _rt = build(tmp_path)
        await broker.create(req)
        assert not await broker.authorize_worker(
            req.session_id, "root", pid=777, uid=0, maintenance_uid=995
        )
        assert not await broker.authorize_worker(
            req.session_id, "maintenance", pid=778, uid=995, maintenance_uid=995
        )
        assert not await broker.authorize_worker(
            req.session_id, "maintenance", pid=777, uid=10001, maintenance_uid=995
        )
        assert await broker.authorize_worker(
            req.session_id, "maintenance", pid=777, uid=995, maintenance_uid=995
        )

    asyncio.run(scenario())


def test_output_flood_stops_session_without_unbounded_memory(tmp_path):
    async def scenario():
        broker, req, rt = build(tmp_path)
        await broker.create(req)
        for _ in range(17):
            await broker.push_output(req.session_id, b"x" * 65536)
        assert not rt.live
        assert broker.registry.sessions[req.session_id].reason == "output_overflow"
        assert broker.buffered_bytes(req.session_id) <= 1048576

    asyncio.run(scenario())


def test_broker_restart_stops_old_workers_before_new_capability(tmp_path):
    async def scenario():
        broker, req, rt = build(tmp_path)
        await broker.create(req)
        await broker.recover()
        assert not rt.live
        journal = broker.private / f"{req.session_id}.json"
        assert "broker_restart" in journal.read_text()

    asyncio.run(scenario())


def test_journal_failure_never_leaves_an_admitted_session(tmp_path, monkeypatch):
    async def scenario():
        broker, req, rt = build(tmp_path)

        def failure(*args):
            raise OSError("disk full")

        monkeypatch.setattr(broker, "persist", failure)
        with pytest.raises(OSError):
            await broker.create(req)
        assert rt.starts == 0 and not rt.held
        assert broker.registry.sessions[req.session_id].reason == "start_failed"

    asyncio.run(scenario())


def test_ended_sessions_release_stream_buffers(tmp_path):
    async def scenario():
        broker, req, _rt = build(tmp_path)
        await broker.create(req)
        await broker.terminate(req.session_id, reason="closed")
        assert req.session_id not in broker.outputs
        assert req.session_id not in broker.worker_ready

    asyncio.run(scenario())


def test_private_history_prunes_old_records_and_rotates_audit(tmp_path):
    import os
    import time

    async def scenario():
        broker, req, _rt = build(tmp_path)
        await broker.create(req)
        await broker.terminate(req.session_id, reason="closed")
        journal = broker.private / f"{req.session_id}.json"
        old = time.time() - 31 * 86400
        os.utime(journal, (old, old))
        event = broker.private / "events-2000-01-01.jsonl"
        event.write_text("{}\n")
        broker.epoch = str(uuid4())  # Old epoch records can no longer admit shells.
        broker.prune_history()
        assert not journal.exists() and not event.exists()
        assert req.session_id not in broker.registry.sessions

    asyncio.run(scenario())


def test_pending_host_operation_releases_terminal_lock_without_deadlock(tmp_path):
    async def scenario():
        broker, req, rt = build(tmp_path)
        await broker.create(req)
        pending = broker.paths.ops / "inbox/approved.json"
        pending.parent.mkdir(parents=True)
        pending.write_text("{}")
        task = asyncio.create_task(broker.watch())
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not rt.held and not rt.live
        assert (
            broker.registry.sessions[req.session_id].reason
            == "host_operation_requested"
        )

    asyncio.run(scenario())


def test_failed_attach_reply_still_starts_disconnect_deadline(tmp_path, monkeypatch):
    async def scenario():
        broker, req, _ = build(tmp_path)
        await broker.create(req)
        broker.worker_ready[req.session_id].set()

        async def fail(*args):
            raise OSError("disconnected before acknowledgment")

        monkeypatch.setattr("robopark_host.terminal_broker.write_frame", fail)
        with pytest.raises(OSError):
            await broker.stream(None, None, req.session_id, "attachment")
        row = broker.registry.sessions[req.session_id]
        assert row.attachment_id is None
        assert row.detached_deadline is not None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "elapsed,expected",
    [
        (1, "shell_exited"),
        (20, "lease_expired"),
        (600, "idle_timeout"),
        (3600, "expired"),
    ],
)
def test_worker_exit_before_watchdog_preserves_elapsed_deadline(
    tmp_path, elapsed, expected
):
    async def scenario():
        broker, req, rt = build(tmp_path)
        clock = [0]
        broker.registry.clock = lambda: clock[0]
        await broker.create(req)
        broker.registry.attach(req.session_id, "attachment")
        clock[0] = elapsed
        if elapsed >= 600:
            broker.registry.sessions[req.session_id].last_lease = elapsed
        result = await broker.terminate(req.session_id, reason="shell_exited")
        assert result["termination_reason"] == expected
        assert not rt.live

    asyncio.run(scenario())


@pytest.mark.parametrize("profile,budget", [("maintenance", 3600), ("root", 900)])
def test_fractional_worker_start_does_not_precede_broker_hard_expiry(
    tmp_path, profile, budget
):
    from robopark_host.terminal_broker import worker_remaining_seconds
    from robopark_host.terminal_protocol import TerminalCreate

    async def scenario():
        broker, req, _ = build(tmp_path)
        req = replace(req, profile=profile, remaining_seconds=budget)
        clock = [0.0]
        broker.registry.clock = lambda: clock[0]
        await broker.create(req)
        broker.registry.attach(req.session_id, "attachment")
        row = broker.registry.sessions[req.session_id]
        clock[0] = 0.20
        wire = replace(req, remaining_seconds=worker_remaining_seconds(row, clock[0]))
        parsed = TerminalCreate.from_dict(wire.as_dict())
        worker_started = 0.21
        worker_deadline = worker_started + parsed.remaining_seconds
        assert worker_deadline >= row.hard_deadline
        assert parsed.remaining_seconds <= budget
        clock[0] = worker_deadline
        result = await broker.terminate(req.session_id, reason="shell_exited")
        assert result["termination_reason"] == "expired"

    asyncio.run(scenario())
