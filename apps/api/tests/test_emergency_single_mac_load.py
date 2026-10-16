"""Wire-level load contract for many mechanics sharing one owner Mac.

This is synthetic capacity evidence, not a production benchmark.  It keeps the
real HTTP, ASGI, signed internal RPC, WebSocket authentication, park ownership
policy and broker lifecycle.  Only the Tracker-backed VIN oracle is replaced;
that ACL has separate focused coverage and would make this hermetic load test
depend on an external service.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import threading
import time
import zlib
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import uvicorn
import websockets
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy.orm import sessionmaker

from conftest import role_id_for
from robopark_api.config import Settings
from robopark_api.emergency_bridge import create_broker_app
from robopark_api.models import AccessStatus, AgentDevice, Park, User, UserPark
from robopark_api.services import emergency_scope
from robopark_api.services.emergency_bridge_auth import body_signature
from robopark_api.services.rbac import RoleSlug

_INSTANCE_ID = "11111111-1111-1111-1111-111111111111"
_SECRET = "single-mac-load-test-secret"


def _vin(number: int) -> str:
    return f"YASADR{number:011d}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _raw_deflate(payload: dict) -> tuple[str, int, int]:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    compressor = zlib.compressobj(wbits=-15)
    encoded = compressor.compress(raw) + compressor.flush()
    return base64.b64encode(encoded).decode(), len(encoded), len(raw)


def _signed_rpc(user_id: int, vin: str, nonce: str) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "vin": vin,
            "requester_user_id": user_id,
            "attempt_budget": 1,
            "deadline_ms": 20_000,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    timestamp = str(int(time.time()))
    return body, {
        "X-Robopark-Timestamp": timestamp,
        "X-Robopark-Nonce": nonce,
        "X-Robopark-Signature": body_signature(_SECRET, timestamp, nonce, body),
        "Content-Type": "application/json",
    }


async def _wait_for_health(base_url: str) -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(100):
            try:
                if (await client.get(base_url + "/health")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.02)
    raise AssertionError("broker did not start")


@dataclass
class BatteryMac:
    device_id: str
    private_key: ec.EllipticCurvePrivateKey
    uri: str
    websocket: object | None = None
    generation: str = ""
    jobs: list[str] = field(default_factory=list)
    active_jobs: int = 0
    reader: asyncio.Task | None = None
    heartbeat_task: asyncio.Task | None = None
    hold_jobs: bool = False
    job_started: asyncio.Event = field(default_factory=asyncio.Event)
    release_jobs: asyncio.Event = field(default_factory=asyncio.Event)
    reader_exit: str | None = None
    heartbeat_exit: str | None = None

    async def connect(self) -> None:
        self.websocket = await websockets.connect(self.uri, max_size=2 * 1024 * 1024)
        challenge = json.loads(await self.websocket.recv())
        signed = (
            "robopark-bridge-v1\n"
            + challenge["instance_id"]
            + "\n"
            + challenge["environment"]
            + "\n"
            + challenge["nonce"]
        ).encode()
        signature = self.private_key.sign(signed, ec.ECDSA(hashes.SHA256()))
        await self.websocket.send(
            json.dumps(
                {
                    "type": "auth",
                    "device_id": self.device_id,
                    "signature": base64.b64encode(signature).decode(),
                }
            )
        )
        ready = json.loads(await self.websocket.recv())
        assert ready["type"] == "ready"
        self.generation = ready["generation"]
        await self.heartbeat()
        self.reader = asyncio.create_task(self._read(), name="single-battery-mac-reader")
        self.heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="single-battery-mac-heartbeat"
        )

    async def heartbeat(self) -> None:
        assert self.websocket is not None
        await self.websocket.send(
            json.dumps(
                {
                    "type": "heartbeat",
                    "generation": self.generation,
                    "ready": True,
                    "latency_ms": 15,
                    "capacity": 1,
                    "active_jobs": self.active_jobs,
                    "load": float(self.active_jobs),
                    "paused": False,
                    "power": "battery",
                    "cpu_percent": 20,
                    "memory_percent": 30,
                    "thermal_state": "normal",
                    "supports": ["raw_deflate"],
                    "probe_available": False,
                }
            )
        )

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.1)
                await self.heartbeat()
        except asyncio.CancelledError:
            return
        except websockets.ConnectionClosed as exc:
            self.heartbeat_exit = f"{exc.code}:{exc.reason}"

    async def _read(self) -> None:
        assert self.websocket is not None
        try:
            async for raw in self.websocket:
                message = json.loads(raw)
                if message.get("type") == "job":
                    await self._answer_job(message)
        except websockets.ConnectionClosed as exc:
            self.reader_exit = f"{exc.code}:{exc.reason}"

    async def _answer_job(self, message: dict) -> None:
        assert self.websocket is not None
        vin = message["vin"]
        self.jobs.append(vin)
        self.active_jobs = 1
        if self.hold_jobs:
            self.job_started.set()
            await self.release_jobs.wait()
        await asyncio.sleep(0.015)
        payload = {"vin": vin, "timestamp": int(time.time() * 1000), "isOnline": True}
        encoded, encoded_bytes, payload_bytes = _raw_deflate(payload)
        await self.websocket.send(
            json.dumps(
                {
                    "type": "result",
                    "job_id": message["job_id"],
                    "attempt_id": message["attempt_id"],
                    "generation": self.generation,
                    "ok": True,
                    "encoding": "deflate-raw",
                    "payload_base64": encoded,
                    "encoded_bytes": encoded_bytes,
                    "payload_bytes": payload_bytes,
                }
            )
        )
        self.active_jobs = 0

    async def close(self) -> None:
        if self.heartbeat_task is not None:
            self.heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.heartbeat_task
        if self.websocket is not None:
            await self.websocket.close()
        if self.reader is not None:
            with suppress(asyncio.CancelledError, websockets.ConnectionClosed):
                await asyncio.wait_for(self.reader, timeout=3)


@pytest.mark.load
def test_two_hundred_mechanics_share_one_battery_mac_with_bounded_queue(
    db_engine, db_session, seed_admin, monkeypatch
):
    """200 callers/20 VIN collapse to 20 jobs; unique work queues and cleans up."""
    operator = User(
        username="single-mac-owner",
        password_hash=seed_admin.password_hash,
        role_id=role_id_for(db_session, RoleSlug.OPERATOR),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    park = Park(
        name="Single Mac Load Park",
        tag="single-mac-load",
        tracker_queue="SINGLEMAC",
        is_active=True,
    )
    db_session.add_all([operator, park])
    db_session.flush()
    mechanics = [
        User(
            username=f"load-mechanic-{index:03d}",
            password_hash=seed_admin.password_hash,
            role_id=role_id_for(db_session, RoleSlug.MECHANIC),
            access_status=AccessStatus.approved.value,
            is_active=True,
        )
        for index in range(200)
    ]
    db_session.add_all(mechanics)
    db_session.flush()
    db_session.add_all(
        [UserPark(user_id=operator.id, park_id=park.id)]
        + [UserPark(user_id=user.id, park_id=park.id) for user in mechanics]
    )
    private_key = ec.generate_private_key(ec.SECP256R1())
    device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=operator.id,
        label="One battery Mac",
        public_key_x963=private_key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        ),
        environment="test",
        instance_id=_INSTANCE_ID,
        approved_at=datetime.now(UTC),
        approved_by_user_id=seed_admin.id,
    )
    db_session.add(device)
    db_session.commit()
    requester_ids = [user.id for user in mechanics]

    # Park-to-owner selection and every database authority recheck stay real.
    # Only the Tracker issue lookup is replaced with a deterministic VIN oracle.
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_args: True)
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key=_SECRET,
        emergency_bridge_enabled=True,
        emergency_bridge_upstream_reads_per_second=64,
        robopark_environment="test",
        robopark_instance_id=_INSTANCE_ID,
    )
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    port = _free_port()

    async def scenario() -> None:
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", access_log=False)
        )
        server_task = asyncio.create_task(server.serve(), name="single-mac-load-uvicorn")
        base_url = f"http://127.0.0.1:{port}"
        mac = BatteryMac(
            device_id=device.id,
            private_key=private_key,
            uri=f"ws://127.0.0.1:{port}/emergency/bridge/ws",
        )
        try:
            await _wait_for_health(base_url)
            await mac.connect()
            for _ in range(100):
                channel = app.state.registry.channels.get(device.id)
                if channel is not None and channel.ready:
                    break
                await asyncio.sleep(0.02)
            channel = app.state.registry.channels[device.id]
            assert channel.ready and channel.capacity == 1 and channel.power == "battery"

            # Saturating normal data workers must not starve the control-plane
            # identity check that keeps the sole Mac connected.
            assert mac.heartbeat_task is not None
            mac.heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await mac.heartbeat_task
            await asyncio.sleep(0.1)
            default_workers_release = threading.Event()
            default_workers = [
                asyncio.create_task(asyncio.to_thread(default_workers_release.wait))
                for _ in range(64)
            ]
            await asyncio.sleep(0.1)
            heartbeat_before = channel.last_heartbeat
            try:
                await mac.heartbeat()
                for _ in range(100):
                    if channel.last_heartbeat > heartbeat_before:
                        break
                    await asyncio.sleep(0.01)
                assert channel.last_heartbeat > heartbeat_before
                assert app.state.registry.channels.get(device.id) is channel
            finally:
                default_workers_release.set()
                await asyncio.gather(*default_workers)
            mac.heartbeat_task = asyncio.create_task(
                mac._heartbeat_loop(), name="single-battery-mac-heartbeat"
            )

            limits = httpx.Limits(max_connections=400, max_keepalive_connections=100)
            async with httpx.AsyncClient(base_url=base_url, timeout=30, limits=limits) as http:

                async def rpc(user_id: int, vin: str, nonce: str) -> httpx.Response:
                    body, headers = _signed_rpc(user_id, vin, nonce)
                    return await http.post(
                        "/internal/v1/robot-payload", content=body, headers=headers
                    )

                shared_vins = [_vin(10_000 + index % 20) for index in range(200)]
                shared = await asyncio.gather(
                    *(
                        rpc(user_id, vin, f"shared-{index:03d}")
                        for index, (user_id, vin) in enumerate(
                            zip(requester_ids, shared_vins, strict=True)
                        )
                    )
                )
                shared_statuses = Counter(response.status_code for response in shared)
                shared_details = Counter(response.json().get("detail", "ok") for response in shared)
                registry = app.state.registry
                live_channel = registry.channels.get(device.id)
                diagnostics = {
                    "statuses": shared_statuses,
                    "details": shared_details,
                    "jobs": len(mac.jobs),
                    "metrics": dict(registry.metrics),
                    "waiters": len(registry._capacity_waiters),
                    "pending": len(registry.pending),
                    "loads": len(registry.loads),
                    "request_loads": len(registry.request_loads),
                    "channel": (
                        {
                            "ready": live_channel.ready,
                            "inflight": live_channel.inflight,
                            "queued": live_channel.queued,
                            "capacity": live_channel.capacity,
                            "reported_active_jobs": live_channel.reported_active_jobs,
                        }
                        if live_channel is not None
                        else None
                    ),
                }
                assert shared_statuses == Counter({200: 200}), json.dumps(
                    diagnostics, sort_keys=True, default=str
                )
                shared_jobs = Counter(mac.jobs)
                assert set(shared_jobs) == set(shared_vins)
                # The cache TTL is deliberately short (3s); under 200 real DB
                # authority checks a VIN may be refreshed once, but fan-out
                # must still collapse to approximately one wire job per VIN.
                assert 20 <= sum(shared_jobs.values()) <= 24
                assert max(shared_jobs.values()) <= 2

                jobs_before_unique = len(mac.jobs)
                unique = await asyncio.gather(
                    *(
                        rpc(requester_ids[index], _vin(20_000 + index), f"unique-{index:02d}")
                        for index in range(20)
                    )
                )
                assert Counter(response.status_code for response in unique) == Counter({200: 20})
                assert len(mac.jobs) - jobs_before_unique == 20

                # Refill the real admission bucket, then hold the only worker so
                # more callers than the global bound arrive while no slot drains.
                await asyncio.sleep(4.1)
                mac.hold_jobs = True
                limited_tasks = [
                    asyncio.create_task(
                        rpc(
                            requester_ids[index % len(requester_ids)],
                            _vin(30_000 + index),
                            f"limited-{index:03d}",
                        )
                    )
                    for index in range(300)
                ]
                await asyncio.wait_for(mac.job_started.wait(), timeout=10)
                for _ in range(200):
                    if app.state.registry.metrics["rejected"]:
                        break
                    await asyncio.sleep(0.01)
                mac.release_jobs.set()
                limited = await asyncio.gather(*limited_tasks)
                assert app.state.registry.metrics["rejected"] > 0
                limited_statuses = Counter(response.status_code for response in limited)
                assert limited_statuses[503] > 0
                assert set(limited_statuses) <= {200, 503}
                rejected_details = Counter(
                    response.json()["detail"] for response in limited if response.status_code == 503
                )
                assert rejected_details["broker_busy"] > 0

            registry = app.state.registry
            assert registry.pending == {}
            assert registry.loads == {}
            assert registry.request_loads == {}
            assert registry._active_requests == {}
            channel = registry.channels.get(device.id)
            assert channel is not None, {
                "reader_exit": mac.reader_exit,
                "heartbeat_exit": mac.heartbeat_exit,
                "reader_done": mac.reader.done() if mac.reader is not None else None,
                "heartbeat_done": (
                    mac.heartbeat_task.done() if mac.heartbeat_task is not None else None
                ),
            }
            assert channel.inflight == 0 and channel.queued == 0
        finally:
            mac.release_jobs.set()
            await mac.close()
            server.should_exit = True
            await asyncio.wait_for(server_task, timeout=10)

    asyncio.run(scenario())
