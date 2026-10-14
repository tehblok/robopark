"""Personal workflows use the requester's Mac Tracker identity without a server token."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from robopark_api.services import (
    inventory,
    media_uploads,
    offline_sync,
    operations,
    platform_settings,
    tracker_gateway_transport,
)
from robopark_api.services.tracker_gateway_transport import GatewayCredential


def _mac_only(monkeypatch, user):
    context = GatewayCredential(user.id)
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda _db: None)
    monkeypatch.setattr(
        tracker_gateway_transport,
        "credential",
        lambda _db, requester: (
            context if requester.id == user.id else pytest.fail("wrong requester identity")
        ),
    )
    return context


def _issue(park, key="ROBOPARK-51"):
    return {
        "key": key,
        "queue": park.tracker_queue,
        "summary": "[447] brake",
        "status": "Open",
        "status_key": "open",
        "tags": [park.tag],
    }


def test_offline_issue_read_uses_requester_mac_without_server_token(
    db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    context = _mac_only(monkeypatch, seed_mechanic)
    seen = []
    monkeypatch.setattr(
        offline_sync.tracker_client,
        "get_issue",
        lambda *, token, key: seen.append((token, key)) or _issue(seed_park_with_tracker, key),
    )

    result = offline_sync._issue(
        db_session,
        seed_mechanic,
        SimpleNamespace(resource_id="ROBOPARK-51"),
        fresh=True,
    )

    assert result["key"] == "ROBOPARK-51"
    assert seen == [(context, "ROBOPARK-51")]


def test_offline_review_component_catalog_uses_requester_mac(
    db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    context = _mac_only(monkeypatch, seed_mechanic)
    issue = _issue(seed_park_with_tracker)
    reviewer = SimpleNamespace(id=91)
    db_session.info["sync_push_service"] = SimpleNamespace(
        emit_in_transaction=lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(offline_sync, "_authorize_review", lambda *_args, **_kwargs: issue)
    monkeypatch.setattr(
        offline_sync.task_lifecycle, "issue_park", lambda *_args: seed_park_with_tracker
    )
    monkeypatch.setattr(
        offline_sync.schedules, "resolve_active_operator", lambda *_args, **_kwargs: reviewer
    )
    monkeypatch.setattr(
        offline_sync.tracker_signatures,
        "build_signature_context",
        lambda *_args: SimpleNamespace(operator_login="operator"),
    )
    seen = []
    monkeypatch.setattr(
        offline_sync.tracker_client,
        "list_queue_components",
        lambda *, token, queue: seen.append((token, queue)) or [],
    )
    monkeypatch.setattr(
        offline_sync.task_lifecycle,
        "submit_review",
        lambda *_args, **_kwargs: {"state": "queued"},
    )
    item = SimpleNamespace(
        resource_type="tracker_issue",
        resource_id="ROBOPARK-51",
        action="submit_review",
        park_id=seed_park_with_tracker.id,
        idempotency_key="review-mac-only",
        payload={"defect_code": "BD-01", "repair_fields": {}},
    )
    media = media_uploads.ConsumedUpload("review.jpg", "image/jpeg", b"photo")

    result = offline_sync.dispatch_action(db_session, seed_mechanic, item, media)

    assert result == {"state": "queued"}
    assert seen == [(context, seed_park_with_tracker.tracker_queue)]


def test_media_upload_authorization_uses_requester_mac_without_server_token(
    db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    context = _mac_only(monkeypatch, seed_mechanic)
    seen = []
    monkeypatch.setattr(
        media_uploads.tracker_cache,
        "get_issue",
        lambda *, token, key: seen.append((token, key)) or _issue(seed_park_with_tracker, key),
    )

    park_id = media_uploads._authorize_start(db_session, seed_mechanic, "ROBOPARK-51")

    assert park_id == seed_park_with_tracker.id
    assert seen == [(context, "ROBOPARK-51")]


def test_inventory_writeoff_authorization_uses_requester_mac_without_server_token(
    db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    context = _mac_only(monkeypatch, seed_mechanic)
    monkeypatch.setattr(
        inventory,
        "get_claim",
        lambda *_args: SimpleNamespace(
            owner_user_id=seed_mechanic.id,
            state="active",
            park_id=seed_park_with_tracker.id,
        ),
    )
    part = SimpleNamespace(id=71, name="Wheel", article="W-1")
    stock = SimpleNamespace(park_id=seed_park_with_tracker.id, quantity=3)
    monkeypatch.setattr(inventory, "_part_and_stock", lambda *_args, **_kwargs: (part, stock, 71))
    monkeypatch.setattr(inventory, "require_park", lambda *_args: seed_park_with_tracker)
    monkeypatch.setattr(inventory.inventory_stock, "ensure_stock", lambda *_args, **_kwargs: stock)
    monkeypatch.setattr(inventory, "mechanic_owns_issue", lambda *_args: True)
    seen = []
    monkeypatch.setattr(
        inventory.tracker_cache,
        "get_issue",
        lambda *, token, key: seen.append((token, key)) or _issue(seed_park_with_tracker, key),
    )

    class ReachedMutation(Exception):
        pass

    monkeypatch.setattr(
        inventory.inventory_stock,
        "apply_stock_delta",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ReachedMutation),
    )

    with pytest.raises(ReachedMutation):
        inventory.task_writeoff(
            db_session,
            seed_mechanic,
            "ROBOPARK-51",
            71,
            1,
            idempotency_key="writeoff-mac-only",
        )

    assert seen == [(context, "ROBOPARK-51")]


def test_operations_overview_uses_requester_mac_without_server_token(
    db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    context = _mac_only(monkeypatch, seed_mechanic)
    seen = []
    monkeypatch.setattr(
        operations.tracker_cache,
        "fetch_park_blockers",
        lambda *, token, **_kwargs: seen.append(token) or [],
    )
    monkeypatch.setattr(
        operations.tracker_history,
        "hydrate_visible_history",
        lambda _db, *, token, issues, parks: seen.append(token) or issues,
    )

    result = operations.build_overview(
        db_session,
        seed_mechanic,
        seed_park_with_tracker,
        days=7,
        selected_status="all",
        now=datetime(2026, 10, 15, tzinfo=UTC),
    )

    assert result.tasks == []
    assert seen == [context, context]
