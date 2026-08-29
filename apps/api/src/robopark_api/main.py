import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from robopark_api.config import get_settings
from robopark_api.db import SessionLocal
from robopark_api.dev_seed import ensure_dev_seed
from robopark_api.middleware.maintenance import MaintenanceGateMiddleware
from robopark_api.routers import (
    admin_audit,
    admin_emergency,
    admin_ops,
    admin_park_requests,
    admin_roles,
    admin_settings,
    admin_users,
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
    reports,
    tracker_actions,
    tracker_read,
)
from robopark_api.seed import ensure_seed_user
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.blocker_history_job import run_blocker_history_loop
from robopark_api.services.emergency_config import ensure_default_section_roles
from robopark_api.services.emergency_keepalive import run_keepalive_loop
from robopark_api.services.ops.context import build_ops_context, resolved_ops_dir
from robopark_api.services.ops.reconcile import reconcile_pending_rebuild
from robopark_api.services.rbac_seed import ensure_rbac_catalog
from robopark_api.services.session_cleanup import run_session_cleanup_loop


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with SessionLocal() as db:
            ensure_rbac_catalog(db)
            ensure_default_section_roles(db)
            ensure_seed_user(db, settings)
            ensure_dev_seed(db, settings)
            # Databases from before the encryption feature can still hold
            # Tracker token / Emergency cookie as plaintext. Re-seal them now
            # so ``SECRET_KEY`` actually protects an existing install, not
            # only fresh writes from the admin UI.
            settings_svc.migrate_plaintext_secrets(db)
            settings_svc.migrate_registration_password_from_env(db)
        try:
            ctx = build_ops_context(settings)
            reconcile_pending_rebuild(
                ctx.ops_dir,
                database_url=ctx.database_url,
                config_files=ctx.config_files,
                data_dir=ctx.data_dir,
            )
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception("ops rebuild reconcile failed on startup")
        stop_event = asyncio.Event()
        tasks = [
            asyncio.create_task(run_keepalive_loop(stop_event)),
            asyncio.create_task(run_blocker_history_loop(stop_event)),
            asyncio.create_task(
                run_session_cleanup_loop(
                    stop_event,
                    interval_seconds=settings.session_cleanup_interval_seconds,
                )
            ),
        ]
        try:
            yield
        finally:
            stop_event.set()
            for task in tasks:
                task.cancel()
            # Await each task separately: a single `await` chain would skip the
            # remaining tasks as soon as the first CancelledError propagates.
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        title="Robopark API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.openapi_enabled else None,
        redoc_url="/redoc" if settings.openapi_enabled else None,
        openapi_url="/openapi.json" if settings.openapi_enabled else None,
    )
    app.state.ops_dir = resolved_ops_dir(settings)
    app.state.session_cookie_name = settings.session_cookie_name
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    # Explicit method/header allowlists paired with ``allow_credentials=True``:
    # a wildcard here would let the browser send credentialed requests with
    # arbitrary custom headers to every configured origin.
    app.add_middleware(MaintenanceGateMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )
    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(parks.router)
    app.include_router(admin_roles.router)
    app.include_router(admin_users.router)
    app.include_router(admin_audit.router)
    app.include_router(admin_emergency.router)
    app.include_router(admin_settings.router)
    app.include_router(admin_ops.router)
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
    app.include_router(reports.router)
    return app


app = create_app()
