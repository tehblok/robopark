"""Bounded business tools backed by the same domain rules as the HTTP API."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select

from robopark_api.ai_models import AIScript
from robopark_api.models import User
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import (
    rbac,
    reliable_actions,
    repair_fields,
    schedules,
    task_lifecycle,
    tracker_claims,
    tracker_client,
    tracker_signatures,
    tracker_submissions,
)
from robopark_api.services.ai import (
    automations,
    issue_context,
    policy,
    runtime,
    script_domain,
    system_api,
)
from robopark_api.services.database_locks import database_idempotency_lock
from robopark_api.services.tracker_policy import ensure_action_allowed
from robopark_api.task_workflow_models import TaskMessage, TaskReview

_ISSUE_KEY = r"^[A-Z][A-Z0-9_]*-\d+$"
_SCRIPT_ID = r"^[0-9a-fA-F-]{36}$"
_TASK_NAMES = frozenset({"task_get", "task_claim", "task_comment", "task_handoff", "task_close"})
_SCRIPT_NAMES = frozenset(
    {
        "script_list",
        "script_get",
        "script_create",
        "script_update",
        "script_enable",
        "script_disable",
        "script_test",
        "script_run",
        "script_delete",
    }
)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ScriptList(_Input):
    offset: int = Field(default=0, ge=0, le=1000000)


class _Task(_Input):
    key: str = Field(pattern=_ISSUE_KEY, max_length=128)


class _Comment(_Task):
    text: str = Field(min_length=1, max_length=4000)


class _Handoff(_Task):
    assignee: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=4000)
    done: str = Field(default="", max_length=4000)
    remaining: str = Field(default="", max_length=4000)
    obstacles: str = Field(default="", max_length=4000)


class _Robot(_Input):
    vin: str = Field(min_length=1, max_length=64)


class _Script(_Input):
    script_id: str = Field(pattern=_SCRIPT_ID, max_length=36)


class _ScriptRevision(_Script):
    revision: int = Field(ge=1)


class _ScriptCreate(_Input):
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=32000)


class _ScriptUpdate(_ScriptRevision):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source: str | None = Field(default=None, min_length=1, max_length=32000)

    @model_validator(mode="after")
    def nonempty(self):
        if self.name is None and self.source is None:
            raise ValueError("empty update")
        return self


class _ScriptInput(_ScriptRevision):
    input: Any = Field(default_factory=dict)


_MODELS: dict[str, type[BaseModel]] = {
    "task_get": _Task,
    "task_claim": _Task,
    "task_comment": _Comment,
    "task_handoff": _Handoff,
    "task_close": _Task,
    "robot_check": _Robot,
    "script_list": _ScriptList,
    "script_get": _Script,
    "script_create": _ScriptCreate,
    "script_update": _ScriptUpdate,
    "script_enable": _ScriptRevision,
    "script_disable": _ScriptRevision,
    "script_test": _ScriptInput,
    "script_run": _ScriptInput,
    "script_delete": _ScriptRevision,
    "system_api": system_api.SystemAPIInput,
}

_DESCRIPTIONS = {
    "task_get": "Показать задачу, этап ремонта и доступные поля отчёта.",
    "task_claim": "Взять доступную задачу в работу механика.",
    "task_comment": "Добавить участникам задачи комментарий.",
    "task_handoff": "Передать свою активную задачу другому механику парка.",
    "task_close": "Принять последнюю отправку на проверку и закрыть задачу.",
    "robot_check": "Прочитать безопасный диагностический снимок робота.",
    "script_list": "Список локальных sandbox-скриптов.",
    "script_get": "Показать локальный sandbox-скрипт.",
    "script_create": "Создать выключенный локальный sandbox-скрипт.",
    "script_update": "Изменить исходник или имя sandbox-скрипта.",
    "script_enable": "Включить протестированную текущую ревизию скрипта.",
    "script_disable": "Выключить скрипт.",
    "script_test": "Запустить текущую ревизию в sandbox и отметить её протестированной.",
    "script_run": "Запустить включённую протестированную ревизию в sandbox.",
    "script_delete": "Удалить sandbox-скрипт.",
    "system_api": "Найти, описать или вызвать операцию локального API с текущими правами.",
}


def _parse(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    model = _MODELS.get(name)
    if model is None:
        raise HTTPException(400, "ai_tool_unknown")
    try:
        if not isinstance(arguments, dict):
            raise TypeError
        value = model.model_validate(arguments)
    except (TypeError, ValidationError):
        raise HTTPException(422, "ai_tool_arguments_invalid") from None
    result = value.model_dump(mode="json")
    automations.bounded_json(result, limit=65536)
    return result


def _can_robot(db, user: User) -> bool:
    return rbac.has_permission(db, user, rbac.PERMISSION_NAV_EMERGENCY)


def _allowed(db, user: User, name: str) -> bool:
    role = rbac.role_slug(user)
    if name == "system_api":
        return True
    if name == "task_get":
        return rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_READ)
    if name == "task_claim":
        return role == rbac.RoleSlug.MECHANIC and rbac.has_permission(
            db, user, rbac.PERMISSION_TRACKER_WRITE
        )
    if name == "task_handoff":
        return role == rbac.RoleSlug.MECHANIC and rbac.has_permission(
            db, user, rbac.PERMISSION_TRACKER_WRITE
        )
    if name == "task_close":
        return role in {
            rbac.RoleSlug.OPERATOR,
            rbac.RoleSlug.ADMIN,
            rbac.RoleSlug.ROYAL,
        } and rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_WRITE)
    if name == "task_comment":
        return rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_WRITE)
    if name == "robot_check":
        return _can_robot(db, user)
    if name in _SCRIPT_NAMES:
        return policy.can_manage(db, user)
    return False


def _authorize_name(db, user: User, name: str) -> None:
    policy.staff(db, user)
    if not _allowed(db, user, name):
        raise HTTPException(403, "ai_tool_forbidden")


def readonly(name: str, arguments: dict[str, Any]) -> bool:
    if name in {"task_get", "robot_check", "script_list", "script_get"}:
        return True
    return name == "system_api" and system_api.is_readonly(arguments)


def catalog(db, user: User) -> list[dict[str, Any]]:
    policy.staff(db, user)
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": _DESCRIPTIONS[name],
                "parameters": system_api.schema()
                if name == "system_api"
                else model.model_json_schema(),
            },
        }
        for name, model in _MODELS.items()
        if _allowed(db, user, name)
    ]


def _token(db) -> str:
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(503, "tracker_token_not_configured")
    return token


def _fresh_issue(db, user: User, park_id: int | None, key: str) -> dict[str, Any]:
    if park_id is None:
        raise HTTPException(403, "ai_park_forbidden")
    policy.park(db, user, park_id)
    if task_lifecycle.is_hidden(db, key):
        raise HTTPException(404, "ai_issue_unavailable")
    try:
        issue = tracker_client.get_issue(token=_token(db), key=key)
    except tracker_client.TrackerError:
        raise HTTPException(503, "ai_issue_unavailable") from None
    return issue_context.authorize_snapshot(db, user, issue, key, park_id)


def _review(db, key: str) -> TaskReview | None:
    return db.scalar(
        select(TaskReview)
        .where(TaskReview.issue_key == key)
        .order_by(TaskReview.created_at.desc(), TaskReview.id.desc())
        .execution_options(populate_existing=True)
    )


def _issue_expected(db, issue: dict[str, Any]) -> dict[str, Any]:
    key = str(issue["key"])
    review = _review(db, key)
    assignee = tracker_claims.local_assignee(db, issue) or issue.get("assignee") or {}
    return {
        "issue": {
            "key": key,
            "updated": str(issue.get("updated") or ""),
            "status": str(issue.get("status") or ""),
            "status_key": str(issue.get("status_key") or ""),
            "assignee": str(assignee.get("login") or ""),
        },
        "review": (
            {"id": review.id, "state": review.state, "updated_at": review.updated_at}
            if review is not None
            else None
        ),
    }


def _script_expected(row: AIScript | None) -> dict[str, Any]:
    return (
        {"script": None}
        if row is None
        else {
            "script": {
                "id": row.id,
                "revision": row.revision,
                "enabled": row.enabled,
                "tested_revision": row.tested_revision,
            }
        }
    )


def _script_view(row: AIScript, *, source: bool = False) -> dict[str, Any]:
    value = {
        "id": row.id,
        "name": row.name,
        "enabled": row.enabled,
        "revision": row.revision,
        "tested_revision": row.tested_revision,
        "updated_at": datetime.fromtimestamp(row.updated_at, UTC).isoformat(),
    }
    if source:
        value["source_preview"] = row.source[:4000]
        value["source_complete"] = len(row.source) <= 4000
    return value


def prepare(
    db, user: User, park_id: int | None, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    if name == "system_api":
        _authorize_name(db, user, name)
        return system_api.prepare(db, user, arguments, park_id=park_id)
    args = _parse(name, arguments)
    _authorize_name(db, user, name)
    expected: dict[str, Any] = {}
    if name in _TASK_NAMES:
        issue = _fresh_issue(db, user, park_id, args["key"])
        if name != "task_get":
            action = (
                "close" if name == "task_close" else "assign" if name == "task_claim" else "comment"
            )
            ensure_action_allowed(db, user, issue, action)
        claim = tracker_claims.get_claim(db, args["key"])
        if name == "task_claim":
            if task_lifecycle.tracker_issue_is_closed(issue):
                raise HTTPException(409, "task_already_closed")
            task_lifecycle.issue_park(db, issue)
        elif name == "task_comment" and rbac.role_slug(user) == rbac.RoleSlug.MECHANIC:
            if not tracker_claims.mechanic_owns_issue(db, user, issue):
                raise HTTPException(409, "tracker_issue_claim_required")
        elif name == "task_handoff":
            if claim is None or claim.owner_user_id != user.id or claim.state != "active":
                raise HTTPException(403, "task_handoff_owner_required")
        elif name == "task_close":
            review = _review(db, args["key"])
            if review is None or review.state != "pending":
                raise HTTPException(409, "task_review_not_pending")
        expected = _issue_expected(db, issue)
    elif name == "robot_check":
        from robopark_api.routers.emergency import authorize_emergency_vin

        policy.park(db, user, park_id)
        args["vin"] = authorize_emergency_vin(db, user, args["vin"], park_id=park_id)
    elif name == "script_create":
        policy.park(db, user, park_id)
        automations.validate_script(args["source"])
        expected = {"script": None}
    elif name == "script_list":
        policy.park(db, user, park_id)
    else:
        policy.park(db, user, park_id)
        row = policy.get_row(db, AIScript, args["script_id"])
        expected = _script_expected(row)
        if "revision" in args and row.revision != args["revision"]:
            raise HTTPException(409, "ai_revision_conflict")
        if name == "script_update":
            automations.validate_script(args.get("source") or row.source)
        if name in {"script_enable", "script_run"} and row.tested_revision != row.revision:
            raise HTTPException(409, "ai_script_test_required")
        if name == "script_run" and not row.enabled:
            raise HTTPException(409, "ai_dependency_disabled")
    preview = _preview(db, name, args)
    return {
        "arguments": args,
        "preview": preview,
        "confirmation_required": name in {"task_close", "script_delete"},
        "expected": expected,
    }


def _preview(db, name: str, args: dict[str, Any]) -> str:
    key = args.get("key")
    if name == "task_get":
        return f"Открыть задачу {key}"
    if name == "task_claim":
        return f"Взять задачу {key} в работу"
    if name == "task_comment":
        return f"Добавить комментарий к {key}: {args['text'][:240]}"
    if name == "task_handoff":
        return f"Передать {key} механику {args['assignee']}: {args['reason'][:180]}"
    if name == "task_close":
        return f"Принять проверку и закрыть задачу {key}"
    if name == "robot_check":
        return f"Проверить робота {args['vin']}"
    if name == "script_list":
        return "Показать локальные sandbox-скрипты"
    if name == "script_create":
        return f"Создать выключенный скрипт «{args['name']}»"
    row = db.get(AIScript, args["script_id"])
    label = row.name if row is not None else args["script_id"]
    verbs = {
        "script_get": "Открыть",
        "script_update": "Изменить",
        "script_enable": "Включить",
        "script_disable": "Выключить",
        "script_test": "Протестировать",
        "script_run": "Запустить",
        "script_delete": "Удалить",
    }
    revision = f" (ревизия {args['revision']})" if "revision" in args else ""
    return f"{verbs[name]} скрипт «{label}»{revision}"


def authorize_view(
    db,
    user: User,
    park_id: int | None,
    name: str,
    arguments: dict[str, Any],
    scope_cache: dict[tuple, Any] | None = None,
    expected: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    if name == "system_api":
        _authorize_name(db, user, name)
        system_api.authorize_view(
            db,
            user,
            arguments,
            expected=expected,
            result=result,
            scope_cache=scope_cache,
        )
        return
    args = _parse(name, arguments)
    _authorize_name(db, user, name)
    if name in _TASK_NAMES:
        cache_key = ("tracker_issue", user.id, args["key"], park_id)
        if scope_cache is None or cache_key not in scope_cache:
            system_api.claim_remote_guard(scope_cache if scope_cache is not None else {})
            issue = _fresh_issue(db, user, park_id, args["key"])
            if scope_cache is not None:
                scope_cache[cache_key] = issue
    elif name == "robot_check":
        from robopark_api.routers.emergency import authorize_emergency_vin
        from robopark_api.services import sdc_inventory

        policy.park(db, user, park_id)
        robot = sdc_inventory.robot_name(args["vin"])
        cache_key = ("emergency_robot", user.id, robot, park_id)
        if scope_cache is None or cache_key not in scope_cache:
            system_api.claim_remote_guard(scope_cache if scope_cache is not None else {})
            vin = authorize_emergency_vin(db, user, args["vin"], park_id=park_id)
            if scope_cache is not None:
                scope_cache[cache_key] = vin
    elif name in _SCRIPT_NAMES:
        policy.manager(db, user)
        policy.park(db, user, park_id)


def _same_expected(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    if actual != expected:
        raise HTTPException(409, "ai_action_changed")


def _task_result(db, user: User, issue: dict[str, Any]) -> dict[str, Any]:
    key = str(issue["key"])
    components = tracker_client.list_queue_components(
        token=_token(db), queue=str(issue.get("queue") or "")
    )
    value = {
        field: issue.get(field)
        for field in (
            "key",
            "summary",
            "status",
            "status_key",
            "queue",
            "robot",
            "priority",
            "components",
            "component_ids",
            "defect_code",
            "solution_method",
        )
    }
    value["description"] = str(issue.get("description") or "")[:2000]
    value["workflow"] = task_lifecycle.workflow(db, issue_key=key, viewer=user, issue=issue)
    value["repair_options"] = repair_fields.options(issue, components)
    value["review"] = _issue_expected(db, issue)["review"]
    value["review_photo_required"] = True
    return value


def _comment(db, user: User, issue: dict[str, Any], text: str, key: str) -> dict[str, Any]:
    if rbac.role_slug(user) == rbac.RoleSlug.MECHANIC and not tracker_claims.mechanic_owns_issue(
        db, user, issue
    ):
        raise HTTPException(409, "tracker_issue_claim_required")
    payload: dict[str, Any] = {"text": text.strip()}
    if rbac.role_slug(user) == rbac.RoleSlug.OPERATOR:
        context = tracker_signatures.build_signature_context(db, user, issue)
        claim = tracker_claims.get_claim(db, str(issue["key"]))
        recipients = schedules.eligible_recipients(
            schedules.RoutingEvent(
                db,
                "operator_comment",
                context.park_id,
                {claim.owner_user_id} if claim is not None else None,
            ),
            datetime.now(UTC),
        )
        payload["_notification_intent"] = {
            "event_type": "operator_comment",
            "park_id": context.park_id,
            "recipient_user_ids": [recipient.id for recipient in recipients],
            "protected_text": f"Оператор прокомментировал задачу {issue['key']}",
        }
    begun = reliable_actions.begin_action(
        db,
        actor=user,
        resource_type="tracker_issue",
        resource_id=str(issue["key"]),
        action="comment",
        idempotency_key=key,
        payload=payload,
    )
    if begun.created:
        now = begun.row.created_at
        db.add(
            TaskMessage(
                id=begun.row.id,
                issue_key=str(issue["key"]),
                kind="user",
                author_user_id=user.id,
                author_name=user.username,
                text=text.strip(),
                action_id=begun.row.id,
                sync_state="pending",
                visibility="participants",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    workflow = task_lifecycle.workflow(db, issue_key=str(issue["key"]), viewer=user, issue=issue)
    return {
        "key": issue["key"],
        "action": "comment",
        "sync_state": workflow["sync_state"],
        "workflow": workflow,
    }


def _execute(
    db,
    settings,
    user: User,
    park_id: int | None,
    name: str,
    arguments: dict[str, Any],
    *,
    idempotency_key: str,
    expected: dict[str, Any],
) -> dict[str, Any]:
    prepared = prepare(db, user, park_id, name, arguments)
    args = prepared["arguments"]
    _same_expected(prepared["expected"], expected)
    if name == "system_api":
        return system_api.execute(
            db,
            settings,
            user,
            args,
            expected=expected,
            idempotency_key=idempotency_key,
            park_id=park_id,
        )
    if name == "task_get":
        return _task_result(db, user, _fresh_issue(db, user, park_id, args["key"]))
    if name == "robot_check":
        from robopark_api.routers.emergency import emergency_snapshot_for_user

        snapshot = emergency_snapshot_for_user(args["vin"], user, db).model_dump(mode="json")
        return {key: snapshot[key] for key in snapshot if key not in {"lat", "lon"}}
    if name in _TASK_NAMES:
        with tracker_submissions.task_mutation_lease(db, args["key"]):
            # A claim may have changed while this process waited for the lease.
            # Force all domain helpers below to observe the post-lease database state.
            db.expire_all()
            issue = _fresh_issue(db, user, park_id, args["key"])
            _same_expected(_issue_expected(db, issue), expected)
            if name == "task_claim":
                return task_lifecycle.claim(
                    db,
                    actor=user,
                    issue_key=args["key"],
                    park=task_lifecycle.issue_park(db, issue),
                    idempotency_key=idempotency_key,
                    issue=issue,
                    component_ids=None,
                    component_options=[],
                )
            if name == "task_comment":
                return _comment(db, user, issue, args["text"], idempotency_key)
            if name == "task_handoff":
                return task_lifecycle.handoff(
                    db,
                    actor=user,
                    issue_key=args["key"],
                    assignee=args["assignee"],
                    reason=args["reason"],
                    done=args["done"],
                    remaining=args["remaining"],
                    obstacles=args["obstacles"],
                    idempotency_key=idempotency_key,
                )
            if name == "task_close":
                return task_lifecycle.approve_review(
                    db, actor=user, issue_key=args["key"], idempotency_key=idempotency_key
                )
    policy.manager(db, user)
    with database_idempotency_lock(db, "ai-controls"):
        if name == "script_list":
            total = db.scalar(select(func.count()).select_from(AIScript)) or 0
            offset = args["offset"]
            rows = db.scalars(
                select(AIScript).order_by(AIScript.name).offset(offset).limit(25)
            ).all()
            next_offset = offset + len(rows)
            return {
                "items": [_script_view(row) for row in rows],
                "total": total,
                "next_offset": next_offset if next_offset < total else None,
            }
        if name == "script_create":
            row = AIScript(name=args["name"], source=args["source"])
            db.add(row)
            db.commit()
            db.refresh(row)
            policy.changed(db, user, "script_created", row.id)
            return _script_view(row)
        row = policy.get_row(db, AIScript, args["script_id"])
        _same_expected(_script_expected(row), expected)
        if name == "script_get":
            return _script_view(row, source=True)
        if name == "script_update":
            changes = {
                key: value
                for key, value in args.items()
                if key in {"name", "source"} and value is not None
            }
            changes.update(enabled=False, tested_revision=None)
            script_domain.disable_dependencies(db, row.id)
            row = policy.cas(db, row, args["revision"], changes)
            policy.changed(db, user, "script_updated", row.id)
            return _script_view(row)
        if name in {"script_enable", "script_disable"}:
            enabled = name == "script_enable"
            if enabled and row.tested_revision != row.revision:
                raise HTTPException(409, "ai_script_test_required")
            changes = {"enabled": enabled}
            if row.tested_revision == row.revision:
                changes["tested_revision"] = row.revision + 1
            if not enabled:
                script_domain.disable_dependencies(db, row.id)
            row = policy.cas(db, row, args["revision"], changes)
            policy.changed(db, user, "script_updated", row.id)
            return _script_view(row)
        if name == "script_delete":
            script_domain.disable_dependencies(db, row.id)
            db.delete(row)
            db.commit()
            policy.changed(db, user, "script_deleted", args["script_id"])
            return {"deleted": True, "script_id": args["script_id"]}
        policy.available(db, settings)
        source, revision = row.source, row.revision
        if name == "script_run" and (not row.enabled or row.tested_revision != row.revision):
            raise HTTPException(409, "ai_script_test_required")
        db.commit()
    try:
        result = runtime.broker(
            settings, "/sandbox", {"source": source, "input": args["input"]}, timeout=30
        )
        automations.bounded_json(result)
    except runtime.RuntimeFailure:
        raise HTTPException(503, "ai_sandbox_failed") from None
    if name == "script_test":
        with database_idempotency_lock(db, "ai-controls"):
            policy.manager(db, user)
            policy.park(db, user, park_id)
            policy.available(db, settings)
            row = policy.get_row(db, AIScript, args["script_id"])
            if row.revision != revision:
                raise HTTPException(409, "ai_script_changed")
            row.tested_revision = revision
            db.commit()
        policy.changed(db, user, "script_tested", row.id)
    else:
        policy.manager(db, user)
        policy.park(db, user, park_id)
        policy.available(db, settings)
        row = policy.get_row(db, AIScript, args["script_id"])
        if row.revision != revision or not row.enabled or row.tested_revision != revision:
            raise HTTPException(409, "ai_script_changed")
    return {"script_id": args["script_id"], "revision": revision, "output": result.get("output")}


def _bounded_result(value: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise HTTPException(502, "ai_tool_result_invalid") from None
    if len(encoded.encode()) <= 8000:
        return value

    def trim(item, depth=0):
        if depth >= 6:
            return None
        if isinstance(item, str):
            return item[:500]
        if isinstance(item, list):
            return [trim(child, depth + 1) for child in item[:10]]
        if isinstance(item, dict):
            return {str(key)[:80]: trim(child, depth + 1) for key, child in list(item.items())[:30]}
        return item

    reduced = trim(value)
    reduced["result_truncated"] = True
    encoded = json.dumps(reduced, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 8000:
        raise HTTPException(502, "ai_tool_result_too_large")
    return reduced


def execute(
    db,
    settings,
    user: User,
    park_id: int | None,
    name: str,
    arguments: dict[str, Any],
    *,
    idempotency_key: str,
    expected: dict[str, Any],
) -> dict[str, Any]:
    return _bounded_result(
        _execute(
            db,
            settings,
            user,
            park_id,
            name,
            arguments,
            idempotency_key=idempotency_key,
            expected=expected,
        )
    )
