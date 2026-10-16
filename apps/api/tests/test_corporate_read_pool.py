import asyncio
import threading
import time

import pytest

from robopark_api.services import corporate_gateway_contract
from robopark_api.services.compute_broker import ComputeChannel, ComputeRegistry
from robopark_api.services.corporate_read_pool import (
    BoundedThreadCalls,
    CorporateReadPool,
    send_json_before,
)


def test_two_hundred_callers_share_one_immutable_inventory_load():
    async def scenario():
        pool = CorporateReadPool()
        started = asyncio.Event()
        release = asyncio.Event()
        loads = 0

        async def load() -> bytes:
            nonlocal loads
            loads += 1
            started.set()
            await release.wait()
            return b'{"payload":{"name":"a664"}}'

        assert all(pool.admit(user_id) for user_id in range(1, 201))
        tasks = [
            asyncio.create_task(pool.read("same-owner-scope", load, deadline=time.monotonic() + 2))
            for _ in range(200)
        ]
        await started.wait()
        release.set()
        results = await asyncio.gather(*tasks)
        for user_id in range(1, 201):
            pool.release(user_id)

        assert loads == 1
        assert results == [b'{"payload":{"name":"a664"}}'] * 200
        assert pool.active == {}
        assert pool.flights == {}

    asyncio.run(scenario())


def test_cancelled_leader_lets_existing_follower_elect_replacement():
    async def scenario():
        pool = CorporateReadPool()
        first_started = asyncio.Event()
        calls = 0

        async def load() -> bytes:
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                await asyncio.Event().wait()
            return b"fresh"

        leader = asyncio.create_task(pool.read("scope", load, deadline=time.monotonic() + 1))
        await first_started.wait()
        follower = asyncio.create_task(pool.read("scope", load, deadline=time.monotonic() + 1))
        await asyncio.sleep(0)
        leader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leader
        assert await follower == b"fresh"
        assert calls == 2
        assert pool.flights == {}

    asyncio.run(scenario())


def test_short_deadline_leader_does_not_fail_long_deadline_follower():
    async def scenario():
        pool = CorporateReadPool()
        first_started = asyncio.Event()
        calls = 0

        async def load() -> bytes:
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                await asyncio.sleep(1)
            return b"fresh"

        leader = asyncio.create_task(pool.read("scope", load, deadline=time.monotonic() + 0.01))
        await first_started.wait()
        follower = asyncio.create_task(pool.read("scope", load, deadline=time.monotonic() + 1))
        with pytest.raises(TimeoutError):
            await leader
        assert await follower == b"fresh"
        assert calls == 2

    asyncio.run(scenario())


def test_inventory_cache_is_owner_scoped_bounded_and_copied():
    async def scenario():
        pool = CorporateReadPool(max_bytes=7, max_item_bytes=6)
        calls = 0

        async def load() -> bytes:
            nonlocal calls
            calls += 1
            return b"123456"

        first = await pool.read("owner-a", load, deadline=time.monotonic() + 1)
        cached = await pool.read("owner-a", load, deadline=time.monotonic() + 1)
        other = await pool.read("owner-b", load, deadline=time.monotonic() + 1)
        assert first == cached == other == b"123456"
        assert calls == 2
        assert list(pool.cache) == ["owner-b"]
        assert pool.cache_bytes == 6

    asyncio.run(scenario())


def test_corporate_read_capacity_is_separate_and_waiters_are_fifo():
    async def scenario():
        registry = ComputeRegistry(max_pending=1, max_requester_pending=1)
        channel = ComputeChannel(device_id="mac", owner_user_id=7, websocket=object())
        channel.ready = True
        channel.supports = {
            corporate_gateway_contract.OPERATION,
            "corporate_inventory_read_v1",
            "image_resize_v1",
        }
        channel.capacity = 1
        channel.corporate_read_capacity = 2
        registry.replace(channel)
        heavy = registry.new_lease(
            channel,
            operation="image_resize_v1",
            payload_bytes=b"heavy",
            requester_user_id=1,
            deadline_monotonic=time.monotonic() + 2,
        )
        selection = {
            "required_supports": {"corporate_inventory_read_v1"},
            "eligible_owner_ids": frozenset({7}),
            "routing_key": "inventory:rover_card",
            "excluded": set(),
        }
        first_channel = await registry.wait_for_read_slot(
            requester_user_id=2, deadline=time.monotonic() + 1, **selection
        )
        assert first_channel is channel
        first = registry.new_lease(
            channel,
            operation=corporate_gateway_contract.OPERATION,
            payload_bytes=b"read-1",
            requester_user_id=2,
            deadline_monotonic=time.monotonic() + 2,
        )
        registry.release_read_slot(channel, 2)
        second = registry.new_lease(
            channel,
            operation=corporate_gateway_contract.OPERATION,
            payload_bytes=b"read-2",
            requester_user_id=3,
            deadline_monotonic=time.monotonic() + 2,
        )
        order: list[str] = []

        async def waiter(name: str, requester: int):
            selected = await registry.wait_for_read_slot(
                requester_user_id=requester, deadline=time.monotonic() + 2, **selection
            )
            order.append(name)
            return selected

        waiting_first = asyncio.create_task(waiter("first", 4))
        waiting_second = asyncio.create_task(waiter("second", 5))
        await asyncio.sleep(0)
        registry.finish(channel, first)
        assert await waiting_first is channel
        replacement = registry.new_lease(
            channel,
            operation=corporate_gateway_contract.OPERATION,
            payload_bytes=b"read-3",
            requester_user_id=4,
            deadline_monotonic=time.monotonic() + 2,
        )
        registry.release_read_slot(channel, 4)
        assert not waiting_second.done()
        registry.finish(channel, second)
        assert await waiting_second is channel
        registry.release_read_slot(channel, 5)
        assert order == ["first", "second"]
        registry.finish(channel, replacement)
        registry.finish(channel, heavy)
        assert registry.pending == {}
        assert registry.pending_payload_bytes == 0
        assert registry.corporate_pending_payload_bytes == 0

    asyncio.run(scenario())


def test_read_waiter_survives_empty_pool_and_accepts_reconnected_generation():
    async def scenario():
        registry = ComputeRegistry()
        selection = {
            "required_supports": {"corporate_inventory_read_v1"},
            "eligible_owner_ids": frozenset({7}),
            "routing_key": "inventory:rover_card",
            "excluded": set(),
        }
        old = ComputeChannel(device_id="mac", owner_user_id=7, websocket=object())
        old.generation = "old-generation"
        old.ready = True
        old.supports = {
            corporate_gateway_contract.OPERATION,
            "corporate_inventory_read_v1",
        }
        old.corporate_read_capacity = 0
        registry.replace(old)
        waiting = asyncio.create_task(
            registry.wait_for_read_slot(
                requester_user_id=2,
                deadline=time.monotonic() + 1,
                excluded_generations={(old.device_id, old.generation)},
                wait_through_empty=True,
                **selection,
            )
        )
        await asyncio.sleep(0)
        assert not waiting.done()

        channel = ComputeChannel(device_id="mac", owner_user_id=7, websocket=object())
        channel.generation = "fresh-generation"
        channel.ready = True
        channel.supports = {
            corporate_gateway_contract.OPERATION,
            "corporate_inventory_read_v1",
        }
        channel.corporate_read_capacity = 1
        registry.replace(channel)

        assert await waiting is channel
        registry.release_read_slot(channel, 2)
        assert registry._read_reservations == {}

    asyncio.run(scenario())


def test_cancelled_read_waiter_is_removed_without_consuming_capacity():
    async def scenario():
        registry = ComputeRegistry()
        waiting = asyncio.create_task(
            registry.wait_for_read_slot(
                requester_user_id=2,
                deadline=time.monotonic() + 10,
                required_supports={"corporate_inventory_read_v1"},
                eligible_owner_ids=frozenset({7}),
                routing_key="inventory:rover_card",
                excluded=set(),
                wait_through_empty=True,
            )
        )
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

        assert list(registry._read_waiters) == []
        assert registry._read_reservations == {}

    asyncio.run(scenario())


def test_read_reservation_counts_against_requester_budget_before_lease_creation():
    async def scenario():
        registry = ComputeRegistry()
        registry.max_corporate_requester_pending = 1
        for device_id in ("mac-a", "mac-b"):
            channel = ComputeChannel(device_id=device_id, owner_user_id=7, websocket=object())
            channel.ready = True
            channel.supports = {
                corporate_gateway_contract.OPERATION,
                "corporate_inventory_read_v1",
            }
            channel.corporate_read_capacity = 1
            registry.replace(channel)
        selection = {
            "required_supports": {"corporate_inventory_read_v1"},
            "eligible_owner_ids": frozenset({7}),
            "routing_key": "inventory:rover_card",
            "excluded": set(),
        }

        first = await registry.wait_for_read_slot(
            requester_user_id=2, deadline=time.monotonic() + 1, **selection
        )
        waiting = asyncio.create_task(
            registry.wait_for_read_slot(
                requester_user_id=2, deadline=time.monotonic() + 1, **selection
            )
        )
        await asyncio.sleep(0)
        assert first is not None
        assert not waiting.done()

        registry.release_read_slot(first, 2)
        assert await waiting is not None
        selected = waiting.result()
        registry.release_read_slot(selected, 2)
        assert registry._read_reservations == {}

    asyncio.run(scenario())


def test_initially_empty_read_pool_returns_immediately_for_server_fallback():
    async def scenario():
        registry = ComputeRegistry()
        started = time.monotonic()
        selected = await registry.wait_for_read_slot(
            requester_user_id=2,
            deadline=started + 1,
            required_supports={"corporate_inventory_read_v1"},
            eligible_owner_ids=frozenset({7}),
            routing_key="inventory:rover_card",
        )
        assert selected is None
        assert time.monotonic() - started < 0.05

    asyncio.run(scenario())


def test_incompatible_older_waiter_does_not_block_ready_owner_mac():
    async def scenario():
        registry = ComputeRegistry()
        blocked = ComputeChannel(device_id="mac-a", owner_user_id=7, websocket=object())
        blocked.ready = True
        blocked.supports = {
            corporate_gateway_contract.OPERATION,
            "corporate_inventory_read_v1",
        }
        blocked.corporate_read_capacity = 0
        ready = ComputeChannel(device_id="mac-b", owner_user_id=8, websocket=object())
        ready.ready = True
        ready.supports = set(blocked.supports)
        ready.corporate_read_capacity = 1
        registry.replace(blocked)
        registry.replace(ready)

        older = asyncio.create_task(
            registry.wait_for_read_slot(
                requester_user_id=2,
                deadline=time.monotonic() + 1,
                required_supports={"corporate_inventory_read_v1"},
                eligible_owner_ids=frozenset({7}),
                routing_key="inventory:rover_card",
            )
        )
        await asyncio.sleep(0)
        younger = await registry.wait_for_read_slot(
            requester_user_id=3,
            deadline=time.monotonic() + 1,
            required_supports={"corporate_inventory_read_v1"},
            eligible_owner_ids=frozenset({8}),
            routing_key="inventory:rover_card",
        )
        assert younger is ready
        registry.release_read_slot(younger, 3)
        older.cancel()
        with pytest.raises(asyncio.CancelledError):
            await older

    asyncio.run(scenario())


def test_unready_inventory_waiter_does_not_block_healthy_tracker_read():
    async def scenario():
        registry = ComputeRegistry()
        channel = ComputeChannel(device_id="shared-mac", owner_user_id=7, websocket=object())
        channel.ready = True
        channel.supports = {
            corporate_gateway_contract.OPERATION,
            "corporate_inventory_read_v1",
            "corporate_tracker_read_v1",
        }
        channel.corporate_read_capacity = 1
        channel.corporate_inventory_status = "unavailable"
        registry.replace(channel)

        inventory = asyncio.create_task(
            registry.wait_for_read_slot(
                requester_user_id=2,
                deadline=time.monotonic() + 1,
                required_supports={"corporate_inventory_read_v1"},
                eligible_owner_ids=frozenset({7}),
                routing_key="inventory:rover_card",
            )
        )
        await asyncio.sleep(0)
        tracker = await registry.wait_for_read_slot(
            requester_user_id=3,
            deadline=time.monotonic() + 1,
            required_supports={"corporate_tracker_read_v1"},
            eligible_owner_ids=frozenset({7}),
            routing_key="tracker:issue_read",
        )
        assert tracker is channel
        registry.release_read_slot(tracker, 3)
        inventory.cancel()
        with pytest.raises(asyncio.CancelledError):
            await inventory

    asyncio.run(scenario())


def test_requester_at_read_limit_does_not_block_another_requester():
    async def scenario():
        registry = ComputeRegistry()
        budget_channel = ComputeChannel(device_id="budget-mac", owner_user_id=8, websocket=object())
        target = ComputeChannel(device_id="target-mac", owner_user_id=7, websocket=object())
        for channel in (budget_channel, target):
            channel.ready = True
            channel.supports = {
                corporate_gateway_contract.OPERATION,
                "corporate_inventory_read_v1",
            }
            channel.corporate_read_capacity = 1
            channel.corporate_inventory_status = "ready"
            registry.replace(channel)
        leases = [
            registry.new_lease(
                budget_channel,
                operation=corporate_gateway_contract.OPERATION,
                payload_bytes=b"read",
                requester_user_id=2,
                deadline_monotonic=time.monotonic() + 2,
            )
            for _ in range(registry.max_corporate_requester_pending)
        ]
        older = asyncio.create_task(
            registry.wait_for_read_slot(
                requester_user_id=2,
                deadline=time.monotonic() + 1,
                required_supports={"corporate_inventory_read_v1"},
                eligible_owner_ids=frozenset({7}),
                routing_key="inventory:rover_card",
            )
        )
        await asyncio.sleep(0)
        younger = await registry.wait_for_read_slot(
            requester_user_id=3,
            deadline=time.monotonic() + 1,
            required_supports={"corporate_inventory_read_v1"},
            eligible_owner_ids=frozenset({7}),
            routing_key="inventory:rover_card",
        )
        assert younger is target
        registry.release_read_slot(younger, 3)
        older.cancel()
        with pytest.raises(asyncio.CancelledError):
            await older
        for lease in leases:
            registry.finish(budget_channel, lease)

    asyncio.run(scenario())


def test_timed_out_thread_call_retains_worker_slot_until_thread_really_finishes():
    async def scenario():
        calls = BoundedThreadCalls(max_workers=1, max_waiters=1)
        started = threading.Event()
        release = threading.Event()

        def blocked():
            started.set()
            release.wait()
            return "done"

        first = asyncio.create_task(calls.run(time.monotonic() + 0.02, blocked))
        assert await asyncio.to_thread(started.wait, 1)
        with pytest.raises(TimeoutError):
            await first
        assert calls.slots._value == 0
        with pytest.raises(TimeoutError):
            await calls.run(time.monotonic() + 0.02, lambda: "must-not-start")
        assert calls.slots._value == 0

        release.set()
        for _ in range(100):
            if calls.slots._value == 1:
                break
            await asyncio.sleep(0.001)
        assert calls.slots._value == 1
        assert await calls.run(time.monotonic() + 1, lambda: "fresh") == "fresh"

    asyncio.run(scenario())


def test_backpressured_corporate_send_obeys_absolute_deadline():
    class BlockedChannel:
        async def send_json(self, _value):
            await asyncio.Event().wait()

    async def scenario():
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await send_json_before(BlockedChannel(), {}, deadline=started + 0.02)
        assert time.monotonic() - started < 0.1

    asyncio.run(scenario())
