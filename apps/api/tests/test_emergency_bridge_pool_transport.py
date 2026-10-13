"""End-to-end transport evidence for the bounded Mac Emergency pool.

The existing broker tests exercise scheduler objects directly.  This test keeps
one real Uvicorn/ASGI instance open and connects 100 authenticated P-256
WebSocket clients, so it catches lifecycle and framing regressions at the
wire boundary without touching a corporate Emergency endpoint.
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
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
import websockets
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy.orm import sessionmaker

from robopark_api import emergency_bridge as bridge_module
from robopark_api.config import Settings
from robopark_api.emergency_bridge import create_broker_app
from robopark_api.models import AgentDevice
from robopark_api.services import emergency_scope
from robopark_api.services.emergency_bridge_auth import body_signature

_INSTANCE_ID = "11111111-1111-1111-1111-111111111111"
_SECRET = "pool-transport-test-secret"
_REPORT = (
    Path(__file__).resolve().parents[3]
    / "artifacts/rc44-offline-pool/pool-transport-verification.json"
)


def _vin(number: int) -> str:
    return f"YASADR{number:011d}"


def _raw_deflate(payload: dict) -> tuple[str, int, int]:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    compressor = zlib.compressobj(wbits=-15)
    encoded = compressor.compress(raw) + compressor.flush()
    return base64.b64encode(encoded).decode("ascii"), len(encoded), len(raw)


@dataclass
class SyntheticMac:
    device_id: str
    private_key: ec.EllipticCurvePrivateKey
    uri: str
    singleflight_vin: str
    corrupt_vin: str
    singleflight_started: asyncio.Event
    singleflight_release: asyncio.Event
    websocket: object | None = None
    generation: str = ""
    jobs: list[str] = field(default_factory=list)
    probes: int = 0
    reader: asyncio.Task | None = None

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
        ).encode("utf-8")
        signature = self.private_key.sign(signed, ec.ECDSA(hashes.SHA256()))
        await self.websocket.send(
            json.dumps(
                {
                    "type": "auth",
                    "device_id": self.device_id,
                    "signature": base64.b64encode(signature).decode("ascii"),
                }
            )
        )
        ready = json.loads(await self.websocket.recv())
        assert ready["type"] == "ready"
        self.generation = ready["generation"]
        await self.heartbeat(paused=False)
        self.reader = asyncio.create_task(self._read(), name=f"synthetic-mac-{self.device_id}")

    async def heartbeat(self, *, paused: bool) -> None:
        assert self.websocket is not None
        await self.websocket.send(
            json.dumps(
                {
                    "type": "heartbeat",
                    "generation": self.generation,
                    "ready": True,
                    "latency_ms": 10,
                    "capacity": 2,
                    "active_jobs": 0,
                    "load": 0.05,
                    "paused": paused,
                    "power": "ac",
                    "supports": ["raw_deflate", "managed_probes"],
                    "probe_available": True,
                }
            )
        )

    async def _read(self) -> None:
        assert self.websocket is not None
        try:
            async for raw in self.websocket:
                message = json.loads(raw)
                if message.get("type") == "probe":
                    self.probes += 1
                    await self.websocket.send(
                        json.dumps(
                            {
                                "type": "probe_result",
                                "generation": self.generation,
                                "probe_id": message["probe_id"],
                                "ready": True,
                                "latency_ms": 9,
                            }
                        )
                    )
                elif message.get("type") == "job":
                    await self._answer_job(message)
        except websockets.ConnectionClosed:
            return

    async def _answer_job(self, message: dict) -> None:
        assert self.websocket is not None
        vin = message["vin"]
        self.jobs.append(vin)
        if vin == self.singleflight_vin:
            self.singleflight_started.set()
            await self.singleflight_release.wait()
        if vin == self.corrupt_vin:
            # A syntactically valid bridge result with an invalid raw-DEFLATE
            # envelope must be ignored and then expire at the caller deadline.
            result = {
                "type": "result",
                "job_id": message["job_id"],
                "attempt_id": message["attempt_id"],
                "generation": self.generation,
                "ok": True,
                "encoding": "deflate-raw",
                "payload_base64": "not-base64!",
                "encoded_bytes": 11,
                "payload_bytes": 20,
            }
        else:
            payload = {"vin": vin, "timestamp": int(time.time() * 1000), "isOnline": True}
            encoded, encoded_bytes, payload_bytes = _raw_deflate(payload)
            result = {
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
        await self.websocket.send(json.dumps(result))

    async def close(self) -> None:
        if self.websocket is not None:
            await self.websocket.close()
        if self.reader is not None:
            await asyncio.wait_for(self.reader, timeout=3)


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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _signed_rpc(
    user_id: int, vin: str, nonce: str, *, deadline_ms: int = 20_000
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {"vin": vin, "requester_user_id": user_id, "attempt_budget": 1, "deadline_ms": deadline_ms},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    timestamp = str(int(time.time()))
    return body, {
        "X-Robopark-Timestamp": timestamp,
        "X-Robopark-Nonce": nonce,
        "X-Robopark-Signature": body_signature(_SECRET, timestamp, nonce, body),
        "Content-Type": "application/json",
    }


def test_one_hundred_real_websocket_agents_distribute_and_survive_lifecycle(
    db_engine, db_session, seed_admin, monkeypatch
):
    """Local-only full wire test: 100 P-256 agents, RPC, cache, pauses and drops."""
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_args: True)
    private_keys: list[ec.EllipticCurvePrivateKey] = []
    devices: list[AgentDevice] = []
    for index in range(100):
        private_key = ec.generate_private_key(ec.SECP256R1())
        device = AgentDevice(
            id=str(uuid4()),
            owner_user_id=seed_admin.id,
            label=f"Synthetic Mac {index:03d}",
            public_key_x963=private_key.public_key().public_bytes(
                Encoding.X962, PublicFormat.UncompressedPoint
            ),
            environment="test",
            instance_id=_INSTANCE_ID,
            approved_at=datetime.now(UTC),
            approved_by_user_id=seed_admin.id,
        )
        devices.append(device)
        private_keys.append(private_key)
    db_session.add_all(devices)
    db_session.commit()

    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key=_SECRET,
        emergency_bridge_enabled=True,
        emergency_bridge_upstream_reads_per_second=64,
        robopark_environment="test",
        robopark_instance_id=_INSTANCE_ID,
    )
    authority_trace: Counter[str] = Counter()
    authority_lock = threading.Lock()

    def trace_authority(name: str, fn):
        def wrapped(*args, **kwargs):
            started_at = time.monotonic()
            with authority_lock:
                authority_trace[f"{name}.started"] += 1
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                with authority_lock:
                    authority_trace[f"{name}.error.{type(exc).__name__}"] += 1
                raise
            with authority_lock:
                authority_trace[f"{name}.completed"] += 1
                authority_trace[f"{name}.slow_over_100ms"] += time.monotonic() - started_at > 0.1
            return result

        return wrapped

    for name in ("_candidate_device_ids", "_result_authority_valid", "_job_authority_valid"):
        monkeypatch.setattr(
            bridge_module, name, trace_authority(name, getattr(bridge_module, name))
        )
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    app.state.registry.max_active_per_requester = 128
    assert app.state.registry.upstream_reads_per_second == 64
    port = _free_port()
    report: dict[str, object] = {"test": "100-real-websocket-agents", "local_only": True}

    async def scenario() -> None:
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", access_log=False)
        )
        server_task = asyncio.create_task(server.serve(), name="pool-transport-uvicorn")
        base_url = f"http://127.0.0.1:{port}"
        websocket_url = f"ws://127.0.0.1:{port}/emergency/bridge/ws"
        started = time.monotonic()
        clients: list[SyntheticMac] = []
        singleflight_vin = _vin(50_001)
        corrupt_vin = _vin(50_002)
        singleflight_started = asyncio.Event()
        singleflight_release = asyncio.Event()
        try:
            await _wait_for_health(base_url)
            clients = [
                SyntheticMac(
                    device_id=device.id,
                    private_key=private_key,
                    uri=websocket_url,
                    singleflight_vin=singleflight_vin,
                    corrupt_vin=corrupt_vin,
                    singleflight_started=singleflight_started,
                    singleflight_release=singleflight_release,
                )
                for device, private_key in zip(devices, private_keys, strict=True)
            ]
            # Deliberately a simultaneous reconnect storm: the supported pool
            # size is 100 and authentication must wait within its deadline,
            # rather than rejecting after a small in-process worker limit.
            await asyncio.wait_for(
                asyncio.gather(*(client.connect() for client in clients)), timeout=35
            )
            assert len(app.state.registry.channels) == 100
            # Registration and the first heartbeat are separate wire events.
            # Wait for every server-side readiness update, rather than treating
            # an accepted socket as a ready source.
            for _ in range(500):
                if sum(channel.ready for channel in app.state.registry.channels.values()) == 100:
                    break
                await asyncio.sleep(0.02)
            assert sum(channel.ready for channel in app.state.registry.channels.values()) == 100

            async with httpx.AsyncClient(base_url=base_url, timeout=30) as http:

                async def rpc(vin: str, nonce: str, deadline_ms: int = 20_000) -> httpx.Response:
                    body, headers = _signed_rpc(seed_admin.id, vin, nonce, deadline_ms=deadline_ms)
                    return await http.post(
                        "/internal/v1/robot-payload", content=body, headers=headers
                    )

                # One unique request per agent demonstrates scheduler fairness through
                # real HTTP->ASGI->WebSocket->raw-DEFLATE->HTTP traffic.
                # A real 100-user fan-out after the 100-device reconnect storm.
                first = await asyncio.gather(
                    *(rpc(_vin(index + 1), f"bulk-{index:03d}") for index in range(100))
                )
                first_statuses = Counter(response.status_code for response in first)
                report.update(
                    {
                        "bulk_statuses": dict(first_statuses),
                        "authority_trace_at_bulk": dict(authority_trace),
                    }
                )
                assert first_statuses == Counter({200: 100}), [
                    (response.status_code, response.json())
                    for response in first
                    if response.status_code != 200
                ]
                distribution = Counter(
                    vin for client in clients for vin in client.jobs if vin <= _vin(100)
                )
                assert len(distribution) == 100
                assigned = Counter(
                    client.device_id
                    for client in clients
                    for vin in client.jobs
                    if vin <= _vin(100)
                )
                assert len(assigned) == 100
                assert set(assigned.values()) == {1}

                jobs_before_cache = sum(len(client.jobs) for client in clients)
                cached = await rpc(_vin(1), "cache-hit")
                assert cached.status_code == 200
                assert cached.json()["attempts_used"] == 0
                await asyncio.sleep(0.05)
                assert sum(len(client.jobs) for client in clients) == jobs_before_cache
                cache_job_delta = sum(len(client.jobs) for client in clients) - jobs_before_cache

                first_singleflight = asyncio.create_task(
                    rpc(singleflight_vin, "singleflight-leader")
                )
                await asyncio.wait_for(singleflight_started.wait(), timeout=5)
                second_singleflight = asyncio.create_task(
                    rpc(singleflight_vin, "singleflight-follower")
                )
                await asyncio.sleep(0.05)
                singleflight_release.set()
                singleflight_results = await asyncio.gather(first_singleflight, second_singleflight)
                assert [response.status_code for response in singleflight_results] == [200, 200]
                assert sum(client.jobs.count(singleflight_vin) for client in clients) == 1

                # These devices must never be selected while they are paused; closed
                # sessions must disappear from the live registry before more work.
                paused = clients[:10]
                dropped = clients[10:20]
                await asyncio.gather(*(client.heartbeat(paused=True) for client in paused))
                await asyncio.gather(*(client.close() for client in dropped))
                for _ in range(50):
                    if len(app.state.registry.channels) == 90:
                        break
                    await asyncio.sleep(0.02)
                assert len(app.state.registry.channels) == 90
                paused_before = {client.device_id: len(client.jobs) for client in paused}
                after_lifecycle = await asyncio.gather(
                    *(
                        rpc(_vin(1_000 + index), f"after-lifecycle-{index:02d}")
                        for index in range(20)
                    )
                )
                assert all(response.status_code == 200 for response in after_lifecycle)
                assert all(len(client.jobs) == paused_before[client.device_id] for client in paused)

                corrupt = await rpc(corrupt_vin, "corrupt-frame", deadline_ms=300)
                assert corrupt.status_code == 503
                assert corrupt.json()["detail"] == "upstream_failed"
                assert sum(client.jobs.count(corrupt_vin) for client in clients) == 1

                # Probe replies are live wire frames too. At least the global probe
                # allowance must have dispatched and completed while all 100 clients
                # were connected; this avoids a 50-second test just to sweep 100.
                assert sum(client.probes for client in clients) >= 2
                report.update(
                    {
                        "connected_agents": 100,
                        "connected_after_drop": len(app.state.registry.channels),
                        "bulk_requests": 100,
                        "bulk_concurrency_per_wave": 100,
                        "bulk_successes": sum(response.status_code == 200 for response in first),
                        "unique_agents_used_for_bulk": len(assigned),
                        "bulk_assignments_min": min(assigned.values()),
                        "bulk_assignments_max": max(assigned.values()),
                        "cache_job_delta": cache_job_delta,
                        "singleflight_wire_jobs": sum(
                            client.jobs.count(singleflight_vin) for client in clients
                        ),
                        "paused_agents": len(paused),
                        "dropped_agents": len(dropped),
                        "managed_probe_replies": sum(client.probes for client in clients),
                        "corrupt_result_status": corrupt.status_code,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    }
                )
        finally:
            for client in clients:
                with suppress(Exception):
                    await client.close()
            server.should_exit = True
            await asyncio.wait_for(server_task, timeout=10)

    try:
        asyncio.run(scenario())
    finally:
        # The artifact contains only aggregate local test evidence.
        _REPORT.parent.mkdir(parents=True, exist_ok=True)
        _REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
