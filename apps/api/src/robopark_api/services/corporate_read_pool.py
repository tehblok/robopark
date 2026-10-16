"""Bounded, short-lived sharing of owner-scoped read results (never writes)."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any


class _LeaderInterrupted(ConnectionError):
    pass


class BoundedThreadCalls:
    """Bound queued/running thread work and retain slots until real completion."""

    def __init__(self, *, max_workers: int = 16, max_waiters: int = 256) -> None:
        self.slots = asyncio.Semaphore(max_workers)
        self.max_waiters = max_waiters
        self.waiters = 0

    async def run(self, deadline: float, fn: Callable[..., Any], *args, **kwargs):
        if self.waiters >= self.max_waiters or deadline <= time.monotonic():
            raise TimeoutError("corporate_authority_capacity")
        self.waiters += 1
        try:
            await asyncio.wait_for(
                self.slots.acquire(), timeout=max(0.001, deadline - time.monotonic())
            )
        finally:
            self.waiters -= 1
        task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))

        def release_when_finished(completed: asyncio.Task) -> None:
            self.slots.release()
            if not completed.cancelled():
                completed.exception()

        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), timeout=max(0.001, deadline - time.monotonic())
            )
        except BaseException:
            task.add_done_callback(release_when_finished)
            raise
        self.slots.release()
        return result


async def send_json_before(channel, value: dict, *, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("corporate_send_deadline")
    await asyncio.wait_for(channel.send_json(value), timeout=remaining)


class CorporateReadPool:
    def __init__(
        self,
        *,
        max_active: int = 256,
        max_per_requester: int = 16,
        max_bytes: int = 16 * 1024 * 1024,
        max_item_bytes: int = 4 * 1024 * 1024,
        ttl_seconds: float = 3.0,
    ) -> None:
        self.max_active = max_active
        self.max_per_requester = max_per_requester
        self.max_bytes = max_bytes
        self.max_item_bytes = max_item_bytes
        self.ttl_seconds = ttl_seconds
        self.active: dict[int, int] = {}
        self.flights: dict[str, asyncio.Future[bytes]] = {}
        self.cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self.cache_bytes = 0

    def admit(self, requester: int) -> bool:
        if sum(self.active.values()) >= self.max_active:
            return False
        count = self.active.get(requester, 0)
        if count >= self.max_per_requester:
            return False
        self.active[requester] = count + 1
        return True

    def release(self, requester: int) -> None:
        count = self.active.get(requester, 0)
        if count <= 1:
            self.active.pop(requester, None)
        else:
            self.active[requester] = count - 1

    def invalidate(self, key: str) -> None:
        previous = self.cache.pop(key, None)
        if previous:
            self.cache_bytes -= len(previous[1])

    async def read(
        self,
        key: str,
        load: Callable[[], Awaitable[bytes]],
        *,
        deadline: float,
    ) -> bytes:
        while True:
            now = time.monotonic()
            for old_key, (expires, _) in list(self.cache.items()):
                if expires <= now:
                    self.invalidate(old_key)
            cached = self.cache.get(key)
            if cached:
                self.cache.move_to_end(key)
                return bytes(cached[1])
            if deadline <= now:
                raise TimeoutError
            existing = self.flights.get(key)
            if existing is not None:
                try:
                    return bytes(await asyncio.wait_for(asyncio.shield(existing), deadline - now))
                except _LeaderInterrupted:
                    # The cancelled request owned the inline load.  Healthy
                    # followers elect a replacement within their own deadline.
                    await asyncio.sleep(0)
                    continue
            future = asyncio.get_running_loop().create_future()
            self.flights[key] = future
            try:
                value = bytes(await asyncio.wait_for(load(), deadline - now))
                size = len(value)
                if 0 < size <= self.max_item_bytes:
                    self.invalidate(key)
                    self.cache[key] = (time.monotonic() + self.ttl_seconds, value)
                    self.cache_bytes += size
                    while len(self.cache) > 256 or self.cache_bytes > self.max_bytes:
                        self.invalidate(next(iter(self.cache)))
                future.set_result(value)
                return value
            except asyncio.CancelledError:
                future.set_exception(_LeaderInterrupted("corporate_read_interrupted"))
                future.exception()
                raise
            except TimeoutError:
                future.set_exception(_LeaderInterrupted("corporate_read_interrupted"))
                future.exception()
                raise
            except BaseException as exc:
                future.set_exception(exc)
                future.exception()
                raise
            finally:
                if self.flights.get(key) is future:
                    self.flights.pop(key, None)
