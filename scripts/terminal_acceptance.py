#!/usr/bin/env python3
"""Destructive terminal acceptance for the marked disposable Linux VM only.

Terminal bytes are inspected in memory and are never written to the report.
The report contains only bounded assertions and non-sensitive observations.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import pwd
import re
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

REPORT_LIMIT = 64 * 1024
CONTROL_LIMIT = 4096
INPUT_LIMIT = 16384
OUTPUT_LIMIT = 65536
BROKER_SOCKET = Path("/run/robopark-terminal/broker.sock")
CAPABILITIES = Path("/var/lib/robopark/ops/public/terminal-capabilities.json")
MARKER = Path("/run/robopark-terminal-test-vm")
FORBIDDEN_REPORT_FIELDS = {
    "auth_session_hash",
    "command",
    "commands",
    "input",
    "output",
    "payload",
    "secret",
    "session_id",
    "ticket",
    "transcript",
}

API_BOUNDARY_KEYS = {
    "api_uid",
    "api_gid",
    "settings_socket",
    "host_root",
    "projection_readable",
    "projection_fresh",
    "socket_present",
    "socket_connect",
    "capabilities",
    "maintenance_uid",
    "maintenance_pty",
    "root_uid",
    "root_pty",
    "cleanup",
}

API_BOUNDARY_PROGRAM = r'''
import asyncio
import json
import os
import select
import socket
import stat
from uuid import uuid4

from robopark_api.config import get_settings
from robopark_api.services.ops import host_bridge
from robopark_api.services.terminal.authorization import capabilities
from robopark_api.services.terminal.broker import BrokerClient, control, read_frame, send


async def drain(reader):
    while True:
        try:
            await asyncio.wait_for(read_frame(reader), 0.1)
        except asyncio.TimeoutError:
            return


async def profile_probe(broker, cap, profile, expected_uid):
    session_id = str(uuid4())
    attachment_id = str(uuid4())
    descriptor = {
        "session_id": session_id,
        "actor_id": 1,
        "auth_session_hash": "b" * 64,
        "profile": profile,
        "credential_generation": 1,
        "capability_revision": cap["capability_revision"],
        "boot_id": cap["boot_id"],
        "broker_epoch": cap["broker_epoch"],
        "remaining_seconds": 60,
    }
    reader = writer = None
    cleaned = False
    try:
        await broker.create(descriptor)
        reader, writer = await broker.attach(session_id, attachment_id)
        await send(writer, 1, b"stty -echo\r")
        await asyncio.sleep(0.2)
        await drain(reader)
        await control(
            writer,
            {
                "op": "resize",
                "id": session_id,
                "attachment_id": attachment_id,
                "cols": 91,
                "rows": 37,
            },
        )
        token = uuid4().hex
        begin = ("BEGIN" + token).encode()
        end = ("END" + token).encode()
        command = (
            "printf '\\n" + begin.decode() + "\\n'; "
            "printf '\u0442\u0435\u0441\u0442:'; id -u | tr -d '\\n'; "
            "printf ':'; stty size | tr -d '\\n'; "
            "printf '" + end.decode() + "\\n'\r"
        ).encode()
        await send(writer, 1, command)
        captured = bytearray()
        deadline = asyncio.get_running_loop().time() + 8
        while asyncio.get_running_loop().time() < deadline:
            kind, data = await asyncio.wait_for(read_frame(reader), 2)
            if kind != 2:
                continue
            captured.extend(data)
            if len(captured) > 262144:
                raise RuntimeError("terminal_api_boundary_output_limit")
            start = captured.find(begin)
            finish = captured.find(end, start + len(begin)) if start >= 0 else -1
            if finish >= 0:
                value = bytes(captured[start + len(begin):finish]).strip()
                expected = f"\u0442\u0435\u0441\u0442:{expected_uid}:37 91".encode()
                return expected_uid, value == expected
        raise RuntimeError("terminal_api_boundary_timeout")
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except Exception:
                pass
        try:
            ended = await broker.terminate(session_id)
            cleaned = ended.get("state") == "ended"
        except Exception:
            pass
        profile_probe.cleanup = getattr(profile_probe, "cleanup", True) and cleaned


async def main():
    settings = get_settings()
    root = host_bridge.host_root(settings)
    projection = root / "public/terminal-capabilities.json"
    cap = capabilities(settings)
    socket_path = settings.terminal_broker_socket
    socket_info = os.stat(socket_path, follow_symlinks=False)
    connected = False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(2)
    try:
        probe.connect(socket_path)
        connected = True
    finally:
        probe.close()
    broker = BrokerClient(settings)
    profile_probe.cleanup = True
    maintenance_uid, maintenance_pty = await profile_probe(broker, cap, "maintenance", 997)
    root_uid, root_pty = await profile_probe(broker, cap, "root", 0)
    result = {
        "api_uid": os.geteuid(),
        "api_gid": os.getegid(),
        "settings_socket": socket_path,
        "host_root": str(root),
        "projection_readable": projection.is_file() and os.access(projection, os.R_OK),
        "projection_fresh": bool(cap),
        "socket_present": stat.S_ISSOCK(socket_info.st_mode),
        "socket_connect": connected,
        "capabilities": bool(cap),
        "maintenance_uid": maintenance_uid,
        "maintenance_pty": maintenance_pty,
        "root_uid": root_uid,
        "root_pty": root_pty,
        "cleanup": profile_probe.cleanup,
    }
    print(json.dumps(result, sort_keys=True))


asyncio.run(main())
'''


def require_disposable_vm(
    root: Path = Path("/"),
    *,
    effective_uid: int | None = None,
    marker_owner_uid: int = 0,
):
    """Fail closed unless this is the explicitly marked systemd fixture."""
    if effective_uid is None:
        effective_uid = os.geteuid()
    if effective_uid != 0:
        raise RuntimeError("linux_root_required")
    marker = root / MARKER.relative_to("/")
    try:
        info = os.stat(marker, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise RuntimeError("disposable_vm_marker_missing") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != marker_owner_uid
        or stat.S_IMODE(info.st_mode) != 0o600
        or marker.is_symlink()
    ):
        raise RuntimeError("disposable_vm_marker_unsafe")
    init = (root / "proc/1/comm").read_text(encoding="utf-8").strip()
    if init != "systemd":
        raise RuntimeError("systemd_pid1_required")
    return {"init": init, "marker_mode": "0600", "marker_uid": info.st_uid}


def session_request(profile: str, capabilities: dict[str, Any]):
    if profile not in ("maintenance", "root"):
        raise ValueError("terminal_invalid_profile")
    return {
        "session_id": str(uuid4()),
        "actor_id": 1,
        "auth_session_hash": "b" * 64,
        "profile": profile,
        "credential_generation": 1,
        "capability_revision": capabilities["capability_revision"],
        "boot_id": capabilities["boot_id"],
        "broker_epoch": capabilities["broker_epoch"],
        "remaining_seconds": 900 if profile == "root" else 3600,
    }


def new_report(platform: dict[str, Any]):
    return {
        "format": 1,
        "suite": "robopark_terminal_linux_acceptance",
        "passed": False,
        "platform": platform,
        "checks": [],
    }


def _validate_report(value: Any, key: str | None = None):
    if key is not None and key.lower() in FORBIDDEN_REPORT_FIELDS:
        raise ValueError("report_forbidden_field")
    if isinstance(value, dict):
        for child_key, child in value.items():
            if not isinstance(child_key, str):
                raise TypeError("report_invalid")
            _validate_report(child, child_key)
    elif isinstance(value, list):
        for child in value:
            _validate_report(child)
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise TypeError("report_invalid")


def write_report(path: Path, report: dict[str, Any]):
    _validate_report(report)
    encoded = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    if len(encoded) > REPORT_LIMIT:
        raise ValueError("report_too_large")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("report_unsafe_path")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise ValueError("report_unsafe_path")
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _frame_limit(kind: int):
    if kind not in (0, 1, 2):
        raise RuntimeError("terminal_invalid_frame")
    return (CONTROL_LIMIT, INPUT_LIMIT, OUTPUT_LIMIT)[kind]


def _pack_frame(kind: int, payload: bytes):
    if len(payload) > _frame_limit(kind):
        raise RuntimeError("terminal_frame_too_large")
    return struct.pack("!IB", len(payload), kind) + payload


def _control(message: dict[str, Any]):
    payload = json.dumps(message, separators=(",", ":"), allow_nan=False).encode()
    if len(payload) > CONTROL_LIMIT:
        raise RuntimeError("terminal_frame_too_large")
    return payload


def _exact(connection: socket.socket, length: int):
    result = bytearray()
    while len(result) < length:
        data = connection.recv(length - len(result))
        if not data:
            raise RuntimeError("terminal_broker_disconnected")
        result.extend(data)
    return bytes(result)


def _read_frame(connection: socket.socket, timeout: float = 5):
    connection.settimeout(timeout)
    length, kind = struct.unpack("!IB", _exact(connection, 5))
    if length > _frame_limit(kind):
        raise RuntimeError("terminal_frame_too_large")
    return kind, _exact(connection, length)


def _decode_control(payload: bytes):
    try:
        value = json.loads(payload)
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("terminal_invalid_control") from exc
    if not isinstance(value, dict):
        raise TypeError("terminal_invalid_control")
    if not isinstance(value.get("op"), str):
        raise TypeError("terminal_invalid_control")
    return value


class ApiSocket:
    """Broker socket connected by a short-lived process with API UID/GID 10001."""

    def __init__(self, path: Path = BROKER_SOCKET):
        parent, child = socket.socketpair()
        pid = os.fork()
        if pid == 0:
            try:
                parent.close()
                os.setgroups([])
                os.setgid(10001)
                os.setuid(10001)
                upstream = socket.socket(socket.AF_UNIX)
                upstream.connect(str(path))
                sockets = (child, upstream)
                while True:
                    readable, _, _ = select.select(sockets, [], [], 5)
                    if not readable:
                        continue
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            os._exit(0)
                        destination = upstream if source is child else child
                        destination.sendall(data)
            except (OSError, ValueError):
                os._exit(111)
        child.close()
        self.socket = parent
        self.pid = pid
        self.closed = False

    def send(self, kind: int, payload: bytes):
        self.socket.sendall(_pack_frame(kind, payload))

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.socket.close()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            waited, _ = os.waitpid(self.pid, os.WNOHANG)
            if waited:
                return
            time.sleep(0.02)
        try:
            os.kill(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.waitpid(self.pid, 0)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _rpc(message: dict[str, Any]):
    with ApiSocket() as connection:
        connection.send(0, _control(message))
        kind, payload = _read_frame(connection.socket)
    if kind != 0:
        raise RuntimeError("terminal_invalid_control")
    reply = _decode_control(payload)
    if reply["op"] == "error":
        raise RuntimeError(reply.get("error", "terminal_unavailable"))
    if reply["op"] != "ok" or not isinstance(reply.get("session"), dict):
        raise RuntimeError("terminal_invalid_control")
    return reply["session"]


def _capabilities():
    raw = CAPABILITIES.read_bytes()
    if len(raw) > 8192:
        raise RuntimeError("terminal_invalid_capabilities")
    value = json.loads(raw)
    required = {"boot_id", "broker_epoch", "capability_revision", "profiles"}
    if not required <= set(value) or sorted(value["profiles"]) != [
        "maintenance",
        "root",
    ]:
        raise RuntimeError("terminal_invalid_capabilities")
    UUID(value["boot_id"])
    UUID(value["broker_epoch"])
    if not re.fullmatch("[a-f0-9]{64}", value["capability_revision"]):
        raise RuntimeError("terminal_invalid_capabilities")
    return value


def create_session(profile: str):
    request = session_request(profile, _capabilities())
    result = _rpc({"op": "create", "request": request})
    if result.get("id") != request["session_id"]:
        raise RuntimeError("terminal_create_mismatch")
    return request


def status(session_id: str):
    return _rpc({"op": "status", "id": session_id})


def terminate(session_id: str):
    return _rpc({"op": "terminate", "id": session_id, "reason": "closed"})


class TerminalStream:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.attachment_id = str(uuid4())
        self.connection = ApiSocket()
        self.connection.send(
            0,
            _control(
                {
                    "op": "attach",
                    "id": session_id,
                    "attachment_id": self.attachment_id,
                }
            ),
        )
        kind, data = _read_frame(self.connection.socket)
        reply = _decode_control(data) if kind == 0 else {}
        if reply.get("op") != "ok":
            self.connection.close()
            raise RuntimeError(reply.get("error", "terminal_attach_failed"))
        self.last_lease = time.monotonic()
        self.total_received = 0
        self.send_input(b"stty -echo\r")
        self._discard(0.3)

    def send_input(self, data: bytes):
        self.connection.send(1, data)

    def lease(self):
        self.connection.send(
            0,
            _control(
                {
                    "op": "lease",
                    "id": self.session_id,
                    "attachment_id": self.attachment_id,
                }
            ),
        )
        self.last_lease = time.monotonic()

    def resize(self, cols: int, rows: int):
        self.connection.send(
            0,
            _control(
                {
                    "op": "resize",
                    "id": self.session_id,
                    "attachment_id": self.attachment_id,
                    "cols": cols,
                    "rows": rows,
                }
            ),
        )

    def _next(self, timeout: float):
        kind, data = _read_frame(self.connection.socket, timeout)
        if kind == 0:
            message = _decode_control(data)
            if message.get("op") == "ended":
                raise RuntimeError(f"terminal_ended_{message.get('reason', 'unknown')}")
            raise RuntimeError("terminal_invalid_output")
        if kind != 2:
            raise RuntimeError("terminal_invalid_output")
        self.total_received += len(data)
        if self.total_received > 16 * 1024 * 1024:
            raise RuntimeError("terminal_acceptance_output_limit")
        return data

    def _discard(self, duration: float):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            readable, _, _ = select.select(
                [self.connection.socket], [], [], max(0, deadline - time.monotonic())
            )
            if not readable:
                return
            self._next(1)

    def probe(self, command: str, timeout: float = 8):
        token = uuid4().hex.encode()
        begin, end = b"BEGIN" + token, b"END" + token
        wrapped = (
            b"printf '\\n"
            + begin
            + b"\\n'; { "
            + command.encode()
            + b"; }; printf '\\n"
            + end
            + b"\\n'\r"
        )
        self.send_input(wrapped)
        deadline = time.monotonic() + timeout
        captured = bytearray()
        while time.monotonic() < deadline:
            if time.monotonic() - self.last_lease >= 5:
                self.lease()
            data = self._next(min(1, deadline - time.monotonic()))
            captured.extend(data)
            if len(captured) > 512 * 1024:
                raise RuntimeError("terminal_probe_output_limit")
            start = captured.find(begin)
            finish = captured.find(end, start + len(begin)) if start >= 0 else -1
            if finish >= 0:
                return bytes(captured[start + len(begin) : finish]).strip(b"\r\n")
        raise RuntimeError("terminal_probe_timeout")

    def close(self):
        self.connection.close()


def _systemctl(*arguments: str, timeout: int = 20):
    result = subprocess.run(
        ["systemctl", *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("terminal_systemd_failed")
    return result.stdout.strip()


def _unit(session_id: str, profile: str):
    return f"robopark-terminal-{profile}@{session_id}.service"


def _unit_properties(session_id: str, profile: str):
    text = _systemctl(
        "show",
        _unit(session_id, profile),
        "--property=ActiveState,MainPID,ControlGroup,User,Group,KillMode,TimeoutStopUSec",
    )
    result = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        result[key] = value
    return result


def _wait_status(session_id: str, reason: str, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = status(session_id)
        if value.get("termination_reason"):
            if value["termination_reason"] != reason:
                raise RuntimeError("terminal_wrong_termination_reason")
            return value
        time.sleep(0.25)
    raise RuntimeError("terminal_termination_timeout")


def _wait_process_gone(pid: int, timeout: float = 8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return
        time.sleep(0.1)
    raise RuntimeError("terminal_cgroup_process_survived")


def _parse_identity(data: bytes):
    fields = data.decode("ascii", "strict").split("|")
    if len(fields) != 6:
        raise RuntimeError("terminal_identity_probe_failed")
    return {
        "uid": int(fields[0]),
        "gid": int(fields[1]),
        "groups": [int(item) for item in fields[2].split()],
        "group_names": fields[3].split(),
        "cap_eff": fields[4],
        "no_new_privs": int(fields[5]),
    }


IDENTITY_COMMAND = (
    'printf \'%s|%s|%s|%s|%s|%s\' "$(id -u)" "$(id -g)" '
    '"$(id -G)" "$(id -nG)" '
    "\"$(awk '/^CapEff:/{print $2}' /proc/self/status)\" "
    "\"$(awk '/^NoNewPrivs:/{print $2}' /proc/self/status)\""
)


def check_units():
    required = [
        "robopark-terminal-setup.service",
        "robopark-terminal-broker.service",
        "robopark-terminal-maintenance@.service",
        "robopark-terminal-root@.service",
    ]
    paths = [f"/etc/systemd/system/{name}" for name in required]
    result = subprocess.run(
        ["systemd-analyze", "verify", *paths],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("terminal_units_invalid")
    if _systemctl("is-active", "robopark-terminal-broker.service") != "active":
        raise RuntimeError("terminal_broker_inactive")
    socket_info = BROKER_SOCKET.stat()
    directory_info = BROKER_SOCKET.parent.stat()
    mode = stat.S_IMODE(socket_info.st_mode)
    if mode != 0o660 or socket_info.st_uid != 0 or socket_info.st_gid != 10001:
        raise RuntimeError("terminal_socket_permissions")
    if (
        stat.S_IMODE(directory_info.st_mode) != 0o750
        or directory_info.st_uid != 0
        or directory_info.st_gid != 10001
    ):
        raise RuntimeError("terminal_socket_directory_permissions")
    return {
        "verified_units": len(required),
        "broker_socket_mode": "0660",
        "broker_directory_mode": "0750",
    }


def check_accelerated_deadlines():
    """Probe the installed registry with a controlled monotonic clock."""
    code = r"""
import sys
from uuid import uuid4
sys.path.insert(0, "/opt/robopark/host-tools")
from robopark_host.terminal_protocol import TerminalCreate, capability_revision
from robopark_host.terminal_state import TerminalRegistry

class Clock:
    value = 100.0
    def __call__(self):
        return self.value

clock = Clock()
boot, epoch = str(uuid4()), str(uuid4())
def request():
    return TerminalCreate.from_dict({
        "session_id": str(uuid4()), "actor_id": 1,
        "auth_session_hash": "b" * 64, "profile": "maintenance",
        "credential_generation": 1,
        "capability_revision": capability_revision(boot, epoch),
        "boot_id": boot, "broker_epoch": epoch, "remaining_seconds": 3600,
    })

registry = TerminalRegistry(clock=clock)
hard, _ = registry.admit(request())
hard.detached_deadline = None
hard.last_lease = clock.value + 4000
hard.last_input = clock.value + 4000
clock.value += 3599
assert registry.expired() == []
clock.value += 1
assert registry.expired() == [(hard.request.session_id, "expired")]

clock.value = 100.0
registry = TerminalRegistry(clock=clock)
idle, _ = registry.admit(request())
idle.detached_deadline = None
idle.last_lease = clock.value + 1000
clock.value += 599
assert registry.expired() == []
clock.value += 1
assert registry.expired() == [(idle.request.session_id, "idle_timeout")]
print("PASS")
"""
    result = subprocess.run(
        ["/usr/bin/python3", "-I", "-c", code],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=10,
    )
    if result.returncode or result.stdout.strip() != "PASS":
        raise RuntimeError("terminal_accelerated_deadline_probe")
    return {
        "maintenance_absolute_seconds": 3600,
        "idle_seconds": 600,
        "clock": "controlled_monotonic",
    }


def _daemon_code(pid_file: Path):
    source = f"""import os,time
p=os.fork()
if p:
    os._exit(0)
os.setsid()
p=os.fork()
if p:
    os._exit(0)
fd=os.open('/dev/null', os.O_RDWR)
for target in (0,1,2):
    os.dup2(fd,target)
open({str(pid_file)!r},'w').write(str(os.getpid()))
time.sleep(300)
"""
    return base64.b64encode(source.encode()).decode()


def check_maintenance_profile():
    account = pwd.getpwnam("robopark-maint")
    request = create_session("maintenance")
    stream = TerminalStream(request["session_id"])
    protected = f"/etc/robopark-terminal-acceptance-{uuid4().hex}"
    home_probe = Path(account.pw_dir) / f".acceptance-{uuid4().hex}"
    daemon_pid_file = Path(account.pw_dir) / f".acceptance-pid-{uuid4().hex}"
    daemon_pid = None
    try:
        identity = _parse_identity(stream.probe(IDENTITY_COMMAND))
        if identity["uid"] != account.pw_uid or identity["gid"] != account.pw_gid:
            raise RuntimeError("terminal_maintenance_identity")
        if identity["groups"] != [account.pw_gid]:
            raise RuntimeError("terminal_maintenance_group")
        if {"sudo", "docker"} & set(identity["group_names"]):
            raise RuntimeError("terminal_maintenance_group")
        if identity["cap_eff"] != "0000000000000000" or identity["no_new_privs"] != 1:
            raise RuntimeError("terminal_maintenance_capabilities")

        result = stream.probe(
            f"printf old > {home_probe}; sed -i s/old/saved/ {home_probe}; "
            f'printf \'%s|%s\' "$PWD" "$(cat {home_probe})"'
        ).decode("utf-8", "strict")
        if result != f"{account.pw_dir}|saved":
            raise RuntimeError("terminal_home_not_writable")

        result = stream.probe(
            f"if printf denied 2>/dev/null > {protected}; then printf W; else printf D; fi; "
            "if head -c1 /var/lib/robopark/ops/state/terminal/"
            f"{request['session_id']}.json >/dev/null 2>&1; "
            "then printf R; else printf P; fi; "
            "if timeout 2 bash -c 'exec 3<>/run/robopark-terminal/broker.sock' "
            "2>/dev/null; then printf S; else printf U; fi"
        )
        if result != b"DPU":
            raise RuntimeError("terminal_maintenance_isolation")

        stream.resize(123, 37)
        result = stream.probe("printf 'Привет|'; stty size")
        if "Привет|37 123" not in result.decode("utf-8", "strict"):
            raise RuntimeError("terminal_pty_utf8_resize")

        started = time.monotonic()
        stream.send_input(b"sleep 30\r")
        time.sleep(0.4)
        stream.send_input(b"\x03")
        if stream.probe("printf INTERRUPTED", timeout=4) != b"INTERRUPTED":
            raise RuntimeError("terminal_pty_ctrl_c")
        if time.monotonic() - started >= 5:
            raise RuntimeError("terminal_pty_ctrl_c")

        encoded = _daemon_code(daemon_pid_file)
        stream.probe(
            "python3 -c \"exec(__import__('base64').b64decode('"
            + encoded
            + "'))\" </dev/null >/dev/null 2>&1 & true"
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not daemon_pid_file.exists():
            time.sleep(0.05)
        daemon_pid = int(daemon_pid_file.read_text())
        properties = _unit_properties(request["session_id"], "maintenance")
        cgroup = Path(f"/proc/{daemon_pid}/cgroup").read_text()
        if (
            properties.get("ActiveState") != "active"
            or properties["ControlGroup"] not in cgroup
        ):
            raise RuntimeError("terminal_worker_cgroup")
        if properties.get("KillMode") != "control-group":
            raise RuntimeError("terminal_worker_kill_mode")

        terminate(request["session_id"])
        _wait_process_gone(daemon_pid)
        if _unit_properties(request["session_id"], "maintenance")[
            "ActiveState"
        ] not in (
            "inactive",
            "failed",
        ):
            raise RuntimeError("terminal_worker_still_active")
        return {
            "uid": identity["uid"],
            "cap_eff": identity["cap_eff"],
            "no_new_privs": identity["no_new_privs"],
            "home_write": True,
            "protected_write_denied": True,
            "private_state_denied": True,
            "broker_socket_denied": True,
            "utf8_resize_ctrl_c": True,
            "fork_cgroup_cleanup": True,
        }
    finally:
        stream.close()
        try:
            terminate(request["session_id"])
        except RuntimeError:
            pass
        for path in (Path(protected), home_probe, daemon_pid_file):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        if daemon_pid is not None and Path(f"/proc/{daemon_pid}").exists():
            try:
                os.kill(daemon_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def check_disconnect_timeout():
    request = create_session("maintenance")
    stream = TerminalStream(request["session_id"])
    started = time.monotonic()
    stream.close()
    _wait_status(request["session_id"], "disconnected", 22)
    elapsed = time.monotonic() - started
    if not 14 <= elapsed <= 22:
        raise RuntimeError("terminal_disconnect_deadline")
    return {"termination_reason": "disconnected", "elapsed_seconds": round(elapsed, 2)}


def check_lease_timeout():
    request = create_session("maintenance")
    stream = TerminalStream(request["session_id"])
    started = time.monotonic()
    _wait_status(request["session_id"], "lease_expired", 27)
    elapsed = time.monotonic() - started
    stream.close()
    if not 19 <= elapsed <= 27:
        raise RuntimeError("terminal_lease_deadline")
    return {"termination_reason": "lease_expired", "elapsed_seconds": round(elapsed, 2)}


def check_output_flood():
    request = create_session("maintenance")
    stream = TerminalStream(request["session_id"])
    stream.send_input(b"yes ROBOPARK_ACCEPTANCE_FLOOD\r")
    started = time.monotonic()
    deadline = started + 18
    try:
        while time.monotonic() < deadline:
            stream.lease()
            value = status(request["session_id"])
            if value.get("termination_reason"):
                if value["termination_reason"] != "output_overflow":
                    raise RuntimeError("terminal_wrong_termination_reason")
                return {
                    "termination_reason": "output_overflow",
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                }
            time.sleep(1)
        raise RuntimeError("terminal_output_not_bounded")
    finally:
        stream.close()
        try:
            terminate(request["session_id"])
        except RuntimeError:
            pass


def check_broker_restart_cleanup():
    account = pwd.getpwnam("robopark-maint")
    request = create_session("maintenance")
    stream = TerminalStream(request["session_id"])
    pid_file = Path(account.pw_dir) / f".acceptance-restart-{uuid4().hex}"
    encoded = _daemon_code(pid_file)
    try:
        stream.probe(
            "python3 -c \"exec(__import__('base64').b64decode('"
            + encoded
            + "'))\" </dev/null >/dev/null 2>&1 & true"
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not pid_file.exists():
            time.sleep(0.05)
        pid = int(pid_file.read_text())
        old_epoch = _capabilities()["broker_epoch"]
        _systemctl("restart", "robopark-terminal-broker.service", timeout=30)
        _wait_process_gone(pid)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                new_epoch = _capabilities()["broker_epoch"]
            except (FileNotFoundError, RuntimeError, ValueError):
                time.sleep(0.2)
                continue
            if new_epoch != old_epoch:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("terminal_broker_epoch_unchanged")
        return {"epoch_rotated": True, "fork_cgroup_cleanup": True}
    finally:
        stream.close()
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass


def check_root_profile():
    request = create_session("root")
    stream = TerminalStream(request["session_id"])
    try:
        identity = _parse_identity(stream.probe(IDENTITY_COMMAND))
        if identity["uid"] != 0 or identity["gid"] != 0:
            raise RuntimeError("terminal_root_identity")
        if stream.probe("test -r /etc/shadow && printf FULL_ROOT") != b"FULL_ROOT":
            raise RuntimeError("terminal_root_access")
        properties = _unit_properties(request["session_id"], "root")
        if (
            properties.get("User") != "root"
            or properties.get("KillMode") != "control-group"
        ):
            raise RuntimeError("terminal_root_unit")
        terminate(request["session_id"])
        return {
            "uid": 0,
            "full_host_root": True,
            "sandbox_guarantee": False,
            "separate_worker_profile": True,
        }
    finally:
        stream.close()
        try:
            terminate(request["session_id"])
        except RuntimeError:
            pass


def check_root_absolute_900s():
    request = create_session("root")
    stream = TerminalStream(request["session_id"])
    started = time.monotonic()
    next_input = started + 300
    reason = None
    try:
        while time.monotonic() - started < 930:
            now = time.monotonic()
            if now >= next_input:
                stream.send_input(b"\r")
                next_input += 300
            if now - stream.last_lease >= 5:
                stream.lease()
            readable, _, _ = select.select([stream.connection.socket], [], [], 0.5)
            if readable:
                try:
                    stream._next(1)
                except RuntimeError as exc:
                    text = str(exc)
                    if text.startswith("terminal_ended_"):
                        reason = text.removeprefix("terminal_ended_")
                        break
                    if text == "terminal_broker_disconnected":
                        break
                    raise
        elapsed = time.monotonic() - started
        if reason is None:
            value = status(request["session_id"])
            reason = value.get("termination_reason")
        if not 895 <= elapsed <= 925 or reason != "expired":
            raise RuntimeError("terminal_root_absolute_deadline")
        return {
            "configured_seconds": 900,
            "elapsed_seconds": round(elapsed, 2),
            "termination_reason": reason,
            "clock": "monotonic_runtime",
        }
    finally:
        stream.close()
        try:
            terminate(request["session_id"])
        except RuntimeError:
            pass


def parse_api_boundary_result(raw: str):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("terminal_api_boundary_invalid") from exc
    if not isinstance(value, dict) or set(value) != API_BOUNDARY_KEYS:
        raise RuntimeError("terminal_api_boundary_invalid")
    if (
        value["api_uid"] != 10001
        or value["api_gid"] != 10001
        or value["settings_socket"] != str(BROKER_SOCKET)
        or value["host_root"] != "/host-ops"
        or type(value["maintenance_uid"]) is not int
        or value["maintenance_uid"] <= 0
        or value["root_uid"] != 0
        or any(
            value[key] is not True
            for key in (
                "projection_readable",
                "projection_fresh",
                "socket_present",
                "socket_connect",
                "capabilities",
                "maintenance_pty",
                "root_pty",
                "cleanup",
            )
        )
    ):
        raise RuntimeError("terminal_api_boundary_invalid")
    return value


def check_api_container_boundary():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "--user",
            "10001:10001",
            "--interactive",
            "robopark-api-1",
            "python",
            "-",
        ],
        input=API_BOUNDARY_PROGRAM,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode or len(result.stdout) > 8192:
        raise RuntimeError("terminal_api_boundary_failed")
    return parse_api_boundary_result(result.stdout)


def _safe_error(exc: BaseException):
    value = str(exc)
    return value if re.fullmatch("[a-z0-9_]{1,80}", value) else "acceptance_failed"


def _record(
    report: dict[str, Any], output: Path, name: str, function: Callable[[], Any]
):
    started = time.monotonic()
    try:
        observations = function()
    except Exception as exc:
        report["checks"].append(
            {
                "name": name,
                "status": "FAIL",
                "duration_seconds": round(time.monotonic() - started, 2),
                "error": _safe_error(exc),
            }
        )
        write_report(output, report)
        raise
    report["checks"].append(
        {
            "name": name,
            "status": "PASS",
            "duration_seconds": round(time.monotonic() - started, 2),
            "observations": observations,
        }
    )
    write_report(output, report)


def selected_checks(mode: str):
    if mode == "api-boundary":
        return [("api_container_to_host_broker", check_api_container_boundary)]
    if mode != "full":
        raise ValueError("terminal_acceptance_mode_invalid")
    return [
        ("systemd_units_and_socket", check_units),
        ("accelerated_maintenance_and_idle_deadlines", check_accelerated_deadlines),
        ("maintenance_profile_and_pty", check_maintenance_profile),
        ("disconnect_15s", check_disconnect_timeout),
        ("lease_20s", check_lease_timeout),
        ("bounded_output_flood", check_output_flood),
        ("broker_restart_cgroup_cleanup", check_broker_restart_cleanup),
        ("root_profile", check_root_profile),
        ("root_absolute_900s", check_root_absolute_900s),
    ]


def run(output: Path, *, mode: str = "full"):
    if sys.platform != "linux":
        raise RuntimeError("linux_required")
    fixture = require_disposable_vm()
    systemd_version = subprocess.run(
        ["systemctl", "--version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=5,
    ).stdout.splitlines()[0]
    fixture.update(
        {
            "architecture": platform.machine(),
            "kernel": platform.release(),
            "systemd": systemd_version,
        }
    )
    report = new_report(fixture)
    for name, function in selected_checks(mode):
        _record(report, output, name, function)
    if mode == "full":
        report["timer_coverage"] = {
            "actual": ["disconnect_15s", "lease_20s", "root_absolute_900s"],
            "accelerated_installed_runtime": [
                "maintenance_absolute_3600s",
                "idle_600s",
            ],
            "excluded": ["soak_8h"],
        }
    else:
        report["scope"] = "api_container_boundary_only"
    report["passed"] = True
    write_report(output, report)
    return report


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("full", "api-boundary"), default="full")
    arguments = parser.parse_args(argv)
    try:
        report = run(arguments.output, mode=arguments.mode)
    # A harness failure must become a sanitized non-zero result without a traceback.
    except Exception as exc:  # noqa: BLE001
        print(_safe_error(exc), file=sys.stderr)
        return 1
    print(f"terminal Linux acceptance: {len(report['checks'])} checks PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
