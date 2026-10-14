from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import secrets
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from robopark_api.config import Settings
from robopark_api.models import AccessStatus, AgentDevice, User
from robopark_api.services import rbac
from robopark_api.services.compute_broker import (
    OPERATIONS,
    ComputeChannel,
    ComputeRegistry,
    validate_result,
)
from robopark_api.services.emergency_bridge_auth import verify_body_signature
from robopark_api.services.image_bounds import image_dimensions

MAX_PAYLOAD_BYTES = 6 * 1024 * 1024
MAX_FRAME_BYTES = 12 * 1024 * 1024  # 8 MiB decoded output plus base64/JSON envelope
DIRECT_RECEIPT_TTL_SECONDS = 120.0
DIRECT_RECEIPT_LIMIT = 256


class ComputeRequest(BaseModel):
    operation: str
    payload_base64: str = Field(max_length=8 * 1024 * 1024)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requester_user_id: int = Field(gt=0)
    deadline_ms: int = Field(default=20_000, ge=1, le=30_000)

    @field_validator("operation")
    @classmethod
    def supported_operation(cls, value: str) -> str:
        if value not in OPERATIONS:
            raise ValueError("unsupported_operation")
        return value


class ComputeStatusRequest(BaseModel):
    requester_user_id: int = Field(gt=0)


class DirectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(gt=0, le=6 * 1024 * 1024)
    offer: dict
    requester_user_id: int = Field(gt=0)
    deadline_ms: int = Field(default=20_000, ge=1, le=30_000)

    @field_validator("operation")
    @classmethod
    def image_only(cls, value: str) -> str:
        if value != "image_resize_v1":
            raise ValueError("direct_image_only")
        return value

    @field_validator("offer")
    @classmethod
    def valid_offer(cls, value: dict) -> dict:
        if (
            set(value) != {"type", "sdp"}
            or value.get("type") != "offer"
            or not isinstance(value.get("sdp"), str)
            or not 1 <= len(value["sdp"].encode()) <= 64 * 1024
        ):
            raise ValueError("invalid_offer")
        return value


class DirectCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requester_user_id: int = Field(gt=0)
    job_id: str


class DirectFinishRequest(DirectCancelRequest):
    outcome: Literal["completed", "fallback", "cancelled"]


@dataclass
class DirectReceipt:
    job_id: str
    attempt_id: str
    device_id: str
    generation: str
    requester_user_id: int
    payload_sha256: str
    expires_at: float
    browser_outcome: str | None = None
    native_done: bool = False
    native_ok: bool | None = None
    completed_counted: bool = False
    fallback_counted: bool = False
    cancelled_counted: bool = False
    failed_counted: bool = False


async def _receive_json(websocket: WebSocket) -> dict[str, Any]:
    raw = await websocket.receive_text()
    if len(raw.encode()) > MAX_FRAME_BYTES:
        raise ValueError("frame_too_large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("object_required")
    return value


async def _bounded_request_body(request: Request, limit: int, detail: str) -> bytes:
    try:
        content_length = int(request.headers.get("content-length", "0"))
    except ValueError:
        content_length = limit + 1
    if content_length > limit:
        raise HTTPException(413, detail)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, detail)
        chunks.append(chunk)
    return b"".join(chunks)


def _authenticate_device(
    session_factory: sessionmaker,
    settings: Settings,
    device_id: str,
    signature: str,
    signed: bytes,
) -> tuple[str, int]:
    try:
        decoded_signature = base64.b64decode(signature, validate=True)
    except (TypeError, ValueError) as exc:
        raise InvalidSignature from exc
    with session_factory() as db:
        device = db.get(AgentDevice, device_id)
        if (
            device is None
            or device.revoked_at is not None
            or device.approved_at is None
            or device.environment != settings.robopark_environment
            or device.instance_id != str(settings.robopark_instance_id)
        ):
            raise InvalidSignature
        owner = db.get(User, device.owner_user_id)
        if (
            owner is None
            or not owner.is_active
            or owner.access_status != AccessStatus.approved.value
        ):
            raise InvalidSignature
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), device.public_key_x963
        )
        public_key.verify(decoded_signature, signed, ec.ECDSA(hashes.SHA256()))
        device.last_seen_at = datetime.now(UTC)
        db.commit()
        return device.id, owner.id


def _device_still_valid(session_factory: sessionmaker, settings: Settings, device_id: str) -> bool:
    try:
        _device_metadata(session_factory, settings, device_id)
    except LookupError:
        return False
    return True


def _device_metadata(session_factory: sessionmaker, settings: Settings, device_id: str) -> dict:
    with session_factory() as db:
        row = db.execute(
            select(AgentDevice, User)
            .join(User, User.id == AgentDevice.owner_user_id)
            .where(AgentDevice.id == device_id)
        ).one_or_none()
        if row is None:
            raise LookupError("device_not_found")
        device, owner = row
        if (
            device.revoked_at is not None
            or device.approved_at is None
            or device.environment != settings.robopark_environment
            or device.instance_id != str(settings.robopark_instance_id)
            or not owner.is_active
            or owner.access_status != AccessStatus.approved.value
        ):
            raise LookupError("device_not_eligible")
        return {
            "id": device.id,
            "label": device.label,
            "owner_user_id": owner.id,
            "owner_username": owner.username,
            "owner_display_name": owner.username,
            "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        }


def _requester_active(session_factory: sessionmaker, requester_user_id: int) -> bool:
    with session_factory() as db:
        user = db.get(User, requester_user_id)
        return bool(
            user is not None
            and user.is_active
            and user.access_status == AccessStatus.approved.value
        )


def _valid_typed_payload(operation: str, raw_payload: bytes) -> bool:
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if operation == "csv_render_v1":
        if set(payload) != {"headers", "rows"}:
            return False
        headers = payload["headers"]
        rows = payload["rows"]
        return bool(
            isinstance(headers, list)
            and 1 <= len(headers) <= 20
            and all(isinstance(value, str) for value in headers)
            and isinstance(rows, list)
            and len(rows) <= 10_000
            and all(
                isinstance(row, list)
                and len(row) == len(headers)
                and all(isinstance(value, str) for value in row)
                for row in rows
            )
        )
    if operation != "image_resize_v1" or set(payload) != {
        "data_base64",
        "mime_type",
        "max_edge",
        "quality",
    }:
        return False
    if (
        payload["mime_type"] not in {"image/jpeg", "image/png", "image/webp"}
        or type(payload["max_edge"]) is not int
        or not 64 <= payload["max_edge"] <= 4096
        or type(payload["quality"]) is not int
        or not 40 <= payload["quality"] <= 95
        or not isinstance(payload["data_base64"], str)
    ):
        return False
    try:
        image = base64.b64decode(payload["data_base64"], validate=True)
    except (TypeError, ValueError):
        return False
    dimensions = image_dimensions(image, payload["mime_type"])
    return bool(
        1 <= len(image) <= 4 * 1024 * 1024
        and dimensions is not None
        and min(dimensions) >= 1
        and dimensions[0] * dimensions[1] <= 40_000_000
    )


def _pool_projection(
    session_factory: sessionmaker,
    settings: Settings,
    requester_user_id: int,
    connected_ids: set[str],
) -> tuple[str, int | None, list[dict]] | None:
    with session_factory() as db:
        requester = db.get(User, requester_user_id)
        if (
            requester is None
            or not requester.is_active
            or requester.access_status != AccessStatus.approved.value
        ):
            return None
        privileged = rbac.is_admin_or_royal(requester)
        query = (
            select(AgentDevice, User)
            .join(User, User.id == AgentDevice.owner_user_id)
            .where(
                AgentDevice.environment == settings.robopark_environment,
                AgentDevice.instance_id == str(settings.robopark_instance_id),
                AgentDevice.revoked_at.is_(None),
                AgentDevice.approved_at.is_not(None),
                User.is_active.is_(True),
                User.access_status == AccessStatus.approved.value,
            )
        )
        if not privileged:
            query = query.where(AgentDevice.owner_user_id == requester.id)
        devices = []
        for device, owner in db.execute(query.order_by(AgentDevice.id)):
            devices.append(
                {
                    "id": device.id,
                    "label": device.label,
                    "owner_user_id": owner.id,
                    "owner_username": owner.username,
                    "owner_display_name": owner.username,
                    "last_seen_at": (
                        device.last_seen_at.isoformat() if device.last_seen_at else None
                    ),
                }
            )
        return "all" if privileged else "own", None if privileged else requester.id, devices


def create_compute_router(session_factory: sessionmaker, settings: Settings) -> APIRouter:
    router = APIRouter()
    registry = ComputeRegistry()
    used_nonces: dict[str, float] = {}
    direct_timeout_tasks: dict[str, asyncio.Task] = {}
    direct_receipts: dict[str, DirectReceipt] = {}

    def prune_direct_receipts() -> None:
        now = time.monotonic()
        for job_id, receipt in list(direct_receipts.items()):
            if receipt.expires_at <= now:
                direct_receipts.pop(job_id, None)
        if len(direct_receipts) > DIRECT_RECEIPT_LIMIT:
            ordered = sorted(direct_receipts.values(), key=lambda item: item.expires_at)
            for receipt in ordered[: len(direct_receipts) - DIRECT_RECEIPT_LIMIT]:
                direct_receipts.pop(receipt.job_id, None)

    def direct_receipt(lease) -> DirectReceipt:
        prune_direct_receipts()
        receipt = direct_receipts.get(lease.job_id)
        if receipt is None:
            receipt = DirectReceipt(
                job_id=lease.job_id,
                attempt_id=lease.attempt_id,
                device_id=lease.device_id,
                generation=lease.generation,
                requester_user_id=lease.requester_user_id,
                payload_sha256=lease.payload_sha256,
                expires_at=time.monotonic() + DIRECT_RECEIPT_TTL_SECONDS,
            )
            direct_receipts[lease.job_id] = receipt
            prune_direct_receipts()
        else:
            receipt.expires_at = time.monotonic() + DIRECT_RECEIPT_TTL_SECONDS
        return receipt

    def count_direct_outcome(
        receipt: DirectReceipt, outcome: str, channel: ComputeChannel | None
    ) -> None:
        counted_field = f"{outcome}_counted"
        if getattr(receipt, counted_field):
            return
        setattr(receipt, counted_field, True)
        metric = f"direct_{outcome}"
        registry.metrics[metric] += 1
        if channel is not None and channel.generation == receipt.generation:
            setattr(channel, metric, getattr(channel, metric) + 1)

    def find_direct_lease(job_id: str, requester_user_id: int):
        return next(
            (
                item
                for item in registry.pending.values()
                if item.job_id == job_id
                and item.direct
                and item.requester_user_id == requester_user_id
            ),
            None,
        )

    def authenticate_internal(request: Request, body: bytes) -> None:
        timestamp = request.headers.get("X-Robopark-Timestamp", "")
        nonce = request.headers.get("X-Robopark-Nonce", "")
        signature = request.headers.get("X-Robopark-Signature", "")
        try:
            issued_at = int(timestamp)
        except ValueError as exc:
            raise HTTPException(401, "invalid_signature") from exc
        now = time.time()
        for value, expiry in list(used_nonces.items()):
            if expiry <= now:
                used_nonces.pop(value, None)
        if (
            abs(now - issued_at) > 30
            or not nonce
            or nonce in used_nonces
            or not verify_body_signature(settings.secret_key, timestamp, nonce, body, signature)
        ):
            raise HTTPException(401, "invalid_signature")
        used_nonces[nonce] = now + 60

    @router.websocket("/emergency/bridge/compute/ws")
    async def compute_ws(websocket: WebSocket) -> None:
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
        channel: ComputeChannel | None = None
        try:
            auth = await asyncio.wait_for(_receive_json(websocket), timeout=10)
            if auth.get("type") != "auth" or not isinstance(auth.get("device_id"), str):
                raise ValueError("invalid_auth")
            signed = (
                "robopark-compute-v1\n"
                + str(settings.robopark_instance_id)
                + "\n"
                + settings.robopark_environment
                + "\n"
                + nonce
            ).encode()
            device_id, owner_user_id = await asyncio.to_thread(
                _authenticate_device,
                session_factory,
                settings,
                auth["device_id"],
                str(auth.get("signature", "")),
                signed,
            )
            channel = ComputeChannel(
                device_id=device_id, owner_user_id=owner_user_id, websocket=websocket
            )
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
                    "supports": sorted(OPERATIONS),
                }
            )
            while True:
                try:
                    message = await asyncio.wait_for(_receive_json(websocket), timeout=15)
                except TimeoutError:
                    if not await asyncio.to_thread(
                        _device_still_valid, session_factory, settings, channel.device_id
                    ):
                        await websocket.close(code=1008)
                        return
                    continue
                if not await asyncio.to_thread(
                    _device_still_valid, session_factory, settings, channel.device_id
                ):
                    await websocket.close(code=1008)
                    return
                if message.get("generation") != channel.generation:
                    continue
                if message.get("type") == "heartbeat":
                    capacity = message.get("capacity")
                    active_jobs = message.get("active_jobs")
                    if type(capacity) is not int or not 0 <= capacity <= 2:
                        continue
                    if type(active_jobs) is not int or not 0 <= active_jobs <= 2:
                        continue
                    channel.capacity = capacity
                    channel.reported_active_jobs = active_jobs
                    channel.paused = capacity == 0
                    for name in ("cpu_percent", "memory_percent"):
                        value = message.get(name)
                        if (
                            isinstance(value, (int, float))
                            and not isinstance(value, bool)
                            and math.isfinite(value)
                            and 0 <= value <= 100
                        ):
                            setattr(channel, name, float(value))
                    if message.get("thermal_state") in {
                        "normal",
                        "fair",
                        "serious",
                        "critical",
                    }:
                        channel.thermal_state = message["thermal_state"]
                    if message.get("power") in {"ac", "battery", "low_power", "unknown"}:
                        channel.power = message["power"]
                    supports = message.get("supports")
                    allowed_supports = {*OPERATIONS, "webrtc_datachannel_v1"}
                    if isinstance(supports, list) and len(supports) <= len(allowed_supports):
                        channel.supports = {
                            item
                            for item in supports
                            if isinstance(item, str) and item in allowed_supports
                        }
                    version = message.get("version")
                    if isinstance(version, str) and len(version) <= 64:
                        channel.version = version
                    channel.ready = True
                    channel.last_heartbeat = time.monotonic()
                elif message.get("type") == "compute_result":
                    lease = registry.pending_lease(
                        device_id=channel.device_id,
                        generation=channel.generation,
                        job_id=str(message.get("job_id", "")),
                        attempt_id=str(message.get("attempt_id", "")),
                    )
                    if lease is None or message.get("payload_sha256") != lease.payload_sha256:
                        continue
                    result = message.get("result")
                    if message.get("ok") is True:
                        validated = validate_result(lease.operation, result)
                        if validated is None:
                            continue
                        result = {key: value for key, value in validated.items() if key != "bytes"}
                    elif message.get("error") not in {
                        "invalid",
                        "cancelled",
                        "timeout",
                        "unavailable",
                        "worker_failed",
                        "invalid_job",
                    }:
                        continue
                    if not await asyncio.to_thread(
                        _requester_active, session_factory, lease.requester_user_id
                    ):
                        continue
                    accepted = registry.resolve_result(
                        device_id=channel.device_id,
                        generation=channel.generation,
                        job_id=lease.job_id,
                        attempt_id=lease.attempt_id,
                        payload_sha256=lease.payload_sha256,
                        result={
                            "ok": message.get("ok") is True,
                            "result": result,
                            "error": message.get("error"),
                        },
                    )
                    if accepted:
                        if message.get("ok") is True:
                            channel.completed += 1
                        else:
                            channel.failed += 1
                elif message.get("type") == "compute_peer_answer":
                    lease = registry.pending_lease(
                        device_id=channel.device_id,
                        generation=channel.generation,
                        job_id=str(message.get("job_id", "")),
                        attempt_id=str(message.get("attempt_id", "")),
                    )
                    answer = message.get("answer")
                    if (
                        lease is None
                        or not lease.direct
                        or message.get("payload_sha256") != lease.payload_sha256
                        or not isinstance(answer, dict)
                        or set(answer) != {"type", "sdp"}
                        or answer.get("type") != "answer"
                        or not isinstance(answer.get("sdp"), str)
                        or not 1 <= len(answer["sdp"].encode()) <= 64 * 1024
                    ):
                        continue
                    if not await asyncio.to_thread(
                        _requester_active, session_factory, lease.requester_user_id
                    ):
                        continue
                    registry.resolve_result(
                        device_id=channel.device_id,
                        generation=channel.generation,
                        job_id=lease.job_id,
                        attempt_id=lease.attempt_id,
                        payload_sha256=lease.payload_sha256,
                        result={"answer": answer},
                    )
                elif message.get("type") == "compute_peer_done":
                    lease = registry.pending_lease(
                        device_id=channel.device_id,
                        generation=channel.generation,
                        job_id=str(message.get("job_id", "")),
                        attempt_id=str(message.get("attempt_id", "")),
                    )
                    input_bytes = message.get("input_bytes")
                    output_bytes = message.get("output_bytes")
                    duration_ms = message.get("duration_ms")
                    if (
                        lease is None
                        or not lease.direct
                        or message.get("payload_sha256") != lease.payload_sha256
                        or type(input_bytes) is not int
                        or type(output_bytes) is not int
                        or type(duration_ms) is not int
                        or not 0 <= input_bytes <= 8 * 1024 * 1024
                        or (
                            input_bytes != lease.direct_payload_size
                            and not (message.get("ok") is False and input_bytes == 0)
                        )
                        or not 0 <= output_bytes <= 8 * 1024 * 1024
                        or not 0 <= duration_ms <= 30_000
                        or type(message.get("ok")) is not bool
                        or not await asyncio.to_thread(
                            _requester_active, session_factory, lease.requester_user_id
                        )
                    ):
                        continue
                    receipt = direct_receipt(lease)
                    if not receipt.native_done:
                        registry.metrics["direct_input_bytes"] += input_bytes
                        registry.metrics["direct_output_bytes"] += output_bytes
                        channel.direct_input_bytes += input_bytes
                        channel.direct_output_bytes += output_bytes
                        receipt.native_done = True
                        receipt.native_ok = message.get("ok") is True
                        if not receipt.native_ok:
                            count_direct_outcome(receipt, "failed", channel)
                    timeout_task = direct_timeout_tasks.pop(lease.job_id, None)
                    if timeout_task is not None:
                        timeout_task.cancel()
                    registry.finish(channel, lease)
                    if not lease.future.done():
                        lease.future.set_exception(ConnectionError("peer_finished_before_answer"))
        except WebSocketDisconnect:
            pass
        except (TimeoutError, ValueError, InvalidSignature, OverflowError):
            await websocket.close(code=1008)
        finally:
            if channel is not None:
                registry.remove(channel)

    async def dispatch(payload: ComputeRequest, raw_payload: bytes) -> tuple[dict, str, int]:
        deadline = time.monotonic() + payload.deadline_ms / 1000
        excluded: set[str] = set()
        attempts = 0
        while attempts < 2 and time.monotonic() < deadline:
            channel = registry.choose(payload.operation, excluded=excluded)
            if channel is None:
                break
            excluded.add(channel.device_id)
            attempts += 1
            lease = registry.new_lease(
                channel,
                operation=payload.operation,
                payload_bytes=raw_payload,
                requester_user_id=payload.requester_user_id,
                deadline_monotonic=deadline,
            )
            try:
                remaining_attempts = 3 - attempts
                timeout = max(0.001, (deadline - time.monotonic()) / remaining_attempts)
                await channel.send_json(
                    {
                        "type": "compute_job",
                        "generation": channel.generation,
                        "job_id": lease.job_id,
                        "attempt_id": lease.attempt_id,
                        "operation": payload.operation,
                        "payload_base64": payload.payload_base64,
                        "payload_sha256": payload.payload_sha256,
                        "timeout_ms": max(1, int(timeout * 1000)),
                    }
                )
                outcome = await asyncio.wait_for(asyncio.shield(lease.future), timeout=timeout)
                if outcome.get("ok") is True and isinstance(outcome.get("result"), dict):
                    return outcome["result"], channel.device_id, attempts
            except (TimeoutError, ConnectionError, WebSocketDisconnect):
                channel.failed += 1
                with suppress(RuntimeError, WebSocketDisconnect):
                    await channel.send_json(
                        {
                            "type": "cancel",
                            "generation": channel.generation,
                            "job_id": lease.job_id,
                            "attempt_id": lease.attempt_id,
                        }
                    )
            except asyncio.CancelledError:
                await channel.send_json(
                    {
                        "type": "cancel",
                        "generation": channel.generation,
                        "job_id": lease.job_id,
                        "attempt_id": lease.attempt_id,
                    }
                )
                raise
            finally:
                registry.finish(channel, lease)
                if not lease.future.done():
                    lease.future.cancel()
        raise RuntimeError("no_compute_device" if attempts == 0 else "compute_failed")

    async def expire_direct(channel: ComputeChannel, job_id: str, timeout: float) -> None:
        try:
            await asyncio.sleep(timeout)
            lease = next(
                (item for item in registry.pending.values() if item.job_id == job_id), None
            )
            if lease is None:
                return
            receipt = direct_receipt(lease)
            if receipt.browser_outcome is None:
                receipt.browser_outcome = "fallback"
                count_direct_outcome(receipt, "fallback", channel)
            with suppress(RuntimeError, ConnectionError, WebSocketDisconnect):
                await channel.send_json(
                    {
                        "type": "cancel",
                        "generation": lease.generation,
                        "job_id": lease.job_id,
                        "attempt_id": lease.attempt_id,
                    }
                )
        except asyncio.CancelledError:
            raise
        finally:
            lease = next(
                (item for item in registry.pending.values() if item.job_id == job_id), None
            )
            if lease is not None:
                registry.finish(channel, lease)
            direct_timeout_tasks.pop(job_id, None)

    async def dispatch_direct(payload: DirectRequest, *, signaling_bytes: int) -> dict:
        deadline = time.monotonic() + payload.deadline_ms / 1000
        excluded: set[str] = set()
        last_channel: ComputeChannel | None = None
        attempts = 0
        while attempts < 2 and time.monotonic() < deadline:
            channel = registry.choose(
                payload.operation,
                excluded=excluded,
                required_supports={"webrtc_datachannel_v1"},
            )
            if channel is None:
                break
            last_channel = channel
            excluded.add(channel.device_id)
            attempts += 1
            if attempts > 1:
                registry.metrics["direct_retries"] += 1
                channel.direct_retries += 1
            token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
            lease = registry.new_direct_lease(
                channel,
                operation=payload.operation,
                payload_sha256=payload.payload_sha256,
                payload_size=payload.payload_bytes,
                requester_user_id=payload.requester_user_id,
                deadline_monotonic=deadline,
                token=token,
            )
            answered = False
            try:
                attempts_left = 3 - attempts
                timeout = max(0.001, (deadline - time.monotonic()) / attempts_left)
                offer_frame = {
                    "type": "compute_peer_offer",
                    "generation": channel.generation,
                    "job_id": lease.job_id,
                    "attempt_id": lease.attempt_id,
                    "operation": payload.operation,
                    "payload_sha256": payload.payload_sha256,
                    "payload_bytes": payload.payload_bytes,
                    "timeout_ms": max(1, int(timeout * 1000)),
                    "offer": payload.offer,
                    "token": token,
                }
                frame_bytes = len(json.dumps(offer_frame, separators=(",", ":")).encode())
                request_bytes = signaling_bytes if attempts == 1 else 0
                registry.metrics["signaling_bytes"] += request_bytes + frame_bytes
                channel.signaling_bytes += request_bytes + frame_bytes
                await channel.send_json(offer_frame)
                outcome = await asyncio.wait_for(asyncio.shield(lease.future), timeout=timeout)
                answer = outcome["answer"]
                answer_bytes = len(json.dumps(answer, separators=(",", ":")).encode())
                registry.metrics["signaling_bytes"] += answer_bytes
                channel.signaling_bytes += answer_bytes
                answered = True
                direct_timeout_tasks[lease.job_id] = asyncio.create_task(
                    expire_direct(channel, lease.job_id, max(0.001, deadline - time.monotonic()))
                )
                return {
                    "generation": lease.generation,
                    "job_id": lease.job_id,
                    "attempt_id": lease.attempt_id,
                    "payload_sha256": lease.payload_sha256,
                    "token": token,
                    "answer": answer,
                    "device_id": channel.device_id,
                    "timeout_ms": max(1, int((deadline - time.monotonic()) * 1000)),
                }
            except (TimeoutError, ConnectionError, RuntimeError, WebSocketDisconnect):
                with suppress(RuntimeError, ConnectionError, WebSocketDisconnect):
                    await channel.send_json(
                        {
                            "type": "cancel",
                            "generation": lease.generation,
                            "job_id": lease.job_id,
                            "attempt_id": lease.attempt_id,
                        }
                    )
            except asyncio.CancelledError:
                with suppress(
                    asyncio.CancelledError, RuntimeError, ConnectionError, WebSocketDisconnect
                ):
                    await channel.send_json(
                        {
                            "type": "cancel",
                            "generation": lease.generation,
                            "job_id": lease.job_id,
                            "attempt_id": lease.attempt_id,
                        }
                    )
                raise
            finally:
                if not answered:
                    registry.finish(channel, lease)
                    if not lease.future.done():
                        lease.future.cancel()
        registry.metrics["direct_fallback"] += 1
        if last_channel is not None:
            last_channel.direct_fallback += 1
        raise RuntimeError("no_compute_device" if attempts == 0 else "direct_signaling_failed")

    @router.post("/internal/v1/compute")
    async def compute(request: Request):
        body = await request.body()
        authenticate_internal(request, body)
        try:
            payload = ComputeRequest.model_validate_json(body)
            raw_payload = base64.b64decode(payload.payload_base64, validate=True)
        except (ValidationError, TypeError, ValueError) as exc:
            raise HTTPException(422, "invalid_compute_request") from exc
        if (
            not raw_payload
            or len(raw_payload) > MAX_PAYLOAD_BYTES
            or hashlib.sha256(raw_payload).hexdigest() != payload.payload_sha256
            or not _valid_typed_payload(payload.operation, raw_payload)
        ):
            raise HTTPException(422, "invalid_compute_payload")
        if not await asyncio.to_thread(
            _requester_active, session_factory, payload.requester_user_id
        ):
            raise HTTPException(403, "forbidden")
        try:
            result, device_id, attempts = await dispatch(payload, raw_payload)
        except (RuntimeError, OverflowError) as exc:
            return JSONResponse(
                status_code=503,
                content={"detail": str(exc), "attempts_used": 0},
            )
        return {
            "result": result,
            "device_id": device_id,
            "attempts_used": attempts,
        }

    @router.post("/internal/v1/compute-status")
    async def compute_status(request: Request):
        body = await request.body()
        authenticate_internal(request, body)
        try:
            payload = ComputeStatusRequest.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_input=False)) from exc
        projection = await asyncio.to_thread(
            _pool_projection,
            session_factory,
            settings,
            payload.requester_user_id,
            set(registry.channels),
        )
        if projection is None:
            raise HTTPException(403, "forbidden")
        scope, owner_user_id, metadata = projection
        live = {
            item["id"]: item for item in registry.status(owner_user_id=owner_user_id)["devices"]
        }
        devices = []
        for stored in metadata:
            item = {
                **stored,
                "status": "offline",
                "connected": False,
                "ready": False,
                "capacity": 0,
                "inflight": 0,
                "active_jobs": 0,
                "cpu_percent": None,
                "memory_percent": None,
                "thermal_state": None,
                "power": "unknown",
                "supports": [],
                "version": None,
                "completed": 0,
                "failed": 0,
                "direct_input_bytes": 0,
                "direct_output_bytes": 0,
                "signaling_bytes": 0,
                "direct_completed": 0,
                "direct_failed": 0,
                "direct_cancelled": 0,
                "direct_retries": 0,
                "direct_fallback": 0,
            }
            if stored["id"] in live:
                item.update(live[stored["id"]])
                item.update(stored)
            devices.append(item)
        summary = {
            "connected": sum(item["connected"] for item in devices),
            "ready": sum(item["ready"] for item in devices),
            "capacity": sum(item["capacity"] for item in devices),
            "inflight": sum(item["inflight"] for item in devices),
            "active_jobs": sum(item["active_jobs"] for item in devices),
            "completed": sum(item["completed"] for item in devices),
            "failed": sum(item["failed"] for item in devices),
            "direct_input_bytes": sum(item["direct_input_bytes"] for item in devices),
            "direct_output_bytes": sum(item["direct_output_bytes"] for item in devices),
            "signaling_bytes": sum(item["signaling_bytes"] for item in devices),
            "direct_completed": sum(item["direct_completed"] for item in devices),
            "direct_failed": sum(item["direct_failed"] for item in devices),
            "direct_cancelled": sum(item["direct_cancelled"] for item in devices),
            "direct_retries": sum(item["direct_retries"] for item in devices),
            "direct_fallback": sum(item["direct_fallback"] for item in devices),
        }
        return {
            "enabled": settings.emergency_bridge_enabled,
            "scope": scope,
            "summary": summary,
            "devices": devices,
        }

    @router.post("/internal/v1/compute-direct")
    async def compute_direct(request: Request):
        body = await _bounded_request_body(request, 80 * 1024, "direct_request_too_large")
        authenticate_internal(request, body)
        try:
            payload = DirectRequest.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_input=False)) from exc
        if not await asyncio.to_thread(
            _requester_active, session_factory, payload.requester_user_id
        ):
            raise HTTPException(403, "forbidden")
        try:
            return await dispatch_direct(payload, signaling_bytes=len(body))
        except (RuntimeError, OverflowError) as exc:
            return JSONResponse(status_code=503, content={"detail": str(exc)})

    async def finish_direct_lease(payload: DirectFinishRequest) -> dict:
        prune_direct_receipts()
        lease = find_direct_lease(payload.job_id, payload.requester_user_id)
        receipt = direct_receipts.get(payload.job_id)
        if lease is None and (
            receipt is None or receipt.requester_user_id != payload.requester_user_id
        ):
            raise HTTPException(404, "direct_lease_not_found")
        if not await asyncio.to_thread(
            _requester_active, session_factory, payload.requester_user_id
        ):
            raise HTTPException(403, "forbidden")
        if lease is not None:
            receipt = direct_receipt(lease)
        assert receipt is not None
        if not await asyncio.to_thread(
            _device_still_valid, session_factory, settings, receipt.device_id
        ):
            raise HTTPException(403, "device_authority_revoked")
        channel = registry.channels.get(receipt.device_id)
        if channel is not None and channel.generation != receipt.generation:
            channel = None
        if payload.outcome == "completed" and lease is not None and channel is None:
            raise HTTPException(409, "direct_channel_changed")
        if receipt.browser_outcome is not None:
            if receipt.browser_outcome != payload.outcome:
                raise HTTPException(409, "direct_outcome_conflict")
            return {"status": "accepted", "outcome": payload.outcome}
        if payload.outcome == "completed" and receipt.native_done and receipt.native_ok is False:
            raise HTTPException(409, "direct_worker_failed")
        receipt.browser_outcome = payload.outcome
        receipt.expires_at = time.monotonic() + DIRECT_RECEIPT_TTL_SECONDS
        count_direct_outcome(receipt, payload.outcome, channel)
        if payload.outcome == "completed":
            return {"status": "accepted", "outcome": payload.outcome}

        try:
            if lease is not None and channel is not None:
                with suppress(RuntimeError, ConnectionError, WebSocketDisconnect):
                    await channel.send_json(
                        {
                            "type": "cancel",
                            "generation": lease.generation,
                            "job_id": lease.job_id,
                            "attempt_id": lease.attempt_id,
                        }
                    )
        finally:
            timeout_task = direct_timeout_tasks.pop(payload.job_id, None)
            if timeout_task is not None:
                timeout_task.cancel()
            if lease is not None and channel is not None:
                registry.finish(channel, lease)
                if not lease.future.done():
                    lease.future.cancel()
        return {"status": "accepted", "outcome": payload.outcome}

    @router.post("/internal/v1/compute-direct-finish")
    async def compute_direct_finish(request: Request):
        body = await _bounded_request_body(request, 4096, "direct_finish_too_large")
        authenticate_internal(request, body)
        try:
            payload = DirectFinishRequest.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_input=False)) from exc
        return await finish_direct_lease(payload)

    @router.post("/internal/v1/compute-direct-cancel")
    async def compute_direct_cancel(request: Request):
        body = await _bounded_request_body(request, 4096, "direct_cancel_too_large")
        authenticate_internal(request, body)
        try:
            payload = DirectCancelRequest.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_input=False)) from exc
        result = await finish_direct_lease(
            DirectFinishRequest(
                requester_user_id=payload.requester_user_id,
                job_id=payload.job_id,
                outcome="cancelled",
            )
        )
        return {**result, "status": "cancelled"}

    async def shutdown() -> None:
        tasks = list(direct_timeout_tasks.values())
        direct_timeout_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    router.registry = registry
    router.shutdown = shutdown
    return router
