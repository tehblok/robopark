import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from robopark_api.config import get_settings
from robopark_api.db import SessionLocal
from robopark_api.dev_seed import ensure_dev_seed
from robopark_api.middleware.maintenance import MaintenanceGateMiddleware
from robopark_api.middleware.observations import ObservationMiddleware
from robopark_api.routers import (
    admin_audit,
    admin_diagnostic_rules,
    admin_diagnostic_unknowns,
    admin_emergency,
    admin_health,
    admin_ops,
    admin_park_requests,
    admin_roles,
    admin_settings,
    admin_users,
    analytics,
    auth,
    campaigns,
    dashboard,
    emergency,
    health,
    inventory,
    mechanic_emergency,
    mechanic_robots,
    mechanic_tasks,
    operations,
    operator_blockers,
    operator_parks,
    operator_report,
    operator_robots,
    parks,
    reports,
    robot_registry,
    tracker_actions,
    tracker_collaboration,
    tracker_read,
)
from robopark_api.seed import ensure_seed_user
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.blocker_history_job import run_blocker_history_loop
from robopark_api.services.emergency_config import ensure_default_section_roles
from robopark_api.services.emergency_keepalive import run_keepalive_loop
from robopark_api.services.live_merge import JobLease, default_live_merge_root, live_merge_enabled
from robopark_api.services.ops.context import build_ops_context, resolved_ops_dir
from robopark_api.services.ops.maintenance import host_maintenance_active
from robopark_api.services.ops.reconcile import reconcile_pending_rebuild
from robopark_api.services.rbac_seed import ensure_rbac_catalog
from robopark_api.services.session_cleanup import run_session_cleanup_loop


def create_app() -> FastAPI:
    settings = get_settings()
    # Attach the same settings to request and background DB connections.
    bind = getattr(SessionLocal, "kw", {}).get("bind")
    if bind is not None:
        bind._robopark_ops_settings = settings

    def initialize_data():
        with SessionLocal() as db:
            ensure_rbac_catalog(db)
            ensure_default_section_roles(db)
            ensure_seed_user(db, settings)
            ensure_dev_seed(db, settings)
            # Re-seal pre-encryption values only once host writes are allowed.
            settings_svc.migrate_plaintext_secrets(db)
            settings_svc.migrate_registration_password_from_env(db)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        deferred = host_maintenance_active(settings)
        if not deferred:
            initialize_data()
        try:
            ctx = build_ops_context(settings)
            if ctx.use_host_updater:
                from robopark_api.services.ops.host_bridge import reconcile_host_job

                reconcile_host_job(ctx.ops_dir, ctx.host_ops_dir)
            else:
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
        job_lease = JobLease(default_live_merge_root(), "lifespan-jobs")
        tasks = []

        async def start_writers():
            # Candidate readiness is read-only. Start seeding and workers only
            # after root commits the release and publishes writes_resumed.
            while host_maintenance_active(settings):
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=0.5)
                if stop_event.is_set():
                    return
            if deferred:
                initialize_data()
            run_background_jobs = (not live_merge_enabled()) or job_lease.try_acquire()
            if not run_background_jobs:
                return
            tasks.extend(
                [
                    asyncio.create_task(run_keepalive_loop(stop_event)),
                    asyncio.create_task(run_blocker_history_loop(stop_event)),
                    asyncio.create_task(
                        run_session_cleanup_loop(
                            stop_event,
                            interval_seconds=settings.session_cleanup_interval_seconds,
                        )
                    ),
                ]
            )

        startup = asyncio.create_task(start_writers())
        try:
            yield
        finally:
            stop_event.set()
            startup.cancel()
            with suppress(asyncio.CancelledError):
                await startup
            for task in tasks:
                task.cancel()
            # Await each task separately: a single `await` chain would skip the
            # remaining tasks as soon as the first CancelledError propagates.
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            job_lease.release()

    app = FastAPI(
        title="Robopark API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.openapi_enabled else None,
        redoc_url="/redoc" if settings.openapi_enabled else None,
        openapi_url="/openapi.json" if settings.openapi_enabled else None,
    )
    app.state.ops_dir = resolved_ops_dir(settings)
    app.state.ops_settings = settings
    app.state.session_cookie_name = settings.session_cookie_name

    @app.exception_handler(RequestValidationError)
    async def hide_sensitive_validation_input(request: Request, exc: RequestValidationError):
        """Sanitize sensitive route families without changing other validation contracts."""
        if any(
            request.url.path == prefix or request.url.path.startswith(prefix + "/")
            for prefix in (
                admin_diagnostic_rules.router.prefix,
                admin_diagnostic_unknowns.router.prefix,
            )
        ):
            return JSONResponse(
                status_code=422,
                content={"detail": admin_diagnostic_rules.validation_error_details(exc.errors())},
            )
        if request.url.path != "/admin/settings/emergency-cookie":
            return await request_validation_exception_handler(request, exc)
        errors = []
        for error in exc.errors():
            sanitized = dict(error)
            sanitized.pop("input", None)
            errors.append(sanitized)
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    # Explicit method/header allowlists paired with ``allow_credentials=True``:
    # a wildcard here would let the browser send credentialed requests with
    # arbitrary custom headers to every configured origin.
    app.add_middleware(MaintenanceGateMiddleware)
    app.add_middleware(ObservationMiddleware, root=resolved_ops_dir(settings) / "observations")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "If-Match",
            "Idempotency-Key",
            "X-Tracker-State",
        ],
        expose_headers=["ETag"],
    )
    app.include_router(auth.router)
    app.include_router(campaigns.router)
    app.include_router(inventory.router)
    app.include_router(health.router)
    app.include_router(parks.router)
    app.include_router(admin_roles.router)
    app.include_router(admin_users.router)
    app.include_router(admin_audit.router)
    app.include_router(admin_diagnostic_rules.router)
    app.include_router(admin_diagnostic_unknowns.router)
    app.include_router(admin_emergency.router)
    app.include_router(admin_settings.router)
    app.include_router(admin_ops.router)
    app.include_router(admin_health.router)
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
    app.include_router(tracker_collaboration.router)
    app.include_router(dashboard.router)
    app.include_router(robot_registry.router)
    app.include_router(operations.router)
    app.include_router(analytics.router)
    app.include_router(reports.router)
    return app


app = create_app()
