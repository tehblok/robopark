from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from robopark_api.models import (
    AuditLog,
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryCount,
    InventoryCountLine,
    InventoryMovement,
    InventoryParkStock,
    InventoryReceipt,
    Park,
    Permission,
    User,
    UserPark,
    UserPermission,
)
from robopark_api.security import hash_password
from robopark_api.services import inventory_catalog, inventory_stock


def _user(db, slug, username, parks=()):
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, slug),
        access_status="approved",
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add_all(UserPark(user_id=user.id, park_id=park.id) for park in parks)
    db.commit()
    return user


def _component(db, user, name="Count component"):
    row = InventoryCatalogComponent(
        name=name,
        normalized_name=name.casefold(),
        is_active=True,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.commit()
    return row


def _part(db, user, component, *, article, name=None):
    row = InventoryCatalogPart(
        component_id=component.id,
        name=name or f"Count part {article}",
        normalized_name=(name or f"Count part {article}").casefold(),
        article=article,
        normalized_article=article.casefold(),
        is_active=True,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.commit()
    return row


def _stock(db, park, part, quantity):
    row = InventoryParkStock(
        park_id=park.id,
        catalog_part_id=part.id,
        quantity=quantity,
        version=1,
    )
    db.add(row)
    db.commit()
    return row


def _create_count(client, park, *, name="Сентябрь", scope=None):
    return client.post(
        f"/inventory/parks/{park.id}/counts",
        json={"name": name, "scope": scope or {"kind": "all"}},
    )


def test_admin_permanently_deletes_only_count_in_assigned_park(
    client, db_session, seed_park_with_tracker
):
    foreign = Park(name="Count delete foreign", tag="Count-delete-foreign", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    admin = _user(db_session, "admin", "count-delete-admin", [seed_park_with_tracker])
    own = InventoryCount(
        park_id=seed_park_with_tracker.id,
        name="Own count",
        created_by=admin.id,
    )
    foreign_count = InventoryCount(
        park_id=foreign.id,
        name="Foreign count",
        created_by=admin.id,
    )
    db_session.add_all([own, foreign_count])
    db_session.flush()
    db_session.add_all(
        [
            InventoryCountLine(
                count_id=own.id,
                catalog_part_id=_part(
                    db_session, admin, _component(db_session, admin), article="DELETE-OWN"
                ).id,
                expected_quantity=0,
            ),
            InventoryCountLine(
                count_id=foreign_count.id,
                catalog_part_id=_part(
                    db_session,
                    admin,
                    _component(db_session, admin, "Foreign delete component"),
                    article="DELETE-FOREIGN",
                ).id,
                expected_quantity=0,
            ),
        ]
    )
    db_session.commit()
    login_as(client, admin.username, "secret")

    denied = client.delete(f"/inventory/counts/{foreign_count.id}", params={"permanent": "true"})
    deleted = client.delete(f"/inventory/counts/{own.id}", params={"permanent": "true"})

    assert denied.status_code == 403
    assert db_session.get(InventoryCount, foreign_count.id) is not None
    assert deleted.status_code == 204, deleted.text
    assert db_session.get(InventoryCount, own.id) is None


def test_non_admin_cannot_permanently_delete_count(client, db_session, seed_park_with_tracker):
    mechanic = _user(db_session, "mechanic", "count-delete-mechanic", [seed_park_with_tracker])
    count = InventoryCount(
        park_id=seed_park_with_tracker.id,
        name="Protected count",
        created_by=mechanic.id,
    )
    db_session.add(count)
    db_session.commit()
    login_as(client, mechanic.username, "secret")

    response = client.delete(f"/inventory/counts/{count.id}", params={"permanent": "true"})

    assert response.status_code == 403
    assert db_session.get(InventoryCount, count.id) is not None


def test_permanent_delete_posted_count_reverses_stock_and_removes_adjustment(
    client, db_session, seed_park_with_tracker, seed_royal
):
    component = _component(db_session, seed_royal, "Count delete reversal")
    part = _part(db_session, seed_royal, component, article="COUNT-DELETE-REVERSAL")
    stock = _stock(db_session, seed_park_with_tracker, part, 10)
    login_as(client, seed_royal.username, "secret")
    created = _create_count(client, seed_park_with_tracker, name="Delete posted count").json()
    updated = client.patch(
        f"/inventory/parks/{seed_park_with_tracker.id}/counts/{created['id']}",
        json={"lines": [{"catalog_part_id": part.id, "actual_quantity": 7}]},
    )
    assert updated.status_code == 200, updated.text
    posted = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/counts/{created['id']}/post"
    )
    assert posted.status_code == 200, posted.text
    db_session.refresh(stock)
    assert stock.quantity == 7

    deleted = client.delete(f"/inventory/counts/{created['id']}", params={"permanent": "true"})

    assert deleted.status_code == 204, deleted.text
    db_session.refresh(stock)
    assert stock.quantity == 10
    assert (
        db_session.scalar(
            select(func.count(InventoryMovement.id)).where(
                InventoryMovement.source_kind == "count",
                InventoryMovement.source_id.like(f"{created['id']}:%"),
            )
        )
        == 0
    )


def test_permanent_delete_posted_count_rolls_back_stock_reversal_on_commit_failure(
    client, db_session, seed_park_with_tracker, seed_royal, monkeypatch
):
    component = _component(db_session, seed_royal, "Count delete rollback")
    part = _part(db_session, seed_royal, component, article="COUNT-DELETE-ROLLBACK")
    stock = _stock(db_session, seed_park_with_tracker, part, 10)
    login_as(client, seed_royal.username, "secret")
    created = _create_count(client, seed_park_with_tracker, name="Rollback posted count").json()
    assert (
        client.patch(
            f"/inventory/parks/{seed_park_with_tracker.id}/counts/{created['id']}",
            json={"lines": [{"catalog_part_id": part.id, "actual_quantity": 7}]},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/inventory/parks/{seed_park_with_tracker.id}/counts/{created['id']}/post"
        ).status_code
        == 200
    )
    original_commit = type(db_session).commit
    monkeypatch.setattr(
        type(db_session),
        "commit",
        lambda _session: (_ for _ in ()).throw(RuntimeError("delete_commit_failed")),
    )

    response = client.delete(f"/inventory/counts/{created['id']}", params={"permanent": "true"})

    assert response.status_code == 502
    monkeypatch.setattr(type(db_session), "commit", original_commit)
    db_session.expire_all()
    assert db_session.get(InventoryCount, created["id"]) is not None
    assert db_session.get(InventoryParkStock, stock.id).quantity == 7
    assert (
        db_session.scalar(
            select(func.count(InventoryMovement.id)).where(
                InventoryMovement.source_kind == "count",
                InventoryMovement.source_id.like(f"{created['id']}:%"),
            )
        )
        == 1
    )


def test_count_component_snapshot_posts_signed_deltas_and_is_idempotent(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "count-lifecycle", [seed_park_with_tracker])
    component = _component(db_session, mechanic)
    increase = _part(db_session, mechanic, component, article="COUNT-UP")
    decrease = _part(db_session, mechanic, component, article="COUNT-DOWN")
    unchanged = _part(db_session, mechanic, component, article="COUNT-SAME")
    _stock(db_session, seed_park_with_tracker, increase, 1)
    _stock(db_session, seed_park_with_tracker, decrease, 5)
    _stock(db_session, seed_park_with_tracker, unchanged, 2)
    login_as(client, mechanic.username, "secret")

    created_response = _create_count(
        client,
        seed_park_with_tracker,
        scope={"kind": "component", "component_id": component.id},
    )

    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["status"] == "draft"
    assert [(line["catalog_part_id"], line["expected_quantity"]) for line in created["lines"]] == [
        (increase.id, 1),
        (decrease.id, 5),
        (unchanged.id, 2),
    ]
    actual = {increase.id: 3, decrease.id: 1, unchanged.id: 2}
    updated = client.patch(
        f"/inventory/parks/{seed_park_with_tracker.id}/counts/{created['id']}",
        json={
            "lines": [
                {"catalog_part_id": part_id, "actual_quantity": quantity}
                for part_id, quantity in actual.items()
            ]
        },
    )
    assert updated.status_code == 200, updated.text
    assert {line["catalog_part_id"]: line["difference"] for line in updated.json()["lines"]} == {
        increase.id: 2,
        decrease.id: -4,
        unchanged.id: 0,
    }

    posted = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/counts/{created['id']}/post"
    )
    retried = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/counts/{created['id']}/post"
    )

    assert posted.status_code == retried.status_code == 200
    assert posted.json()["status"] == retried.json()["status"] == "posted"
    db_session.expire_all()
    assert dict(
        db_session.execute(
            select(InventoryParkStock.catalog_part_id, InventoryParkStock.quantity).where(
                InventoryParkStock.park_id == seed_park_with_tracker.id
            )
        ).all()
    ) == {increase.id: 3, decrease.id: 1, unchanged.id: 2}
    movements = list(
        db_session.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.source_kind == "count")
            .order_by(InventoryMovement.catalog_part_id)
        )
    )
    assert [(row.catalog_part_id, row.delta, row.kind) for row in movements] == [
        (increase.id, 2, "adjustment"),
        (decrease.id, -4, "adjustment"),
    ]
    assert {row.source_id for row in movements} == {
        f"{created['id']}:canonical:{increase.id}",
        f"{created['id']}:canonical:{decrease.id}",
    }


def test_count_collects_every_stale_conflict_before_any_mutation(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "count-conflicts", [seed_park_with_tracker])
    component = _component(db_session, mechanic, "Conflict component")
    first = _part(db_session, mechanic, component, article="STALE-1")
    second = _part(db_session, mechanic, component, article="STALE-2")
    _stock(db_session, seed_park_with_tracker, first, 2)
    _stock(db_session, seed_park_with_tracker, second, 4)
    login_as(client, mechanic.username, "secret")
    count = _create_count(client, seed_park_with_tracker).json()
    assert (
        client.patch(
            f"/inventory/parks/{seed_park_with_tracker.id}/counts/{count['id']}",
            json={
                "lines": [
                    {"catalog_part_id": first.id, "actual_quantity": 8},
                    {"catalog_part_id": second.id, "actual_quantity": 9},
                ]
            },
        ).status_code
        == 200
    )
    for part in (first, second):
        inventory_stock.apply_stock_delta(
            db_session,
            user=mechanic,
            park_id=seed_park_with_tracker.id,
            catalog_part_id=part.id,
            delta=1,
            kind="receipt",
            source_kind=None,
            source_id=None,
            note=None,
        )
    db_session.commit()
    before = dict(
        db_session.execute(
            select(InventoryParkStock.catalog_part_id, InventoryParkStock.quantity)
        ).all()
    )

    response = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/counts/{count['id']}/post"
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "inventory_count_stale",
        "conflicts": [
            {
                "catalog_part_id": first.id,
                "expected_quantity": 2,
                "current_quantity": 3,
                "affected_lines": [
                    {"count_line_id": count["lines"][0]["id"], "catalog_part_id": first.id}
                ],
            },
            {
                "catalog_part_id": second.id,
                "expected_quantity": 4,
                "current_quantity": 5,
                "affected_lines": [
                    {"count_line_id": count["lines"][1]["id"], "catalog_part_id": second.id}
                ],
            },
        ],
    }
    db_session.expire_all()
    assert (
        dict(
            db_session.execute(
                select(InventoryParkStock.catalog_part_id, InventoryParkStock.quantity)
            ).all()
        )
        == before
    )
    assert db_session.get(InventoryCount, count["id"]).status == "draft"
    assert (
        db_session.scalar(
            select(func.count(InventoryMovement.id)).where(InventoryMovement.source_kind == "count")
        )
        == 0
    )
    assert (
        db_session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "inventory.count.posted")
        )
        == 0
    )


def test_stale_count_can_refresh_snapshot_then_post_one_atomic_adjustment(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "count-refresh", [seed_park_with_tracker])
    component = _component(db_session, mechanic, "Refresh component")
    part = _part(db_session, mechanic, component, article="REFRESH-1")
    _stock(db_session, seed_park_with_tracker, part, 5)
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/counts"
    count = _create_count(client, seed_park_with_tracker).json()
    assert (
        client.patch(
            f"{base}/{count['id']}",
            json={"lines": [{"catalog_part_id": part.id, "actual_quantity": 8}]},
        ).status_code
        == 200
    )
    inventory_stock.apply_stock_delta(
        db_session,
        user=mechanic,
        park_id=seed_park_with_tracker.id,
        catalog_part_id=part.id,
        delta=1,
        kind="receipt",
        source_kind=None,
        source_id=None,
        note=None,
    )
    db_session.commit()

    assert client.post(f"{base}/{count['id']}/post").status_code == 409
    refreshed = client.post(f"{base}/{count['id']}/refresh")

    assert refreshed.status_code == 200, refreshed.text
    assert (
        refreshed.json()["lines"][0]
        | {
            "expected_quantity": 6,
            "actual_quantity": 8,
            "difference": 2,
        }
        == refreshed.json()["lines"][0]
    )
    posted = client.post(f"{base}/{count['id']}/post")
    assert posted.status_code == 200, posted.text
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(InventoryParkStock.quantity).where(
                InventoryParkStock.park_id == seed_park_with_tracker.id,
                InventoryParkStock.catalog_part_id == part.id,
            )
        )
        == 8
    )
    adjustments = list(
        db_session.scalars(
            select(InventoryMovement).where(InventoryMovement.source_kind == "count")
        )
    )
    assert [(row.delta, row.balance_before, row.balance_after) for row in adjustments] == [
        (2, 6, 8)
    ]


def test_count_validates_scope_lines_and_draft_only_mutations(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "count-validation", [seed_park_with_tracker])
    first_component = _component(db_session, mechanic, "Validation component")
    empty_component = _component(db_session, mechanic, "Empty component")
    part = _part(db_session, mechanic, first_component, article="COUNT-VALIDATE")
    _stock(db_session, seed_park_with_tracker, part, 1)
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/counts"

    assert _create_count(client, seed_park_with_tracker, name=" ").status_code == 422
    assert (
        _create_count(
            client,
            seed_park_with_tracker,
            scope={"kind": "component", "component_id": 999_999},
        ).status_code
        == 404
    )
    empty = _create_count(
        client,
        seed_park_with_tracker,
        scope={"kind": "component", "component_id": empty_component.id},
    )
    assert empty.status_code == 201
    assert empty.json()["lines"] == []
    assert client.post(f"{base}/{empty.json()['id']}/post").status_code == 200

    count = _create_count(client, seed_park_with_tracker).json()
    assert (
        client.patch(
            f"{base}/{count['id']}",
            json={"lines": [{"catalog_part_id": part.id, "actual_quantity": -1}]},
        ).status_code
        == 422
    )
    duplicate = client.patch(
        f"{base}/{count['id']}",
        json={
            "lines": [
                {"catalog_part_id": part.id, "actual_quantity": 1},
                {"catalog_part_id": part.id, "actual_quantity": 2},
            ]
        },
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "inventory_count_line_duplicate"
    unknown = client.patch(
        f"{base}/{count['id']}",
        json={"lines": [{"catalog_part_id": 999_999, "actual_quantity": 1}]},
    )
    assert unknown.status_code == 400
    assert unknown.json()["detail"] == "inventory_count_line_invalid"
    missing_actual = client.post(f"{base}/{count['id']}/post")
    assert missing_actual.status_code == 400
    assert missing_actual.json()["detail"] == "inventory_count_actual_required"

    assert (
        client.patch(
            f"{base}/{count['id']}",
            json={"lines": [{"catalog_part_id": part.id, "actual_quantity": 1}]},
        ).status_code
        == 200
    )
    assert client.post(f"{base}/{count['id']}/post").status_code == 200
    assert client.patch(f"{base}/{count['id']}", json={"lines": []}).status_code == 422
    assert client.post(f"{base}/{count['id']}/cancel").status_code == 409


def test_count_cancel_access_post_permission_and_audit(client, db_session, seed_park_with_tracker):
    foreign = Park(name="Count foreign", tag="Count foreign", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    mechanic = _user(db_session, "mechanic", "count-access", [seed_park_with_tracker])
    component = _component(db_session, mechanic, "Access component")
    part = _part(db_session, mechanic, component, article="COUNT-ACCESS")
    _stock(db_session, seed_park_with_tracker, part, 0)
    login_as(client, mechanic.username, "secret")
    own_base = f"/inventory/parks/{seed_park_with_tracker.id}/counts"

    assert client.get(f"/inventory/parks/{foreign.id}/counts").status_code == 403
    assert _create_count(client, foreign).status_code == 403
    draft = _create_count(client, seed_park_with_tracker, name="Отмена").json()
    assert client.post(f"{own_base}/{draft['id']}/cancel").status_code == 200
    assert client.post(f"{own_base}/{draft['id']}/cancel").status_code == 200
    assert client.patch(f"{own_base}/{draft['id']}", json={"lines": []}).status_code == 422
    admin = _user(db_session, "admin", "count-cross-park")
    login_as(client, admin.username, "secret")
    cross_park = client.patch(
        f"/inventory/parks/{foreign.id}/counts/{draft['id']}",
        json={"lines": [{"catalog_part_id": part.id, "actual_quantity": 0}]},
    )
    assert cross_park.status_code == 404
    assert cross_park.json()["detail"] == "inventory_count_not_found"

    login_as(client, mechanic.username, "secret")
    count = _create_count(client, seed_park_with_tracker, name="Post forbidden").json()
    client.patch(
        f"{own_base}/{count['id']}",
        json={"lines": [{"catalog_part_id": part.id, "actual_quantity": 0}]},
    )
    permission_id = db_session.scalar(
        select(Permission.id).where(Permission.key == "inventory.documents.post")
    )
    db_session.add(UserPermission(user_id=mechanic.id, permission_id=permission_id, granted=False))
    db_session.commit()
    assert client.post(f"{own_base}/{count['id']}/post").status_code == 403
    assert client.post(f"{own_base}/{count['id']}/refresh").status_code == 403

    audits = list(
        db_session.scalars(select(AuditLog).where(AuditLog.target_type == "inventory_count"))
    )
    assert {row.action for row in audits} >= {
        "inventory.count.created",
        "inventory.count.updated",
        "inventory.count.cancelled",
    }
    assert all(row.actor_user_id == mechanic.id for row in audits)
    assert all(row.actor_role == "mechanic" for row in audits)
    assert all(row.park_id == seed_park_with_tracker.id for row in audits)


def test_count_list_has_unicode_search_stable_pagination_and_one_line_query(
    client, db_engine, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "count-list", [seed_park_with_tracker])
    component = _component(db_session, mechanic, "List component")
    part = _part(db_session, mechanic, component, article="COUNT-LIST")
    _stock(db_session, seed_park_with_tracker, part, 0)
    login_as(client, mechanic.username, "secret")
    created = [
        _create_count(client, seed_park_with_tracker, name=name).json()
        for name in ["Склад Северный", "Other", "склад Южный"]
    ]
    line_queries = []
    count_page_queries = []

    def capture(_conn, _cursor, statement, parameters, _context, _executemany):
        if "FROM inventory_count_lines" in statement:
            line_queries.append(statement)
        if "FROM inventory_counts" in statement and "ORDER BY" in statement:
            count_page_queries.append((statement, parameters))

    from sqlalchemy import event

    event.listen(db_engine, "before_cursor_execute", capture)
    try:
        response = client.get(
            f"/inventory/parks/{seed_park_with_tracker.id}/counts",
            params={"q": " СКЛАД ", "limit": 1, "offset": 1},
        )
        no_query = client.get(
            f"/inventory/parks/{seed_park_with_tracker.id}/counts",
            params={"limit": 2},
        )
    finally:
        event.remove(db_engine, "before_cursor_execute", capture)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2
    assert response.json()["limit"] == 1
    assert response.json()["offset"] == 1
    assert [row["id"] for row in response.json()["items"]] == [created[2]["id"]]
    assert no_query.status_code == 200
    assert len(line_queries) == 2
    assert len(count_page_queries) == 2
    with db_engine.connect() as connection:
        prefix_plan = connection.exec_driver_sql(
            f"EXPLAIN QUERY PLAN {count_page_queries[0][0]}", count_page_queries[0][1]
        ).all()
        no_query_plan = connection.exec_driver_sql(
            f"EXPLAIN QUERY PLAN {count_page_queries[1][0]}", count_page_queries[1][1]
        ).all()
    prefix_detail = " ".join(str(row[-1]) for row in prefix_plan)
    no_query_detail = " ".join(str(row[-1]) for row in no_query_plan)
    assert "ix_inventory_counts_park_name_key_id" in prefix_detail
    assert "ix_inventory_counts_park_created_id" in no_query_detail
    assert "USE TEMP B-TREE" not in prefix_detail
    assert "USE TEMP B-TREE" not in no_query_detail
    assert (
        client.get(
            f"/inventory/parks/{seed_park_with_tracker.id}/counts", params={"limit": 0}
        ).status_code
        == 422
    )


def test_count_lines_survive_merge_and_archiving_before_post(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "count-history")
    component = _component(db_session, admin, "History component")
    source = _part(db_session, admin, component, article="COUNT-SOURCE")
    target = _part(db_session, admin, component, article="COUNT-TARGET")
    archived = _part(db_session, admin, component, article="COUNT-ARCHIVED")
    _stock(db_session, seed_park_with_tracker, source, 2)
    _stock(db_session, seed_park_with_tracker, target, 3)
    _stock(db_session, seed_park_with_tracker, archived, 1)
    login_as(client, admin.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/counts"
    count = _create_count(client, seed_park_with_tracker).json()
    client.patch(
        f"{base}/{count['id']}",
        json={
            "lines": [
                {"catalog_part_id": source.id, "actual_quantity": 1},
                {"catalog_part_id": target.id, "actual_quantity": 5},
                {"catalog_part_id": archived.id, "actual_quantity": 0},
            ]
        },
    )
    inventory_catalog.merge_parts(db_session, admin, source.id, target.id)
    archived.is_active = False
    db_session.commit()

    response = client.post(f"{base}/{count['id']}/post")

    assert response.status_code == 200, response.text
    reopened = next(item for item in client.get(base).json()["items"] if item["id"] == count["id"])
    source_line = next(line for line in reopened["lines"] if line["catalog_part_id"] == source.id)
    assert (
        source_line
        | {
            "catalog_part_name": source.name,
            "catalog_part_article": "COUNT-SOURCE",
            "catalog_component_id": source.component_id,
            "catalog_component_name": component.name,
        }
        == source_line
    )
    db_session.expire_all()
    quantities = dict(
        db_session.execute(
            select(InventoryParkStock.catalog_part_id, InventoryParkStock.quantity)
        ).all()
    )
    assert quantities[target.id] == 6
    assert quantities[archived.id] == 0
    assert db_session.get(InventoryCatalogPart, archived.id).is_active is False
    assert {
        row.source_id
        for row in db_session.scalars(
            select(InventoryMovement).where(InventoryMovement.source_kind == "count")
        )
    } == {
        f"{count['id']}:canonical:{target.id}",
        f"{count['id']}:canonical:{archived.id}",
    }


def test_stale_count_conflict_identifies_original_lines_after_catalog_merge(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "count-stale-alias")
    component = _component(db_session, admin, "Stale alias component")
    source = _part(db_session, admin, component, article="STALE-SOURCE")
    target = _part(db_session, admin, component, article="STALE-TARGET")
    _stock(db_session, seed_park_with_tracker, source, 2)
    _stock(db_session, seed_park_with_tracker, target, 3)
    login_as(client, admin.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/counts"
    count = _create_count(client, seed_park_with_tracker).json()
    assert (
        client.patch(
            f"{base}/{count['id']}",
            json={
                "lines": [
                    {
                        "catalog_part_id": line["catalog_part_id"],
                        "actual_quantity": line["expected_quantity"],
                    }
                    for line in count["lines"]
                ]
            },
        ).status_code
        == 200
    )
    inventory_catalog.merge_parts(db_session, admin, source.id, target.id)
    inventory_stock.apply_stock_delta(
        db_session,
        user=admin,
        park_id=seed_park_with_tracker.id,
        catalog_part_id=target.id,
        delta=1,
        kind="receipt",
        source_kind=None,
        source_id=None,
        note=None,
    )
    db_session.commit()

    response = client.post(f"{base}/{count['id']}/post")

    assert response.status_code == 409, response.text
    conflicts = response.json()["detail"]["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["catalog_part_id"] == target.id
    expected_lines = {
        (line["id"], line["catalog_part_id"])
        for line in count["lines"]
        if line["catalog_part_id"] in {source.id, target.id}
    }
    assert {
        (line["count_line_id"], line["catalog_part_id"]) for line in conflicts[0]["affected_lines"]
    } == expected_lines


@pytest.mark.parametrize(
    ("source_expected", "target_expected", "source_actual", "target_actual"),
    [
        (1, 2**63 - 2, 0, 2**63 - 1),
        (0, 1, 1, 0),
    ],
)
def test_count_post_aggregates_compensating_alias_deltas_before_stock_mutation(
    client,
    db_session,
    seed_park_with_tracker,
    source_expected,
    target_expected,
    source_actual,
    target_actual,
):
    admin = _user(db_session, "admin", f"count-aggregate-{source_expected}")
    component = _component(db_session, admin, f"Aggregate {source_expected}")
    source = _part(db_session, admin, component, article=f"AGGREGATE-SOURCE-{source_expected}")
    target = _part(db_session, admin, component, article=f"AGGREGATE-TARGET-{source_expected}")
    _stock(db_session, seed_park_with_tracker, source, source_expected)
    _stock(db_session, seed_park_with_tracker, target, target_expected)
    login_as(client, admin.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/counts"
    count = _create_count(client, seed_park_with_tracker).json()
    assert (
        client.patch(
            f"{base}/{count['id']}",
            json={
                "lines": [
                    {"catalog_part_id": source.id, "actual_quantity": source_actual},
                    {"catalog_part_id": target.id, "actual_quantity": target_actual},
                ]
            },
        ).status_code
        == 200
    )
    inventory_catalog.merge_parts(db_session, admin, source.id, target.id)

    response = client.post(f"{base}/{count['id']}/post")
    retry = client.post(f"{base}/{count['id']}/post")

    assert response.status_code == 200, response.text
    assert retry.status_code == 200, retry.text
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(InventoryParkStock.quantity).where(
                InventoryParkStock.park_id == seed_park_with_tracker.id,
                InventoryParkStock.catalog_part_id == target.id,
            )
        )
        == source_actual + target_actual
    )
    assert (
        db_session.scalar(
            select(func.count(InventoryMovement.id)).where(InventoryMovement.source_kind == "count")
        )
        == 0
    )


def test_concurrent_count_post_retries_apply_each_line_once(
    client, db_engine, db_session, seed_park_with_tracker
):
    from robopark_api.services import inventory_counts

    mechanic = _user(db_session, "mechanic", "count-concurrent", [seed_park_with_tracker])
    component = _component(db_session, mechanic, "Concurrent component")
    part = _part(db_session, mechanic, component, article="COUNT-CONCURRENT")
    _stock(db_session, seed_park_with_tracker, part, 1)
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/counts"
    count = _create_count(client, seed_park_with_tracker).json()
    client.patch(
        f"{base}/{count['id']}",
        json={"lines": [{"catalog_part_id": part.id, "actual_quantity": 4}]},
    )
    user_id = mechanic.id
    park_id = seed_park_with_tracker.id

    def post():
        with Session(db_engine) as session:
            return inventory_counts.post_count(
                session,
                session.get(User, user_id),
                park_id=park_id,
                count_id=count["id"],
            ).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _index: post(), range(2)))

    assert statuses == ["posted", "posted"]
    db_session.expire_all()
    assert db_session.scalar(select(InventoryParkStock.quantity)) == 4
    assert (
        db_session.scalar(
            select(func.count(InventoryMovement.id)).where(InventoryMovement.source_kind == "count")
        )
        == 1
    )


def test_count_alias_lock_precedes_document_and_sorted_canonical_stock_locks(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import inventory_counts

    admin = _user(db_session, "admin", "count-lock-order")
    component = _component(db_session, admin, "Lock component")
    low = _part(db_session, admin, component, article="COUNT-LOCK-LOW")
    high = _part(db_session, admin, component, article="COUNT-LOCK-HIGH")
    _stock(db_session, seed_park_with_tracker, low, 0)
    _stock(db_session, seed_park_with_tracker, high, 0)
    login_as(client, admin.username, "secret")
    count = _create_count(client, seed_park_with_tracker).json()
    client.patch(
        f"/inventory/parks/{seed_park_with_tracker.id}/counts/{count['id']}",
        json={
            "lines": [
                {"catalog_part_id": high.id, "actual_quantity": 0},
                {"catalog_part_id": low.id, "actual_quantity": 0},
            ]
        },
    )
    events = []
    real_lock = inventory_counts.inventory_catalog.acquire_alias_graph_read_lock
    real_count = inventory_counts._count
    real_ensure = inventory_counts.inventory_stock.ensure_stock

    def record_alias(db):
        events.append("alias")
        return real_lock(db)

    def record_count(*args, **kwargs):
        events.append("document")
        return real_count(*args, **kwargs)

    def record_stock(db, *, park_id, catalog_part_id, allow_archived=False):
        events.append(f"stock:{catalog_part_id}")
        return real_ensure(
            db,
            park_id=park_id,
            catalog_part_id=catalog_part_id,
            allow_archived=allow_archived,
        )

    monkeypatch.setattr(
        inventory_counts.inventory_catalog, "acquire_alias_graph_read_lock", record_alias
    )
    monkeypatch.setattr(inventory_counts, "_count", record_count)
    monkeypatch.setattr(inventory_counts.inventory_stock, "ensure_stock", record_stock)

    assert (
        client.post(
            f"/inventory/parks/{seed_park_with_tracker.id}/counts/{count['id']}/post"
        ).status_code
        == 200
    )
    assert events[:4] == ["alias", "document", f"stock:{low.id}", f"stock:{high.id}"]


def test_count_materializes_missing_zero_stock_before_archive_and_authorizes_its_line_only(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "count-zero-archive")
    component = _component(db_session, admin, "Zero archive component")
    counted = _part(db_session, admin, component, article="COUNT-ZERO-ARCHIVE")
    unrelated = _part(db_session, admin, component, article="COUNT-ZERO-UNRELATED")
    login_as(client, admin.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/counts"

    count = _create_count(
        client,
        seed_park_with_tracker,
        scope={"kind": "component", "component_id": component.id},
    ).json()
    stocks_after_snapshot = dict(
        db_session.execute(
            select(InventoryParkStock.catalog_part_id, InventoryParkStock.quantity)
        ).all()
    )
    assert stocks_after_snapshot == {counted.id: 0, unrelated.id: 0}
    assert (
        client.patch(
            f"{base}/{count['id']}",
            json={
                "lines": [
                    {"catalog_part_id": counted.id, "actual_quantity": 1},
                    {"catalog_part_id": unrelated.id, "actual_quantity": 0},
                ]
            },
        ).status_code
        == 200
    )
    counted.is_active = False
    unrelated.is_active = False
    db_session.commit()

    posted = client.post(f"{base}/{count['id']}/post")
    retried = client.post(f"{base}/{count['id']}/post")

    assert posted.status_code == retried.status_code == 200
    db_session.expire_all()
    assert dict(
        db_session.execute(
            select(InventoryParkStock.catalog_part_id, InventoryParkStock.quantity)
        ).all()
    ) == {counted.id: 1, unrelated.id: 0}
    movements = list(
        db_session.scalars(
            select(InventoryMovement).where(InventoryMovement.source_kind == "count")
        )
    )
    assert [(row.catalog_part_id, row.delta, row.source_id) for row in movements] == [
        (counted.id, 1, f"{count['id']}:canonical:{counted.id}")
    ]


def test_count_accepts_quantities_above_one_million_within_database_integer_capacity(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "count-large", [seed_park_with_tracker])
    component = _component(db_session, mechanic, "Large quantity component")
    unchanged = _part(db_session, mechanic, component, article="COUNT-LARGE-SAME")
    increased = _part(db_session, mechanic, component, article="COUNT-LARGE-UP")
    _stock(db_session, seed_park_with_tracker, unchanged, 1_000_001)
    _stock(db_session, seed_park_with_tracker, increased, 0)
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/counts"
    count = _create_count(client, seed_park_with_tracker).json()

    updated = client.patch(
        f"{base}/{count['id']}",
        json={
            "lines": [
                {"catalog_part_id": unchanged.id, "actual_quantity": 1_000_001},
                {"catalog_part_id": increased.id, "actual_quantity": 2_000_000_000},
            ]
        },
    )
    posted = client.post(f"{base}/{count['id']}/post")

    assert updated.status_code == 200, updated.text
    assert posted.status_code == 200, posted.text
    db_session.expire_all()
    assert dict(
        db_session.execute(
            select(InventoryParkStock.catalog_part_id, InventoryParkStock.quantity)
        ).all()
    ) == {unchanged.id: 1_000_001, increased.id: 2_000_000_000}
    assert [
        row.delta
        for row in db_session.scalars(
            select(InventoryMovement).where(InventoryMovement.source_kind == "count")
        )
    ] == [2_000_000_000]


def test_count_name_search_uses_persisted_python_casefold_on_all_dialects(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    mechanic = _user(db_session, "mechanic", "count-casefold", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    created = _create_count(client, seed_park_with_tracker, name="Straße ος").json()

    response = client.get(
        f"/inventory/parks/{seed_park_with_tracker.id}/counts",
        params={"q": "STRASSE ΟΣ"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [created["id"]]
    assert db_session.get(InventoryCount, created["id"]).normalized_name == "strasse οσ"

    class Bind:
        dialect = postgresql.dialect()

    class RecordingSession:
        statements = []

        def get_bind(self):
            return Bind()

        def scalar(self, statement):
            self.statements.append(statement)
            return 0

        def scalars(self, statement):
            self.statements.append(statement)
            return []

    from robopark_api.services import inventory_counts

    db = RecordingSession()
    monkeypatch.setattr(inventory_counts.inventory_access, "require_park", lambda *_args: None)
    inventory_counts.list_counts(
        db,
        object(),
        park_id=7,
        query="STRASSE ΟΣ",
        limit=10,
        offset=0,
    )
    compiled = "\n".join(
        str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for statement in db.statements
    )
    assert "inventory_counts.normalized_name_key" in compiled
    assert "inventory_counts.normalized_name >=" not in compiled
    assert "inventory_counts.normalized_name <" not in compiled
    assert "ORDER BY inventory_counts.normalized_name_key, inventory_counts.id" in compiled
    assert "LIKE" not in compiled.upper()
    assert "translate(" not in compiled
    assert "lower(" not in compiled


def test_count_name_normalization_tracks_orm_updates(client, db_session, seed_park_with_tracker):
    mechanic = _user(db_session, "mechanic", "count-casefold-update", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    created = _create_count(client, seed_park_with_tracker, name="Original").json()
    row = db_session.get(InventoryCount, created["id"])

    row.name = "Weiße ος"
    db_session.commit()

    db_session.refresh(row)
    assert row.normalized_name == "weisse οσ"
    assert row.normalized_name_key == "weisse οσ".encode()
    response = client.get(
        f"/inventory/parks/{seed_park_with_tracker.id}/counts",
        params={"q": "WEISSE ΟΣ"},
    )
    assert [item["id"] for item in response.json()["items"]] == [created["id"]]


def test_count_actual_rejects_values_above_signed_int64_without_mutating_draft(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "count-int64", [seed_park_with_tracker])
    component = _component(db_session, mechanic, "Int64 component")
    part = _part(db_session, mechanic, component, article="COUNT-INT64")
    login_as(client, mechanic.username, "secret")
    base = f"/inventory/parks/{seed_park_with_tracker.id}/counts"
    count = _create_count(client, seed_park_with_tracker).json()
    too_large = 2**63

    response = client.patch(
        f"{base}/{count['id']}",
        json={"lines": [{"catalog_part_id": part.id, "actual_quantity": too_large}]},
    )

    assert response.status_code == 422
    db_session.expire_all()
    line = db_session.scalar(
        select(InventoryCountLine).where(InventoryCountLine.count_id == count["id"])
    )
    assert line.actual_quantity is None
    assert line.difference is None
    from robopark_api.services import inventory_counts, inventory_stock

    with pytest.raises(inventory_stock.InventoryValidation, match="inventory_quantity_overflow"):
        inventory_counts.update_count_lines(
            db_session,
            mechanic,
            park_id=seed_park_with_tracker.id,
            count_id=count["id"],
            lines=[
                SimpleNamespace(
                    model_dump=lambda: {
                        "catalog_part_id": part.id,
                        "actual_quantity": too_large,
                        "comment": None,
                    }
                )
            ],
        )
    assert (
        db_session.scalar(
            select(InventoryCountLine.actual_quantity).where(
                InventoryCountLine.count_id == count["id"]
            )
        )
        is None
    )


def test_receipt_post_rejects_int64_stock_overflow_and_rolls_back_every_line(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "stock-int64-overflow", [seed_park_with_tracker])
    component = _component(db_session, mechanic, "Overflow component")
    first = _part(db_session, mechanic, component, article="OVERFLOW-FIRST")
    overflowing = _part(db_session, mechanic, component, article="OVERFLOW-SECOND")
    _stock(db_session, seed_park_with_tracker, first, 0)
    _stock(db_session, seed_park_with_tracker, overflowing, 2**63 - 1)
    login_as(client, mechanic.username, "secret")
    receipt = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/receipts",
        json={
            "received_on": "2026-09-11",
            "lines": [
                {"catalog_part_id": first.id, "quantity": 1},
                {"catalog_part_id": overflowing.id, "quantity": 1},
            ],
        },
    ).json()

    response = client.post(
        f"/inventory/parks/{seed_park_with_tracker.id}/receipts/{receipt['id']}/post",
        json={"revision": receipt["revision"]},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "inventory_quantity_overflow"
    db_session.expire_all()
    assert dict(
        db_session.execute(
            select(InventoryParkStock.catalog_part_id, InventoryParkStock.quantity)
        ).all()
    ) == {first.id: 0, overflowing.id: 2**63 - 1}
    assert db_session.get(InventoryReceipt, receipt["id"]).status == "draft"
    assert (
        db_session.scalar(
            select(func.count(InventoryMovement.id)).where(
                InventoryMovement.source_kind == "receipt"
            )
        )
        == 0
    )


def test_stock_version_rejects_signed_int64_overflow_without_mutation(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "stock-version-overflow", [seed_park_with_tracker])
    component = _component(db_session, mechanic, "Version component")
    part = _part(db_session, mechanic, component, article="VERSION-OVERFLOW")
    stock = _stock(db_session, seed_park_with_tracker, part, 1)
    stock.version = 2**63 - 1
    db_session.commit()
    login_as(client, mechanic.username, "secret")

    response = client.post(
        f"/inventory/parts/{-part.id}/movements",
        json={"park_id": seed_park_with_tracker.id, "kind": "receipt", "quantity": 1},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "inventory_version_overflow"
    db_session.expire_all()
    assert db_session.get(InventoryParkStock, stock.id).quantity == 1
    assert db_session.get(InventoryParkStock, stock.id).version == 2**63 - 1
