"""Synthetic wire-load contract for Inventory reads through one owner Mac.

The test exercises real signed HTTP, ASGI, authenticated WebSocket frames,
admission, coalescing, queueing and cleanup.  The owner-authority function is
scoped to a deterministic oracle here; its database/RBAC behavior has separate
focused tests in ``test_compute_bridge.py``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import time
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
from robopark_api.models import AccessStatus, AgentDevice, User
from robopark_api.security import hash_password
from robopark_api.services.emergency_bridge_auth import body_signature

_INSTANCE_ID = "11111111-1111-1111-1111-111111111111"
_SECRET = "inventory-single-mac-load-secret"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _signed_read(requester: int, resource: str, nonce: str) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "service": "inventory",
            "action": "rover_card",
            "resource": resource,
            "params": {},
            "requester_user_id": requester,
            "park_id": None,
            "deadline_ms": 15_000,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    timestamp = str(int(time.time()))
    return body, {
        "Content-Type": "application/json",
        "X-Robopark-Timestamp": timestamp,
        "X-Robopark-Nonce": nonce,
        "X-Robopark-Signature": body_signature(_SECRET, timestamp, nonce, body),
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
class InventoryMac:
    app: object
    device_id: str
    owner_user_id: int
    owner_login: str
    private_key: ec.EllipticCurvePrivateKey
    uri: str
    websocket: object | None = None
    generation: str = ""
    resources: list[str] = field(default_factory=list)
    max_server_pending: int = 0
    reader: asyncio.Task | None = None

    async def connect(self) -> None:
        self.websocket = await websockets.connect(self.uri, max_size=2 * 1024 * 1024)
        challenge = json.loads(await self.websocket.recv())
        signed = (
            "robopark-compute-v1\n"
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
        await self.websocket.send(
            json.dumps(
                {
                    "type": "heartbeat",
                    "generation": self.generation,
                    "capacity": 1,
                    "active_jobs": 0,
                    "cpu_percent": 20,
                    "memory_percent": 30,
                    "thermal_state": "normal",
                    "power": "battery",
                    "supports": ["corporate_read_v1", "corporate_inventory_read_v1"],
                    "corporate_read_capacity": 1,
                    "corporate_write_capacity": 0,
                    "corporate_active_jobs": 0,
                    "corporate_active_writes": 0,
                    "corporate_inventory_status": "ready",
                }
            )
        )
        assert json.loads(await self.websocket.recv())["type"] == "heartbeat_ack"
        self.reader = asyncio.create_task(self._read(), name="inventory-load-mac-reader")

    async def _read(self) -> None:
        assert self.websocket is not None
        try:
            async for raw in self.websocket:
                message = json.loads(raw)
                if message.get("type") != "compute_job":
                    continue
                envelope = json.loads(base64.b64decode(message["payload_base64"], validate=True))
                resource = envelope["resource"]
                self.resources.append(resource)
                registry = self.app.state.compute_registry
                self.max_server_pending = max(
                    self.max_server_pending,
                    sum(
                        lease.device_id == self.device_id and lease.generation == self.generation
                        for lease in registry.pending.values()
                    ),
                )
                await asyncio.sleep(0.015)
                await self.websocket.send(
                    json.dumps(
                        {
                            "type": "compute_result",
                            "generation": self.generation,
                            "job_id": message["job_id"],
                            "attempt_id": message["attempt_id"],
                            "payload_sha256": message["payload_sha256"],
                            "ok": True,
                            "result": {
                                "service": "inventory",
                                "action": "rover_card",
                                "resource": resource,
                                "owner_user_id": self.owner_user_id,
                                "identity_login": self.owner_login,
                                "status_code": 200,
                                "headers": {},
                                "payload": {"name": resource},
                            },
                        }
                    )
                )
        except websockets.ConnectionClosed:
            return

    async def close(self) -> None:
        if self.websocket is not None:
            await self.websocket.close()
        if self.reader is not None:
            with suppress(asyncio.CancelledError, websockets.ConnectionClosed):
                await asyncio.wait_for(self.reader, timeout=3)


@pytest.mark.load
def test_two_hundred_inventory_reads_coalesce_on_one_capacity_one_mac(
    db_engine, db_session, monkeypatch
):
    owner_login = "inventory.owner"
    owner = User(
        username="inventory-load-owner",
        password_hash=hash_password("synthetic"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
        tracker_login=owner_login,
    )
    db_session.add(owner)
    db_session.flush()
    private_key = ec.generate_private_key(ec.SECP256R1())
    device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=owner.id,
        label="One battery Inventory Mac",
        public_key_x963=private_key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        ),
        environment="test",
        instance_id=_INSTANCE_ID,
        approved_at=datetime.now(UTC),
        approved_by_user_id=owner.id,
    )
    db_session.add(device)
    db_session.commit()

    authority_checks = 0

    def authority_oracle(*_args, device_id=None, owner_user_id=None, **_kwargs):
        nonlocal authority_checks
        authority_checks += 1
        if device_id is not None and device_id != device.id:
            return None
        if owner_user_id is not None and owner_user_id != owner.id:
            return None
        return {owner.id: owner_login}

    monkeypatch.setattr("robopark_api.compute_bridge._corporate_owner_candidates", authority_oracle)
    monkeypatch.setattr("robopark_api.compute_bridge._requester_active", lambda *_args: True)
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key=_SECRET,
        emergency_bridge_enabled=True,
        mac_corporate_gateway_enabled=True,
        robopark_environment="test",
        robopark_instance_id=_INSTANCE_ID,
    )
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    port = _free_port()

    async def scenario() -> None:
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", access_log=False)
        )
        server_task = asyncio.create_task(server.serve(), name="inventory-load-uvicorn")
        base_url = f"http://127.0.0.1:{port}"
        mac = InventoryMac(
            app=app,
            device_id=device.id,
            owner_user_id=owner.id,
            owner_login=owner_login,
            private_key=private_key,
            uri=f"ws://127.0.0.1:{port}/emergency/bridge/compute/ws",
        )
        try:
            await _wait_for_health(base_url)
            await mac.connect()
            for _ in range(100):
                channel = app.state.compute_registry.channels.get(device.id)
                if channel is not None and channel.ready:
                    break
                await asyncio.sleep(0.01)
            assert app.state.compute_registry.channels[device.id].ready
            limits = httpx.Limits(max_connections=256, max_keepalive_connections=100)
            async with httpx.AsyncClient(base_url=base_url, timeout=25, limits=limits) as http:

                async def rpc(index: int) -> httpx.Response:
                    resource = f"a{1 + index % 20}"
                    body, headers = _signed_read(
                        requester=10_000 + index,
                        resource=resource,
                        nonce=f"inventory-shared-{index:03d}",
                    )
                    return await http.post(
                        "/internal/v1/corporate-read", content=body, headers=headers
                    )

                responses = await asyncio.gather(*(rpc(index) for index in range(200)))

            statuses = Counter(response.status_code for response in responses)
            assert statuses == Counter({200: 200}), Counter(
                (response.status_code, response.json().get("detail")) for response in responses
            ) | Counter(
                {
                    f"jobs={len(mac.resources)} authority={authority_checks} "
                    f"supports={sorted(app.state.compute_registry.channels[device.id].supports)} "
                    f"readcap={app.state.compute_registry.channels[device.id].corporate_read_capacity} "
                    f"status={app.state.compute_registry.channels[device.id].corporate_inventory_status}": 1
                }
            )
            assert {response.json()["payload"]["name"] for response in responses} == {
                f"a{index}" for index in range(1, 21)
            }
            assert Counter(mac.resources) == Counter({f"a{index}": 1 for index in range(1, 21)})
            assert mac.max_server_pending == 1
            assert authority_checks >= 420  # 200 prechecks + 200 recipients + 20 leader checks.

            registry = app.state.compute_registry
            assert registry.pending == {}
            assert registry.corporate_pending_payload_bytes == 0
            assert registry._read_reservations == {}
            assert registry._read_waiters == []
        finally:
            await mac.close()
            server.should_exit = True
            await asyncio.wait_for(server_task, timeout=10)

    asyncio.run(scenario())
