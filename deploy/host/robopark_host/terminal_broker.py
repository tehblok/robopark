"""Host PTY broker: API policy is trusted, no public TCP listener or exec RPC."""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import signal
import socket
import stat
import struct
import time
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from math import ceil
from uuid import uuid4

from .state import HostBusy, atomic_write_json, operation_pending
from .terminal_protocol import (
    TerminalCreate,
    capability_revision,
    decode_control,
    encode_control,
    identity,
    read_frame,
    write_frame,
)
from .terminal_state import TerminalRegistry

TERMINAL_HISTORY_ROTATE_AT = 3072
TERMINAL_HISTORY_RETAIN = 2048


def worker_remaining_seconds(row, now):
    # The broker enforces the exact deadline; rounding down could let the worker
    # exit first and misclassify expiry as a voluntary shell exit.
    return max(1, min(row.request.remaining_seconds, ceil(row.hard_deadline - now)))


class TerminalBroker:
    def __init__(self, paths, runtime, *, boot_id, epoch):
        self.paths, self.runtime = paths, runtime
        self.boot_id, self.epoch = identity(boot_id), identity(epoch)
        self.revision = capability_revision(boot_id, epoch)
        self.registry = TerminalRegistry()
        self.private = paths.state / "terminal"
        self.private.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.gate = asyncio.Lock()
        self.outputs, self.sizes, self.workers = {}, {}, {}
        self.worker_ready = {}
        self.api_connections = self.worker_connections = 0

    def persist(self, row, event):
        atomic_write_json(
            self.private / f"{row.request.session_id}.json",
            {"request": row.request.as_dict(), "session": row.public()},
            mode=0o600,
        )
        audit = (
            self.private
            / f"events-{datetime.now(timezone.utc).date().isoformat()}.jsonl"
        )
        if audit.exists() and audit.stat().st_size >= 1024 * 1024:
            audit.unlink()
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "id": row.request.session_id,
            "actor_id": row.request.actor_id,
            "profile": row.request.profile,
            "event": event,
            "reason": row.reason,
        }
        fd = os.open(
            audit, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "a") as stream:
            stream.write(json.dumps(entry) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def prune_history(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        for path in self.private.glob("events-*.jsonl"):
            if path.name <= f"events-{cutoff.date().isoformat()}.jsonl":
                path.unlink()
        records = sorted(
            self.private.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for index, path in enumerate(records):
            row = self.registry.sessions.get(path.stem)
            if row and (not row.reason or row.request.broker_epoch == self.epoch):
                continue
            if (
                path.stat().st_mtime < cutoff.timestamp()
                or index >= TERMINAL_HISTORY_RETAIN
            ):
                path.unlink()
                self.registry.sessions.pop(path.stem, None)

    def _rotate_history(self):
        if len(self.registry.sessions) < TERMINAL_HISTORY_ROTATE_AT or any(
            not row.reason for row in self.registry.sessions.values()
        ):
            return False
        previous_epoch, previous_revision = self.epoch, self.revision
        self.epoch = str(uuid4())
        self.revision = capability_revision(self.boot_id, self.epoch)
        try:
            self.publish()
        except BaseException:
            self.epoch, self.revision = previous_epoch, previous_revision
            raise
        excess = len(self.registry.sessions) - TERMINAL_HISTORY_RETAIN
        for session_id, row in list(self.registry.sessions.items()):
            if excess <= 0:
                break
            if row.reason:
                self.registry.sessions.pop(session_id)
                excess -= 1
        return True

    async def maintain_history(self):
        async with self.gate:
            rotated = self._rotate_history()
            self.prune_history()
            if not rotated:
                self.publish()

    async def create(self, request):
        async with self.gate:
            if (
                request.boot_id != self.boot_id
                or request.broker_epoch != self.epoch
                or request.capability_revision != self.revision
            ):
                raise ValueError("terminal_capabilities_changed")
            if operation_pending(self.paths):
                raise HostBusy("host_busy")
            row, created = self.registry.admit(request)
            if not created:
                return row.public()
            self.outputs[request.session_id] = asyncio.Queue(maxsize=256)
            self.sizes[request.session_id] = 0
            self.worker_ready[request.session_id] = asyncio.Event()
            started = False
            try:
                self.persist(row, "create")
                self.runtime.hold()
                started = True
                await self.runtime.start(request)
                self.publish()
            except BaseException:
                # Stop also when the start reply is lost: no unowned child survives admission.
                if started:
                    try:
                        await self.runtime.stop(request.session_id, request.profile)
                    except (OSError, RuntimeError, ValueError, TimeoutError):
                        raise RuntimeError("terminal_stop_failed") from None
                self.registry.finish(request.session_id, "start_failed")
                self.discard_buffers(request.session_id)
                if not any(not r.reason for r in self.registry.sessions.values()):
                    self.runtime.release()
                with suppress(OSError):
                    self.persist(row, "ended")
                raise
            return row.public()

    def discard_buffers(self, session_id):
        queue = self.outputs.pop(session_id, None)
        if queue is not None:
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)
        self.sizes.pop(session_id, None)
        self.worker_ready.pop(session_id, None)

    async def terminate(self, session_id, *, reason):
        async with self.gate:
            row = self.registry.sessions.get(session_id)
            if row is None:
                raise ValueError("terminal_session_missing")
            if not row.reason:
                if reason == "shell_exited":
                    # The worker enforces its own deadlines and may exit before
                    # the watchdog observes the same expiry.
                    reason = dict(self.registry.expired()).get(session_id, reason)
                await self.runtime.stop(session_id, row.request.profile)
                self.registry.finish(session_id, reason)
                stream = self.workers.pop(session_id, None)
                if stream:
                    stream.close()
                self.discard_buffers(session_id)
                if not any(not r.reason for r in self.registry.sessions.values()):
                    self.runtime.release()
                self.persist(row, "ended")
                self.publish()
            return row.public()

    async def authorize_worker(self, session_id, profile, *, pid, uid, maintenance_uid):
        row = self.registry.sessions.get(session_id)
        if not row or row.reason or profile != row.request.profile:
            return False
        if uid != (0 if profile == "root" else maintenance_uid):
            return False
        return pid > 0 and pid == await self.runtime.worker_pid(session_id, profile)

    def buffered_bytes(self, session_id):
        return self.sizes.get(session_id, 0)

    async def push_output(self, session_id, data):
        row = self.registry.sessions[session_id]
        if row.reason:
            return
        queue = self.outputs[session_id]
        if self.sizes[session_id] + len(data) > 1048576 or queue.full():
            await self.terminate(session_id, reason="output_overflow")
            return
        self.sizes[session_id] += len(data)
        row.output_bytes += len(data)
        queue.put_nowait(data)

    async def recover(self):
        await self.runtime.stop_all()
        self.prune_history()
        records = sorted(
            self.private.glob("*.json"), key=lambda path: path.stat().st_mtime
        )
        for path in records:
            if path.name == "capabilities.json":
                continue
            identity(path.stem)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as stream:
                raw = stream.read(8193)
            if len(raw) > 8192:
                raise ValueError("terminal_invalid_state")
            value = json.loads(raw)
            request = TerminalCreate.from_dict(value["request"])
            row, _ = self.registry.admit(request)
            self.registry.finish(
                request.session_id,
                value["session"].get("termination_reason") or "broker_restart",
            )
            if not value["session"].get("termination_reason"):
                self.persist(row, "recovered")
        self.runtime.release()

    def publish(self):
        now = datetime.now(timezone.utc).isoformat()
        active = sum(not row.reason for row in self.registry.sessions.values())
        atomic_write_json(
            self.paths.ops / "public/terminal-capabilities.json",
            {
                "schema": 1,
                "boot_id": self.boot_id,
                "broker_epoch": self.epoch,
                "capability_revision": self.revision,
                "generated_at": now,
                "valid_for_seconds": 30,
                "profiles": ["maintenance", "root"],
                "active_sessions": active,
            },
            mode=0o644,
        )

    @staticmethod
    def peer(writer):
        return struct.unpack(
            "3i",
            writer.get_extra_info("socket").getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, 12
            ),
        )

    async def worker_connection(self, reader, writer):
        if self.worker_connections >= 4:
            writer.close()
            return
        self.worker_connections += 1
        session_id = None
        try:
            pid, uid, _ = self.peer(writer)
            kind, data = await asyncio.wait_for(read_frame(reader), 5)
            msg = decode_control(data) if kind == 0 else {}
            session_id, profile = identity(msg.get("id")), msg.get("profile")
            normal_uid = pwd.getpwnam("robopark-maint").pw_uid
            if msg.get("op") != "worker-hello" or not await self.authorize_worker(
                session_id, profile, pid=pid, uid=uid, maintenance_uid=normal_uid
            ):
                raise ValueError("terminal_worker_rejected")
            if session_id in self.workers:
                raise ValueError("terminal_worker_exists")
            self.workers[session_id] = writer
            row = self.registry.sessions[session_id]
            await write_frame(
                writer,
                0,
                encode_control(
                    {
                        "op": "worker-config",
                        "request": replace(
                            row.request,
                            remaining_seconds=worker_remaining_seconds(
                                row, time.monotonic()
                            ),
                        ).as_dict(),
                    }
                ),
            )
            self.worker_ready[session_id].set()
            while True:
                kind, data = await read_frame(reader)
                if kind != 2:
                    raise ValueError("terminal_invalid_output")
                await self.push_output(session_id, data)
        except (
            OSError,
            ValueError,
            asyncio.IncompleteReadError,
            TimeoutError,
            RuntimeError,
        ):
            pass
        finally:
            self.worker_connections -= 1
            if session_id and self.workers.get(session_id) is writer:
                self.workers.pop(session_id, None)
                with suppress(ValueError, RuntimeError):
                    await self.terminate(session_id, reason="shell_exited")
            writer.close()

    async def api_connection(self, reader, writer):
        if self.api_connections >= 32:
            writer.close()
            return
        self.api_connections += 1
        try:
            _, uid, _ = self.peer(writer)
            if uid != 10001:
                raise ValueError("terminal_peer_rejected")
            kind, data = await asyncio.wait_for(read_frame(reader), 5)
            if kind != 0:
                raise ValueError("terminal_invalid_control")
            msg = decode_control(data)
            if msg["op"] == "create":
                result = await self.create(TerminalCreate.from_dict(msg["request"]))
            else:
                session_id = identity(msg.get("id"))
                if msg["op"] == "attach":
                    await self.stream(reader, writer, session_id, msg["attachment_id"])
                    return
                if msg["op"] == "status":
                    row = self.registry.sessions.get(session_id)
                    if row is None:
                        raise ValueError("terminal_session_missing")
                    result = row.public()
                elif msg["op"] == "terminate":
                    result = await self.terminate(
                        session_id,
                        reason="revoked" if msg["reason"] == "revoked" else "closed",
                    )
                else:
                    raise ValueError("terminal_invalid_operation")
            await write_frame(
                writer, 0, encode_control({"op": "ok", "session": result})
            )
        except (
            HostBusy,
            ValueError,
            OSError,
            RuntimeError,
            asyncio.IncompleteReadError,
            TimeoutError,
        ) as exc:
            code = "terminal_host_busy" if isinstance(exc, HostBusy) else str(exc)
            if not code.startswith("terminal_") or len(code) > 80:
                code = "terminal_unavailable"
            with suppress(Exception):
                await write_frame(
                    writer, 0, encode_control({"op": "error", "error": code})
                )
        finally:
            self.api_connections -= 1
            writer.close()

    async def stream(self, reader, writer, session_id, attachment_id):
        row = self.registry.active(session_id)
        await asyncio.wait_for(self.worker_ready[session_id].wait(), 5)
        self.registry.attach(session_id, attachment_id)
        queue = self.outputs[session_id]

        async def output():
            while True:
                data = await queue.get()
                if data is None:
                    await write_frame(
                        writer, 0, encode_control({"op": "ended", "reason": row.reason})
                    )
                    return
                self.sizes[session_id] -= len(data)
                await write_frame(writer, 2, data)

        async def inputs():
            while True:
                kind, data = await read_frame(reader)
                self.registry.active(session_id, attachment_id)
                worker = self.workers.get(session_id)
                if worker is None:
                    raise ValueError("terminal_worker_missing")
                if kind == 1:
                    if data:
                        self.registry.input_seen(session_id, attachment_id)
                    row.input_bytes += len(data)
                    await write_frame(worker, 1, data)
                elif kind == 0:
                    msg = decode_control(data)
                    if (
                        msg.get("id") != session_id
                        or msg.get("attachment_id") != attachment_id
                    ):
                        raise ValueError("terminal_attachment_changed")
                    if msg["op"] == "lease":
                        self.registry.renew(session_id, attachment_id)
                    elif msg["op"] == "resize":
                        if (
                            type(msg["cols"]) is not int
                            or not 20 <= msg["cols"] <= 300
                            or type(msg["rows"]) is not int
                            or not 5 <= msg["rows"] <= 150
                        ):
                            raise ValueError("terminal_invalid_size")
                    else:
                        raise ValueError("terminal_invalid_operation")
                    await write_frame(worker, 0, data)
                else:
                    raise ValueError("terminal_invalid_input")

        tasks = []
        try:
            self.persist(row, "attach")
            await write_frame(
                writer, 0, encode_control({"op": "ok", "session": row.public()})
            )
            tasks = [asyncio.create_task(output()), asyncio.create_task(inputs())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.registry.detach(session_id, attachment_id)
            self.persist(row, "detach")

    async def watch(self):
        publish_at = 0
        while True:
            if operation_pending(self.paths):
                for session_id, row in list(self.registry.sessions.items()):
                    if not row.reason:
                        await self.terminate(
                            session_id, reason="host_operation_requested"
                        )
            for session_id, reason in self.registry.expired():
                await self.terminate(session_id, reason=reason)
            if time.monotonic() >= publish_at:
                await self.maintain_history()
                publish_at = time.monotonic() + 10
            await asyncio.sleep(1)


async def serve(paths):
    from .terminal_runtime import TerminalRuntime

    boot = (paths.root / "proc/sys/kernel/random/boot_id").read_text().strip()
    broker = TerminalBroker(
        paths, TerminalRuntime(paths), boot_id=boot, epoch=str(uuid4())
    )
    await broker.recover()
    api_path = paths.root / "run/robopark-terminal/broker.sock"
    worker_path = paths.root / "run/robopark-terminal-workers/worker.sock"
    for path in (api_path, worker_path):
        if path.exists() or path.is_symlink():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise ValueError("terminal_unsafe_socket")
            path.unlink()
    servers = [
        await asyncio.start_unix_server(
            broker.api_connection, path=str(api_path), limit=65536
        ),
        await asyncio.start_unix_server(
            broker.worker_connection, path=str(worker_path), limit=65536
        ),
    ]
    os.chown(api_path, 0, 10001)
    api_path.chmod(0o660)
    os.chown(worker_path, 0, pwd.getpwnam("robopark-maint").pw_gid)
    worker_path.chmod(0o660)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    watch = asyncio.create_task(broker.watch())
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            [watch, stop_task], return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            task.result()
    finally:
        watch.cancel()
        stop_task.cancel()
        await asyncio.gather(watch, stop_task, return_exceptions=True)
        for server in servers:
            server.close()
            await server.wait_closed()
        for sid, row in list(broker.registry.sessions.items()):
            if not row.reason:
                await broker.terminate(sid, reason="broker_stopped")
        broker.runtime.release()
        with suppress(FileNotFoundError):
            (paths.ops / "public/terminal-capabilities.json").unlink()
        for path in (api_path, worker_path):
            with suppress(FileNotFoundError):
                path.unlink()


def run_broker(paths):
    if os.geteuid() != 0 or not hasattr(socket, "SO_PEERCRED"):
        raise ValueError("terminal_requires_linux_root")
    asyncio.run(serve(paths))
    return 0
