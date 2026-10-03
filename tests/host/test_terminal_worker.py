"""Real PTY smoke tests; systemd/UID/cgroup guarantees are checked separately on Linux."""

import importlib.util
import json
import socket
import struct
import subprocess
import sys
import time

from test_terminal_protocol import descriptor


def test_worker_has_clean_environment():
    assert importlib.util.find_spec("robopark_host.terminal_worker"), (
        "terminal worker missing"
    )
    from robopark_host.terminal_worker import shell_environment

    env = shell_environment("/example")
    assert env["HOME"] == "/example" and env["HISTFILE"] == "/dev/null"
    assert set(env) == {
        "HOME",
        "TERM",
        "LANG",
        "PATH",
        "HISTFILE",
        "HISTSIZE",
        "HISTFILESIZE",
        "SHELL",
    }


def test_real_pty_utf8_resize_and_disconnect(tmp_path):
    assert importlib.util.find_spec("robopark_host.terminal_worker"), (
        "terminal worker missing"
    )
    from robopark_host.terminal_protocol import encode_control, pack_frame

    request = descriptor()
    a, b = socket.socketpair()
    script = """import json,socket,sys
from robopark_host.terminal_protocol import TerminalCreate
from robopark_host.terminal_worker import relay
relay(socket.socket(fileno=int(sys.argv[1])), TerminalCreate.from_dict(json.loads(sys.argv[2])), sys.argv[3])
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(b.fileno()),
            json.dumps(request),
            str(tmp_path),
        ],
        pass_fds=(b.fileno(),),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    b.close()
    a.settimeout(5)
    output = bytearray()

    def receive_until(needle):
        end = time.monotonic() + 5
        while needle not in output and time.monotonic() < end:
            header = b""
            while len(header) < 5:
                piece = a.recv(5 - len(header))
                assert piece, "unexpected PTY EOF"
                header += piece
            size, kind = struct.unpack("!IB", header)
            data = b""
            while len(data) < size:
                piece = a.recv(size - len(data))
                assert piece, "unexpected PTY EOF"
                data += piece
            assert kind == 2
            output.extend(data)
        assert needle in output

    try:
        a.sendall(pack_frame(1, "printf '\\nPTY_%s\\n' 'Привет'\n".encode()))
        receive_until("PTY_Привет".encode())
        a.sendall(
            pack_frame(
                0,
                encode_control(
                    {
                        "op": "resize",
                        "id": request["session_id"],
                        "attachment_id": "a",
                        "cols": 110,
                        "rows": 33,
                    }
                ),
            )
        )
        a.sendall(pack_frame(1, b"stty size; printf 'SIZE_%s\\n' done\n"))
        receive_until(b"33 110")
    finally:
        a.close()
        try:
            child.wait(timeout=7)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            raise
    assert child.returncode == 0, child.stderr.read().decode()


def test_maintenance_worker_rechecks_supplementary_groups_before_connecting(
    host_paths, monkeypatch
):
    from types import SimpleNamespace
    from uuid import uuid4

    import pytest

    from robopark_host import terminal_worker

    account = SimpleNamespace(
        pw_name="robopark-maint",
        pw_uid=995,
        pw_gid=995,
        pw_dir="/var/lib/robopark/terminal-home",
        pw_shell="/usr/sbin/nologin",
    )
    monkeypatch.setattr(terminal_worker.socket, "SO_PEERCRED", 17, raising=False)
    monkeypatch.setattr(terminal_worker.pwd, "getpwnam", lambda _: account)
    monkeypatch.setattr(terminal_worker.os, "geteuid", lambda: 995)
    monkeypatch.setattr(terminal_worker.os, "getegid", lambda: 995)
    monkeypatch.setattr(terminal_worker.os, "getgroups", lambda: [995, 6])
    with pytest.raises(ValueError, match="terminal_unsafe_account"):
        terminal_worker.run_worker(host_paths, str(uuid4()), "maintenance")
