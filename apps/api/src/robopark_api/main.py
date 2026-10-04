import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from robopark_api.config import get_settings
from robopark_api.db import SessionLocal
from robopark_api.dev_seed import ensure_dev_seed
from robopark_api.middleware.csrf import BrowserOriginMiddleware
from robopark_api.middleware.maintenance import MaintenanceGateMiddleware
from robopark_api.middleware.observations import ObservationMiddleware
from robopark_api.routers import (
    admin_audit,
    admin_bot,
    admin_bot_config,
    admin_diagnostic_rules,
    admin_diagnostic_unknowns,
    admin_emergency,
    admin_emergency_readings,
    admin_health,
    admin_ops,
    admin_ota,
    admin_park_requests,
    admin_roles,
    admin_settings,
    admin_system,
    admin_terminal,
    admin_users,
    ai,
    ai_admin,
    analytics,
    auth,
    campaigns,
    changes,
    client_telemetry,
    dashboard,
    emergency,
    health,
    internal_bot,
    inventory,
    mechanic_emergency,
    mechanic_robots,
    mechanic_tasks,
    media_uploads,
    operations,
    operator_blockers,
    operator_parks,
    operator_report,
    operator_robots,
    parks,
    privileged_auth,
    push,
    reports,
    robot_registry,
    schedules,
    sync,
    task_timeline,
    tracker_actions,
    tracker_collaboration,
    tracker_read,
)
from robopark_api.seed import ensure_seed_user
from robopark_api.services.bootstrap import initialize_data
from robopark_api.services.change_revisions import (
    default_change_revision_store,
    scope_for_mutation,
)
from robopark_api.services.database_locks import dispose_database_lock_engines
from robopark_api.services.ops.context import build_ops_context, resolved_ops_dir
from robopark_api.services.ops.maintenance import host_maintenance_active
from robopark_api.services.ops.reconcile import reconcile_pending_rebuild


def create_app() -> FastAPI:
    settings = get_settings()
    # Attach the same settings to request and background DB connections.
    bind = getattr(SessionLocal, "kw", {}).get("bind")
    if bind is not None:
        bind._robopark_ops_settings = settings

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        deferred = host_maintenance_active(settings)
        if not deferred:
            initialize_data(
                SessionLocal,
                settings,
                seed_user=ensure_seed_user,
                dev_seed=ensure_dev_seed,
            )
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
            logging.getLogger(__name__).exception("ops rebuild reconcile failed on startup")
        try:
            yield
        finally:
            try:
                await asyncio.to_thread(_app.state.push_service.close)
            finally:
                await asyncio.to_thread(dispose_database_lock_engines)

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
    app.state.change_revision_store = default_change_revision_store()

    @app.middleware("http")
    async def publish_change_revision(request: Request, call_next):
        result = await call_next(request)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and result.status_code < 400:
            scopes = set(getattr(request.state, "change_scopes", ()))
            default_scope = scope_for_mutation(request.url.path)
            if default_scope:
                scopes.add(default_scope)
            for scope in scopes:
                try:
                    await asyncio.to_thread(app.state.change_revision_store.mark_changed, scope)
                except OSError:
                    logging.getLogger(__name__).exception("change revision publish failed")
        return result

    @app.exception_handler(RequestValidationError)
    async def hide_sensitive_validation_input(request: Request, exc: RequestValidationError):
        """Sanitize sensitive route families without changing other validation contracts."""
        if any(
            request.url.path == prefix or request.url.path.startswith(prefix + "/")
            for prefix in (
                admin_diagnostic_rules.router.prefix,
                admin_diagnostic_unknowns.router.prefix,
                admin_emergency_readings.router.prefix,
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
    app.add_middleware(BrowserOriginMiddleware, allowed_origins=origins)
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
            "X-Privileged-Authorization",
            "Upload-Offset",
        ],
        expose_headers=["ETag", "Upload-Offset", "Upload-Length", "Upload-Expires"],
    )
    app.include_router(auth.router)
    app.include_router(ai.router)
    app.include_router(ai_admin.router)
    app.include_router(campaigns.router)
    app.include_router(changes.router)
    app.include_router(client_telemetry.router)
    app.include_router(inventory.router)
    app.include_router(internal_bot.router)
    app.include_router(health.router)
    app.include_router(parks.router)
    app.include_router(admin_roles.router)
    app.include_router(admin_users.router)
    app.include_router(admin_audit.router)
    app.include_router(admin_bot.router)
    app.include_router(admin_bot_config.router)
    app.include_router(admin_diagnostic_rules.router)
    app.include_router(admin_diagnostic_unknowns.router)
    app.include_router(admin_emergency.router)
    app.include_router(admin_emergency_readings.router)
    app.include_router(admin_settings.router)
    app.include_router(admin_ops.router)
    app.include_router(admin_ota.router)
    app.include_router(privileged_auth.router)
    app.include_router(admin_health.router)
    app.include_router(admin_system.router)
    app.include_router(admin_terminal.router)
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
    app.include_router(task_timeline.router)
    app.include_router(dashboard.router)
    app.include_router(robot_registry.router)
    app.include_router(operations.router)
    app.include_router(analytics.router)
    app.include_router(reports.router)
    app.include_router(sync.router)
    app.include_router(media_uploads.router)
    app.include_router(schedules.router)
    app.include_router(push.router)
    app.state.terminal_session_factory = SessionLocal
    app.state.push_service = push.PushService(lambda: SessionLocal())
    return app


app = create_app()
