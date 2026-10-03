"""Cadence and shutdown guarantees for the combined history worker loop."""

from __future__ import annotations

import asyncio
import threading

from robopark_api.services import blocker_history_job


def test_pending_sla_drain_repeats_without_repeating_full_scan(monkeypatch):
    full_scans: list[None] = []
    pending_drains: list[None] = []
    stop = asyncio.Event()

    monkeypatch.setattr(blocker_history_job, "_scan_once", lambda: full_scans.append(None))

    def drain_pending() -> None:
        pending_drains.append(None)
        if len(pending_drains) == 3:
            stop.set()

    monkeypatch.setattr(blocker_history_job, "_scan_pending_sla_once", drain_pending)

    asyncio.run(
        blocker_history_job.run_blocker_history_loop(
            stop,
            interval_seconds=3600,
            pending_interval_seconds=0.01,
        )
    )

    assert len(full_scans) == 1
    assert len(pending_drains) == 3


def test_stop_waits_for_inflight_pending_sla_drain(monkeypatch):
    pending_started = threading.Event()
    release_pending = threading.Event()
    pending_finished = threading.Event()
    calls: list[None] = []

    monkeypatch.setattr(blocker_history_job, "_scan_once", lambda: None)

    def drain_pending() -> None:
        calls.append(None)
        pending_started.set()
        release_pending.wait(timeout=2)
        pending_finished.set()

    monkeypatch.setattr(blocker_history_job, "_scan_pending_sla_once", drain_pending)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(
            blocker_history_job.run_blocker_history_loop(
                stop,
                interval_seconds=3600,
                pending_interval_seconds=0.01,
            )
        )
        assert await asyncio.to_thread(pending_started.wait, 1)
        stop.set()
        await asyncio.sleep(0)
        assert not task.done()
        release_pending.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())

    assert pending_finished.is_set()
    assert len(calls) == 1


def test_pending_sla_scan_is_bounded_and_skips_host_maintenance(monkeypatch):
    drain_calls: list[int] = []
    session_entries: list[None] = []

    class Session:
        def __enter__(self):
            session_entries.append(None)
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(blocker_history_job, "SessionLocal", Session)
    monkeypatch.setattr(
        blocker_history_job.tracker_history,
        "drain_pending_history",
        lambda db, *, limit: drain_calls.append(limit),
    )
    monkeypatch.setattr(blocker_history_job, "host_maintenance_active", lambda: True)

    blocker_history_job._scan_pending_sla_once()

    assert session_entries == []
    assert drain_calls == []

    monkeypatch.setattr(blocker_history_job, "host_maintenance_active", lambda: False)
    blocker_history_job._scan_pending_sla_once()

    assert session_entries == [None]
    assert drain_calls == [2]
