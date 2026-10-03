"""Fixed systemd adapter and exclusive host maintenance ownership."""

from __future__ import annotations

import asyncio
import re

from .state import host_operation
from .terminal_protocol import identity


class TerminalRuntime:
    def __init__(self, paths):
        self.paths = paths
        self.lock = None

    def hold(self):
        if self.lock is None:
            lock = host_operation(self.paths)
            lock.__enter__()
            self.lock = lock

    def release(self):
        if self.lock is not None:
            lock, self.lock = self.lock, None
            lock.__exit__(None, None, None)

    @staticmethod
    def unit(session_id, profile):
        identity(session_id)
        if profile not in ("maintenance", "root"):
            raise ValueError("terminal_invalid_profile")
        return f"robopark-terminal-{profile}@{session_id}.service"

    async def command(self, *arguments):
        process = await asyncio.create_subprocess_exec(
            "systemctl",
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            data = await asyncio.wait_for(process.stdout.read(16385), 12)
            code = await asyncio.wait_for(process.wait(), 12)
            if code or len(data) > 16384:
                raise RuntimeError("terminal_service_failed")
            return data.decode("utf-8", "strict").strip()
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise

    async def start(self, request):
        await self.command("start", self.unit(request.session_id, request.profile))

    async def stop(self, session_id, profile):
        unit = self.unit(session_id, profile)
        await self.command("stop", unit)
        if await self.command(
            "show", unit, "--property=ActiveState", "--value"
        ) not in ("inactive", "failed"):
            raise RuntimeError("terminal_stop_failed")

    async def worker_pid(self, session_id, profile):
        value = await self.command(
            "show", self.unit(session_id, profile), "--property=MainPID", "--value"
        )
        return int(value)

    async def stop_all(self):
        text = await self.command(
            "list-units",
            "--all",
            "--plain",
            "--no-legend",
            "robopark-terminal-*@*.service",
        )
        for line in text.splitlines():
            name = line.split()[0]
            match = re.fullmatch(
                r"robopark-terminal-(maintenance|root)@([0-9a-f-]{36})\.service", name
            )
            if not match:
                raise RuntimeError("terminal_invalid_unit")
            await self.stop(identity(match[2]), match[1])
