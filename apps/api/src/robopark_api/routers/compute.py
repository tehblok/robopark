from __future__ import annotations

import asyncio
import base64
import hashlib
from contextlib import suppress
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from robopark_api.deps import require_approved
from robopark_api.models import User
from robopark_api.services import compute_client
from robopark_api.services.emergency_bridge_client import BridgeError
from robopark_api.services.image_bounds import image_dimensions, jpeg_dimensions

router = APIRouter(prefix="/compute", tags=["compute"])
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
RELAY_BODY_TIMEOUT_SECONDS = 30.0


class ImageComputeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_base64: str = Field(max_length=5_600_000)
    mime_type: str
    max_edge: int = Field(ge=64, le=4096)
    quality: int = Field(ge=40, le=95)

    @field_validator("mime_type")
    @classmethod
    def valid_mime_type(cls, value: str) -> str:
        if value not in IMAGE_MIME_TYPES:
            raise ValueError("unsupported_image_type")
        return value


class DirectComputeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(gt=0, le=6 * 1024 * 1024)
    offer: dict

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


class DirectFinishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["completed", "fallback", "cancelled"]


class RelayAdmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["image_resize_v1"]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(gt=0, le=6 * 1024 * 1024)


async def _bounded_body(request: Request, limit: int) -> bytes:
    try:
        content_length = int(request.headers.get("content-length", "0"))
    except ValueError:
        content_length = limit + 1
    if content_length > limit:
        raise HTTPException(413, "direct_request_too_large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "direct_request_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_image(value: str, mime_type: str) -> tuple[bytes, int, int]:
    try:
        raw = base64.b64decode(value, validate=True)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "invalid_image") from exc
    if not 256 * 1024 <= len(raw) <= 4 * 1024 * 1024:
        raise HTTPException(422, "image_size_out_of_bounds")
    dimensions = image_dimensions(raw, mime_type)
    if dimensions is None or min(dimensions) < 1 or dimensions[0] * dimensions[1] > 40_000_000:
        raise HTTPException(422, "invalid_image")
    return raw, *dimensions


@router.post("/relay/admit")
async def admit_relay(
    request: Request,
    response: Response,
    user: User = Depends(require_approved),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        payload = RelayAdmitIn.model_validate_json(await _bounded_body(request, 4096))
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_input=False)) from exc
    try:
        return await asyncio.to_thread(
            compute_client.admit_relay,
            operation=payload.operation,
            payload_bytes=payload.payload_bytes,
            payload_sha256=payload.payload_sha256,
            requester_user_id=user.id,
        )
    except compute_client.RelayAdmissionError as exc:
        response.headers["Retry-After"] = str(max(1, (exc.retry_after_ms + 999) // 1000))
        return JSONResponse(
            status_code=503,
            content={"detail": exc.detail, "retry_after_ms": exc.retry_after_ms},
            headers={
                "Cache-Control": "no-store",
                "Retry-After": str(max(1, (exc.retry_after_ms + 999) // 1000)),
            },
        )
    except BridgeError as exc:
        raise HTTPException(503, "compute_unavailable") from exc


@router.delete("/relay/{ticket}", status_code=204)
def cancel_relay(
    ticket: str,
    user: User = Depends(require_approved),
) -> Response:
    if not 20 <= len(ticket) <= 128:
        raise HTTPException(404, "relay_ticket_not_found")
    try:
        compute_client.cancel_relay(ticket=ticket, requester_user_id=user.id)
    except compute_client.RelayTicketError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except BridgeError as exc:
        raise HTTPException(503, "compute_unavailable") from exc
    return Response(status_code=204)


@router.post("/image")
async def resize_image(
    request: Request,
    response: Response,
    user: User = Depends(require_approved),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    ticket = request.headers.get("X-Robopark-Relay-Ticket", "")
    if not ticket:
        raise HTTPException(428, "relay_ticket_required")
    if not 20 <= len(ticket) <= 128:
        raise HTTPException(409, "relay_ticket_invalid")

    async def abort_ticket() -> None:
        with suppress(BridgeError):
            await asyncio.to_thread(
                compute_client.abort_relay, ticket=ticket, requester_user_id=user.id
            )

    try:
        claim = await asyncio.to_thread(
            compute_client.claim_relay, ticket=ticket, requester_user_id=user.id
        )
    except asyncio.CancelledError:
        await abort_ticket()
        raise
    except compute_client.RelayTicketError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except BridgeError as exc:
        await abort_ticket()
        raise HTTPException(503, "compute_unavailable") from exc
    expected_bytes = claim["payload_bytes"]

    try:
        content_length_header = request.headers.get("content-length")
        if content_length_header is not None:
            try:
                content_length = int(content_length_header)
            except ValueError as exc:
                raise HTTPException(409, "relay_payload_size_mismatch") from exc
            if content_length != expected_bytes:
                raise HTTPException(409, "relay_payload_size_mismatch")
        async with asyncio.timeout(RELAY_BODY_TIMEOUT_SECONDS):
            body = await _bounded_body(request, expected_bytes)
        if len(body) != expected_bytes:
            raise HTTPException(409, "relay_payload_size_mismatch")
        if hashlib.sha256(body).hexdigest() != claim["payload_sha256"]:
            raise HTTPException(409, "relay_payload_hash_mismatch")
        try:
            payload = ImageComputeIn.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_input=False)) from exc
    except asyncio.CancelledError:
        await abort_ticket()
        raise
    except TimeoutError as exc:
        await abort_ticket()
        raise HTTPException(408, "relay_payload_timeout") from exc
    except Exception:
        await abort_ticket()
        raise
    try:
        source, source_width, source_height = _decode_image(payload.data_base64, payload.mime_type)
    except HTTPException:
        await abort_ticket()
        raise
    try:
        result = await asyncio.to_thread(
            compute_client.dispatch_compute,
            "image_resize_v1",
            payload.model_dump(),
            user.id,
            20_000,
            relay_ticket=ticket,
            raw_payload=body,
        )
        output = base64.b64decode(result.get("data_base64", ""), validate=True)
    except (BridgeError, TypeError, ValueError) as exc:
        raise HTTPException(503, "compute_unavailable") from exc
    dimensions = jpeg_dimensions(output)
    if (
        not output
        or len(output) > 4 * 1024 * 1024
        or len(output) >= len(source)
        or result.get("mime_type") != "image/jpeg"
        or hashlib.sha256(output).hexdigest() != result.get("sha256")
        or dimensions is None
        or dimensions != (result.get("width"), result.get("height"))
        or max(dimensions) > payload.max_edge
        or dimensions[0] > source_width
        or dimensions[1] > source_height
    ):
        raise HTTPException(503, "invalid_compute_result")
    return result


@router.get("/pool")
def pool(response: Response, user: User = Depends(require_approved)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return compute_client.fetch_compute_status(requester_user_id=user.id)
    except BridgeError as exc:
        raise HTTPException(503, "compute_unavailable") from exc


@router.post("/direct")
async def direct(
    request: Request,
    response: Response,
    user: User = Depends(require_approved),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        payload = DirectComputeIn.model_validate_json(await _bounded_body(request, 80 * 1024))
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_input=False)) from exc
    try:
        return await asyncio.to_thread(
            compute_client.dispatch_direct,
            operation=payload.operation,
            payload_sha256=payload.payload_sha256,
            payload_bytes=payload.payload_bytes,
            offer=payload.offer,
            requester_user_id=user.id,
            deadline_ms=20_000,
        )
    except BridgeError as exc:
        raise HTTPException(503, "compute_unavailable") from exc


@router.post("/direct/{job_id}/finish")
def finish_direct(
    job_id: UUID,
    payload: DirectFinishIn,
    response: Response,
    user: User = Depends(require_approved),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return compute_client.finish_direct(
            job_id=str(job_id), requester_user_id=user.id, outcome=payload.outcome
        )
    except BridgeError as exc:
        raise HTTPException(503, "compute_unavailable") from exc


@router.delete("/direct/{job_id}", status_code=204)
def cancel_direct(
    job_id: UUID,
    user: User = Depends(require_approved),
) -> Response:
    try:
        compute_client.cancel_direct(job_id=str(job_id), requester_user_id=user.id)
    except BridgeError as exc:
        raise HTTPException(503, "compute_unavailable") from exc
    return Response(status_code=204)
