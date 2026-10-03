"""Bounded terminal v1 Unix transport. No shell command construction in the API."""

import asyncio
import json
import struct
from contextlib import suppress

from .authorization import TerminalError

LIMITS = (4096, 16384, 65536)


def encode(kind, payload):
    if type(kind) is not int or not 0 <= kind < 3 or len(payload) > LIMITS[kind]:
        raise TerminalError("terminal_frame_too_large", 422)
    return struct.pack("!IB", len(payload), kind) + payload


async def read_frame(reader):
    size, kind = struct.unpack("!IB", await reader.readexactly(5))
    if kind not in (0, 1, 2) or size > LIMITS[kind]:
        raise TerminalError("terminal_invalid_frame")
    return kind, await reader.readexactly(size)


async def send(writer, kind, data):
    writer.write(encode(kind, data))
    await asyncio.wait_for(writer.drain(), 5)


async def control(writer, message):
    await send(writer, 0, json.dumps(message, separators=(",", ":"), allow_nan=False).encode())


async def reply(reader):
    kind, data = await asyncio.wait_for(read_frame(reader), 10)
    if kind != 0:
        raise TerminalError("terminal_invalid_response")
    value = json.loads(data)
    if value.get("op") == "error":
        # Host messages are codes only; never reflect arbitrary broker strings.
        code = value.get("error")
        safe = {
            "terminal_host_busy",
            "terminal_session_limit",
            "terminal_session_ended",
            "terminal_already_attached",
            "terminal_session_missing",
            "terminal_capabilities_changed",
            "terminal_history_limit",
            "terminal_request_conflict",
        }
        raise TerminalError(code if code in safe else "terminal_unavailable")
    if value.get("op") != "ok" or not isinstance(value.get("session"), dict):
        raise TerminalError("terminal_invalid_response")
    return value["session"]


class BrokerClient:
    def __init__(self, settings):
        self.path = settings.terminal_broker_socket

    async def connect(self):
        if not self.path:
            raise TerminalError("terminal_unavailable", 503)
        try:
            return await asyncio.wait_for(asyncio.open_unix_connection(self.path, limit=65536), 5)
        except (OSError, TimeoutError):
            raise TerminalError("terminal_unavailable", 503) from None

    async def rpc(self, message):
        reader, writer = await self.connect()
        try:
            await control(writer, message)
            return await reply(reader)
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError) as exc:
            if isinstance(exc, TerminalError):
                raise
            raise TerminalError("terminal_result_unknown", 503) from None
        finally:
            writer.close()
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 1)

    async def create(self, descriptor):
        return await self.rpc({"op": "create", "request": descriptor})

    async def status(self, session_id):
        return await self.rpc({"op": "status", "id": session_id})

    async def terminate(self, session_id, reason="closed"):
        return await self.rpc({"op": "terminate", "id": session_id, "reason": reason})

    async def attach(self, session_id, attachment_id):
        reader, writer = await self.connect()
        try:
            await control(
                writer, {"op": "attach", "id": session_id, "attachment_id": attachment_id}
            )
            await reply(reader)
            return reader, writer
        except BaseException:
            writer.close()
            raise
