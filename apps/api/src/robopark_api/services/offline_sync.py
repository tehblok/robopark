from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import Park, User, UserPark
from robopark_api.schedule_models import ScheduleEntry
from robopark_api.schedule_schemas import (
    ScheduleCopy,
    ScheduleCreate,
    ScheduleOut,
    SchedulePatternCreate,
    ScheduleUpdate,
)
from robopark_api.services import (
    inventory,
    inventory_stock,
    media_uploads,
    platform_settings,
    rbac,
    schedules,
    task_lifecycle,
    tracker_cache,
    tracker_client,
    tracker_policy,
    tracker_signatures,
)
from robopark_api.services.database_locks import database_idempotency_lock
from robopark_api.services.reliable_actions import canonical_payload
from robopark_api.services.task_timeline import append_user_message
from robopark_api.services.tracker_policy import enforce_issue_scope
from robopark_api.sync_schemas import SyncActionIn, SyncActionResultOut, SyncBatchIn, SyncBatchOut
from robopark_api.task_workflow_models import OfflineSyncReceipt


def _ordered(actions: list[SyncActionIn]) -> list[SyncActionIn]:
    by_id = {item.client_action_id: item for item in actions}
    if len(by_id) != len(actions):
        raise HTTPException(400, "sync_action_id_duplicate")
    remaining = list(actions)
    ordered: list[SyncActionIn] = []
    done: set[str] = set()
    while remaining:
        ready = [
            item
            for item in remaining
            if all(dep in done or dep not in by_id for dep in item.dependencies)
        ]
        if not ready:
            raise HTTPException(400, "sync_dependency_cycle")
        for item in ready:
            remaining.remove(item)
            ordered.append(item)
            done.add(item.client_action_id)
    return ordered


def _park_allowed(db: Session, user: User, park_id: int | None) -> bool:
    if park_id is None:
        return True
    if rbac.is_admin_or_royal(user) or rbac.has_permission(db, user, rbac.PERMISSION_PARKS_MANAGE):
        return db.get(Park, park_id) is not None
    return (
        db.scalar(
            select(UserPark.user_id).where(UserPark.user_id == user.id, UserPark.park_id == park_id)
        )
        is not None
    )


def _issue(db: Session, user: User, item: SyncActionIn, *, fresh: bool = False) -> dict:
    token = platform_settings.get_tracker_token(db)
    if not token:
        raise HTTPException(503, "tracker_token_not_configured")
    try:
        issue = (
            tracker_client.get_issue(token=token, key=item.resource_id)
            if fresh
            else tracker_cache.get_issue(token=token, key=item.resource_id)
        )
    except tracker_client.TrackerError as exc:
        raise HTTPException(503, "tracker_upstream_error") from exc
    if issue is None:
        raise HTTPException(409, "task_not_found")
    enforce_issue_scope(db, user, issue)
    return issue


def _authorize_review(db: Session, user: User, item: SyncActionIn, *, fresh: bool = False) -> dict:
    issue = _issue(db, user, item, fresh=fresh)
    for action in ("comment", "attach", "transition"):
        tracker_policy.ensure_action_allowed(db, user, issue, action)
    return issue


def dispatch_action(
    db: Session,
    user: User,
    item: SyncActionIn,
    consumed_media: media_uploads.ConsumedUpload | None = None,
) -> dict[str, Any]:
    if item.resource_type == "schedule_entry":
        try:
            return _schedule_action(db, user, item)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if item.resource_type != "tracker_issue":
        raise HTTPException(400, "sync_resource_unsupported")
    if item.action == "comment":
        _issue(db, user, item)
        text = str(item.payload.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "task_comment_required")
        row = append_user_message(
            db,
            issue_key=item.resource_id,
            actor=user,
            text=text,
            idempotency_key=item.idempotency_key,
        )
        return {"message_id": row.id, "sync_state": row.sync_state}
    if item.action == "claim":
        issue = _issue(db, user, item, fresh=True)
        if rbac.role_slug(user) != rbac.RoleSlug.MECHANIC:
            raise HTTPException(403, "task_claim_mechanic_required")
        if task_lifecycle.tracker_issue_is_closed(issue):
            raise HTTPException(409, "task_already_closed")
        if item.park_id is None:
            raise HTTPException(400, "sync_park_required")
        park = db.get(Park, item.park_id)
        if park is None:
            raise HTTPException(404, "park_not_found")
        if task_lifecycle.issue_park(db, issue).id != park.id:
            raise HTTPException(409, "sync_park_mismatch")
        return task_lifecycle.claim(
            db,
            actor=user,
            issue_key=item.resource_id,
            park=park,
            idempotency_key=item.idempotency_key,
            issue=issue,
            component_ids=item.payload.get("component_ids"),
            component_options=[],
        )
    if item.action == "handoff":
        _issue(db, user, item)
        return task_lifecycle.handoff(
            db,
            actor=user,
            issue_key=item.resource_id,
            assignee=str(item.payload.get("assignee") or ""),
            reason=str(item.payload.get("reason") or ""),
            done=str(item.payload.get("done") or ""),
            remaining=str(item.payload.get("remaining") or ""),
            obstacles=str(item.payload.get("obstacles") or ""),
            idempotency_key=item.idempotency_key,
        )
    if item.action == "inventory_writeoff":
        row = inventory.task_writeoff(
            db,
            user,
            item.resource_id,
            int(item.payload.get("part_id") or 0),
            int(item.payload.get("quantity") or 0),
            park_id=item.park_id,
            catalog_part_id=item.payload.get("catalog_part_id"),
            idempotency_key=item.idempotency_key,
        )
        return {"movement_id": row.id, "balance_after": row.balance_after}
    if item.action == "submit_review":
        issue = _authorize_review(db, user, item, fresh=True)
        park = task_lifecycle.issue_park(db, issue)
        if item.park_id is not None and item.park_id != park.id:
            raise HTTPException(409, "sync_park_mismatch")
        reviewer = schedules.resolve_active_operator(db, park_id=park.id)
        if reviewer is None:
            raise HTTPException(409, "task_review_operator_unavailable")
        push_service = db.info.get("sync_push_service")
        if push_service is None:
            raise HTTPException(503, "sync_notification_service_unavailable")
        if consumed_media is None:
            raise HTTPException(409, "media_dependency_pending")
        context = tracker_signatures.build_signature_context(db, user, issue)
        structured = item.payload.get("repair_fields")
        if structured is not None and not isinstance(structured, dict):
            raise HTTPException(422, "repair_fields_invalid")
        try:
            components = (
                tracker_client.list_queue_components(
                    token=platform_settings.get_tracker_token(db) or "",
                    queue=str(issue.get("queue") or ""),
                )
                if structured is not None
                else None
            )
        except tracker_client.TrackerError as exc:
            raise HTTPException(503, "tracker_upstream_error") from exc
        return task_lifecycle.submit_review(
            db,
            actor=user,
            issue_key=item.resource_id,
            defect_code=str(item.payload.get("defect_code") or ""),
            filename=consumed_media.original_name,
            content=consumed_media.content,
            content_type=consumed_media.mime_type,
            comment=str(item.payload.get("comment") or "") or None,
            repair_fields_payload=(
                {**structured, "defect_code": str(item.payload.get("defect_code") or "")}
                if isinstance(structured, dict)
                else structured
            ),
            component_options=components,
            current_issue=issue,
            operator_login=context.operator_login,
            reviewer=reviewer,
            idempotency_key=item.idempotency_key,
            notification_hook=lambda tx, performed_at: push_service.emit_in_transaction(
                tx,
                event_type="review_task",
                park_id=park.id,
                protected_text=f"Задача {item.resource_id} ожидает проверки",
                target_user_ids={reviewer.id},
                event_key=f"review:{item.resource_id}:{item.idempotency_key or datetime.fromtimestamp(performed_at, UTC).isoformat()}",
            ),
        )
    raise HTTPException(400, "sync_action_unsupported")


def _schedule_action(db: Session, user: User, item: SyncActionIn) -> dict[str, Any]:
    if item.park_id is None:
        raise HTTPException(400, "sync_park_required")
    if item.action in {"schedule_pattern", "schedule_copy"}:
        schema = SchedulePatternCreate if item.action == "schedule_pattern" else ScheduleCopy
        try:
            payload = schema.model_validate(
                {
                    **item.payload,
                    "idempotency_key": item.idempotency_key,
                }
            )
        except ValidationError as exc:
            raise HTTPException(400, "schedule_payload_invalid") from exc
        if payload.park_id != item.park_id:
            raise HTTPException(409, "sync_park_mismatch")
        created = (
            schedules.create_pattern(db, user, payload)
            if item.action == "schedule_pattern"
            else schedules.copy_period(db, user, payload)
        )
        return {"created_count": len(created)}
    if item.action == "schedule_create":
        try:
            payload = ScheduleCreate.model_validate(
                {
                    **item.payload,
                    "idempotency_key": item.idempotency_key,
                }
            )
        except ValidationError as exc:
            raise HTTPException(400, "schedule_payload_invalid") from exc
        if payload.park_id != item.park_id:
            raise HTTPException(409, "sync_park_mismatch")
        result = schedules.create_entry(db, user, payload)
        return {"entry": ScheduleOut.model_validate(result).model_dump(mode="json")}

    if item.action not in {"schedule_update", "schedule_delete"}:
        raise HTTPException(400, "sync_action_unsupported")
    if item.base_revision is None:
        raise HTTPException(400, "sync_base_revision_required")
    row = db.get(ScheduleEntry, item.resource_id)
    if row is not None and row.park_id != item.park_id:
        raise HTTPException(409, "sync_park_mismatch")
    if item.action == "schedule_delete":
        schedules.delete_entry(
            db,
            user,
            item.resource_id,
            base_revision=item.base_revision,
            idempotency_key=item.idempotency_key,
        )
        return {"deleted": True, "entry_id": item.resource_id}

    try:
        payload = ScheduleUpdate.model_validate(
            {
                **item.payload,
                "base_revision": item.base_revision,
                "idempotency_key": item.idempotency_key,
            }
        )
    except ValidationError as exc:
        raise HTTPException(400, "schedule_payload_invalid") from exc
    result = schedules.update_entry(db, user, item.resource_id, payload)
    return {"entry": ScheduleOut.model_validate(result).model_dump(mode="json")}


def _classification(exc: Exception) -> tuple[str, str]:
    def safe_code(value: object, fallback: str) -> str:
        code = str(value)
        return code if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else fallback

    if isinstance(exc, HTTPException):
        if exc.status_code == 409:
            return "conflict", safe_code(exc.detail, "sync_conflict")
        if exc.status_code >= 500 or exc.status_code in {408, 425, 429}:
            return "attention", safe_code(exc.detail, "temporary_failure")
        return "rejected", safe_code(exc.detail, "sync_rejected")
    if isinstance(exc, inventory_stock.InventoryConflict):
        return "conflict", safe_code(exc, "inventory_conflict")
    if isinstance(exc, PermissionError):
        return "rejected", safe_code(exc, "forbidden")
    if isinstance(exc, (RuntimeError, ConnectionError, TimeoutError)):
        return "attention", "temporary_failure"
    raise exc


def _receipt(
    db: Session, user: User, device_id: str, item: SyncActionIn
) -> tuple[OfflineSyncReceipt | None, str, str]:
    canonical, digest = canonical_payload(item.model_dump(mode="json"))
    row = db.scalar(
        select(OfflineSyncReceipt).where(
            OfflineSyncReceipt.actor_user_id == user.id,
            OfflineSyncReceipt.device_id == device_id,
            OfflineSyncReceipt.client_action_id == item.client_action_id,
        )
    )
    return row, canonical, digest


def _verify_prior_dependencies(
    db: Session, user: User, device_id: str, item: SyncActionIn, states: dict[str, str]
) -> None:
    media_id = str(item.payload.get("media_id") or "") if item.action == "submit_review" else ""
    action_ids = [dep for dep in item.dependencies if dep not in states and dep != media_id]
    if not action_ids:
        return
    receipts = {
        row.client_action_id: row
        for row in db.scalars(
            select(OfflineSyncReceipt).where(
                OfflineSyncReceipt.actor_user_id == user.id,
                OfflineSyncReceipt.device_id == device_id,
                OfflineSyncReceipt.client_action_id.in_(action_ids),
            )
        )
    }
    for action_id in action_ids:
        receipt = receipts.get(action_id)
        if receipt is None:
            raise HTTPException(503, "dependency_missing")
        try:
            state = SyncActionResultOut.model_validate_json(receipt.result_json).state
        except ValidationError as exc:
            raise HTTPException(503, "dependency_missing") from exc
        if state != "confirmed":
            raise HTTPException(503, "dependency_failed")


def _visible_scopes(db: Session, user: User, requested: Iterable[str]) -> tuple[set[str], set[str]]:
    park_ids = set(db.scalars(select(UserPark.park_id).where(UserPark.user_id == user.id)))
    staff = rbac.is_admin_or_royal(user) or rbac.has_permission(
        db, user, rbac.PERMISSION_PARKS_MANAGE
    )
    visible: set[str] = set()
    revoked: set[str] = set()
    for scope in requested:
        if scope in {"work", "inventory:catalog"}:
            visible.add(scope)
            continue
        suffix = scope.rsplit(":", 1)[-1]
        if (
            scope.startswith("work:park:")
            or scope.startswith("inventory:")
            or scope.startswith("schedule:park:")
        ) and suffix.isdecimal():
            if staff or int(suffix) in park_ids:
                visible.add(scope)
            else:
                revoked.add(scope)
        else:
            revoked.add(scope)
    return visible, revoked


def synchronize(
    db: Session,
    user: User,
    batch: SyncBatchIn,
    *,
    revision_store,
) -> SyncBatchOut:
    ordered = _ordered(batch.actions)
    results: list[SyncActionResultOut] = []
    states: dict[str, str] = {}
    changed_scopes: set[str] = set()
    action_scopes: set[str] = set()
    revoked: set[str] = set()
    for item in ordered:
        dependent_media_id = (
            str(item.payload.get("media_id") or "") if item.action == "submit_review" else ""
        )
        if item.resource_type == "tracker_issue":
            action_scopes.add("work")
        if item.park_id is not None:
            if item.resource_type == "schedule_entry":
                action_scopes.add(f"schedule:park:{item.park_id}")
            else:
                action_scopes.add(f"work:park:{item.park_id}")
                if item.action == "inventory_writeoff":
                    action_scopes.add(f"inventory:{item.park_id}")
        failed_dependency = next(
            (dep for dep in item.dependencies if dep in states and states[dep] != "confirmed"), None
        )
        if failed_dependency is not None:
            result = SyncActionResultOut(
                client_action_id=item.client_action_id,
                state="attention",
                code="dependency_failed",
            )
            results.append(result)
            states[item.client_action_id] = result.state
            continue
        receipt_key = f"offline-sync:{user.id}:{batch.device_id}:{item.client_action_id}"
        consumed_media = None
        media_preflight_error: Exception | None = None
        if dependent_media_id:
            preexisting, _precanonical, _prehash = _receipt(db, user, batch.device_id, item)
            if preexisting is None and _park_allowed(db, user, item.park_id):
                try:
                    _verify_prior_dependencies(db, user, batch.device_id, item, states)
                    _authorize_review(db, user, item)
                    consumed_media = media_uploads.consume_action_dependency(
                        db,
                        user,
                        media_id=dependent_media_id,
                        issue_key=item.resource_id,
                        device_id=batch.device_id,
                        action_id=item.client_action_id,
                    )
                except Exception as exc:
                    db.rollback()
                    media_preflight_error = exc
        with database_idempotency_lock(db, receipt_key):
            # A durable receipt is not an authorization grant. Recheck the
            # current park membership before returning a previous result.
            if item.park_id is not None and not _park_allowed(db, user, item.park_id):
                result = SyncActionResultOut(
                    client_action_id=item.client_action_id,
                    state="rejected",
                    code="park_forbidden",
                )
                revoked.add(
                    f"schedule:park:{item.park_id}"
                    if item.resource_type == "schedule_entry"
                    else f"work:park:{item.park_id}"
                )
                results.append(result)
                states[item.client_action_id] = result.state
                if dependent_media_id:
                    media_uploads.acknowledge_action_dependency(
                        db,
                        actor_user_id=user.id,
                        media_id=dependent_media_id,
                        device_id=batch.device_id,
                        action_id=item.client_action_id,
                    )
                continue
            existing, _canonical, payload_hash = _receipt(db, user, batch.device_id, item)
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    result = SyncActionResultOut(
                        client_action_id=item.client_action_id,
                        state="conflict",
                        code="sync_payload_conflict",
                    )
                else:
                    result = SyncActionResultOut.model_validate_json(existing.result_json)
            else:
                if not _park_allowed(db, user, item.park_id):
                    result = SyncActionResultOut(
                        client_action_id=item.client_action_id,
                        state="rejected",
                        code="park_forbidden",
                    )
                    if item.park_id is not None:
                        revoked.add(
                            f"schedule:park:{item.park_id}"
                            if item.resource_type == "schedule_entry"
                            else f"work:park:{item.park_id}"
                        )
                else:
                    try:
                        _verify_prior_dependencies(db, user, batch.device_id, item, states)
                        if media_preflight_error is not None:
                            raise media_preflight_error
                        if dependent_media_id and consumed_media is None:
                            raise HTTPException(409, "media_dependency_pending")
                        value = dispatch_action(db, user, item, consumed_media)
                        result = SyncActionResultOut(
                            client_action_id=item.client_action_id,
                            state="confirmed",
                            result=value,
                        )
                        if item.resource_type == "schedule_entry":
                            changed_scopes.add(f"schedule:park:{item.park_id}")
                        else:
                            changed_scopes.add("work")
                            if item.park_id is not None:
                                changed_scopes.add(f"work:park:{item.park_id}")
                                if item.action == "inventory_writeoff":
                                    changed_scopes.add(f"inventory:{item.park_id}")
                    except Exception as exc:
                        db.rollback()
                        state, code = _classification(exc)
                        result = SyncActionResultOut(
                            client_action_id=item.client_action_id,
                            state=state,
                            code=code,
                        )
                # Attention and conflict are nonterminal. Persist only applied or
                # irreversible rejection outcomes; otherwise a resolved conflict
                # would replay the old response forever.
                if result.state in {"confirmed", "rejected"}:
                    db.add(
                        OfflineSyncReceipt(
                            actor_user_id=user.id,
                            device_id=batch.device_id,
                            client_action_id=item.client_action_id,
                            payload_hash=payload_hash,
                            result_json=result.model_dump_json(),
                        )
                    )
                    db.commit()
            if dependent_media_id and result.state in {"confirmed", "rejected"}:
                media_uploads.acknowledge_action_dependency(
                    db,
                    actor_user_id=user.id,
                    media_id=dependent_media_id,
                    device_id=batch.device_id,
                    action_id=item.client_action_id,
                )
        results.append(result)
        states[item.client_action_id] = result.state

    for scope in changed_scopes:
        revision_store.mark_changed(scope)
    visible, inaccessible = _visible_scopes(
        db, user, set(batch.known_revisions) | changed_scopes | action_scopes
    )
    revoked.update(inaccessible)
    revisions = {scope: revision_store.current(scope) for scope in sorted(visible)}
    return SyncBatchOut(
        results=results,
        deltas={},
        revisions=revisions,
        revoked_scopes=sorted(revoked),
    )
