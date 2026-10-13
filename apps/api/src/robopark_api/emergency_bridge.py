from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import re
import time
import zlib
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

import uvicorn
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from robopark_api.config import Settings, get_settings
from robopark_api.db import SessionLocal
from robopark_api.models import AccessStatus, AgentDevice, User
from robopark_api.services import audit, emergency_scope, rbac
from robopark_api.services.emergency_bridge_auth import verify_body_signature
from robopark_api.services.emergency_bridge_broker import BrokerRegistry, Channel

LOG = logging.getLogger(__name__)
MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_COMPRESSED_BYTES = 1_400_000
_CANONICAL_VIN = re.compile(r"YASADR[0-9]{11}\Z")
_PAYLOAD_MAX_DEPTH = 16
_PAYLOAD_MAX_NODES = 20_000
_PAYLOAD_MAX_COLLECTION = 2_000
_PAYLOAD_MAX_STRING = 128 * 1024
_PAYLOAD_MIN_TIMESTAMP_MS = 946_684_800_000


class RpcRequest(BaseModel):
    vin: str = Field(min_length=17, max_length=32)
    requester_user_id: int = Field(gt=0)
    attempt_budget: int = Field(default=1, ge=1, le=2)
    deadline_ms: int = Field(default=20_000, ge=1, le=20_000)


class PoolStatusRequest(BaseModel):
    requester_user_id: int = Field(gt=0)


def _validated_robot_payload_size(
    payload: object, expected_vin: str, *, now_ms: int | None = None
) -> int | None:
    if not isinstance(payload, dict):
        return None
    vin = payload.get("vin")
    timestamp = payload.get("timestamp")
    if (
        not isinstance(vin, str)
        or _CANONICAL_VIN.fullmatch(vin) is None
        or vin != expected_vin
        or type(timestamp) is not int
    ):
        return None
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if timestamp < _PAYLOAD_MIN_TIMESTAMP_MS or timestamp > now_ms + 5 * 60 * 1000:
        return None
    if "isOnline" in payload and type(payload["isOnline"]) is not bool:
        return None

    nodes = 0
    stack: list[tuple[object, int]] = [(payload, 0)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > _PAYLOAD_MAX_NODES or depth > _PAYLOAD_MAX_DEPTH:
            return None
        if isinstance(value, dict):
            if len(value) > _PAYLOAD_MAX_COLLECTION:
                return None
            for key, child in value.items():
                if not isinstance(key, str) or len(key) > 256:
                    return None
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            if len(value) > _PAYLOAD_MAX_COLLECTION:
                return None
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str):
            if len(value) > _PAYLOAD_MAX_STRING:
                return None
        elif isinstance(value, float):
            if not math.isfinite(value):
                return None
        elif value is not None and type(value) not in {bool, int}:
            return None
    try:
        size = len(
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        return None
    return size if size <= MAX_FRAME_BYTES else None


def _decode_result_payload(
    message: dict[str, Any], expected_vin: str, *, now_ms: int | None = None
) -> tuple[dict, int, int] | None:
    """Decode a legacy/plain or raw-DEFLATE result under strict byte bounds."""
    if message.get("encoding") is None:
        payload = message.get("payload")
        decoded_bytes = _validated_robot_payload_size(payload, expected_vin, now_ms=now_ms)
        if decoded_bytes is None or not isinstance(payload, dict):
            return None
        return payload, decoded_bytes, decoded_bytes
    if message.get("encoding") != "deflate-raw":
        return None
    encoded_size = message.get("encoded_bytes")
    decoded_size = message.get("payload_bytes")
    encoded_base64 = message.get("payload_base64")
    if (
        type(encoded_size) is not int
        or type(decoded_size) is not int
        or not isinstance(encoded_base64, str)
        or encoded_size < 1
        or encoded_size > MAX_COMPRESSED_BYTES
        or decoded_size < 1
        or decoded_size > MAX_FRAME_BYTES
    ):
        return None
    try:
        encoded = base64.b64decode(encoded_base64, validate=True)
    except (ValueError, TypeError):
        return None
    if len(encoded) != encoded_size:
        return None
    try:
        inflater = zlib.decompressobj(wbits=-15)
        raw = inflater.decompress(encoded, MAX_FRAME_BYTES + 1)
        if (
            len(raw) > MAX_FRAME_BYTES
            or inflater.unconsumed_tail
            or not inflater.eof
            or inflater.unused_data
        ):
            return None
        raw += inflater.flush(MAX_FRAME_BYTES + 1 - len(raw))
    except zlib.error:
        return None
    if len(raw) != decoded_size or len(raw) > MAX_FRAME_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if _validated_robot_payload_size(payload, expected_vin, now_ms=now_ms) is None:
        return None
    return payload, encoded_size, decoded_size


def _device_valid(
    db: Session, device_id: str, settings: Settings
) -> tuple[AgentDevice, User] | None:
    device = db.get(AgentDevice, device_id)
    if (
        device is None
        or device.revoked_at is not None
        or device.environment != settings.robopark_environment
        or device.instance_id != str(settings.robopark_instance_id)
    ):
        return None
    owner = db.get(User, device.owner_user_id)
    if (
        owner is None
        or not owner.is_active
        or owner.access_status != AccessStatus.approved.value
        or not rbac.has_permission(db, owner, rbac.PERMISSION_NAV_EMERGENCY)
    ):
        return None
    return device, owner


def _candidate_device_ids(
    session_factory: sessionmaker,
    settings: Settings,
    requester_user_id: int,
    vin: str,
    connected_device_ids: set[str],
) -> set[str]:
    if not connected_device_ids:
        return set()
    with session_factory() as db:
        requester = db.get(User, requester_user_id)
        if requester is None or not requester.is_active:
            return set()
        try:
            requester_allowed = (
                requester.access_status == AccessStatus.approved.value
                and rbac.has_permission(db, requester, rbac.PERMISSION_NAV_EMERGENCY)
                and emergency_scope.vin_allowed_for_user(db, requester, vin)
            )
        except Exception:
            return set()
        if not requester_allowed:
            return set()
        eligible: set[str] = set()
        devices = db.scalars(
            select(AgentDevice).where(
                AgentDevice.id.in_(connected_device_ids),
                AgentDevice.revoked_at.is_(None),
                AgentDevice.environment == settings.robopark_environment,
                AgentDevice.instance_id == str(settings.robopark_instance_id),
            )
        ).all()
        # Several Macs can belong to one account. Resolve that account's role
        # and VIN scope once per request, never once per device and never across
        # requests (revocation must still take effect immediately).
        owner_authority: dict[int, bool] = {requester.id: True}
        for device in devices:
            owner_id = device.owner_user_id
            if owner_id not in owner_authority:
                owner = db.get(User, owner_id)
                try:
                    owner_authority[owner_id] = bool(
                        owner is not None
                        and owner.is_active
                        and owner.access_status == AccessStatus.approved.value
                        and rbac.has_permission(db, owner, rbac.PERMISSION_NAV_EMERGENCY)
                        and emergency_scope.vin_allowed_for_user(db, owner, vin)
                    )
                except Exception:
                    owner_authority[owner_id] = False
            if owner_authority[owner_id]:
                eligible.add(device.id)
        return eligible


def _result_authority_valid(
    session_factory: sessionmaker, settings: Settings, device_id: str, vin: str
) -> bool:
    with session_factory() as db:
        pair = _device_valid(db, device_id, settings)
        if pair is None:
            return False
        _, owner = pair
        try:
            return emergency_scope.vin_allowed_for_user(db, owner, vin)
        except Exception:
            return False


def _job_authority_valid(
    session_factory: sessionmaker,
    settings: Settings,
    device_id: str,
    requester_user_id: int,
    vin: str,
) -> bool:
    with session_factory() as db:
        requester = db.get(User, requester_user_id)
        pair = _device_valid(db, device_id, settings)
        if requester is None or pair is None or not requester.is_active:
            return False
        _, owner = pair
        try:
            return (
                requester.access_status == AccessStatus.approved.value
                and rbac.has_permission(db, requester, rbac.PERMISSION_NAV_EMERGENCY)
                and emergency_scope.vin_allowed_for_user(db, requester, vin)
                and emergency_scope.vin_allowed_for_user(db, owner, vin)
            )
        except Exception:
            return False


def _device_still_valid(session_factory: sessionmaker, settings: Settings, device_id: str) -> bool:
    with session_factory() as db:
        return _device_valid(db, device_id, settings) is not None


def _pool_devices(
    session_factory: sessionmaker, settings: Settings, requester_user_id: int
) -> tuple[str, int | None, list[dict]] | None:
    with session_factory() as db:
        requester = db.get(User, requester_user_id)
        if (
            requester is None
            or not requester.is_active
            or requester.access_status != AccessStatus.approved.value
            or not rbac.has_permission(db, requester, rbac.PERMISSION_NAV_EMERGENCY)
        ):
            return None
        include_all = rbac.is_admin_or_royal(requester)
        query = select(AgentDevice).where(
            AgentDevice.revoked_at.is_(None),
            AgentDevice.environment == settings.robopark_environment,
            AgentDevice.instance_id == str(settings.robopark_instance_id),
        )
        if not include_all:
            query = query.where(AgentDevice.owner_user_id == requester.id)
        devices = []
        for device in db.scalars(query.order_by(AgentDevice.created_at, AgentDevice.id)).all():
            if _device_valid(db, device.id, settings) is None:
                continue
            devices.append(
                {
                    "id": device.id,
                    "label": device.label,
                    "owner_user_id": device.owner_user_id,
                    "last_seen_at": (
                        device.last_seen_at.isoformat() if device.last_seen_at is not None else None
                    ),
                }
            )
        return "all" if include_all else "own", None if include_all else requester.id, devices


def _authenticate_device(
    session_factory: sessionmaker,
    settings: Settings,
    device_id: str,
    signature_base64: str,
    signed: bytes,
) -> tuple[str, int]:
    with session_factory() as db:
        pair = _device_valid(db, device_id, settings)
        if pair is None:
            raise ValueError("invalid_device")
        device, owner = pair
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), device.public_key_x963
        )
        signature = base64.b64decode(signature_base64, validate=True)
        public_key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        now = datetime.now(UTC)
        auto_trusted = device.approved_at is None
        if auto_trusted:
            device.approved_at = now
            device.approved_by_user_id = owner.id
        device.last_seen_at = now
        db.commit()
        if auto_trusted:
            audit.record(
                db,
                action=audit.ACTION_AGENT_DEVICE_AUTO_TRUSTED,
                actor=owner,
                target_type="agent_device",
                target_id=device.id,
                detail=json.dumps(
                    {"environment": device.environment, "instance_id": device.instance_id},
                    sort_keys=True,
                ),
            )
        return device.id, owner.id


async def _receive_json(websocket: WebSocket) -> dict[str, Any]:
    raw = await websocket.receive_text()
    if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ValueError("frame_too_large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("object_required")
    return value


def create_broker_app(session_factory: sessionmaker, settings: Settings) -> FastAPI:
    registry = BrokerRegistry(
        max_connections=100,
        upstream_reads_per_second=settings.emergency_bridge_upstream_reads_per_second,
    )

    async def probe_scheduler() -> None:
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            for channel in registry.due_probe_channels(now=now):
                if not registry.reserve_probe(channel, now=now):
                    continue
                probe_id = channel.probe_id or ""
                try:
                    await channel.send_json(
                        {
                            "type": "probe",
                            "probe_id": probe_id,
                            "generation": channel.generation,
                            "operation": "emergency_readiness",
                            "timeout_ms": 10_000,
                        }
                    )
                except (RuntimeError, WebSocketDisconnect):
                    registry.finish_probe(
                        channel,
                        probe_id=probe_id,
                        ready=False,
                        latency_ms=None,
                    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(probe_scheduler())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="Robopark Emergency Bridge",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    used_nonces: dict[str, float] = {}
    authority_slots = asyncio.Semaphore(16)
    authority_waiters = 0
    app.state.registry = registry

    def authenticate_internal(request: Request, body: bytes) -> None:
        timestamp = request.headers.get("X-Robopark-Timestamp", "")
        nonce = request.headers.get("X-Robopark-Nonce", "")
        signature = request.headers.get("X-Robopark-Signature", "")
        try:
            issued_at = int(timestamp)
        except ValueError as exc:
            raise HTTPException(401, "invalid_signature") from exc
        now = time.time()
        for value, expires in list(used_nonces.items()):
            if expires <= now:
                used_nonces.pop(value, None)
        if (
            abs(now - issued_at) > 30
            or not nonce
            or nonce in used_nonces
            or not verify_body_signature(settings.secret_key, timestamp, nonce, body, signature)
        ):
            raise HTTPException(401, "invalid_signature")
        used_nonces[nonce] = now + 60

    async def bounded_authority_call(deadline: float, fn, *args):
        nonlocal authority_waiters
        if authority_waiters >= 384 or deadline <= time.monotonic():
            raise TimeoutError("authority_capacity")
        # A reconnect burst must wait for the bounded DB worker pool rather
        # than reject every device after the first sixteen. Waiting counts
        # against the caller's original deadline and cannot grow without bound.
        authority_waiters += 1
        try:
            await asyncio.wait_for(
                authority_slots.acquire(), timeout=max(0.001, deadline - time.monotonic())
            )
        finally:
            authority_waiters -= 1
        task = asyncio.create_task(asyncio.to_thread(fn, *args))

        def release_when_finished(completed):
            authority_slots.release()
            if not completed.cancelled():
                completed.exception()

        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), timeout=max(0.001, deadline - time.monotonic())
            )
        except BaseException:
            # Cancelling an await does not stop a running DB thread. Retain
            # its slot until completion on both timeout and caller cancellation.
            task.add_done_callback(release_when_finished)
            raise
        authority_slots.release()
        return result

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "enabled": settings.emergency_bridge_enabled}

    @app.websocket("/emergency/bridge/ws")
    async def bridge_ws(websocket: WebSocket) -> None:
        if not settings.emergency_bridge_enabled:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        nonce = (
            base64.urlsafe_b64encode(__import__("secrets").token_bytes(32)).rstrip(b"=").decode()
        )
        await websocket.send_json(
            {
                "type": "challenge",
                "nonce": nonce,
                "environment": settings.robopark_environment,
                "instance_id": str(settings.robopark_instance_id),
                "protocol": 1,
            }
        )
        channel: Channel | None = None
        try:
            auth = await asyncio.wait_for(_receive_json(websocket), timeout=10)
            if auth.get("type") != "auth" or not isinstance(auth.get("device_id"), str):
                raise ValueError("invalid_auth")
            signed = (
                "robopark-bridge-v1\n"
                + str(settings.robopark_instance_id)
                + "\n"
                + settings.robopark_environment
                + "\n"
                + nonce
            ).encode("utf-8")
            device_id, owner_user_id = await bounded_authority_call(
                time.monotonic() + 10,
                _authenticate_device,
                session_factory,
                settings,
                auth["device_id"],
                str(auth.get("signature", "")),
                signed,
            )
            channel = Channel(device_id=device_id, owner_user_id=owner_user_id, websocket=websocket)
            old = registry.replace(channel)
            if old is not None:
                await old.websocket.close(code=1012)
            await websocket.send_json(
                {
                    "type": "ready",
                    "generation": channel.generation,
                    "heartbeat_interval_ms": 10_000,
                    "readiness_timeout_ms": 30_000,
                    "max_inflight": 2,
                    "supports": ["raw_deflate", "managed_probes"],
                    "probe_interval_ms": 60_000,
                    "probe_timeout_ms": 10_000,
                }
            )
            while True:
                message = await _receive_json(websocket)
                if not await bounded_authority_call(
                    time.monotonic() + 5,
                    _device_still_valid,
                    session_factory,
                    settings,
                    channel.device_id,
                ):
                    await websocket.close(code=1008)
                    return
                if message.get("generation") != channel.generation:
                    continue
                if message.get("type") == "heartbeat":
                    channel.ready = message.get("ready") is True
                    latency = message.get("latency_ms")
                    if isinstance(latency, (int, float)) and 0 <= latency <= 120_000:
                        channel.latency_ms = float(latency)
                    capacity = message.get("capacity")
                    if type(capacity) is int and 1 <= capacity <= 8:
                        channel.capacity = capacity
                    active_jobs = message.get("active_jobs")
                    if type(active_jobs) is int and 0 <= active_jobs <= channel.capacity:
                        channel.reported_active_jobs = active_jobs
                    load = message.get("load")
                    if (
                        isinstance(load, (int, float))
                        and not isinstance(load, bool)
                        and math.isfinite(load)
                        and 0 <= load <= 1
                    ):
                        channel.reported_load = float(load)
                    if type(message.get("paused")) is bool:
                        channel.paused = message["paused"]
                    if message.get("power") in {"ac", "battery", "low_power", "unknown"}:
                        channel.power = message["power"]
                    supports = message.get("supports")
                    if isinstance(supports, list) and len(supports) <= 8:
                        channel.supports = {
                            item for item in supports if item in {"raw_deflate", "managed_probes"}
                        }
                    if type(message.get("probe_available")) is bool:
                        channel.probe_available = message["probe_available"]
                    channel.last_heartbeat = time.monotonic()
                    if registry.reserve_probe(channel):
                        await channel.send_json(
                            {
                                "type": "probe",
                                "probe_id": channel.probe_id,
                                "generation": channel.generation,
                                "operation": "emergency_readiness",
                                "timeout_ms": 10_000,
                            }
                        )
                elif message.get("type") == "probe_result":
                    latency = message.get("latency_ms")
                    registry.finish_probe(
                        channel,
                        probe_id=str(message.get("probe_id", "")),
                        ready=message.get("ready") is True,
                        latency_ms=(
                            float(latency)
                            if isinstance(latency, (int, float))
                            and not isinstance(latency, bool)
                            and math.isfinite(latency)
                            else None
                        ),
                    )
                elif message.get("type") == "result":
                    pending = registry.pending_job(
                        device_id=channel.device_id,
                        generation=channel.generation,
                        job_id=str(message.get("job_id", "")),
                        attempt_id=str(message.get("attempt_id", "")),
                    )
                    if pending is None:
                        continue
                    ok = message.get("ok") is True
                    decoded = _decode_result_payload(message, pending.vin) if ok else None
                    payload_size = decoded[2] if decoded is not None else 0
                    encoded_size = decoded[1] if decoded is not None else 0
                    valid = ok and decoded is not None
                    valid = valid or (
                        not ok and message.get("error") in {"auth", "network", "invalid"}
                    )
                    if not valid:
                        LOG.warning(
                            "Emergency bridge rejected malformed result",
                            extra={
                                "device_id": channel.device_id,
                                "vin": pending.vin,
                                "generation": channel.generation,
                                "job_id": pending.job_id,
                                "attempt_id": pending.attempt_id,
                                "outcome": "invalid_payload",
                            },
                        )
                        continue
                    if decoded is not None:
                        message["payload"] = decoded[0]
                        message.pop("payload_base64", None)
                        message.pop("payload_bytes", None)
                        message.pop("encoded_bytes", None)
                        message.pop("encoding", None)
                    if pending.requester_user_id is not None and await bounded_authority_call(
                        pending.deadline_monotonic or time.monotonic() + 20,
                        _job_authority_valid,
                        session_factory,
                        settings,
                        channel.device_id,
                        pending.requester_user_id,
                        pending.vin,
                    ):
                        LOG.info(
                            "Emergency bridge delivered result",
                            extra={
                                "device_id": channel.device_id,
                                "requester_user_id": pending.requester_user_id,
                                "vin": pending.vin,
                                "generation": channel.generation,
                                "job_id": pending.job_id,
                                "attempt_id": pending.attempt_id,
                                "payload_size": payload_size,
                                "latency_ms": channel.latency_ms,
                                "outcome": "ok" if ok else str(message.get("error")),
                            },
                        )
                        registry.resolve_result(
                            device_id=channel.device_id,
                            generation=channel.generation,
                            job_id=pending.job_id,
                            attempt_id=pending.attempt_id,
                            result=message,
                        )
                        if ok:
                            registry.record_success(
                                channel,
                                encoded_bytes=encoded_size,
                                decoded_bytes=payload_size,
                            )
                        else:
                            registry.record_failure(channel)
        except WebSocketDisconnect:
            pass
        except (TimeoutError, ValueError, InvalidSignature, OverflowError):
            await websocket.close(code=1008)
        finally:
            if channel is not None:
                registry.remove(channel)

    async def dispatch_attempts(
        payload: RpcRequest, candidates: set[str], deadline: float
    ) -> tuple[dict | None, int, str, str | None]:
        async def final_authority(channel: Channel) -> bool:
            if time.monotonic() >= deadline:
                return False
            try:
                return await bounded_authority_call(
                    deadline,
                    _job_authority_valid,
                    session_factory,
                    settings,
                    channel.device_id,
                    payload.requester_user_id,
                    payload.vin,
                )
            except TimeoutError:
                return False

        excluded: set[str] = set()
        attempts = 0
        source_checks = 0
        max_source_checks = min(len(candidates), max(2, payload.attempt_budget * 2))
        while attempts < payload.attempt_budget and source_checks < max_source_checks:
            channel = registry.choose(candidates, excluded=excluded)
            if channel is None:
                return (
                    None,
                    attempts,
                    "all_agents_busy" if attempts == 0 else "upstream_failed",
                    None,
                )
            source_checks += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Reserve synchronously with selection. Otherwise every concurrent
            # caller picks the same idle Mac before awaiting its DB check.
            if not registry.reserve_queue_slot(channel):
                excluded.add(channel.device_id)
                continue
            try:
                try:
                    source_allowed = await bounded_authority_call(
                        deadline,
                        _result_authority_valid,
                        session_factory,
                        settings,
                        channel.device_id,
                        payload.vin,
                    )
                except TimeoutError:
                    break
                if not source_allowed:
                    excluded.add(channel.device_id)
                    continue
                if not await registry.wait_for_upstream(channel, deadline=deadline):
                    return None, attempts, "upstream_busy", None
                if not registry.channel_available(channel):
                    excluded.add(channel.device_id)
                    continue
            finally:
                registry.release_queue_slot(channel)
            load_future, is_load_leader = registry.begin_load(channel, payload.vin)
            if not is_load_leader:
                try:
                    remaining = max(0.001, deadline - time.monotonic())
                    shared = await asyncio.wait_for(asyncio.shield(load_future), timeout=remaining)
                    if isinstance(shared, dict):
                        if await final_authority(channel):
                            return shared, attempts, "", channel.device_id
                        excluded.add(channel.device_id)
                        continue
                except (TimeoutError, ConnectionError):
                    excluded.add(channel.device_id)
                    continue
            attempts += 1
            excluded.add(channel.device_id)
            try:
                job = registry.new_pending(
                    channel,
                    payload.vin,
                    requester_user_id=payload.requester_user_id,
                    deadline_monotonic=deadline,
                )
            except OverflowError:
                registry.finish_load(channel, payload.vin, error=ConnectionError("pending_limit"))
                return None, attempts - 1, "all_agents_busy", None
            load_error: BaseException | None = ConnectionError("bridge_attempt_failed")
            try:
                remaining_attempts = payload.attempt_budget - attempts + 1
                attempt_seconds = min(
                    10.0,
                    max(0.001, deadline - time.monotonic()) / remaining_attempts,
                )
                remaining_ms = max(1, int(attempt_seconds * 1000))
                await channel.send_json(
                    {
                        "type": "job",
                        "job_id": job.job_id,
                        "attempt_id": job.attempt_id,
                        "generation": channel.generation,
                        "operation": "robot_payload",
                        "vin": payload.vin,
                        "timeout_ms": remaining_ms,
                    }
                )
                result = await asyncio.wait_for(job.future, timeout=remaining_ms / 1000)
                if result.get("ok") is True and isinstance(result.get("payload"), dict):
                    registry.shared_cache_put(
                        payload.vin,
                        result["payload"],
                        source_device_id=channel.device_id,
                    )
                    registry.finish_load(channel, payload.vin, payload=result["payload"])
                    load_error = None
                    channel.failure_penalty = max(0, channel.failure_penalty - 0.25)
                    return result["payload"], attempts, "", channel.device_id
                if result.get("error") == "auth":
                    channel.ready = False
                channel.failure_penalty = min(10, channel.failure_penalty + 1)
            except (TimeoutError, ConnectionError, WebSocketDisconnect):
                channel.failure_penalty = min(10, channel.failure_penalty + 1)
                registry.record_failure(channel)
            finally:
                registry.finish(channel, job)
                if load_error is not None:
                    registry.finish_load(channel, payload.vin, error=load_error)
            if time.monotonic() >= deadline:
                break
        return None, attempts, "upstream_failed", None

    async def dispatch(payload: RpcRequest) -> tuple[dict | None, int, str, str | None]:
        deadline = time.monotonic() + payload.deadline_ms / 1000
        shared, is_leader = registry.begin_request(payload.vin)
        if not is_leader:
            try:
                result, device_id = await asyncio.wait_for(
                    asyncio.shield(shared), timeout=max(0.001, deadline - time.monotonic())
                )
                still_allowed = await bounded_authority_call(
                    deadline,
                    _job_authority_valid,
                    session_factory,
                    settings,
                    device_id,
                    payload.requester_user_id,
                    payload.vin,
                )
                if still_allowed:
                    return result, 0, "", device_id
                return None, 0, "no_eligible_agent", None
            except (TimeoutError, ConnectionError):
                return None, 0, "upstream_failed", None

        outcome: tuple[dict | None, int, str, str | None]
        try:
            cached = registry.shared_cache_get(payload.vin)
            if cached is not None:
                cached_payload, source_device_id = cached
                try:
                    still_allowed = await bounded_authority_call(
                        deadline,
                        _job_authority_valid,
                        session_factory,
                        settings,
                        source_device_id,
                        payload.requester_user_id,
                        payload.vin,
                    )
                except TimeoutError:
                    still_allowed = False
                if still_allowed:
                    outcome = (cached_payload, 0, "", source_device_id)
                else:
                    registry.shared_cache_remove(payload.vin)
                    cached = None
            if cached is None:
                try:
                    candidates = await bounded_authority_call(
                        deadline,
                        _candidate_device_ids,
                        session_factory,
                        settings,
                        payload.requester_user_id,
                        payload.vin,
                        set(registry.channels),
                    )
                except TimeoutError:
                    outcome = (None, 0, "upstream_failed", None)
                else:
                    outcome = (
                        await dispatch_attempts(payload, candidates, deadline)
                        if candidates
                        else (None, 0, "no_eligible_agent", None)
                    )
        except BaseException as exc:
            registry.finish_request(payload.vin, error=ConnectionError(str(exc)))
            raise
        result, _, _, device_id = outcome
        if result is not None and device_id is not None:
            registry.finish_request(payload.vin, result=(result, device_id))
        else:
            registry.finish_request(
                payload.vin,
                error=ConnectionError(outcome[2]),
            )
        return outcome

    @app.post("/internal/v1/robot-payload")
    async def robot_payload(request: Request) -> dict:
        if not settings.emergency_bridge_enabled:
            return JSONResponse(
                status_code=503, content={"detail": "bridge_disabled", "attempts_used": 0}
            )
        body = await request.body()
        authenticate_internal(request, body)
        try:
            payload = RpcRequest.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_input=False)) from exc
        if not registry.admit_request(payload.requester_user_id):
            return JSONResponse(
                status_code=503, content={"detail": "broker_busy", "attempts_used": 0}
            )
        try:
            result, attempts, detail, device_id = await dispatch(payload)
        finally:
            registry.release_request(payload.requester_user_id)
        if result is None:
            return JSONResponse(
                status_code=503, content={"detail": detail, "attempts_used": attempts}
            )
        return {
            "source": "bridge",
            "payload": result,
            "device_id": device_id,
            "attempts_used": attempts,
        }

    @app.post("/internal/v1/pool-status")
    async def pool_status(request: Request) -> dict:
        body = await request.body()
        authenticate_internal(request, body)
        try:
            payload = PoolStatusRequest.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_input=False)) from exc
        projection = await bounded_authority_call(
            time.monotonic() + 2,
            _pool_devices,
            session_factory,
            settings,
            payload.requester_user_id,
        )
        if projection is None:
            raise HTTPException(403, "forbidden")
        scope, owner_user_id, devices = projection
        live_by_id = {
            item["id"]: item for item in registry.status(owner_user_id=owner_user_id)["devices"]
        }
        result_devices = []
        for metadata in devices:
            live = live_by_id.get(metadata["id"])
            item = {
                **metadata,
                "status": "offline",
                "connected": False,
                "ready": False,
                "capacity": 0,
                "inflight": 0,
                "queued": 0,
                "load": 0.0,
                "latency_ms": None,
                "power": "unknown",
                "requests": 0,
                "completed": 0,
                "failed": 0,
                "encoded_bytes": 0,
                "decoded_bytes": 0,
                "probes_sent": 0,
                "probes_failed": 0,
                "upstream_dispatches": 0,
                "upstream_backpressure": 0,
            }
            if live is not None:
                item.update(live)
                item["label"] = metadata["label"]
                item["owner_user_id"] = metadata["owner_user_id"]
                item["last_seen_at"] = metadata["last_seen_at"]
            result_devices.append(item)
        summary_keys = (
            "capacity",
            "inflight",
            "queued",
            "requests",
            "completed",
            "failed",
            "encoded_bytes",
            "decoded_bytes",
            "probes_sent",
            "probes_failed",
            "upstream_dispatches",
            "upstream_backpressure",
        )
        summary = {key: sum(int(item[key]) for item in result_devices) for key in summary_keys}
        summary.update(
            {
                "connected": sum(bool(item["connected"]) for item in result_devices),
                "ready": sum(bool(item["ready"]) for item in result_devices),
                "busy": sum(item["status"] == "busy" for item in result_devices),
                "paused": sum(item["status"] == "paused" for item in result_devices),
            }
        )
        return {
            "enabled": True,
            "scope": scope,
            "summary": summary,
            "devices": result_devices,
        }

    return app


app = create_broker_app(SessionLocal, get_settings())


def main() -> None:
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8091,
        workers=1,
        ws_max_size=MAX_FRAME_BYTES,
        ws_max_queue=1,
    )


if __name__ == "__main__":
    main()
