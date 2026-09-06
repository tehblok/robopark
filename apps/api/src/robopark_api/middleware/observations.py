import asyncio
import time
from contextlib import suppress

from starlette.concurrency import run_in_threadpool

from robopark_api.services.operational_health import RequestObservations

FLUSH_DELAY_SECONDS = 10


class ObservationMiddleware:
    def __init__(self, app, root):
        self.app = app
        self.observations = RequestObservations(root)
        self.pending = None

    async def flush_later(self):
        try:
            await asyncio.sleep(FLUSH_DELAY_SECONDS)
        finally:
            if self.pending is asyncio.current_task():
                self.pending = None
        # Requests arriving after the flush snapshot need their own trailing
        # flush, even while this thread is still writing the previous snapshot.
        await run_in_threadpool(self.observations.flush)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            try:
                return await self.app(scope, receive, send)
            finally:
                if self.pending is not None:
                    pending = self.pending
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending
                    await run_in_threadpool(self.observations.flush)
        path = scope.get("path", "")
        family = None
        if path.startswith("/tracker/"):
            family = "tracker"
        elif path.startswith(("/emergency/", "/mechanic/emergency/")):
            family = "diagnostics"
        elif path == "/reports" or path.startswith("/reports/"):
            family = "reports"
        if scope["type"] != "http" or family is None:
            return await self.app(scope, receive, send)
        started = time.monotonic()
        status = 500

        async def observe_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, observe_send)
        finally:
            flush = self.observations.record(family, (time.monotonic() - started) * 1000, status)
            if flush and self.pending is None:
                await run_in_threadpool(self.observations.flush)
            elif self.pending is None:
                self.pending = asyncio.create_task(self.flush_later())
