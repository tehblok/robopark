"""Royal-only snapshot, restore, and ZIP update."""

from __future__ import annotations

import json
from contextlib import suppress
from functools import partial
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_builtin_admin_or_royal, require_royal
from robopark_api.models import HostOperationStatus, User
from robopark_api.ops_schemas import (
    AvailableUpdateOut,
    GithubApprovalIn,
    HostCapabilitiesOut,
    HostOperationIn,
    HostResultOut,
    ReleaseStatusOut,
    SystemHealthOut,
    UpdateApprovalIn,
    UpdateInspectionOut,
    public_result,
)
from robopark_api.security import hash_session_token
from robopark_api.services import audit, privileged_auth
from robopark_api.services.login_throttle import client_ip
from robopark_api.services.ops import host_bridge, operation_registry
from robopark_api.services.ops.archives import ArchiveError
from robopark_api.services.ops.context import build_ops_context, resolved_ops_dir
from robopark_api.services.ops.jobs import (
    KIND_RESTORE,
    KIND_SNAPSHOT,
    KIND_UPDATE,
    JobConflict,
    abort_job,
    is_exempt_session,
    is_maintenance_active,
    load_job,
)
from robopark_api.services.ops.reconcile import reconcile_pending_rebuild
from robopark_api.services.ops.runner import (
    RESTORE_PHRASE,
    UPDATE_PHRASE,
    OpsError,
    artifact_path,
    start_and_run,
)
from robopark_api.services.release_status import release_status

ACTION_OPS_SNAPSHOT = "admin.ops.snapshot"
ACTION_OPS_RESTORE = "admin.ops.restore"
ACTION_OPS_UPDATE = "admin.ops.update"

router = APIRouter(tags=["ops"])


class MaintenanceOut(BaseModel):
    active: bool
    kind: str | None = None
    operator: bool = False


class OpsJobOut(BaseModel):
    id: str
    kind: str
    state: str
    phase: str
    log: str
    error: str | None
    artifact_ready: bool
    restart_required: bool
    created_at: str
    updated_at: str
    restore_phrase: str = RESTORE_PHRASE
    update_phrase: str = UPDATE_PHRASE
    host_result: HostResultOut | None = None
    progress_percent: int | None = None
    progress_phase: str | None = None


class ExactOperationStatusOut(BaseModel):
    id: str
    kind: str
    receipt_state: str
    state: str
    phase: str
    error: str | None
    host_result: HostResultOut | None = None
    progress_percent: int | None = None


def _operation_out(row: HostOperationStatus) -> ExactOperationStatusOut:
    projected = None
    if row.host_result_json:
        with suppress(ValueError):
            projected = HostResultOut.model_validate(json.loads(row.host_result_json))
    return ExactOperationStatusOut(
        id=row.operation_id,
        kind=row.kind,
        receipt_state=row.receipt_state,
        state=row.state,
        phase=row.phase,
        error=row.error,
        host_result=projected,
        progress_percent=row.progress_percent,
    )


def _token_hash(request: Request, settings: Settings) -> str:
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return hash_session_token(raw)


def _require_privileged(
    request: Request,
    actor: User,
    db: Session,
    settings: Settings,
    operation_kind: str,
    operation_id: str,
    capability_revision: str | None = None,
) -> None:
    token = request.headers.get("X-Privileged-Authorization", "")
    enrolled = privileged_auth.enrolled(db, actor.id)
    context = privileged_auth.AuditContext(
        ip=client_ip(request),
        device=request.headers.get("user-agent"),
        session_token_hash=_token_hash(request, settings),
        operation_kind=str(operation_kind),
        operation_id=str(operation_id),
        capability_revision=capability_revision,
    )
    valid = privileged_auth.consume_reauthorization(
        db,
        actor,
        raw_token=token,
        context=context,
    )
    if not valid:
        raise HTTPException(
            status_code=401 if enrolled else 409,
            detail="privileged_authorization_required"
            if enrolled
            else "privileged_enrollment_required",
        )


def _reconcile_if_needed(settings: Settings) -> None:
    """Finalize Docker cutover when ops-agent writes rebuild.result after API boot."""
    if settings.ops_host_root:
        with suppress(host_bridge.BridgeError):
            host_bridge.reconcile_host_job(
                resolved_ops_dir(settings), host_bridge.host_root(settings)
            )
        return
    ctx = build_ops_context(settings)
    with suppress(Exception):
        reconcile_pending_rebuild(
            ctx.ops_dir,
            database_url=ctx.database_url,
            config_files=ctx.config_files,
            data_dir=ctx.data_dir,
        )


def _job_out(job, *, progress: tuple[str | None, int | None] = (None, None)) -> OpsJobOut:
    data = job.to_public_dict()
    phase, percent = progress
    return OpsJobOut(
        **data,
        host_result=public_result(job.extra.get("host_result")),
        progress_percent=percent,
        progress_phase=phase,
    )


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    # UploadFile uses a seekable spooled file. Measure it first so reading a
    # small archive never reserves memory for the whole configured limit and
    # joining chunks never keeps a second full archive copy alive.
    stream = file.file
    stream.seek(0, 2)
    upload_bytes = stream.tell()
    stream.seek(0)
    if upload_bytes > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="archive_too_large"
        )
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="archive_too_large"
        )
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="archive_required")
    return content


@router.get("/ops/maintenance", response_model=MaintenanceOut)
def maintenance_status(
    request: Request, settings: Settings = Depends(get_settings)
) -> MaintenanceOut:
    _reconcile_if_needed(settings)
    ops_dir = resolved_ops_dir(settings)
    job = load_job(ops_dir)
    active = is_maintenance_active(ops_dir)
    if settings.ops_host_root:
        try:
            root = host_bridge.host_root(settings)
            active = active or host_bridge.host_marker_active(root)
        except host_bridge.BridgeError:
            active = True
    raw = request.cookies.get(settings.session_cookie_name)
    token_hash = hash_session_token(raw) if raw else None
    return MaintenanceOut(
        active=active,
        kind=job.kind if job and active else None,
        operator=is_exempt_session(ops_dir, token_hash),
    )


@router.get("/admin/ops/job", response_model=OpsJobOut)
def get_job(
    _royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
) -> OpsJobOut:
    _reconcile_if_needed(settings)
    job = load_job(resolved_ops_dir(settings))
    if job is None:
        return OpsJobOut(
            id="",
            kind="",
            state="idle",
            phase="",
            log="",
            error=None,
            artifact_ready=False,
            restart_required=False,
            created_at="",
            updated_at="",
        )
    progress = (None, None)
    if settings.ops_host_root:
        with suppress(host_bridge.BridgeError):
            progress = host_bridge.update_progress(host_bridge.host_root(settings), job)
    return _job_out(job, progress=progress)


@router.get("/admin/ops/operations/{operation_id}", response_model=ExactOperationStatusOut)
def get_exact_operation(
    operation_id: UUID,
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ExactOperationStatusOut:
    _reconcile_if_needed(settings)
    row = db.get(HostOperationStatus, str(operation_id))
    found = row is not None and row.actor_user_id == royal.id
    audit.record(
        db, action="admin.ops.operation.status", actor=royal,
        outcome=audit.OUTCOME_SUCCESS if found else audit.OUTCOME_DENIED,
        detail="found" if found else "not_found",
    )
    if not found or row is None:
        raise HTTPException(status_code=404, detail="operation_not_found")
    job = load_job(resolved_ops_dir(settings))
    if job is not None and job.id == row.operation_id:
        progress = (None, None)
        if settings.ops_host_root:
            with suppress(host_bridge.BridgeError):
                progress = host_bridge.update_progress(host_bridge.host_root(settings), job)
        row = operation_registry.update_from_job(
            db, operation_id=row.operation_id, job=job, progress=progress,
        )
    return _operation_out(row)


@router.get("/admin/ops/artifact")
def download_artifact(
    _royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
):
    job = load_job(resolved_ops_dir(settings))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_job")
    path = artifact_path(resolved_ops_dir(settings), job)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact_missing")
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.post("/admin/ops/abort", response_model=OpsJobOut)
def post_abort(
    request: Request,
    _royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> OpsJobOut:
    """Force-clear a stuck running ops job and lift maintenance."""
    _require_privileged(request, _royal, db, settings, "abort", "abort")
    ops_dir = resolved_ops_dir(settings)
    try:
        job = abort_job(ops_dir)
    except JobConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_active_job")
    audit.record(
        db,
        action="admin.ops.abort",
        actor=_royal,
        detail=job.error or job.state,
    )
    return _job_out(job)


def _launch(
    *,
    kind: str,
    exempt: str,
    archive: bytes | None,
    confirm: str,
    settings: Settings,
    background: BackgroundTasks,
):
    if kind == KIND_RESTORE and confirm.strip() != RESTORE_PHRASE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="confirm_required")
    if kind == KIND_UPDATE and confirm.strip() != UPDATE_PHRASE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="confirm_required")
    if settings.ops_host_root:
        if kind == KIND_RESTORE:
            raise HTTPException(status_code=503, detail="host_restore_required")
        if kind == KIND_UPDATE:
            raise HTTPException(status_code=400, detail="inspection_required")
        host_bridge.require_host_idle(_bridge_root(settings))
    ctx = build_ops_context(settings)
    if settings.ops_sync:
        try:
            return start_and_run(
                ctx, kind, exempt_token_hash=exempt, archive=archive, confirm=confirm
            )
        except OpsError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    # Begin inside the request so maintenance is on before we return.
    from robopark_api.services.ops.runner import begin_job, execute_job

    try:
        job = begin_job(ctx, kind, exempt_token_hash=exempt)
    except JobConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    background.add_task(partial(execute_job, ctx, job, archive=archive, confirm=confirm))
    return job


@router.post("/admin/ops/snapshot", response_model=OpsJobOut)
def post_snapshot(
    request: Request,
    background: BackgroundTasks,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OpsJobOut:
    _require_privileged(request, royal, db, settings, "snapshot", "snapshot")
    exempt = _token_hash(request, settings)
    try:
        job = _launch(
            kind=KIND_SNAPSHOT,
            exempt=exempt,
            archive=None,
            confirm="",
            settings=settings,
            background=background,
        )
    except JobConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job_in_progress") from exc
    audit.record(
        db, action=ACTION_OPS_SNAPSHOT, actor=royal, outcome=audit.OUTCOME_SUCCESS, detail=job.state
    )
    return _job_out(job)


@router.post("/admin/ops/restore", response_model=OpsJobOut)
async def post_restore(
    request: Request,
    background: BackgroundTasks,
    confirm: str = Form(default=""),
    archive: UploadFile = File(...),
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OpsJobOut:
    _require_privileged(request, royal, db, settings, "restore", "restore")
    exempt = _token_hash(request, settings)
    blob = await _read_upload(archive, settings.ops_max_upload_bytes)
    try:
        if settings.ops_host_root:
            if confirm.strip() != RESTORE_PHRASE:
                raise HTTPException(status_code=400, detail="confirm_required")
            try:
                job = host_bridge.enqueue_restore(
                    resolved_ops_dir(settings), _bridge_root(settings), blob, royal.id, exempt
                )
            except ArchiveError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            job = _launch(
                kind=KIND_RESTORE,
                exempt=exempt,
                archive=blob,
                confirm=confirm,
                settings=settings,
                background=background,
            )
    except JobConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job_in_progress") from exc
    audit.record(
        db,
        action=ACTION_OPS_RESTORE,
        actor=royal,
        outcome=audit.OUTCOME_FAILURE if job.state == "failed" else audit.OUTCOME_SUCCESS,
        detail=job.error or job.state,
    )
    return _job_out(job)


@router.post("/admin/ops/update", response_model=OpsJobOut)
async def post_update(
    request: Request,
    background: BackgroundTasks,
    confirm: str = Form(default=""),
    archive: UploadFile = File(...),
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OpsJobOut:
    _require_privileged(request, royal, db, settings, "update", "update")
    exempt = _token_hash(request, settings)
    blob = await _read_upload(archive, settings.ops_max_upload_bytes)
    try:
        job = _launch(
            kind=KIND_UPDATE,
            exempt=exempt,
            archive=blob,
            confirm=confirm,
            settings=settings,
            background=background,
        )
    except JobConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job_in_progress") from exc
    audit.record(
        db,
        action=ACTION_OPS_UPDATE,
        actor=royal,
        outcome=audit.OUTCOME_FAILURE if job.state == "failed" else audit.OUTCOME_SUCCESS,
        detail=job.error or job.state,
    )
    return _job_out(job)


def _bridge_root(settings):
    try:
        return host_bridge.host_root(settings)
    except host_bridge.BridgeError as exc:
        raise HTTPException(status_code=503, detail="host_bridge_unavailable") from exc


def _host_action(db, actor, action, operation):
    try:
        result = operation()
    except (host_bridge.BridgeError, ArchiveError, JobConflict, OSError) as exc:
        detail = (
            str(exc)
            if isinstance(exc, (host_bridge.BridgeError, ArchiveError, JobConflict))
            else "host_bridge_unavailable"
        )
        audit.record(db, action=action, actor=actor, outcome=audit.OUTCOME_FAILURE, detail=detail)
        code = (
            409
            if isinstance(exc, JobConflict) or detail in {
                "capability_unavailable", "capabilities_changed",
            }
            else 503
            if detail in {"host_bridge_unavailable", "capabilities_unavailable"}
            else 400
        )
        raise HTTPException(status_code=code, detail=detail) from exc
    audit.record(db, action=action, actor=actor, outcome=audit.OUTCOME_SUCCESS, detail="accepted")
    return result


@router.get("/admin/ops/capabilities", response_model=HostCapabilitiesOut)
def get_host_capabilities(
    royal: User = Depends(require_royal), settings: Settings = Depends(get_settings),
):
    return host_bridge.operation_capabilities(_bridge_root(settings))


@router.post("/admin/ops/operations", response_model=ExactOperationStatusOut)
def post_host_operation(
    payload: HostOperationIn,
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    identity = str(payload.operation_id)
    try:
        receipt = operation_registry.reserve(
            db, operation_id=identity, actor_user_id=royal.id, kind=payload.kind.value,
        )
    except operation_registry.OperationIdentityConflict as exc:
        raise HTTPException(status_code=409, detail="duplicate_operation_id") from exc
    except operation_registry.OperationRegistryFull as exc:
        raise HTTPException(status_code=503, detail="operation_registry_full") from exc
    if receipt.receipt_state != "received":
        return _operation_out(receipt)

    def authorize():
        _require_privileged(
            request, royal, db, settings, payload.kind.value, identity,
            payload.capability_revision,
        )
        return {
            "operation_id": str(payload.operation_id), "operation_kind": payload.kind.value,
            "actor_user_id": royal.id, "consumed": True,
        }

    def enqueue(root):
        host_bridge.require_operation_capability(root, payload.kind)
        return host_bridge.enqueue_typed_operation(
            resolved_ops_dir(settings), root, payload.model_dump(mode="json"), royal.id,
            _token_hash(request, settings), authorize=authorize,
        )

    try:
        root = _bridge_root(settings)
        job = _host_action(
            db, royal, "admin.ops." + payload.kind.value, lambda: enqueue(root)
        )
    except HTTPException as exc:
        current = load_job(resolved_ops_dir(settings))
        if current is not None and current.id == identity:
            receipt = operation_registry.update_from_job(
                db, operation_id=identity, job=current,
            )
        else:
            receipt = operation_registry.mark_rejected(
                db, operation_id=identity, error=str(exc.detail),
            )
        raise
    receipt = operation_registry.update_from_job(db, operation_id=identity, job=job)
    return _operation_out(receipt)


@router.get("/admin/ops/system-health", response_model=SystemHealthOut)
def get_system_health(
    royal: User = Depends(require_royal), settings: Settings = Depends(get_settings)
):
    root = _bridge_root(settings)
    host_bridge.reconcile_host_job(resolved_ops_dir(settings), root)
    return host_bridge.system_health(root)


@router.get("/admin/ops/release-status", response_model=ReleaseStatusOut)
def get_release_status(
    _user: User = Depends(require_builtin_admin_or_royal),
    settings: Settings = Depends(get_settings),
):
    return release_status(_bridge_root(settings))


@router.post("/admin/ops/update/inspect", response_model=UpdateInspectionOut)
async def inspect_host_update(
    request: Request,
    archive: UploadFile = File(...),
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _require_privileged(request, royal, db, settings, "update.inspect", "inspect")
    root = _bridge_root(settings)
    blob = await _read_upload(archive, settings.ops_max_upload_bytes)
    return _host_action(
        db,
        royal,
        "admin.ops.update.inspect",
        lambda: host_bridge.inspect_update(
            settings, resolved_ops_dir(settings), root, blob, royal.id
        ),
    )


@router.post("/admin/ops/update/approve", response_model=OpsJobOut)
def approve_host_update(
    payload: UpdateApprovalIn,
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _require_privileged(
        request, royal, db, settings, "update.approve", payload.inspection_id
    )
    root = _bridge_root(settings)

    def approve():
        if payload.confirm != UPDATE_PHRASE:
            raise host_bridge.BridgeError("confirm_required")
        host_bridge.reconcile_host_job(resolved_ops_dir(settings), root)
        return host_bridge.approve_update(
            settings,
            resolved_ops_dir(settings),
            root,
            payload.inspection_id,
            royal.id,
            _token_hash(request, settings),
        )

    return _job_out(_host_action(db, royal, "admin.ops.update.approve", approve))


def _start_host_operation(kind, request, royal, db, settings):
    _require_privileged(request, royal, db, settings, kind, kind)
    root = _bridge_root(settings)
    host_bridge.reconcile_host_job(resolved_ops_dir(settings), root)
    return _job_out(
        _host_action(
            db,
            royal,
            "admin.ops." + kind,
            lambda: host_bridge.enqueue_operation(
                resolved_ops_dir(settings), root, kind, royal.id, _token_hash(request, settings)
            ),
        )
    )


@router.post("/admin/ops/diagnostics", response_model=OpsJobOut)
def post_diagnostics(
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    raise HTTPException(status_code=410, detail="typed_operation_required")


@router.post("/admin/ops/repair", response_model=OpsJobOut)
def post_repair(
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return _start_host_operation("repair", request, royal, db, settings)


@router.get("/admin/ops/diagnostic-artifact")
def download_diagnostic_artifact(
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    root = _bridge_root(settings)
    job = host_bridge.reconcile_host_job(resolved_ops_dir(settings), root)
    path = host_bridge.diagnostic_artifact(root, job)
    audit.record(
        db,
        action="admin.ops.diagnostics.download",
        actor=royal,
        outcome=audit.OUTCOME_SUCCESS if path else audit.OUTCOME_FAILURE,
        detail="download" if path else "artifact_missing",
    )
    if path is None:
        raise HTTPException(status_code=404, detail="artifact_missing")
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.get("/admin/ops/available-update", response_model=AvailableUpdateOut)
def get_available_update(
    royal: User = Depends(require_royal), settings: Settings = Depends(get_settings)
):
    return host_bridge.available_update(_bridge_root(settings))


@router.post("/admin/ops/github-update/approve", response_model=OpsJobOut)
def approve_github_update(
    payload: GithubApprovalIn,
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _require_privileged(
        request, royal, db, settings, "github-update.approve", payload.release_id
    )
    root = _bridge_root(settings)

    def approve():
        if payload.confirm != UPDATE_PHRASE:
            raise host_bridge.BridgeError("confirm_required")
        host_bridge.reconcile_host_job(resolved_ops_dir(settings), root)
        return host_bridge.approve_github_update(
            resolved_ops_dir(settings),
            root,
            payload.release_id,
            royal.id,
            _token_hash(request, settings),
        )

    return _job_out(_host_action(db, royal, "admin.ops.github-update.approve", approve))
