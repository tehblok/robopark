"""One PTY per systemd cgroup. Never copy terminal bytes to logs or files."""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import pwd
import select
import signal
import socket
import struct
import termios
import time
from contextlib import suppress

from .terminal_protocol import (
    TerminalCreate,
    decode_control,
    encode_control,
    frame_limit,
    identity,
    pack_frame,
)


def shell_environment(home):
    return {
        "HOME": home,
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HISTFILE": "/dev/null",
        "HISTSIZE": "0",
        "HISTFILESIZE": "0",
        "SHELL": "/bin/bash",
    }


def resize(fd, cols, rows):
    if (
        type(cols) is not int
        or not 20 <= cols <= 300
        or type(rows) is not int
        or not 5 <= rows <= 150
    ):
        raise ValueError("terminal_invalid_size")
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def relay(connection, request, home):
    """UID/capabilities are established by the fixed systemd template before entry."""
    started = time.monotonic()
    deadline = started + request.remaining_seconds
    last_input = last_lease = started
    pid, master = pty.fork()
    if pid == 0:
        try:
            connection.close()
            os.chdir(home)
            os.umask(0o077)
            os.execve(
                "/bin/bash",
                ["/bin/bash", "--noprofile", "--norc", "-i"],
                shell_environment(home),
            )
        except BaseException:  # noqa: BLE001 - fork child must never resume the parent runtime
            os._exit(127)
    incoming, to_pty, outgoing = bytearray(), bytearray(), bytearray()
    os.set_blocking(master, False)
    connection.setblocking(False)
    resize(master, 80, 24)
    try:
        while True:
            now = time.monotonic()
            if now >= min(deadline, last_lease + 20, last_input + 600):
                break
            readable, writable, _ = select.select(
                [connection, master],
                ([connection] if outgoing else []) + ([master] if to_pty else []),
                [],
                0.25,
            )
            if connection in readable:
                data = connection.recv(16384)
                if not data:
                    break
                incoming.extend(data)
                # Parse a bounded frame before reading more; no unbounded partial-frame allocation.
                while len(incoming) >= 5:
                    length, kind = struct.unpack("!IB", incoming[:5])
                    if length > frame_limit(kind):
                        raise ValueError("terminal_frame_too_large")
                    if len(incoming) < length + 5:
                        break
                    payload = bytes(incoming[5 : 5 + length])
                    del incoming[: 5 + length]
                    if kind == 1:
                        if len(to_pty) + len(payload) > 65536:
                            raise ValueError("terminal_input_overflow")
                        if payload:
                            last_input = time.monotonic()
                        to_pty.extend(payload)
                    elif kind == 0:
                        message = decode_control(payload)
                        if message.get("id") != request.session_id:
                            raise ValueError("terminal_invalid_id")
                        if message["op"] == "lease":
                            last_lease = time.monotonic()
                        elif message["op"] == "resize":
                            resize(master, message["cols"], message["rows"])
                        else:
                            raise ValueError("terminal_invalid_operation")
                    else:
                        raise ValueError("terminal_invalid_input")
            if master in readable:
                try:
                    data = os.read(master, 16384)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                outgoing.extend(pack_frame(2, data))
                if len(outgoing) > 1048576:
                    raise ValueError("terminal_output_overflow")
            if connection in writable:
                with suppress(BlockingIOError):
                    del outgoing[: connection.send(outgoing)]
            if master in writable:
                with suppress(BlockingIOError):
                    del to_pty[: os.write(master, to_pty)]
    except (OSError, ValueError):
        # Exit closes the cgroup through systemd; no payload-bearing exception is logged.
        pass
    finally:
        connection.close()
        os.close(master)
        with suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGTERM)
        end = time.monotonic() + 4
        while time.monotonic() < end:
            if os.waitpid(pid, os.WNOHANG)[0]:
                break
            time.sleep(0.05)
        else:
            with suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            with suppress(ChildProcessError):
                os.waitpid(pid, 0)
    return 0


def _exact(connection, length):
    result = bytearray()
    while len(result) < length:
        data = connection.recv(length - len(result))
        if not data:
            raise ValueError("terminal_broker_disconnected")
        result.extend(data)
    return bytes(result)


def run_worker(paths, session_id, profile):
    identity(session_id)
    if profile not in ("maintenance", "root") or not hasattr(socket, "SO_PEERCRED"):
        raise ValueError("terminal_invalid_worker")
    account = pwd.getpwnam("robopark-maint" if profile == "maintenance" else "root")
    if os.geteuid() != account.pw_uid:
        raise ValueError("terminal_invalid_worker_uid")
    if profile == "maintenance":
        from .terminal_setup import validate_account

        validate_account(account, os.getgroups(), forbidden_gids={0, 10001})
        if os.getegid() != account.pw_gid:
            raise ValueError("terminal_invalid_worker_gid")
    home = "/var/lib/robopark/terminal-home" if profile == "maintenance" else "/root"
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(5)
        connection.connect(
            str(paths.root / "run/robopark-terminal-workers/worker.sock")
        )
        _, uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != 0:
            raise ValueError("terminal_invalid_broker")
        connection.sendall(
            pack_frame(
                0,
                encode_control(
                    {"op": "worker-hello", "id": session_id, "profile": profile}
                ),
            )
        )
        length, kind = struct.unpack("!IB", _exact(connection, 5))
        if kind != 0 or length > frame_limit(kind):
            raise ValueError("terminal_invalid_config")
        config = decode_control(_exact(connection, length))
        if config["op"] != "worker-config":
            raise ValueError("terminal_invalid_config")
        request = TerminalCreate.from_dict(config["request"])
        if request.session_id != session_id or request.profile != profile:
            raise ValueError("terminal_invalid_config")
        return relay(connection, request, home)
