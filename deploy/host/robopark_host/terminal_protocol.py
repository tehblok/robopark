"""Bounded terminal v1 framing and immutable admission descriptors (stdlib only)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import struct
from dataclasses import asdict, dataclass
from uuid import UUID

CONTROL_LIMIT = 4096
INPUT_LIMIT = 16384
OUTPUT_LIMIT = 65536
FIELDS = {
    "create": {"request"},
    "status": {"id"},
    "attach": {"id", "attachment_id"},
    "lease": {"id", "attachment_id"},
    "resize": {"id", "attachment_id", "cols", "rows"},
    "detach": {"id", "attachment_id"},
    "terminate": {"id", "reason"},
    "worker-hello": {"id", "profile"},
    "worker-config": {"request"},
    "ok": {"session"},
    "error": {"error"},
    "ended": {"reason"},
}


def identity(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("terminal_invalid_id")
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("terminal_invalid_integer")


@dataclass(frozen=True)
class TerminalCreate:
    session_id: str
    actor_id: int
    auth_session_hash: str
    profile: str
    credential_generation: int
    capability_revision: str
    boot_id: str
    broker_epoch: str
    remaining_seconds: int

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("terminal_invalid_request")
        for field in ("session_id", "boot_id", "broker_epoch"):
            identity(value[field])
        for field in ("auth_session_hash", "capability_revision"):
            if not isinstance(value[field], str) or not re.fullmatch(
                "[a-f0-9]{64}", value[field]
            ):
                raise ValueError("terminal_invalid_digest")
        if value["profile"] not in ("maintenance", "root"):
            raise ValueError("terminal_invalid_profile")
        _integer(value["actor_id"], 1, 2**63 - 1)
        _integer(value["credential_generation"], 0, 2**31 - 1)
        _integer(
            value["remaining_seconds"], 1, 900 if value["profile"] == "root" else 3600
        )
        return cls(**value)

    def as_dict(self):
        return asdict(self)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("terminal_duplicate_key")
        result[key] = value
    return result


def decode_control(payload: bytes) -> dict:
    if len(payload) > CONTROL_LIMIT:
        raise ValueError("terminal_frame_too_large")
    try:
        value = json.loads(payload, object_pairs_hook=_unique_pairs)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("terminal_invalid_control") from exc
    if not isinstance(value, dict) or not isinstance(value.get("op"), str):
        raise ValueError("terminal_invalid_control")  # noqa: TRY004 - malformed wire data
    fields = FIELDS.get(value["op"])
    if fields is None or set(value) != fields | {"op"}:
        raise ValueError("terminal_invalid_control")
    return value


def encode_control(message: dict) -> bytes:
    payload = json.dumps(message, separators=(",", ":"), allow_nan=False).encode()
    decode_control(payload)
    return payload


def frame_limit(kind):
    if kind not in (0, 1, 2):
        raise ValueError("terminal_invalid_frame")
    return (CONTROL_LIMIT, INPUT_LIMIT, OUTPUT_LIMIT)[kind]


def pack_frame(kind: int, payload: bytes) -> bytes:
    if len(payload) > frame_limit(kind):
        raise ValueError("terminal_frame_too_large")
    return struct.pack("!IB", len(payload), kind) + payload


async def read_frame(reader):
    length, kind = struct.unpack("!IB", await reader.readexactly(5))
    if length > frame_limit(kind):
        raise ValueError("terminal_frame_too_large")
    return kind, await reader.readexactly(length)


async def write_frame(writer, kind, payload):
    writer.write(pack_frame(kind, payload))
    await asyncio.wait_for(writer.drain(), 5)


def capability_revision(boot_id, epoch):
    return hashlib.sha256(
        f"terminal-v1:{identity(boot_id)}:{identity(epoch)}".encode()
    ).hexdigest()
