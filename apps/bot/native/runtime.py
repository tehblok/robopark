from __future__ import annotations

import fcntl
import json
import os
import signal
import stat
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from queue import Empty, Queue
from tempfile import NamedTemporaryFile

from native.service import BotService
from native.transport import APIClient, ServiceError, TelegramClient


def acquire_instance(data_dir: Path):
    descriptor = os.open(
        data_dir / "native-bot.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
        ):
            raise OSError("unsafe_bot_lock")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_offset(data_dir: Path):
    path = data_dir / "native-update-offset.json"
    if not path.exists():
        # Preserve old acknowledged updates without activating any old scheduler.
        previous = data_dir / "dispatcher_bot.offset"
        if not previous.exists():
            return 0
        descriptor = os.open(previous, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 32:
                raise ValueError("unsafe_bot_offset")
            value = stream.read().decode("ascii").strip()
        if not value.isdigit():
            raise ValueError("invalid_bot_offset")
        return int(value)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 256:
            raise ValueError("unsafe_bot_offset")
        value = json.load(stream)
    offset = value.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("invalid_bot_offset")
    return offset


def save_offset(data_dir: Path, offset: int):
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", dir=data_dir, prefix=".native-offset-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"offset": offset}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, data_dir / "native-update-offset.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _scheduler(stop, service, state):
    while not stop.is_set():
        try:
            # Claim one at a time: large report preparation cannot expire a queue
            # of leases before those deliveries begin.
            data = service.api.call("POST", "/claim", {"limit": 1})
            state.update(
                scheduler_ok=True, scheduler_at=time.monotonic(), scheduler_error=None
            )
            for item in data["deliveries"]:
                if not stop.is_set():
                    service.deliver(item)
            stop.wait(2 if data["deliveries"] else 10)
        except Exception as error:  # noqa: BLE001 - isolate a job; log only a safe code
            state["scheduler_ok"] = False
            state["scheduler_error"] = (
                error.code if isinstance(error, ServiceError) else "scheduler_error"
            )
            print(
                "bot scheduler:",
                error.code if isinstance(error, ServiceError) else "scheduler_error",
                flush=True,
            )
            stop.wait(10)


def health_snapshot(state, *, now=None):
    now = time.monotonic() if now is None else now
    telegram_ok = state["telegram_ok"] and now - state["poll_at"] < 90
    scheduler_ok = state["scheduler_ok"] and now - state["scheduler_at"] < 300
    return {
        "telegram_ok": telegram_ok,
        "scheduler_ok": scheduler_ok,
        "last_error": state["last_error"]
        or (state.get("scheduler_error") if not scheduler_ok else None),
    }


class UpdateWorkers:
    """Four bounded FIFO lanes keep each user's conversation ordered."""

    def __init__(self, factory, *, lanes=4):
        self.stop = threading.Event()
        self.queues = [Queue(maxsize=20) for _ in range(lanes)]
        self.threads = []
        for index, queue in enumerate(self.queues):
            thread = threading.Thread(
                target=self._work,
                args=(factory, queue),
                name=f"bot-inbox-{index}",
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)

    def _work(self, factory, queue):
        service = factory()
        try:
            while not self.stop.is_set():
                try:
                    update, future = queue.get(timeout=0.2)
                except Empty:
                    continue
                try:
                    service.handle_update(update)
                except Exception as error:  # noqa: BLE001 - isolate untrusted updates; never log their bodies
                    print(
                        "bot update:",
                        error.code
                        if isinstance(error, ServiceError)
                        else "update_failed",
                        flush=True,
                    )
                finally:
                    future.set_result(None)
                    queue.task_done()
        finally:
            service.api.close()
            service.telegram.close()

    def submit(self, update):
        callback = update.get("callback_query") or {}
        message = callback.get("message") or update.get("message") or {}
        sender = callback.get("from") or message.get("from") or {}
        user_id = sender.get("id")
        lane = user_id % len(self.queues) if isinstance(user_id, int) else 0
        future = Future()
        self.queues[lane].put_nowait((update, future))
        return future

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=0.3)


def run_service(
    *,
    data_dir: Path,
    api_url: str,
    bridge_key: str,
    token: str,
    ready_file: Path,
    heartbeat_fd: int,
):
    stop = threading.Event()
    lock_fd = acquire_instance(data_dir)
    telegram = TelegramClient(token)
    scheduler_api, scheduler_tg = APIClient(api_url, bridge_key), TelegramClient(token)
    health_api = APIClient(api_url, bridge_key, timeout=(3, 5))
    state = {
        "telegram_ok": False,
        "scheduler_ok": False,
        "last_error": None,
        "poll_at": 0.0,
        "scheduler_at": 0.0,
    }
    scheduler = threading.Thread(
        target=_scheduler,
        args=(stop, BotService(scheduler_api, scheduler_tg), state),
        name="bot-scheduler",
        daemon=True,
    )

    def health_loop():
        last_report, previous = 0.0, None
        while not stop.is_set():
            try:
                snapshot = health_snapshot(state)
                if snapshot["telegram_ok"] and snapshot["scheduler_ok"]:
                    os.utime(heartbeat_fd, None)
                    if not ready_file.exists():
                        ready_file.write_text("native-ready\n")
                        ready_file.chmod(0o600)
                    os.utime(ready_file, None)
                else:
                    ready_file.unlink(missing_ok=True)
                if snapshot != previous or time.monotonic() - last_report >= 30:
                    health_api.call("POST", "/health", snapshot)
                    previous, last_report = snapshot, time.monotonic()
            except ServiceError:
                pass
            except OSError:
                # A dead health writer must not leave the process falsely ready.
                print("bot health: heartbeat_write_failed", flush=True)
                stop.set()
            stop.wait(5)

    health = threading.Thread(target=health_loop, name="bot-health", daemon=True)

    def request_stop(_signum, _frame):
        stop.set()

    previous_signals = {
        sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    inbox = None
    try:
        # No ready marker until both Telegram and the Robopark scheduler respond.
        try:
            telegram.call("getMe")
        except ServiceError as error:
            try:
                health_api.call(
                    "POST",
                    "/health",
                    {
                        "telegram_ok": False,
                        "scheduler_ok": False,
                        "last_error": error.code,
                    },
                )
            except ServiceError:
                pass
            raise
        offset = read_offset(data_dir)
        inbox = UpdateWorkers(
            lambda: BotService(APIClient(api_url, bridge_key), TelegramClient(token))
        )
        scheduler.start()
        health.start()
        while not stop.is_set():
            try:
                updates = telegram.call(
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": 25,
                        "limit": 20,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                if not isinstance(updates, list) or len(updates) > 20:
                    raise ServiceError("invalid_telegram_updates")
                state.update(
                    telegram_ok=True, poll_at=time.monotonic(), last_error=None
                )
                pending = []
                for update in updates:
                    uid = update.get("update_id") if isinstance(update, dict) else None
                    if (
                        not isinstance(uid, int)
                        or isinstance(uid, bool)
                        or uid < offset
                    ):
                        continue
                    if stop.is_set():
                        break
                    pending.append((uid, inbox.submit(update)))
                # Acknowledge only a completed prefix. Each batch is at most 20;
                # no unbounded backlog or early Telegram acknowledgement.
                while pending and not stop.is_set():
                    if pending[0][1].done():
                        uid, _ = pending.pop(0)
                        offset = uid + 1
                        save_offset(data_dir, offset)
                        state["poll_at"] = time.monotonic()
                    else:
                        stop.wait(0.05)
            except ServiceError as error:
                state.update(telegram_ok=False, last_error=error.code)
                print("bot polling:", error.code, flush=True)
                stop.wait(5)
        return 0
    finally:
        stop.set()
        ready_file.unlink(missing_ok=True)
        if inbox is not None:
            inbox.close()
        if scheduler.is_alive():
            scheduler.join(timeout=45)
        if health.is_alive():
            health.join(timeout=5)
        clients = [telegram]
        if not scheduler.is_alive():
            clients.extend([scheduler_api, scheduler_tg])
        if not health.is_alive():
            clients.append(health_api)
        for client in clients:
            client.close()
        for sig, handler in previous_signals.items():
            signal.signal(sig, handler)
        os.close(lock_fd)
