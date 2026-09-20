from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import Park, User, UserPark
from robopark_api.services import (
    inventory,
    inventory_stock,
    media_uploads,
    platform_settings,
    rbac,
    task_lifecycle,
    tracker_cache,
    tracker_client,
    tracker_signatures,
)
from robopark_api.services.reliable_actions import canonical_payload
from robopark_api.services.task_timeline import append_user_message
from robopark_api.services.tracker_policy import enforce_issue_scope
from robopark_api.sync_schemas import SyncActionIn, SyncActionResultOut, SyncBatchIn, SyncBatchOut
from robopark_api.task_workflow_models import MediaUploadSession, OfflineSyncReceipt


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


def _issue(db: Session, user: User, item: SyncActionIn) -> dict:
    token = platform_settings.get_tracker_token(db)
    if not token:
        raise HTTPException(503, "tracker_token_not_configured")
    try:
        issue = tracker_cache.get_issue(token=token, key=item.resource_id)
    except tracker_client.TrackerError as exc:
        raise HTTPException(503, "tracker_upstream_error") from exc
    if issue is None:
        raise HTTPException(409, "task_not_found")
    enforce_issue_scope(db, user, issue)
    return issue


def dispatch_action(db: Session, user: User, item: SyncActionIn) -> dict[str, Any]:
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
        issue = _issue(db, user, item)
        if task_lifecycle.tracker_issue_is_closed(issue):
            raise HTTPException(409, "task_already_closed")
        if item.park_id is None:
            raise HTTPException(400, "sync_park_required")
        park = db.get(Park, item.park_id)
        if park is None:
            raise HTTPException(404, "park_not_found")
        return task_lifecycle.claim(
            db,
            actor=user,
            issue_key=item.resource_id,
            park=park,
            idempotency_key=item.idempotency_key,
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
        issue = _issue(db, user, item)
        media_id = str(item.payload.get("media_id") or "")
        upload = db.scalar(
            select(MediaUploadSession).where(
                MediaUploadSession.actor_user_id == user.id,
                MediaUploadSession.media_id == media_id,
                MediaUploadSession.issue_key == item.resource_id,
                MediaUploadSession.completed.is_(True),
            )
        )
        if upload is None:
            raise HTTPException(409, "media_dependency_pending")
        context = tracker_signatures.build_signature_context(db, user, issue)
        path = media_uploads.content_path(upload)
        return task_lifecycle.submit_review(
            db,
            actor=user,
            issue_key=item.resource_id,
            defect_code=str(item.payload.get("defect_code") or ""),
            filename=upload.original_name,
            content=path.read_bytes(),
            content_type=upload.mime_type,
            comment=str(item.payload.get("comment") or "") or None,
            operator_login=context.operator_login,
            idempotency_key=item.idempotency_key,
        )
    raise HTTPException(400, "sync_action_unsupported")


def _classification(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, HTTPException):
        code = str(exc.detail)
        if exc.status_code == 409:
            return "conflict", code
        if exc.status_code >= 500 or exc.status_code in {408, 425, 429}:
            return "attention", code
        return "rejected", code
    if isinstance(exc, inventory_stock.InventoryConflict):
        return "conflict", str(exc)
    if isinstance(exc, PermissionError):
        return "rejected", str(exc) or "forbidden"
    if isinstance(exc, (RuntimeError, ConnectionError, TimeoutError)):
        return "attention", str(exc) or "temporary_failure"
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
            scope.startswith("work:park:") or scope.startswith("inventory:")
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
    action_scopes: set[str] = {"work"} if ordered else set()
    revoked: set[str] = set()
    for item in ordered:
        if item.park_id is not None:
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
            results.append(result)
            states[item.client_action_id] = result.state
            continue
        if not _park_allowed(db, user, item.park_id):
            result = SyncActionResultOut(
                client_action_id=item.client_action_id,
                state="rejected",
                code="park_forbidden",
            )
            if item.park_id is not None:
                revoked.add(f"work:park:{item.park_id}")
        else:
            try:
                value = dispatch_action(db, user, item)
                result = SyncActionResultOut(
                    client_action_id=item.client_action_id,
                    state="confirmed",
                    result=value,
                )
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
        # Temporary upstream failures must remain replayable. Persist only
        # terminal outcomes; otherwise one 503 becomes permanent.
        if result.state != "attention":
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
