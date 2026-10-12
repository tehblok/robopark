import os
import shutil
import subprocess
import sys

import pytest


def _python_build(script: str) -> list[str]:
    return [sys.executable, "-c", script, "build"]


def test_bootstrap_log_survives_removing_volatile_var_log(host_paths):
    from robopark_host import runtime

    volatile_log = host_paths.root / "var/log"
    volatile_log.mkdir(parents=True)
    log = runtime.bootstrap_log_path(host_paths)

    runtime._run(
        _python_build("print('durable bootstrap diagnostic', flush=True)"),
        build_log=log,
    )
    shutil.rmtree(volatile_log)

    assert log == host_paths.state / "runtime-bootstrap.log"
    assert log.read_text() == "durable bootstrap diagnostic\n"
    assert log.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "fifo"])
def test_bootstrap_log_refuses_unsafe_existing_path(tmp_path, unsafe_kind):
    from robopark_host import runtime

    log = tmp_path / "runtime-bootstrap.log"
    victim = tmp_path / "victim"
    victim.write_text("must remain intact")
    if unsafe_kind == "symlink":
        log.symlink_to(victim)
    elif unsafe_kind == "hardlink":
        os.link(victim, log)
    else:
        os.mkfifo(log)

    with pytest.raises(OSError):
        runtime._stream_build(
            _python_build("print('must not run')"),
            log,
            env=os.environ.copy(),
        )

    assert victim.read_text() == "must remain intact"


def test_bootstrap_log_retains_a_bounded_useful_tail(tmp_path):
    from robopark_host import runtime

    log = tmp_path / "runtime-bootstrap.log"
    runtime._stream_build(
        _python_build(
            "import sys; "
            "sys.stdout.write('discard-me\\n' + 'x' * (3 * 1024 * 1024) + '\\nuseful-tail\\n')"
        ),
        log,
        env=os.environ.copy(),
    )

    contents = log.read_bytes()
    assert len(contents) <= 2 * 1024 * 1024
    assert b"discard-me" not in contents
    assert contents.endswith(b"\nuseful-tail\n")


def test_bootstrap_log_write_failure_does_not_mask_build_failure(
    tmp_path, monkeypatch
):
    from robopark_host import runtime

    monkeypatch.setattr(runtime, "_BUILD_LOG_FSYNC_BYTES", 1)
    monkeypatch.setattr(runtime.os, "fsync", lambda _descriptor: (_ for _ in ()).throw(OSError("disk failed")))

    with pytest.raises(subprocess.CalledProcessError) as caught:
        runtime._stream_build(
            _python_build("import sys; print('original failure', flush=True); sys.exit(23)"),
            tmp_path / "runtime-bootstrap.log",
            env=os.environ.copy(),
        )

    assert caught.value.returncode == 23
    assert "original failure" in caught.value.stderr


def test_bootstrap_log_fsyncs_periodically_instead_of_per_line(tmp_path, monkeypatch):
    from robopark_host import runtime

    fsync_calls = []
    monkeypatch.setattr(runtime.os, "fsync", lambda descriptor: fsync_calls.append(descriptor))

    runtime._stream_build(
        _python_build("print(('x' * 1023 + '\\n') * 300, end='')"),
        tmp_path / "runtime-bootstrap.log",
        env=os.environ.copy(),
    )

    assert 1 < len(fsync_calls) < 300


def test_bootstrap_cli_reports_durable_log_path(host_paths, monkeypatch, capsys):
    from robopark_host import cli, runtime

    def fail(_paths):
        raise subprocess.CalledProcessError(17, ["docker", "build"], stderr="failed")

    monkeypatch.setattr(runtime, "bootstrap_compose", fail)

    assert cli._bootstrap_handler(host_paths) == 1
    assert str(host_paths.state / "runtime-bootstrap.log") in capsys.readouterr().err
