"""U2 / AE1: two processes, overlapping identical keys, one upstream call."""

from __future__ import annotations

import fcntl
import json
import multiprocessing
import time
from pathlib import Path

from robopark_api.services.live_merge import LiveMergeStore
from robopark_api.services.response_cache import ResponseCache


def _incr(path: str) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.seek(0)
        raw = fh.read().strip()
        n = int(raw) if raw else 0
        n += 1
        fh.seek(0)
        fh.truncate()
        fh.write(str(n))
        fh.flush()
        return n


def _read_count(path: Path) -> int:
    if not path.exists():
        return 0
    raw = path.read_text(encoding="utf-8").strip()
    return int(raw) if raw else 0


def _overlap_worker(
    merge_dir: str,
    name: str,
    key: str,
    counter_path: str,
    hold: float,
    result_path: str,
) -> None:
    store = LiveMergeStore(Path(merge_dir), waiter_timeout=5.0)
    cache: ResponseCache[dict] = ResponseCache(60, name=name, shared=store)

    def loader() -> dict:
        _incr(counter_path)
        time.sleep(hold)
        return {"body": "live", "key": key}

    value = cache.get_or_load(key, loader)
    Path(result_path).write_text(json.dumps(value), encoding="utf-8")


def _hang_worker(
    merge_dir: str,
    counter_path: str,
    started_path: str,
    hold: float,
    result_path: str,
) -> None:
    store = LiveMergeStore(Path(merge_dir), waiter_timeout=5.0)
    cache: ResponseCache[dict] = ResponseCache(60, name="kill", shared=store)

    def loader() -> dict:
        _incr(counter_path)
        Path(started_path).write_text("1", encoding="utf-8")
        time.sleep(hold)
        return {"body": "from-leader"}

    value = cache.get_or_load("shared", loader)
    Path(result_path).write_text(json.dumps(value), encoding="utf-8")


def test_two_processes_overlapping_identical_key_hit_upstream_once(tmp_path):
    merge_dir = tmp_path / "live-merge"
    counter = tmp_path / "calls.txt"
    r1 = tmp_path / "r1.json"
    r2 = tmp_path / "r2.json"
    ctx = multiprocessing.get_context("spawn")
    workers = [
        ctx.Process(
            target=_overlap_worker,
            args=(str(merge_dir), "ae1", "same", str(counter), 0.4, str(path)),
        )
        for path in (r1, r2)
    ]
    for proc in workers:
        proc.start()
    for proc in workers:
        proc.join(timeout=8.0)
        assert proc.exitcode == 0

    assert _read_count(counter) == 1
    assert json.loads(r1.read_text(encoding="utf-8")) == {"body": "live", "key": "same"}
    assert json.loads(r2.read_text(encoding="utf-8")) == {"body": "live", "key": "same"}


def test_two_processes_different_keys_do_not_share_a_fetch(tmp_path):
    merge_dir = tmp_path / "live-merge"
    c_a = tmp_path / "a.txt"
    c_b = tmp_path / "b.txt"
    r_a = tmp_path / "ra.json"
    r_b = tmp_path / "rb.json"
    ctx = multiprocessing.get_context("spawn")
    pa = ctx.Process(
        target=_overlap_worker,
        args=(str(merge_dir), "keys", "alpha", str(c_a), 0.3, str(r_a)),
    )
    pb = ctx.Process(
        target=_overlap_worker,
        args=(str(merge_dir), "keys", "beta", str(c_b), 0.3, str(r_b)),
    )
    pa.start()
    pb.start()
    pa.join(timeout=8.0)
    pb.join(timeout=8.0)
    assert pa.exitcode == 0
    assert pb.exitcode == 0
    assert _read_count(c_a) == 1
    assert _read_count(c_b) == 1
    assert json.loads(r_a.read_text(encoding="utf-8"))["key"] == "alpha"
    assert json.loads(r_b.read_text(encoding="utf-8"))["key"] == "beta"


def test_killed_leader_does_not_hang_waiters(tmp_path):
    merge_dir = tmp_path / "live-merge"
    counter = tmp_path / "calls.txt"
    started = tmp_path / "started.txt"
    leader_out = tmp_path / "leader.json"
    waiter_out = tmp_path / "waiter.json"
    ctx = multiprocessing.get_context("spawn")
    leader = ctx.Process(
        target=_hang_worker,
        args=(str(merge_dir), str(counter), str(started), 30.0, str(leader_out)),
    )
    waiter = ctx.Process(
        target=_hang_worker,
        args=(str(merge_dir), str(counter), str(started), 0.05, str(waiter_out)),
    )
    leader.start()
    deadline = time.monotonic() + 5.0
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert started.exists()
    waiter.start()
    time.sleep(0.15)
    leader.kill()
    leader.join(timeout=2.0)
    waiter.join(timeout=6.0)
    assert waiter.exitcode == 0
    assert json.loads(waiter_out.read_text(encoding="utf-8")) == {"body": "from-leader"}
    assert _read_count(counter) == 2


def _lease_worker(root: str, held_path: str, hold: float) -> None:
    from robopark_api.services.live_merge import JobLease

    lease = JobLease(Path(root), "lifespan-jobs")
    won = lease.try_acquire()
    Path(held_path).write_text("1" if won else "0", encoding="utf-8")
    if won:
        time.sleep(hold)
        lease.release()


def test_job_lease_only_one_owner_across_processes(tmp_path):
    root = tmp_path / "live-merge"
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    ctx = multiprocessing.get_context("spawn")
    p1 = ctx.Process(target=_lease_worker, args=(str(root), str(a), 0.6))
    p2 = ctx.Process(target=_lease_worker, args=(str(root), str(b), 0.6))
    p1.start()
    time.sleep(0.1)
    p2.start()
    p1.join(timeout=5.0)
    p2.join(timeout=5.0)
    assert p1.exitcode == 0
    assert p2.exitcode == 0
    assert {a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8")} == {"1", "0"}
