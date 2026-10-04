import sys
import time
from types import SimpleNamespace

import pytest


def test_selector_setup_failure_reaps_child_and_closes_all_pipes(monkeypatch):
    from robopark_host import ai_broker

    original = ai_broker.subprocess.Popen
    children = []

    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    def unavailable():
        raise OSError("selector unavailable")

    monkeypatch.setattr(ai_broker.subprocess, "Popen", spawn)
    monkeypatch.setattr(ai_broker.selectors, "DefaultSelector", unavailable)
    try:
        with pytest.raises(OSError, match="selector unavailable"):
            ai_broker._run_container(
                [sys.executable, "-c", "import time; time.sleep(2)"],
                input_bytes=b"test", timeout=0.1, max_output=1024,
            )
        assert len(children) == 1
        child = children[0]
        assert child.poll() is not None
        assert all(pipe.closed for pipe in (child.stdin, child.stdout, child.stderr))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)
            for pipe in (child.stdin, child.stdout, child.stderr):
                pipe.close()


def test_container_input_backpressure_obeys_wall_clock_timeout(monkeypatch):
    from robopark_host import ai_broker

    real_popen = ai_broker.subprocess.Popen

    def small_pipe(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        try:
            import fcntl

            command = getattr(fcntl, "F_SETPIPE_SZ", None)
            if command is not None:
                assert fcntl.fcntl(process.stdin.fileno(), command, 8192) <= 8192
        except ImportError:
            pass
        return process

    monkeypatch.setattr(ai_broker.subprocess, "Popen", small_pipe)
    monkeypatch.setattr(ai_broker.subprocess, "run", lambda *args, **kwargs: SimpleNamespace())
    argv = [
        sys.executable,
        "-c",
        "import time; time.sleep(0.6)",
        "--name",
        "bounded-backpressure-probe",
    ]
    started = time.monotonic()
    with pytest.raises(ai_broker.BrokerError, match="sandbox_timeout"):
        ai_broker._run_container(
            argv,
            input_bytes=b"x" * ai_broker.MAX_BODY,
            timeout=0.1,
            max_output=1024,
        )
    assert time.monotonic() - started < 0.5


def test_container_broken_stdin_is_closed_and_child_is_reaped():
    from robopark_host import ai_broker

    argv = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('rejected'); raise SystemExit(7)",
        "--name",
        "broken-pipe-probe",
    ]
    started = time.monotonic()
    code, stdout, stderr = ai_broker._run_container(
        argv,
        input_bytes=b"x" * (2 * 1024 * 1024),
        timeout=1,
        max_output=1024,
    )
    assert (code, stdout, stderr) == (7, b"", b"rejected")
    assert time.monotonic() - started < 0.5


def test_detached_descendant_holding_output_pipe_cannot_block_cleanup(monkeypatch):
    from robopark_host import ai_broker

    monkeypatch.setattr(ai_broker.subprocess, "run", lambda *args, **kwargs: SimpleNamespace())
    source = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(0.8)'], "
        "start_new_session=True); raise SystemExit(0)"
    )
    argv = [sys.executable, "-c", source, "--name", "inherited-output-probe"]
    started = time.monotonic()
    with pytest.raises(ai_broker.BrokerError, match="sandbox_timeout"):
        ai_broker._run_container(
            argv,
            input_bytes=b"{}",
            timeout=0.1,
            max_output=1024,
        )
    assert time.monotonic() - started < 0.5


def test_container_output_limit_stops_process_before_deadline(monkeypatch):
    from robopark_host import ai_broker

    monkeypatch.setattr(ai_broker.subprocess, "run", lambda *args, **kwargs: SimpleNamespace())
    argv = [
        sys.executable,
        "-c",
        "import os,time; os.write(1,b'x'*8192); time.sleep(0.8)",
        "--name",
        "output-limit-probe",
    ]
    started = time.monotonic()
    with pytest.raises(ai_broker.BrokerError, match="sandbox_output_limit"):
        ai_broker._run_container(argv, input_bytes=b"{}", timeout=1, max_output=1024)
    assert time.monotonic() - started < 0.5
