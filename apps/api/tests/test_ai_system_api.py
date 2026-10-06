"""Generic assistant API access stays inside the real local router contract."""

import concurrent.futures
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from robopark_api.models import AccessStatus, UserPark
from robopark_api.services.ai import system_api, tool_actions, tool_domain


def _operation(entries, method, path):
    return next(item for item in entries if item["method"] == method and item["path"] == path)


def test_registry_classifies_every_openapi_operation_and_blocks_sensitive_families():
    entries = system_api.registry()

    assert len(entries) > 250
    assert all(item["classification"] in {"read", "mutation", "interactive"} for item in entries)
    assert all(item["reason"] for item in entries if item["classification"] == "interactive")
    assert _operation(entries, "GET", "/parks")["classification"] == "read"
    assert _operation(entries, "GET", "/admin/diagnostic-rules")["classification"] == "read"
    assert _operation(entries, "GET", "/sdc-inventory/robots/{robot}")["classification"] == "read"
    assert _operation(entries, "POST", "/auth/login")["classification"] == "interactive"
    assert _operation(entries, "GET", "/admin/terminal/sessions")["classification"] == "interactive"
    for path in (
        "/inventory/export",
        "/inventory/components/{component_id}/photo",
        "/reports/{report_id}/attachments/{attachment_id}",
        "/tracker/issues/{key}/attachments/{attachment_id}/content",
    ):
        assert _operation(entries, "GET", path)["classification"] == "interactive"
    assert _operation(entries, "GET", "/push/inbox")["classification"] == "interactive"
    assert all(
        item["classification"] == "interactive"
        for item in entries
        if item["path"].startswith("/ai/")
    )


def test_tool_catalog_adds_one_bounded_multiplex_schema(db_session, seed_admin):
    tools = tool_domain.catalog(db_session, seed_admin)
    definitions = {item["function"]["name"]: item["function"] for item in tools}

    assert "system_api" in definitions
    assert len(tools) <= 16
    assert len(str(definitions["system_api"]["parameters"]).encode()) < 12_000


def test_old_discovery_pairs_are_compacted_before_next_model_turn():
    messages = [{"role": "system", "content": "policy"}, {"role": "user", "content": "task"}]
    for index in range(7):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [{"id": str(index), "function": {"name": "system_api"}}],
                },
                {"role": "tool", "tool_call_id": str(index), "content": "x" * 1000},
            ]
        )

    compacted = tool_actions._compact_messages(messages)

    assert compacted[:2] == messages[:2]
    assert [item["tool_call_id"] for item in compacted if item["role"] == "tool"] == [
        "3",
        "4",
        "5",
        "6",
    ]


def test_history_authorizes_every_receipt_even_when_tool_arguments_repeat(monkeypatch):
    receipts = [
        SimpleNamespace(
            tool="system_api", arguments={"action": "call"}, expected={"proof": index}, result={}
        )
        for index in (1, 2)
    ]
    checked = []
    monkeypatch.setattr(tool_actions, "rows", lambda *_args: receipts)

    def authorize(*_args, expected, **_kwargs):
        checked.append(expected["proof"])
        if expected["proof"] == 2:
            raise HTTPException(409, "revoked")

    monkeypatch.setattr(tool_domain, "authorize_view", authorize)
    with pytest.raises(HTTPException, match="revoked"):
        tool_actions.authorize_views(object(), object(), SimpleNamespace(id="job", park_id=1))
    assert checked == [1, 2]


def test_arguments_forbid_urls_unknown_headers_and_secrets():
    operation_id = _operation(system_api.registry(), "GET", "/parks")["operation_id"]
    with pytest.raises(HTTPException, match="ai_tool_arguments_invalid"):
        system_api.parse({"action": "call", "operation_id": operation_id, "url": "/parks"})
    with pytest.raises(HTTPException, match="ai_system_api_header_forbidden"):
        system_api.parse(
            {
                "action": "call",
                "operation_id": operation_id,
                "headers": {"Authorization": "Bearer forged"},
            }
        )
    with pytest.raises(HTTPException, match="ai_system_api_secret_forbidden"):
        system_api.parse(
            {
                "action": "call",
                "operation_id": operation_id,
                "body": {"nested": {"token": "must-not-enter-action-row"}},
            }
        )
    with pytest.raises(HTTPException, match="ai_system_api_secret_forbidden"):
        system_api.parse(
            {
                "action": "call",
                "operation_id": operation_id,
                "body": {"subscription": {"p256dh": "secret"}},
            }
        )
    parsed = system_api.parse(
        {
            "action": "call",
            "operation_id": operation_id,
            "query": {"issue_key": "SDCFLEETOPS-1"},
        }
    )
    assert parsed["query"]["issue_key"] == "SDCFLEETOPS-1"
    with pytest.raises(HTTPException, match="ai_system_api_secret_forbidden"):
        system_api.parse(
            {
                "action": "call",
                "operation_id": operation_id,
                "query": {"api_key": "must-not-enter-action-row"},
            }
        )


def test_list_is_paged_and_describe_exposes_route_contract(db_session, seed_admin):
    first = system_api.prepare(db_session, seed_admin, {"action": "list", "limit": 7})
    result = system_api.execute(
        db_session, object(), seed_admin, first["arguments"], expected=first["expected"]
    )
    assert len(result["items"]) == 7
    assert result["next_offset"] == 7

    filtered = system_api.prepare(
        db_session,
        seed_admin,
        {"action": "list", "search": "sdc-inventory", "category": "read"},
    )
    filtered_result = system_api.execute(
        db_session,
        object(),
        seed_admin,
        filtered["arguments"],
        expected=filtered["expected"],
    )
    assert {item["path"] for item in filtered_result["items"]} == {
        "/sdc-inventory/robots/{robot}",
        "/sdc-inventory/robots/{robot}/installations",
    }

    operation = _operation(system_api.registry(), "GET", "/parks")
    described = system_api.prepare(
        db_session,
        seed_admin,
        {"action": "describe", "operation_id": operation["operation_id"]},
    )
    value = system_api.execute(
        db_session, object(), seed_admin, described["arguments"], expected=described["expected"]
    )
    assert value["method"] == "GET"
    assert value["path"] == "/parks"
    assert "responses" in value

    create = _operation(system_api.registry(), "POST", "/parks")
    create_description = system_api.prepare(
        db_session,
        seed_admin,
        {"action": "describe", "operation_id": create["operation_id"]},
    )
    create_value = system_api.execute(
        db_session,
        object(),
        seed_admin,
        create_description["arguments"],
        expected=create_description["expected"],
    )
    assert "properties" in str(create_value["requestBody"])
    assert "$ref" not in str(create_value["requestBody"])


def test_real_get_uses_router_rbac_and_validation(
    db_session, db_engine, seed_admin, seed_mechanic, test_settings
):
    parks = _operation(system_api.registry(), "GET", "/parks")
    prepared = system_api.prepare(
        db_session,
        seed_admin,
        {"action": "call", "operation_id": parks["operation_id"]},
    )
    result = system_api.execute(
        db_session,
        test_settings,
        seed_admin,
        prepared["arguments"],
        expected=prepared["expected"],
    )
    assert result["status_code"] == 200
    assert isinstance(result["body"], list)

    rules = _operation(system_api.registry(), "GET", "/admin/diagnostic-rules")
    denied = system_api.prepare(
        db_session,
        seed_mechanic,
        {"action": "call", "operation_id": rules["operation_id"]},
    )
    with pytest.raises(HTTPException) as forbidden:
        system_api.execute(
            db_session,
            test_settings,
            seed_mechanic,
            denied["arguments"],
            expected=denied["expected"],
        )
    assert forbidden.value.status_code == 403

    invalid = _operation(system_api.registry(), "GET", "/inventory/parks/{park_id}/counts")
    bad = system_api.prepare(
        db_session,
        seed_admin,
        {
            "action": "call",
            "operation_id": invalid["operation_id"],
            "path": {"park_id": "not-an-int"},
        },
    )
    with pytest.raises(HTTPException) as validation:
        system_api.execute(
            db_session,
            test_settings,
            seed_admin,
            bad["arguments"],
            expected=bad["expected"],
        )
    assert validation.value.status_code == 422


def test_parallel_calls_keep_actor_scope_isolated(
    db_engine, seed_admin, seed_mechanic, test_settings
):
    operation = _operation(system_api.registry(), "GET", "/parks")
    factory = sessionmaker(bind=db_engine, future=True)

    def invoke(user_id):
        with factory() as db:
            user = db.get(type(seed_admin), user_id)
            prepared = system_api.prepare(
                db, user, {"action": "call", "operation_id": operation["operation_id"]}
            )
            return system_api.execute(
                db, test_settings, user, prepared["arguments"], expected=prepared["expected"]
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        admin_result, mechanic_result = list(pool.map(invoke, [seed_admin.id, seed_mechanic.id]))

    assert len(admin_result["body"]) >= len(mechanic_result["body"])
    assert {park["id"] for park in mechanic_result["body"]}.issubset(
        {park["id"] for park in admin_result["body"]}
    )


def test_mutation_policy_uses_real_router_rbac_and_preserves_confirmation(
    db_session,
    seed_pending_operator,
    seed_park_with_tracker,
    seed_mechanic,
    test_settings,
):
    seed_pending_operator.access_status = AccessStatus.approved.value
    db_session.commit()
    operator_operation = _operation(system_api.registry(), "POST", "/operator/park-requests")
    operator_arguments = {
        "action": "call",
        "operation_id": operator_operation["operation_id"],
        "body": {"park_id": seed_park_with_tracker.id},
    }
    prepared = system_api.prepare(db_session, seed_pending_operator, operator_arguments)
    assert prepared["confirmation_required"] is True
    result = system_api.execute(
        db_session,
        test_settings,
        seed_pending_operator,
        prepared["arguments"],
        expected=prepared["expected"],
    )
    assert result["status_code"] == 201

    admin_operation = _operation(system_api.registry(), "POST", "/parks")
    admin_arguments = {
        "action": "call",
        "operation_id": admin_operation["operation_id"],
        "body": {"name": "Forbidden", "tag": "Forbidden"},
    }
    admin_prepared = system_api.prepare(db_session, seed_pending_operator, admin_arguments)
    with pytest.raises(HTTPException) as forbidden:
        system_api.execute(
            db_session,
            test_settings,
            seed_pending_operator,
            admin_prepared["arguments"],
            expected=admin_prepared["expected"],
        )
    assert forbidden.value.status_code == 403

    seed_pending_operator.role_id = seed_mechanic.role_id
    db_session.commit()
    with pytest.raises(HTTPException, match="ai_action_changed"):
        system_api.authorize_view(
            db_session,
            seed_pending_operator,
            prepared["arguments"],
            expected=prepared["expected"],
            result=result,
        )
    with pytest.raises(HTTPException, match="ai_action_changed"):
        system_api.execute(
            db_session,
            test_settings,
            seed_pending_operator,
            prepared["arguments"],
            expected=prepared["expected"],
        )

    seed_pending_operator.is_active = False
    db_session.commit()
    with pytest.raises(HTTPException, match="ai_system_api_forbidden"):
        system_api.authorize_view(
            db_session,
            seed_pending_operator,
            prepared["arguments"],
            expected=prepared["expected"],
            result=result,
        )


def test_mechanic_tasks_history_replays_safe_router_and_closes_after_park_revocation(
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    test_settings,
    monkeypatch,
):
    monkeypatch.setattr(
        "robopark_api.services.platform_settings.get_tracker_token", lambda *_args: "token"
    )
    monkeypatch.setattr(
        "robopark_api.services.tracker_cache.fetch_park_blockers", lambda **_kwargs: []
    )
    operation = _operation(system_api.registry(), "GET", "/mechanic/tasks")
    prepared = system_api.prepare(
        db_session,
        seed_mechanic,
        {"action": "call", "operation_id": operation["operation_id"]},
    )
    assert prepared["expected"]["history_guard"]["family"] == "router_replay"
    result = system_api.execute(
        db_session,
        test_settings,
        seed_mechanic,
        prepared["arguments"],
        expected=prepared["expected"],
    )
    assert result["status_code"] == 200
    system_api.authorize_view(
        db_session,
        seed_mechanic,
        prepared["arguments"],
        expected=prepared["expected"],
        result=result,
        scope_cache={},
    )

    membership = db_session.get(UserPark, (seed_mechanic.id, seed_park_with_tracker.id))
    assert membership is not None
    db_session.delete(membership)
    db_session.commit()
    with pytest.raises(HTTPException, match="ai_action_changed"):
        system_api.authorize_view(
            db_session,
            seed_mechanic,
            prepared["arguments"],
            expected=prepared["expected"],
            result=result,
            scope_cache={},
        )


def test_real_mutation_preserves_stale_if_match_status(db_session, seed_admin, test_settings):
    operation = _operation(system_api.registry(), "PUT", "/admin/diagnostic-rules/reorder")
    prepared = system_api.prepare(
        db_session,
        seed_admin,
        {
            "action": "call",
            "operation_id": operation["operation_id"],
            "body": {"ids": []},
            "headers": {"If-Match": '"stale"'},
        },
    )

    with pytest.raises(HTTPException) as stale:
        system_api.execute(
            db_session,
            test_settings,
            seed_admin,
            prepared["arguments"],
            expected=prepared["expected"],
        )

    assert stale.value.status_code == 409
    assert stale.value.detail == "diagnostic_rules_changed"


def test_path_values_cannot_escape_selected_route(db_session, seed_admin):
    operation = _operation(system_api.registry(), "GET", "/inventory/parks/{park_id}/counts")
    for value in ("../parks", "%2e%2e%2fparks", "1/../../parks"):
        with pytest.raises(HTTPException, match="ai_system_api_path_invalid"):
            system_api.prepare(
                db_session,
                seed_admin,
                {
                    "action": "call",
                    "operation_id": operation["operation_id"],
                    "path": {"park_id": value},
                },
            )

    sdc = _operation(system_api.registry(), "GET", "/sdc-inventory/robots/{robot}")
    prepared = system_api.prepare(
        db_session,
        seed_admin,
        {
            "action": "call",
            "operation_id": sdc["operation_id"],
            "path": {"robot": "447"},
            "query": {"park_id": "not-an-int"},
        },
    )
    assert prepared["expected"]["history_guard"] is None


def test_read_history_reauthorizes_scope_without_rejecting_volatile_fields(
    db_session, seed_admin, monkeypatch
):
    operation = _operation(system_api.registry(), "GET", "/parks")
    prepared = system_api.prepare(
        db_session,
        seed_admin,
        {"action": "call", "operation_id": operation["operation_id"]},
    )
    monkeypatch.setattr(system_api, "_call", lambda *args, **kwargs: pytest.fail("GET replay"))
    system_api.authorize_view(
        db_session,
        seed_admin,
        prepared["arguments"],
        expected=prepared["expected"],
        result={"status_code": 200, "body": [{"id": 1, "checked_at": "old"}]},
    )


def test_safe_router_replay_checks_identities_but_ignores_volatile_fields(
    db_session, seed_admin, seed_park_with_tracker, monkeypatch
):
    operation = _operation(system_api.registry(), "GET", "/schedules")
    prepared = system_api.prepare(
        db_session,
        seed_admin,
        {
            "action": "call",
            "operation_id": operation["operation_id"],
            "query": {"park_id": seed_park_with_tracker.id},
        },
    )
    assert prepared["expected"]["history_guard"]["family"] == "router_replay"
    monkeypatch.setattr(
        system_api,
        "_call",
        lambda *_args, **_kwargs: {
            "status_code": 200,
            "body": [{"id": "schedule-1", "updated_at": "new"}],
        },
    )
    system_api.authorize_view(
        db_session,
        seed_admin,
        prepared["arguments"],
        expected=prepared["expected"],
        result={"status_code": 200, "body": [{"id": "schedule-1", "updated_at": "old"}]},
        scope_cache={},
    )
    with pytest.raises(HTTPException, match="ai_action_changed"):
        system_api.authorize_view(
            db_session,
            seed_admin,
            prepared["arguments"],
            expected=prepared["expected"],
            result={"status_code": 200, "body": [{"id": "schedule-2"}]},
            scope_cache={},
        )


def test_unsupported_aggregate_is_interactive_before_call(db_session, seed_admin):
    operation = _operation(system_api.registry(), "GET", "/tracker/issues")
    assert operation["classification"] == "interactive"
    assert operation["reason"] == "history_scope_unsupported"
    with pytest.raises(HTTPException, match="ai_system_api_interactive"):
        system_api.prepare(
            db_session,
            seed_admin,
            {"action": "call", "operation_id": operation["operation_id"]},
        )


def test_park_scoped_aggregate_has_explicit_history_guard(
    db_session, seed_admin, seed_park_with_tracker
):
    operation = _operation(system_api.registry(), "GET", "/analytics")
    park_id = seed_park_with_tracker.id
    prepared = system_api.prepare(
        db_session,
        seed_admin,
        {
            "action": "call",
            "operation_id": operation["operation_id"],
            "query": {"park_id": park_id},
        },
    )

    assert prepared["expected"]["history_guard"] == {
        "v": 1,
        "family": "park",
        "park_id": park_id,
    }


def test_emergency_history_rechecks_all_previously_visible_sections(
    db_session, seed_admin, monkeypatch
):
    monkeypatch.setattr(
        system_api.emergency_config,
        "list_sections_for_role",
        lambda *_args: [("metadata", "Metadata"), ("service_raw", "Raw")],
    )
    operation = _operation(system_api.registry(), "GET", "/emergency/{vin}/snapshot")
    prepared = system_api.prepare(
        db_session,
        seed_admin,
        {
            "action": "call",
            "operation_id": operation["operation_id"],
            "path": {"vin": "447"},
        },
    )
    guard = prepared["expected"]["history_guard"]
    assert guard["family"] == "emergency_robot"
    assert guard["sections"]

    monkeypatch.setattr(
        "robopark_api.routers.emergency.authorize_emergency_vin",
        lambda *args, **kwargs: "YASADR00000000447",
    )
    denied_section = guard["sections"][0]
    monkeypatch.setattr(
        system_api.emergency_config,
        "role_can_view_section",
        lambda _db, _role, section: section != denied_section,
    )
    with pytest.raises(HTTPException, match="ai_system_api_forbidden"):
        system_api.authorize_view(
            db_session,
            seed_admin,
            prepared["arguments"],
            expected=prepared["expected"],
            result={"status_code": 200, "body": {}},
        )


def test_remote_history_guards_have_a_request_local_budget(db_session, seed_admin, monkeypatch):
    monkeypatch.setattr(
        "robopark_api.routers.emergency.authorize_emergency_vin",
        lambda *args, **kwargs: "vin",
    )
    cache = {}
    for index in range(system_api.MAX_REMOTE_HISTORY_GUARDS):
        system_api._authorize_history_guard(
            db_session,
            seed_admin,
            {"v": 1, "family": "emergency_robot", "robot": str(index), "park_id": None},
            scope_cache=cache,
        )
    with pytest.raises(HTTPException, match="ai_system_api_history_unavailable"):
        system_api._authorize_history_guard(
            db_session,
            seed_admin,
            {"v": 1, "family": "emergency_robot", "robot": "99", "park_id": None},
            scope_cache=cache,
        )


def test_typed_reads_share_generic_remote_history_budget(db_session, seed_admin, monkeypatch):
    monkeypatch.setattr(tool_domain, "_fresh_issue", lambda *_args: {})
    cache = {}
    for index in range(system_api.MAX_REMOTE_HISTORY_GUARDS):
        tool_domain.authorize_view(
            db_session,
            seed_admin,
            1,
            "task_get",
            {"key": f"TEST-{index}"},
            scope_cache=cache,
        )
    with pytest.raises(HTTPException, match="ai_system_api_history_unavailable"):
        tool_domain.authorize_view(
            db_session,
            seed_admin,
            1,
            "task_get",
            {"key": "TEST-99"},
            scope_cache=cache,
        )


def test_tracker_hidden_history_preserves_include_hidden_admin_access(
    db_session, seed_admin, seed_mechanic, monkeypatch
):
    operation = _operation(system_api.registry(), "GET", "/tracker/issues/{key}")
    arguments = {
        "action": "call",
        "operation_id": operation["operation_id"],
        "path": {"key": "TEST-1"},
        "query": {"include_hidden": True},
    }
    admin = system_api.prepare(db_session, seed_admin, arguments)
    assert admin["expected"]["history_guard"]["include_hidden"] is True
    monkeypatch.setattr(
        "robopark_api.services.tracker_client.get_issue",
        lambda **_kwargs: {"key": "TEST-1", "queue": "TEST", "tags": []},
    )
    monkeypatch.setattr(
        "robopark_api.services.ai.issue_context.task_lifecycle.is_hidden",
        lambda *_args: True,
    )
    system_api.authorize_view(
        db_session,
        seed_admin,
        admin["arguments"],
        expected=admin["expected"],
        result={"status_code": 200, "body": {"key": "TEST-1"}},
        scope_cache={},
    )

    mechanic = system_api.prepare(db_session, seed_mechanic, arguments)
    with pytest.raises(HTTPException, match="task_hidden_manager_required"):
        system_api.authorize_view(
            db_session,
            seed_mechanic,
            mechanic["arguments"],
            expected=mechanic["expected"],
            result={"status_code": 200, "body": {"key": "TEST-1"}},
            scope_cache={},
        )


def test_tracker_history_parses_string_false_like_fastapi(db_session, seed_mechanic):
    operation = _operation(system_api.registry(), "GET", "/tracker/issues/{key}")
    prepared = system_api.prepare(
        db_session,
        seed_mechanic,
        {
            "action": "call",
            "operation_id": operation["operation_id"],
            "path": {"key": "TEST-1"},
            "query": {"include_hidden": "false"},
        },
    )

    assert prepared["expected"]["history_guard"]["include_hidden"] is False


@pytest.mark.parametrize(
    "path",
    ["/tracker/issues/{key}/comments", "/tracker/issues/{key}/timeline"],
)
def test_mechanic_tracker_history_rechecks_active_staff_visibility(
    path, db_session, seed_admin, seed_mechanic, monkeypatch
):
    operation = _operation(system_api.registry(), "GET", path)
    prepared = system_api.prepare(
        db_session,
        seed_mechanic,
        {
            "action": "call",
            "operation_id": operation["operation_id"],
            "path": {"key": "TEST-1"},
        },
    )
    guard = prepared["expected"]["history_guard"]
    assert guard["staff_visibility"] == system_api._staff_visibility(db_session)
    monkeypatch.setattr(
        "robopark_api.services.tracker_client.get_issue",
        lambda **_kwargs: {"key": "TEST-1", "queue": "TEST", "tags": []},
    )
    monkeypatch.setattr(
        "robopark_api.services.ai.issue_context.task_lifecycle.is_hidden",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        "robopark_api.services.ai.issue_context.enforce_issue_scope",
        lambda *_args: None,
    )

    seed_admin.is_active = False
    db_session.commit()
    with pytest.raises(HTTPException, match="ai_action_changed"):
        system_api.authorize_view(
            db_session,
            seed_mechanic,
            prepared["arguments"],
            expected=prepared["expected"],
            result={
                "status_code": 200,
                "body": [{"id": "comment-1", "author_login": seed_admin.username}],
            },
            scope_cache={},
        )


def test_large_json_read_is_projected_with_explicit_truncation():
    value = system_api.response_value(
        "GET",
        200,
        [{"id": index, "value": "x" * 2000} for index in range(100)],
        {},
    )

    assert value["result_truncated"] is True
    assert len(value["body"]) == 8


def test_response_redaction_and_mutation_minimization():
    assert system_api.safe_read_body(
        {"token": "secret", "nested": {"password": "secret", "name": "visible"}}
    ) == {"token": "[скрыто]", "nested": {"password": "[скрыто]", "name": "visible"}}
    assert system_api.response_value("PATCH", 200, {"id": 1, "name": "do-not-echo"}, {}) == {
        "status_code": 200,
        "succeeded": True,
        "accepted": False,
    }
    assert system_api.response_value(
        "POST", 202, {"job_id": "job-1", "sync_state": "pending", "secret": "x"}, {}
    ) == {
        "status_code": 202,
        "succeeded": False,
        "accepted": True,
        "sync_state": "pending",
        "job_id": "job-1",
    }
