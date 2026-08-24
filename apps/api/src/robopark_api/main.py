import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from robopark_api.config import get_settings
from robopark_api.db import SessionLocal
from robopark_api.routers import (
    admin_access,
    admin_emergency,
    admin_mechanics,
    admin_park_requests,
    admin_settings,
    auth,
    dashboard,
    emergency,
    health,
    mechanic_emergency,
    mechanic_robots,
    mechanic_tasks,
    operator_blockers,
    operator_parks,
    operator_report,
    operator_robots,
    parks,
    tracker_actions,
    tracker_read,
)
from robopark_api.seed import ensure_seed_user
from robopark_api.services.blocker_history_job import run_blocker_history_loop
from robopark_api.services.emergency_keepalive import run_keepalive_loop


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with SessionLocal() as db:
            ensure_seed_user(db, settings)
        stop_event = asyncio.Event()
        keepalive_task = asyncio.create_task(run_keepalive_loop(stop_event))
        history_task = asyncio.create_task(run_blocker_history_loop(stop_event))
        try:
            yield
        finally:
            stop_event.set()
            keepalive_task.cancel()
            history_task.cancel()
            with suppress(asyncio.CancelledError):
                await keepalive_task
                await history_task

    app = FastAPI(title="Robopark API", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(parks.router)
    app.include_router(admin_access.router)
    app.include_router(admin_emergency.router)
    app.include_router(admin_settings.router)
    app.include_router(admin_mechanics.router)
    app.include_router(operator_parks.router)
    app.include_router(operator_blockers.router)
    app.include_router(operator_report.router)
    app.include_router(operator_robots.router)
    app.include_router(admin_park_requests.router)
    app.include_router(mechanic_tasks.router)
    app.include_router(mechanic_robots.router)
    app.include_router(emergency.router)
    app.include_router(mechanic_emergency.router)
    app.include_router(tracker_read.router)
    app.include_router(tracker_actions.router)
    app.include_router(dashboard.router)
    return app


app = create_app()
