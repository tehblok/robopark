from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_builtin_admin_or_royal, require_user
from robopark_api.models import (
    NativeBotDelivery,
    NativeBotJob,
    Park,
    ParkRequest,
    TelegramAccount,
    User,
)
from robopark_api.native_telegram_schemas import (
    AccessDecisionIn,
    AccessDecisionOut,
    AccessParkOut,
    AccessRequestCreate,
    AccessRequestOut,
    BotAccountOut,
    BotJobCreate,
    BotJobOut,
    BotJobUpdate,
    ClaimIn,
    FinishIn,
    JobRunIn,
    JobRunOut,
    LeaseIn,
    LinkCodeIn,
    LinkCodeOut,
    NativeAccessOut,
    NativeAccessRequestCreate,
    NativeBotAdminOut,
    NativeBotHealthIn,
    NativeManagedUserOut,
    NativeManagedUserParkMutationIn,
    NativeMigrationApplyIn,
    NativeMigrationApplyOut,
    NativeMigrationPreview,
    ParkBotOut,
    ParkBotUpdate,
    TelegramContextOut,
    TelegramOnboardingParksOut,
    TelegramOnboardingRequestIn,
    TelegramOnboardingRequestOut,
)
from robopark_api.native_telegram_usage_schemas import (
    NativeBotControlOut,
    NativeBotControlUpdate,
    NativeUsageUserOut,
)
from robopark_api.routers.internal_bot import require_bot_key
from robopark_api.services import access_requests, audit, bot_shared_settings
from robopark_api.services import native_telegram as service
from robopark_api.services import native_telegram_migration as migration_service
from robopark_api.services import native_telegram_usage as usage_service

router = APIRouter(tags=["native-telegram"])


def _access_request_out(db: Session, row: ParkRequest) -> AccessRequestOut:
    applicant = db.get(User, row.user_id)
    park = db.get(Park, row.park_id)
    if applicant is None or park is None or applicant.role not in access_requests.APPLICANT_ROLES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="access_request_invalid")
    account = db.get(TelegramAccount, applicant.id)
    return AccessRequestOut(
        id=row.id,
        revision=row.revision,
        user_id=applicant.id,
        username=applicant.username,
        telegram_user_id=account.telegram_user_id if account is not None else None,
        display_name=account.display_name if account is not None else None,
        telegram_username=account.telegram_username if account is not None else None,
        role=applicant.role,
        user_access_status=applicant.access_status,
        park=AccessParkOut(id=park.id, name=park.name),
        status=row.status,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        resolved_by=row.resolved_by,
    )


def _access_snapshot(db: Session, user: User) -> NativeAccessOut:
    is_applicant = user.role in access_requests.APPLICANT_ROLES
    can_request = is_applicant and user.access_status != "rejected"
    can_manage = user.access_status == "approved" and user.role in {"admin", "royal"}
    if not user.is_active or (not is_applicant and not can_manage):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return NativeAccessOut(
        user_id=user.id,
        username=user.username,
        role=user.role,
        access_status=user.access_status,
        can_manage=can_manage,
        assigned_parks=[
            AccessParkOut(id=park.id, name=park.name)
            for park in access_requests.assigned_parks(db, user.id)
        ],
        available_parks=(
            [
                AccessParkOut(id=park.id, name=park.name)
                for park in access_requests.available_parks(db, user.id)
            ]
            if can_request
            else []
        ),
        requests=(
            [_access_request_out(db, row) for row in access_requests.own_requests(db, user.id)]
            if is_applicant
            else []
        ),
    )


def _managed_user_out(
    user: User, account: TelegramAccount, parks: list[Park]
) -> NativeManagedUserOut:
    return NativeManagedUserOut(
        user_id=user.id,
        username=user.username,
        role=user.role,
        telegram_user_id=account.telegram_user_id,
        display_name=account.display_name,
        telegram_username=account.telegram_username,
        parks=[AccessParkOut(id=park.id, name=park.name) for park in parks],
    )


def _admin_snapshot(db: Session, user: User) -> NativeBotAdminOut:
    parks = service.manageable_parks(db, user)
    park_ids = [park.id for park in parks]
    jobs = (
        list(
            db.scalars(
                select(NativeBotJob)
                .where(NativeBotJob.park_id.in_(park_ids))
                .order_by(NativeBotJob.park_id, NativeBotJob.title, NativeBotJob.id)
            )
        )
        if park_ids
        else []
    )
    deliveries = []
    for park_id in park_ids:
        deliveries.extend(
            list(
                db.scalars(
                    select(NativeBotDelivery)
                    .where(NativeBotDelivery.park_id == park_id)
                    .order_by(NativeBotDelivery.scheduled_at.desc(), NativeBotDelivery.id.desc())
                    .limit(100)
                )
            )
        )
    deliveries.sort(key=lambda row: (row.scheduled_at, row.id), reverse=True)
    return NativeBotAdminOut(
        parks=[service.park_out(park) for park in parks],
        jobs=[service.job_out(job) for job in jobs],
        deliveries=[service.delivery_out(row) for row in deliveries],
        health=service.health(db),
    )


@router.get("/admin/bot/native", response_model=NativeBotAdminOut)
def admin_native(
    db: Session = Depends(get_db),
    user: User = Depends(require_builtin_admin_or_royal),
) -> NativeBotAdminOut:
    return _admin_snapshot(db, user)


@router.get("/admin/bot/native/control", response_model=NativeBotControlOut)
def admin_native_control(
    db: Session = Depends(get_db),
    _user: User = Depends(require_builtin_admin_or_royal),
) -> NativeBotControlOut:
    return usage_service.control(db)


@router.get("/admin/bot/native/usage", response_model=list[NativeUsageUserOut])
def admin_native_usage(
    db: Session = Depends(get_db),
    user: User = Depends(require_builtin_admin_or_royal),
) -> list[NativeUsageUserOut]:
    return usage_service.all_usage(db, user)


@router.put("/admin/bot/native/control", response_model=NativeBotControlOut)
def admin_update_native_control(
    payload: NativeBotControlUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_builtin_admin_or_royal),
) -> NativeBotControlOut:
    return usage_service.update_control(db, user, **payload.model_dump())


def _require_royal(user: User) -> None:
    if user.role != "royal":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


@router.get("/admin/bot/native/migration/preview", response_model=NativeMigrationPreview)
def admin_native_migration_preview(
    db: Session = Depends(get_db),
    user: User = Depends(require_builtin_admin_or_royal),
    settings: Settings = Depends(get_settings),
) -> NativeMigrationPreview:
    _require_royal(user)
    return NativeMigrationPreview.model_validate(
        migration_service.preview(db, settings.host_data_path)
    )


@router.post("/admin/bot/native/migration/apply", response_model=NativeMigrationApplyOut)
def admin_native_migration_apply(
    payload: NativeMigrationApplyIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_builtin_admin_or_royal),
    settings: Settings = Depends(get_settings),
) -> NativeMigrationApplyOut:
    _require_royal(user)
    return NativeMigrationApplyOut.model_validate(
        migration_service.apply(db, user, settings.host_data_path, payload.fingerprint)
    )


@router.put("/admin/bot/native/parks/{park_id}", response_model=ParkBotOut)
def admin_update_park(
    park_id: int,
    payload: ParkBotUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_builtin_admin_or_royal),
) -> ParkBotOut:
    return service.park_out(service.update_park(db, user, park_id, **payload.model_dump()))


@router.post(
    "/admin/bot/native/jobs", response_model=BotJobOut, status_code=status.HTTP_201_CREATED
)
def admin_create_job(
    payload: BotJobCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_builtin_admin_or_royal),
) -> BotJobOut:
    return service.job_out(service.create_job(db, user, payload))


@router.put("/admin/bot/native/jobs/{job_id}", response_model=BotJobOut)
def admin_update_job(
    job_id: str,
    payload: BotJobUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_builtin_admin_or_royal),
) -> BotJobOut:
    return service.job_out(service.update_job(db, user, job_id, payload, payload.revision))


@router.post("/admin/bot/native/jobs/{job_id}/run", response_model=JobRunOut)
def admin_run_job(
    job_id: str,
    payload: JobRunIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_builtin_admin_or_royal),
) -> JobRunOut:
    row, created = service.enqueue_manual_run(
        db,
        user,
        job_id,
        revision=payload.revision,
        request_id=str(payload.request_id),
        allow_disabled=payload.allow_disabled,
    )
    return JobRunOut(delivery=service.delivery_out(row), created=created)


@router.delete("/admin/bot/native/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_job(
    job_id: str,
    revision: int = Query(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_builtin_admin_or_royal),
) -> Response:
    service.delete_job(db, user, job_id, revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/bot/account", response_model=BotAccountOut)
def bot_account(db: Session = Depends(get_db), user: User = Depends(require_user)) -> BotAccountOut:
    account = db.get(TelegramAccount, user.id)
    return BotAccountOut(
        linked=account is not None,
        telegram_user_id=account.telegram_user_id if account is not None else None,
    )


@router.post("/bot/account/link-code", response_model=LinkCodeOut)
def bot_link_code(db: Session = Depends(get_db), user: User = Depends(require_user)) -> LinkCodeOut:
    code, expires_at = service.issue_link_code(db, user)
    return LinkCodeOut(code=code, expires_at=expires_at)


@router.delete("/bot/account", status_code=status.HTTP_204_NO_CONTENT)
def bot_unlink(db: Session = Depends(get_db), user: User = Depends(require_user)) -> Response:
    db.execute(delete(TelegramAccount).where(TelegramAccount.user_id == user.id))
    db.commit()
    audit.record(
        db,
        action=audit.ACTION_TELEGRAM_UNLINKED,
        actor=user,
        target_type="user",
        target_id=user.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/access", response_model=NativeAccessOut)
def access_status(
    db: Session = Depends(get_db), user: User = Depends(require_user)
) -> NativeAccessOut:
    return _access_snapshot(db, user)


@router.post(
    "/access/requests",
    response_model=AccessRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def request_access(
    payload: AccessRequestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> AccessRequestOut:
    return _access_request_out(db, access_requests.create_request(db, user, payload.park_id))


@router.post("/internal/bot/native/link", dependencies=[Depends(require_bot_key)])
def internal_link(payload: LinkCodeIn, db: Session = Depends(get_db)) -> dict:
    service.consume_link_code(db, payload.code, payload.telegram_user_id)
    return {"linked": True}


@router.get(
    "/internal/bot/native/onboarding/parks",
    response_model=TelegramOnboardingParksOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_onboarding_parks(db: Session = Depends(get_db)) -> TelegramOnboardingParksOut:
    return TelegramOnboardingParksOut(
        parks=[AccessParkOut(id=park.id, name=park.name) for park in service.onboarding_parks(db)]
    )


@router.post(
    "/internal/bot/native/onboarding/request",
    response_model=TelegramOnboardingRequestOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_onboarding_request(
    payload: TelegramOnboardingRequestIn,
    db: Session = Depends(get_db),
) -> TelegramOnboardingRequestOut:
    state, user, request, created = service.request_onboarding_access(
        db,
        telegram_user_id=payload.telegram_user_id,
        requested_role=payload.role,
        park_id=payload.park_id,
        display_name=payload.display_name,
        telegram_username=payload.telegram_username,
    )
    return TelegramOnboardingRequestOut(
        state=state,
        user_id=user.id,
        request_id=request.id if request is not None else None,
        role=user.role,
        park_id=payload.park_id,
        display_name=payload.display_name,
        notify_admin_ids=(
            service.onboarding_admin_telegram_ids(db, payload.park_id) if created else []
        ),
    )


@router.get(
    "/internal/bot/native/access",
    response_model=NativeAccessOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_access(
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> NativeAccessOut:
    return _access_snapshot(db, service.linked_account_user(db, telegram_user_id))


@router.post(
    "/internal/bot/native/access/requests",
    response_model=AccessRequestOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_bot_key)],
)
def internal_request_access(
    payload: NativeAccessRequestCreate,
    db: Session = Depends(get_db),
) -> AccessRequestOut:
    user = service.linked_account_user(db, payload.telegram_user_id)
    return _access_request_out(db, access_requests.create_request(db, user, payload.park_id))


@router.get(
    "/internal/bot/native/access/requests",
    response_model=list[AccessRequestOut],
    dependencies=[Depends(require_bot_key)],
)
def internal_access_requests(
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> list[AccessRequestOut]:
    manager = _telegram_manager(db, telegram_user_id)
    return [
        _access_request_out(db, row) for row in access_requests.pending_for_manager(db, manager)
    ]


@router.post(
    "/internal/bot/native/access/requests/{request_id}/decision",
    response_model=AccessDecisionOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_access_decision(
    request_id: int,
    payload: AccessDecisionIn,
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> AccessDecisionOut:
    row, applicant = access_requests.decide(
        db,
        _telegram_manager(db, telegram_user_id),
        request_id,
        approve=payload.approve,
        revision=payload.revision,
        target_park_id=payload.target_park_id,
        global_access=payload.global_access,
    )
    return AccessDecisionOut(
        request=_access_request_out(db, row),
        user_access_status=applicant.access_status,
    )


@router.get(
    "/internal/bot/native/manage/users",
    response_model=list[NativeManagedUserOut],
    dependencies=[Depends(require_bot_key)],
)
def internal_managed_users(
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> list[NativeManagedUserOut]:
    actor = _telegram_manager(db, telegram_user_id)
    return [
        _managed_user_out(user, account, parks)
        for user, account, parks in service.managed_telegram_users(db, actor)
    ]


def _internal_update_managed_user_park(
    user_id: int,
    park_id: int,
    payload: NativeManagedUserParkMutationIn,
    telegram_user_id: int,
    db: Session,
    *,
    assigned: bool,
) -> NativeManagedUserOut:
    user, account, parks = service.update_managed_user_park(
        db,
        _telegram_manager(db, telegram_user_id),
        user_id=user_id,
        park_id=park_id,
        expected_park_ids=payload.expected_park_ids,
        assigned=assigned,
    )
    return _managed_user_out(user, account, parks)


@router.put(
    "/internal/bot/native/manage/users/{user_id}/parks/{park_id}",
    response_model=NativeManagedUserOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_assign_managed_user_park(
    user_id: int,
    park_id: int,
    payload: NativeManagedUserParkMutationIn,
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> NativeManagedUserOut:
    return _internal_update_managed_user_park(
        user_id, park_id, payload, telegram_user_id, db, assigned=True
    )


@router.delete(
    "/internal/bot/native/manage/users/{user_id}/parks/{park_id}",
    response_model=NativeManagedUserOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_remove_managed_user_park(
    user_id: int,
    park_id: int,
    payload: NativeManagedUserParkMutationIn,
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> NativeManagedUserOut:
    return _internal_update_managed_user_park(
        user_id, park_id, payload, telegram_user_id, db, assigned=False
    )


@router.get(
    "/internal/bot/native/context",
    response_model=TelegramContextOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_context(
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> TelegramContextOut:
    user = service.linked_user(db, telegram_user_id)
    parks = service.readable_parks(db, user)
    return TelegramContextOut(
        user_id=user.id,
        role=user.role,
        name=user.username,
        parks=[service.park_out(park) for park in parks],
        can_manage=user.role in {"royal", "admin"},
    )


def _telegram_manager(db: Session, telegram_user_id: int) -> User:
    user = service.linked_user(db, telegram_user_id)
    if user.role not in {"royal", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


@router.get(
    "/internal/bot/native/manage",
    response_model=NativeBotAdminOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_manage(
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> NativeBotAdminOut:
    return _admin_snapshot(db, _telegram_manager(db, telegram_user_id))


@router.put(
    "/internal/bot/native/manage/parks/{park_id}",
    response_model=ParkBotOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_update_park(
    park_id: int,
    payload: ParkBotUpdate,
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> ParkBotOut:
    user = _telegram_manager(db, telegram_user_id)
    return service.park_out(service.update_park(db, user, park_id, **payload.model_dump()))


@router.post(
    "/internal/bot/native/manage/jobs",
    response_model=BotJobOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_bot_key)],
)
def internal_create_job(
    payload: BotJobCreate,
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> BotJobOut:
    return service.job_out(service.create_job(db, _telegram_manager(db, telegram_user_id), payload))


@router.put(
    "/internal/bot/native/manage/jobs/{job_id}",
    response_model=BotJobOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_update_job(
    job_id: str,
    payload: BotJobUpdate,
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> BotJobOut:
    return service.job_out(
        service.update_job(
            db, _telegram_manager(db, telegram_user_id), job_id, payload, payload.revision
        )
    )


@router.post(
    "/internal/bot/native/manage/jobs/{job_id}/run",
    response_model=JobRunOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_run_job(
    job_id: str,
    payload: JobRunIn,
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> JobRunOut:
    row, created = service.enqueue_manual_run(
        db,
        _telegram_manager(db, telegram_user_id),
        job_id,
        revision=payload.revision,
        request_id=str(payload.request_id),
        allow_disabled=payload.allow_disabled,
    )
    return JobRunOut(delivery=service.delivery_out(row), created=created)


@router.delete(
    "/internal/bot/native/manage/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_bot_key)],
)
def internal_delete_job(
    job_id: str,
    revision: int = Query(ge=1),
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> Response:
    service.delete_job(db, _telegram_manager(db, telegram_user_id), job_id, revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/internal/bot/native/usage",
    response_model=NativeUsageUserOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_usage(
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> NativeUsageUserOut:
    return usage_service.self_usage(db, service.linked_user(db, telegram_user_id))


@router.get(
    "/internal/bot/native/usage/all",
    response_model=list[NativeUsageUserOut],
    dependencies=[Depends(require_bot_key)],
)
def internal_all_usage(
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> list[NativeUsageUserOut]:
    return usage_service.all_usage(db, _telegram_manager(db, telegram_user_id))


@router.get(
    "/internal/bot/native/control",
    response_model=NativeBotControlOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_control(
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> NativeBotControlOut:
    _telegram_manager(db, telegram_user_id)
    return usage_service.control(db)


@router.put(
    "/internal/bot/native/control",
    response_model=NativeBotControlOut,
    dependencies=[Depends(require_bot_key)],
)
def internal_update_control(
    payload: NativeBotControlUpdate,
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    db: Session = Depends(get_db),
) -> NativeBotControlOut:
    return usage_service.update_control(
        db, _telegram_manager(db, telegram_user_id), **payload.model_dump()
    )


@router.get("/internal/bot/native/robots/{robot}", dependencies=[Depends(require_bot_key)])
def internal_robot(
    robot: str,
    telegram_user_id: int = Query(ge=1, le=2**63 - 1),
    view: str = Query(pattern=r"^(?:open|history|moves|moves_history|parts)$"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    user = service.linked_user(db, telegram_user_id)
    usage_service.require_queries_active(db, user)
    try:
        auxiliary_queues = bot_shared_settings.auxiliary_tracker_queues(settings.host_data_path)
    except bot_shared_settings.BotConfigError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="bot_config_unavailable",
        ) from None
    result = service.robot_issues(
        db,
        user,
        robot,
        view,
        auxiliary_queues=auxiliary_queues,
    )
    recorded = usage_service.record_success(db, user)
    return {
        **result,
        "usage": recorded.stats.model_dump(),
        "greeting": recorded.greeting,
    }


@router.post("/internal/bot/native/claim", dependencies=[Depends(require_bot_key)])
def internal_claim(
    payload: ClaimIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return {"deliveries": service.claim(db, payload.limit, host_data_path=settings.host_data_path)}


@router.post(
    "/internal/bot/native/health",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_bot_key)],
)
def internal_health(payload: NativeBotHealthIn, db: Session = Depends(get_db)) -> Response:
    service.record_health(db, payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/internal/bot/native/deliveries/{delivery_id}/content",
    dependencies=[Depends(require_bot_key)],
)
def internal_content(delivery_id: int, payload: LeaseIn, db: Session = Depends(get_db)) -> dict:
    return service.delivery_content(db, delivery_id, payload.lease_token)


@router.post(
    "/internal/bot/native/deliveries/{delivery_id}/begin",
    dependencies=[Depends(require_bot_key)],
)
def internal_begin(
    delivery_id: int,
    payload: LeaseIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    service.begin(
        db,
        delivery_id,
        payload.lease_token,
        host_data_path=settings.host_data_path,
    )
    return {"ready": True}


@router.post(
    "/internal/bot/native/deliveries/{delivery_id}/finish",
    dependencies=[Depends(require_bot_key)],
)
def internal_finish(delivery_id: int, payload: FinishIn, db: Session = Depends(get_db)) -> dict:
    service.finish(db, delivery_id, payload.lease_token, payload.state, payload.error_code)
    return {"recorded": True}
