import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Update

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
    InventoryReceiptLine,
    Park,
    Permission,
    Role,
    User,
    UserPark,
)
from robopark_api.security import hash_password
from robopark_api.services import inventory, inventory_catalog, inventory_stock
from robopark_api.services.rbac import has_permission


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
    for park in parks:
        db.add(UserPark(user_id=user.id, park_id=park.id))
    db.commit()
    return user


def _catalog(db, actor, *, name="Тяга", article="ABC-01"):
    component = InventoryCatalogComponent(
        name="Подвязка",
        normalized_name="подвязка",
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(component)
    db.flush()
    part = InventoryCatalogPart(
        component_id=component.id,
        name=name,
        normalized_name=" ".join(name.strip().casefold().split()),
        article=article,
        normalized_article=" ".join(article.strip().casefold().split()),
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(part)
    db.commit()
    return component, part


_JPEG = b"\xff\xd8\xff\xe0catalog-photo"
_PNG = b"\x89PNG\r\n\x1a\ncatalog-photo"
_WEBP = b"RIFF\x08\x00\x00\x00WEBPcatalog-photo"


def _photos_in(tmp_path):
    root = tmp_path / "inventory-photos"
    return set(root.iterdir()) if root.exists() else set()


@pytest.mark.parametrize(
    ("kind", "payload", "filename", "content_type"),
    [
        ("components", _JPEG, "photo.jpg", "image/jpeg"),
        ("parts", _PNG, "photo.png", "image/png"),
        ("parts", _WEBP, "photo.webp", "image/webp"),
    ],
)
def test_catalog_photo_accepts_only_supported_images(
    client,
    db_session,
    seed_park_with_tracker,
    seed_royal,
    tmp_path,
    monkeypatch,
    kind,
    payload,
    filename,
    content_type,
):
    monkeypatch.setattr(
        inventory,
        "get_settings",
        lambda: SimpleNamespace(inventory_photos_dir=str(tmp_path / "inventory-photos")),
    )
    component, part = _catalog(db_session, seed_royal, article=f"PHOTO-{kind}")
    row_id = component.id if kind == "components" else part.id
    login_as(client, seed_royal.username, "secret")

    uploaded = client.put(
        f"/inventory/catalog/{kind}/{row_id}/photo",
        files={"photo": (filename, payload, content_type)},
    )
    rejected = client.put(
        f"/inventory/catalog/{kind}/{row_id}/photo",
        files={"photo": ("photo.heic", b"heic", "image/heic")},
    )

    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["has_photo"] is True
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "inventory_photo_invalid_type"
    assert len(_photos_in(tmp_path)) == 1


def test_catalog_photo_replace_and_remove_clean_up_managed_files(
    client, db_session, seed_royal, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        inventory,
        "get_settings",
        lambda: SimpleNamespace(inventory_photos_dir=str(tmp_path / "inventory-photos")),
    )
    _component, part = _catalog(db_session, seed_royal, article="PHOTO-REPLACE")
    login_as(client, seed_royal.username, "secret")

    assert (
        client.put(
            f"/inventory/catalog/parts/{part.id}/photo",
            files={"photo": ("first.jpg", _JPEG, "image/jpeg")},
        ).status_code
        == 200
    )
    first_files = _photos_in(tmp_path)
    assert len(first_files) == 1
    assert (
        client.put(
            f"/inventory/catalog/parts/{part.id}/photo",
            files={"photo": ("second.png", _PNG, "image/png")},
        ).status_code
        == 200
    )
    second_files = _photos_in(tmp_path)
    assert len(second_files) == 1
    assert first_files.isdisjoint(second_files)

    removed = client.delete(f"/inventory/catalog/parts/{part.id}/photo")

    assert removed.status_code == 204
    assert _photos_in(tmp_path) == set()
    db_session.refresh(part)
    assert (part.photo_storage_key, part.photo_filename, part.photo_content_type) == (
        None,
        None,
        None,
    )


def test_catalog_photo_replace_persists_retryable_cleanup_after_unlink_failure(
    client, db_engine, db_session, seed_royal, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        inventory,
        "get_settings",
        lambda: SimpleNamespace(inventory_photos_dir=str(tmp_path / "inventory-photos")),
    )
    _component, part = _catalog(db_session, seed_royal, article="PHOTO-CLEANUP-RETRY")
    old_key, filename, content_type = inventory.save_photo("old.jpg", _JPEG, "image/jpeg")
    part.photo_storage_key = old_key
    part.photo_filename = filename
    part.photo_content_type = content_type
    db_session.commit()
    login_as(client, seed_royal.username, "secret")
    from robopark_api.services import inventory_photo_cleanup

    real_unlink = inventory_photo_cleanup._unlink_storage_key
    monkeypatch.setattr(
        inventory_photo_cleanup,
        "_unlink_storage_key",
        lambda _key: (_ for _ in ()).throw(OSError("unlink failed")),
    )

    response = client.put(
        f"/inventory/catalog/parts/{part.id}/photo",
        files={"photo": ("new.png", _PNG, "image/png")},
    )

    assert response.status_code == 202, response.text
    db_session.expire_all()
    stored = db_session.get(InventoryCatalogPart, part.id)
    assert stored is not None and stored.photo_storage_key != old_key
    current_key = stored.photo_storage_key
    assert inventory.photo_path(current_key).is_file()
    assert (tmp_path / "inventory-photos" / old_key).is_file()

    from robopark_api.models import InventoryPhotoCleanup

    assert db_session.get(InventoryPhotoCleanup, old_key) is not None
    monkeypatch.setattr(inventory_photo_cleanup, "_unlink_storage_key", real_unlink)
    with Session(db_engine) as restarted:
        assert inventory_photo_cleanup.process_pending(restarted) == 1
        assert inventory_photo_cleanup.process_pending(restarted) == 0
        assert restarted.get(InventoryPhotoCleanup, old_key) is None
    assert not (tmp_path / "inventory-photos" / old_key).exists()
    with Session(db_engine) as restarted:
        inventory_photo_cleanup.enqueue(restarted, {current_key})
        restarted.commit()
        assert inventory_photo_cleanup.process_pending(restarted) == 1
        assert restarted.get(InventoryPhotoCleanup, current_key) is None
    assert inventory.photo_path(current_key).is_file()


def test_catalog_photo_replace_keeps_committed_blob_when_commit_ack_is_ambiguous(
    client, db_engine, db_session, seed_royal, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        inventory,
        "get_settings",
        lambda: SimpleNamespace(inventory_photos_dir=str(tmp_path / "inventory-photos")),
    )
    _component, part = _catalog(db_session, seed_royal, article="PHOTO-COMMIT-AMBIGUOUS")
    old_key, filename, content_type = inventory.save_photo("old.jpg", _JPEG, "image/jpeg")
    part.photo_storage_key = old_key
    part.photo_filename = filename
    part.photo_content_type = content_type
    db_session.commit()
    login_as(client, seed_royal.username, "secret")
    real_commit = db_session.commit

    def commit_then_raise():
        real_commit()
        raise RuntimeError("commit_ack_lost")

    monkeypatch.setattr(db_session, "commit", commit_then_raise)

    response = client.put(
        f"/inventory/catalog/parts/{part.id}/photo",
        files={"photo": ("new.png", _PNG, "image/png")},
    )

    assert response.status_code == 502
    from robopark_api.models import InventoryPhotoCleanup
    from robopark_api.services import inventory_photo_cleanup

    with Session(db_engine) as restarted:
        stored = restarted.get(InventoryCatalogPart, part.id)
        assert stored is not None and stored.photo_storage_key != old_key
        assert inventory.photo_path(stored.photo_storage_key).is_file()
        assert restarted.get(InventoryPhotoCleanup, stored.photo_storage_key) is None
        assert restarted.get(InventoryPhotoCleanup, old_key) is not None
        assert inventory_photo_cleanup.process_pending(restarted) == 1
    assert not (tmp_path / "inventory-photos" / old_key).exists()


def test_catalog_photo_replace_true_commit_failure_cleans_unreferenced_new_blob(
    client, db_engine, db_session, seed_royal, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        inventory,
        "get_settings",
        lambda: SimpleNamespace(inventory_photos_dir=str(tmp_path / "inventory-photos")),
    )
    _component, part = _catalog(db_session, seed_royal, article="PHOTO-COMMIT-FAILED")
    old_key, filename, content_type = inventory.save_photo("old.jpg", _JPEG, "image/jpeg")
    part.photo_storage_key = old_key
    part.photo_filename = filename
    part.photo_content_type = content_type
    db_session.commit()
    login_as(client, seed_royal.username, "secret")
    monkeypatch.setattr(
        inventory,
        "_remove_photo",
        lambda _key: (_ for _ in ()).throw(AssertionError("direct unlink is unsafe")),
    )
    monkeypatch.setattr(
        db_session,
        "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("commit_failed")),
    )

    response = client.put(
        f"/inventory/catalog/parts/{part.id}/photo",
        files={"photo": ("new.png", _PNG, "image/png")},
    )

    assert response.status_code == 502
    from robopark_api.models import InventoryPhotoCleanup

    with Session(db_engine) as restarted:
        stored = restarted.get(InventoryCatalogPart, part.id)
        assert stored is not None and stored.photo_storage_key == old_key
        assert restarted.query(InventoryPhotoCleanup).count() == 0
    assert _photos_in(tmp_path) == {tmp_path / "inventory-photos" / old_key}


def test_catalog_photo_row_is_selected_for_update_before_mutation():
    captured = []
    expected = object()

    class StubSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def get(self, _model, _row_id):
            return expected

        def scalar(self, statement):
            captured.append(statement)
            return expected

    row = inventory_catalog._catalog_photo_row(StubSession(), "part", 7)

    assert row is expected
    assert captured
    assert captured[0]._for_update_arg is not None


def test_permanent_delete_persists_retryable_photo_cleanup_after_unlink_failure(
    client, db_engine, db_session, seed_royal, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        inventory,
        "get_settings",
        lambda: SimpleNamespace(inventory_photos_dir=str(tmp_path / "inventory-photos")),
    )
    _component, part = _catalog(db_session, seed_royal, article="DELETE-CLEANUP-RETRY")
    key, filename, content_type = inventory.save_photo("delete.jpg", _JPEG, "image/jpeg")
    part.photo_storage_key = key
    part.photo_filename = filename
    part.photo_content_type = content_type
    db_session.commit()
    login_as(client, seed_royal.username, "secret")
    from robopark_api.services import inventory_photo_cleanup

    real_unlink = inventory_photo_cleanup._unlink_storage_key
    monkeypatch.setattr(
        inventory_photo_cleanup,
        "_unlink_storage_key",
        lambda _key: (_ for _ in ()).throw(OSError("unlink failed")),
    )

    response = client.delete(f"/inventory/catalog/parts/{part.id}", params={"permanent": "true"})

    assert response.status_code == 202, response.text
    assert db_session.get(InventoryCatalogPart, part.id) is None
    assert (tmp_path / "inventory-photos" / key).is_file()
    from robopark_api.models import InventoryPhotoCleanup

    assert db_session.get(InventoryPhotoCleanup, key) is not None
    monkeypatch.setattr(inventory_photo_cleanup, "_unlink_storage_key", real_unlink)
    with Session(db_engine) as restarted:
        assert inventory_photo_cleanup.process_pending(restarted) == 1
        assert inventory_photo_cleanup.process_pending(restarted) == 0
        assert restarted.get(InventoryPhotoCleanup, key) is None
    assert not (tmp_path / "inventory-photos" / key).exists()


@pytest.mark.parametrize("kind", ["parts", "components"])
def test_permanent_catalog_delete_locks_photo_rows_before_snapshot(
    client, db_session, seed_royal, monkeypatch, kind
):
    component, part = _catalog(db_session, seed_royal, article=f"DELETE-LOCK-{kind.upper()}")
    login_as(client, seed_royal.username, "secret")
    statements = []
    real_execute = db_session.execute

    def recording_execute(statement, *args, **kwargs):
        statements.append(statement)
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", recording_execute)
    row_id = part.id if kind == "parts" else component.id

    response = client.delete(f"/inventory/catalog/{kind}/{row_id}", params={"permanent": "true"})

    assert response.status_code == 200, response.text
    locked_tables = {
        statement.table.name for statement in statements if isinstance(statement, Update)
    }
    assert "inventory_catalog_parts" in locked_tables
    if kind == "components":
        assert "inventory_catalog_components" in locked_tables


def test_catalog_photo_keeps_committed_new_file_when_refresh_fails(
    client, db_session, seed_royal, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        inventory,
        "get_settings",
        lambda: SimpleNamespace(inventory_photos_dir=str(tmp_path / "inventory-photos")),
    )
    _component, part = _catalog(db_session, seed_royal, article="PHOTO-REFRESH")
    old_key, filename, content_type = inventory.save_photo("old.jpg", _JPEG, "image/jpeg")
    part.photo_storage_key = old_key
    part.photo_filename = filename
    part.photo_content_type = content_type
    db_session.commit()
    login_as(client, seed_royal.username, "secret")
    original_refresh = type(db_session).refresh
    monkeypatch.setattr(
        type(db_session),
        "refresh",
        lambda _session, _row: (_ for _ in ()).throw(RuntimeError("refresh_failed")),
    )

    response = client.put(
        f"/inventory/catalog/parts/{part.id}/photo",
        files={"photo": ("new.png", _PNG, "image/png")},
    )

    assert response.status_code == 502
    monkeypatch.setattr(type(db_session), "refresh", original_refresh)
    db_session.expire_all()
    stored = db_session.get(InventoryCatalogPart, part.id)
    assert stored is not None
    assert stored.photo_storage_key != old_key
    assert inventory.photo_path(stored.photo_storage_key).is_file()
    assert not (tmp_path / "inventory-photos" / old_key).exists()


def test_mechanic_can_attach_photo_to_new_catalog_part(
    client, db_session, seed_park_with_tracker, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        inventory,
        "get_settings",
        lambda: SimpleNamespace(inventory_photos_dir=str(tmp_path / "inventory-photos")),
    )
    mechanic = _user(db_session, "mechanic", "photo-create-mechanic", [seed_park_with_tracker])
    _component, part = _catalog(db_session, mechanic, article="PHOTO-MECHANIC")
    login_as(client, mechanic.username, "secret")

    response = client.put(
        f"/inventory/catalog/parts/{part.id}/photo",
        files={"photo": ("part.jpg", _JPEG, "image/jpeg")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["has_photo"] is True


def test_only_royal_can_permanently_delete_global_part_graph(
    client, db_session, seed_park_with_tracker, seed_royal
):
    admin = _user(db_session, "admin", "part-delete-admin", [seed_park_with_tracker])
    component, part = _catalog(db_session, seed_royal, article="DELETE-PART")
    alias = InventoryCatalogPart(
        component_id=component.id,
        name="Delete alias",
        normalized_name="delete alias",
        article="DELETE-PART-ALIAS",
        normalized_article="delete-part-alias",
        is_active=False,
        merged_into_part_id=part.id,
        created_by=seed_royal.id,
        updated_by=seed_royal.id,
    )
    stock = InventoryParkStock(
        park_id=seed_park_with_tracker.id,
        catalog_part_id=part.id,
        quantity=1,
        updated_by=seed_royal.id,
    )
    receipt = InventoryReceipt(
        park_id=seed_park_with_tracker.id,
        receipt_date=date.today(),
        created_by=seed_royal.id,
    )
    count = InventoryCount(
        park_id=seed_park_with_tracker.id,
        name="Delete graph",
        created_by=seed_royal.id,
    )
    db_session.add_all([alias, stock, receipt, count])
    db_session.flush()
    db_session.add_all(
        [
            InventoryReceiptLine(receipt_id=receipt.id, catalog_part_id=part.id, quantity=1),
            InventoryCountLine(count_id=count.id, catalog_part_id=part.id, expected_quantity=1),
            InventoryMovement(
                catalog_part_id=part.id,
                park_id=seed_park_with_tracker.id,
                actor_user_id=seed_royal.id,
                kind="receipt",
                delta=1,
                balance_after=1,
            ),
        ]
    )
    db_session.commit()
    alias_id, stock_id, receipt_id, count_id = alias.id, stock.id, receipt.id, count.id

    login_as(client, admin.username, "secret")
    assert (
        client.delete(
            f"/inventory/catalog/parts/{part.id}", params={"permanent": "true"}
        ).status_code
        == 403
    )
    login_as(client, seed_royal.username, "secret")
    response = client.delete(f"/inventory/catalog/parts/{part.id}", params={"permanent": "true"})

    assert response.status_code == 200, response.text
    assert response.json()["deleted_part_count"] == 2
    assert db_session.get(InventoryCatalogPart, part.id) is None
    assert db_session.get(InventoryCatalogPart, alias_id) is None
    assert db_session.get(InventoryParkStock, stock_id) is None
    assert db_session.get(InventoryReceipt, receipt_id) is None
    assert db_session.get(InventoryCount, count_id) is None
    assert db_session.get(InventoryCatalogComponent, component.id) is not None


def test_part_delete_preserves_other_lines_and_movements_in_mixed_documents(
    client, db_session, seed_park_with_tracker, seed_royal
):
    component, deleted_part = _catalog(db_session, seed_royal, article="DELETE-MIXED-A")
    surviving_part = InventoryCatalogPart(
        component_id=component.id,
        name="Сохраняемая позиция",
        normalized_name="сохраняемая позиция",
        article="DELETE-MIXED-B",
        normalized_article="delete-mixed-b",
        created_by=seed_royal.id,
        updated_by=seed_royal.id,
    )
    receipt = InventoryReceipt(
        park_id=seed_park_with_tracker.id,
        receipt_date=date.today(),
        created_by=seed_royal.id,
    )
    count = InventoryCount(
        park_id=seed_park_with_tracker.id,
        name="Mixed delete graph",
        created_by=seed_royal.id,
    )
    db_session.add_all([surviving_part, receipt, count])
    db_session.flush()
    deleted_movement = InventoryMovement(
        catalog_part_id=deleted_part.id,
        park_id=seed_park_with_tracker.id,
        actor_user_id=seed_royal.id,
        kind="receipt",
        delta=1,
        balance_after=1,
    )
    surviving_movement = InventoryMovement(
        catalog_part_id=surviving_part.id,
        park_id=seed_park_with_tracker.id,
        actor_user_id=seed_royal.id,
        kind="receipt",
        delta=2,
        balance_after=2,
    )
    db_session.add_all(
        [
            InventoryReceiptLine(
                receipt_id=receipt.id, catalog_part_id=deleted_part.id, quantity=1
            ),
            InventoryReceiptLine(
                receipt_id=receipt.id, catalog_part_id=surviving_part.id, quantity=2
            ),
            InventoryCountLine(
                count_id=count.id, catalog_part_id=deleted_part.id, expected_quantity=1
            ),
            InventoryCountLine(
                count_id=count.id, catalog_part_id=surviving_part.id, expected_quantity=2
            ),
            deleted_movement,
            surviving_movement,
        ]
    )
    db_session.commit()
    receipt_id, count_id = receipt.id, count.id
    deleted_movement_id, surviving_movement_id = deleted_movement.id, surviving_movement.id
    login_as(client, seed_royal.username, "secret")

    response = client.delete(
        f"/inventory/catalog/parts/{deleted_part.id}", params={"permanent": "true"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["deleted_part_count"] == 1
    db_session.expire_all()
    assert db_session.get(InventoryReceipt, receipt_id) is not None
    assert db_session.get(InventoryCount, count_id) is not None
    assert (
        list(
            db_session.scalars(
                select(InventoryReceiptLine).where(InventoryReceiptLine.receipt_id == receipt_id)
            )
        )[0].catalog_part_id
        == surviving_part.id
    )
    assert (
        list(
            db_session.scalars(
                select(InventoryCountLine).where(InventoryCountLine.count_id == count_id)
            )
        )[0].catalog_part_id
        == surviving_part.id
    )
    assert db_session.get(InventoryMovement, deleted_movement_id) is None
    assert db_session.get(InventoryMovement, surviving_movement_id) is not None


def test_royal_permanently_deletes_component_graph(
    client, db_session, seed_park_with_tracker, seed_royal
):
    component, part = _catalog(db_session, seed_royal, article="DELETE-COMPONENT")
    db_session.add(
        InventoryParkStock(
            park_id=seed_park_with_tracker.id,
            catalog_part_id=part.id,
            quantity=0,
            updated_by=seed_royal.id,
        )
    )
    db_session.commit()
    login_as(client, seed_royal.username, "secret")

    response = client.delete(
        f"/inventory/catalog/components/{component.id}", params={"permanent": "true"}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "deleted_part_count": 1,
        "deleted_part_ids": [part.id],
        "matched_deleted_count": 1,
        "deleted_parts": [
            {
                "id": part.id,
                "name": part.name,
                "article": part.article,
                "is_active": True,
                "component_is_active": True,
                "merged_into_part_id": None,
            }
        ],
    }
    assert db_session.get(InventoryCatalogComponent, component.id) is None
    assert db_session.get(InventoryCatalogPart, part.id) is None


def test_component_delete_counts_sql_like_wildcard_matches_before_deleting(
    client, db_session, seed_royal
):
    component, first = _catalog(db_session, seed_royal, name="A", article="WILDCARD-A")
    second = InventoryCatalogPart(
        component_id=component.id,
        name="B",
        normalized_name="b",
        article="WILDCARD-B",
        normalized_article="wildcard-b",
        created_by=seed_royal.id,
        updated_by=seed_royal.id,
    )
    db_session.add(second)
    db_session.commit()
    login_as(client, seed_royal.username, "secret")

    response = client.delete(
        f"/inventory/catalog/components/{component.id}",
        params={"permanent": "true", "q": "_", "mode": "active"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["deleted_part_count"] == 2
    assert response.json()["matched_deleted_count"] == 2
    assert response.json()["deleted_part_ids"] == [first.id, second.id]


def test_permanent_delete_rolls_back_database_and_keeps_photo_on_failure(
    client, db_session, seed_royal, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        inventory,
        "get_settings",
        lambda: SimpleNamespace(inventory_photos_dir=str(tmp_path / "inventory-photos")),
    )
    _component, part = _catalog(db_session, seed_royal, article="DELETE-ROLLBACK")
    key, filename, content_type = inventory.save_photo("kept.jpg", _JPEG, "image/jpeg")
    part.photo_storage_key = key
    part.photo_filename = filename
    part.photo_content_type = content_type
    db_session.commit()
    login_as(client, seed_royal.username, "secret")
    original_commit = type(db_session).commit
    monkeypatch.setattr(
        type(db_session),
        "commit",
        lambda _session: (_ for _ in ()).throw(RuntimeError("delete_step_failed")),
    )
    response = client.delete(f"/inventory/catalog/parts/{part.id}", params={"permanent": "true"})

    assert response.status_code == 502
    monkeypatch.setattr(type(db_session), "commit", original_commit)
    assert db_session.get(InventoryCatalogPart, part.id) is not None
    assert inventory.photo_path(key).is_file()


_INVENTORY_ACTION_PERMISSION = {
    "stock-settings": "inventory.stock.manage",
    "receive-and-inventory": "inventory.documents.post",
    "labels-and-export": "inventory.export",
    "catalog-delete-or-merge": "inventory.catalog.manage",
}


@pytest.mark.parametrize(
    ("action", "slug", "allowed"),
    [
        pytest.param("stock-settings", "royal", True, id="stock-settings-royal-allow"),
        pytest.param("stock-settings", "admin", True, id="stock-settings-admin-allow"),
        pytest.param("stock-settings", "operator", False, id="stock-settings-operator-deny"),
        pytest.param("stock-settings", "mechanic", True, id="stock-settings-mechanic-allow"),
        pytest.param("stock-settings", "driver", False, id="stock-settings-driver-deny"),
        pytest.param("stock-settings", "restricted", True, id="stock-settings-restricted-allow"),
        pytest.param(
            "receive-and-inventory", "royal", True, id="receive-and-inventory-royal-allow"
        ),
        pytest.param(
            "receive-and-inventory", "admin", True, id="receive-and-inventory-admin-allow"
        ),
        pytest.param(
            "receive-and-inventory", "operator", False, id="receive-and-inventory-operator-deny"
        ),
        pytest.param(
            "receive-and-inventory", "mechanic", True, id="receive-and-inventory-mechanic-allow"
        ),
        pytest.param(
            "receive-and-inventory", "driver", False, id="receive-and-inventory-driver-deny"
        ),
        pytest.param(
            "receive-and-inventory", "restricted", True, id="receive-and-inventory-restricted-allow"
        ),
        pytest.param("labels-and-export", "royal", True, id="labels-and-export-royal-allow"),
        pytest.param("labels-and-export", "admin", True, id="labels-and-export-admin-allow"),
        pytest.param("labels-and-export", "operator", False, id="labels-and-export-operator-deny"),
        pytest.param("labels-and-export", "mechanic", True, id="labels-and-export-mechanic-allow"),
        pytest.param("labels-and-export", "driver", False, id="labels-and-export-driver-deny"),
        pytest.param(
            "labels-and-export", "restricted", True, id="labels-and-export-restricted-allow"
        ),
        pytest.param(
            "catalog-delete-or-merge", "royal", True, id="catalog-delete-or-merge-royal-allow"
        ),
        pytest.param(
            "catalog-delete-or-merge", "admin", True, id="catalog-delete-or-merge-admin-allow"
        ),
        pytest.param(
            "catalog-delete-or-merge", "operator", False, id="catalog-delete-or-merge-operator-deny"
        ),
        pytest.param(
            "catalog-delete-or-merge", "mechanic", False, id="catalog-delete-or-merge-mechanic-deny"
        ),
        pytest.param(
            "catalog-delete-or-merge", "driver", False, id="catalog-delete-or-merge-driver-deny"
        ),
        pytest.param(
            "catalog-delete-or-merge",
            "restricted",
            True,
            id="catalog-delete-or-merge-restricted-allow",
        ),
    ],
)
def test_inventory_permission_role_matrix(db_session, action, slug, allowed):
    permission_key = _INVENTORY_ACTION_PERMISSION[action]
    if slug == "restricted":
        permission = db_session.scalar(select(Permission).where(Permission.key == permission_key))
        role = Role(slug=f"matrix-{action}", name=f"Matrix {action}", permissions=[permission])
        db_session.add(role)
        db_session.flush()
        actor = User(
            username=f"matrix-{action}",
            password_hash=hash_password("secret"),
            role_id=role.id,
            access_status="approved",
            is_active=True,
        )
        db_session.add(actor)
        db_session.commit()
    else:
        actor = _user(db_session, slug, f"matrix-permission-{action}-{slug}")
    assert has_permission(db_session, actor, permission_key) is allowed


@pytest.mark.parametrize(
    ("action", "slug", "expected"),
    [
        pytest.param("stock-settings", "royal", 200, id="stock-settings-royal-allow"),
        pytest.param("stock-settings", "admin", 200, id="stock-settings-admin-allow"),
        pytest.param("stock-settings", "operator", 403, id="stock-settings-operator-deny"),
        pytest.param("stock-settings", "mechanic", 200, id="stock-settings-mechanic-allow"),
        pytest.param("stock-settings", "driver", 403, id="stock-settings-driver-deny"),
        pytest.param("stock-settings", "restricted", 200, id="stock-settings-restricted-allow"),
        pytest.param("receive-and-inventory", "royal", 404, id="receive-and-inventory-royal-allow"),
        pytest.param("receive-and-inventory", "admin", 404, id="receive-and-inventory-admin-allow"),
        pytest.param(
            "receive-and-inventory", "operator", 403, id="receive-and-inventory-operator-deny"
        ),
        pytest.param(
            "receive-and-inventory", "mechanic", 404, id="receive-and-inventory-mechanic-allow"
        ),
        pytest.param(
            "receive-and-inventory", "driver", 403, id="receive-and-inventory-driver-deny"
        ),
        pytest.param(
            "receive-and-inventory", "restricted", 404, id="receive-and-inventory-restricted-allow"
        ),
        pytest.param("labels-and-export", "royal", 200, id="labels-and-export-royal-allow"),
        pytest.param("labels-and-export", "admin", 200, id="labels-and-export-admin-allow"),
        pytest.param("labels-and-export", "operator", 403, id="labels-and-export-operator-deny"),
        pytest.param("labels-and-export", "mechanic", 200, id="labels-and-export-mechanic-allow"),
        pytest.param("labels-and-export", "driver", 403, id="labels-and-export-driver-deny"),
        pytest.param(
            "labels-and-export", "restricted", 200, id="labels-and-export-restricted-allow"
        ),
        pytest.param(
            "catalog-delete-or-merge", "royal", 200, id="catalog-delete-or-merge-royal-allow"
        ),
        pytest.param(
            "catalog-delete-or-merge", "admin", 200, id="catalog-delete-or-merge-admin-allow"
        ),
        pytest.param(
            "catalog-delete-or-merge", "operator", 403, id="catalog-delete-or-merge-operator-deny"
        ),
        pytest.param(
            "catalog-delete-or-merge", "mechanic", 403, id="catalog-delete-or-merge-mechanic-deny"
        ),
        pytest.param(
            "catalog-delete-or-merge", "driver", 403, id="catalog-delete-or-merge-driver-deny"
        ),
        pytest.param(
            "catalog-delete-or-merge",
            "restricted",
            200,
            id="catalog-delete-or-merge-restricted-allow",
        ),
    ],
)
def test_inventory_action_role_matrix(
    client, db_session, seed_park_with_tracker, action, slug, expected
):
    if slug == "restricted":
        action_permission = _INVENTORY_ACTION_PERMISSION[action]
        permission_keys = {"nav.inventory", action_permission}
        role = Role(
            slug=f"restricted_{action}",
            name=f"Restricted {action}",
            permissions=list(
                db_session.scalars(select(Permission).where(Permission.key.in_(permission_keys)))
            ),
        )
        db_session.add(role)
        db_session.flush()
        actor = User(
            username=f"matrix-{action}-restricted",
            password_hash=hash_password("secret"),
            role_id=role.id,
            access_status="approved",
            is_active=True,
        )
        db_session.add(actor)
        db_session.flush()
        db_session.add(UserPark(user_id=actor.id, park_id=seed_park_with_tracker.id))
        db_session.commit()
    else:
        actor = _user(
            db_session,
            slug,
            f"matrix-{action}-{slug}",
            [seed_park_with_tracker],
        )
    _, part = _catalog(db_session, actor, article=f"MATRIX-{action}-{slug}")
    login_as(client, actor.username, "secret")

    if action == "stock-settings":
        response = client.put(
            f"/inventory/parks/{seed_park_with_tracker.id}/stocks/{part.id}",
            json={"minimum_quantity": 2, "location": "A-1", "is_active": True},
        )
    elif action == "catalog-delete-or-merge":
        response = client.patch(f"/inventory/catalog/parts/{part.id}", json={"is_active": False})
    elif action == "receive-and-inventory":
        response = client.post(
            f"/inventory/parks/{seed_park_with_tracker.id}/receipts/999/post",
            json={"revision": "missing"},
        )
    else:
        response = client.get(f"/inventory/export?park_id={seed_park_with_tracker.id}&format=csv")

    assert response.status_code == expected, response.text


def test_catalog_search_exposes_exact_contract_zero_stock_and_mechanic_scope(
    client, db_session, seed_park_with_tracker
):
    foreign = Park(name="Foreign", tag="Foreign", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    mechanic = _user(db_session, "mechanic", "catalog-mechanic", [seed_park_with_tracker])
    component, part = _catalog(db_session, mechanic, name="ABC тяга", article=" AbC-01 ")
    login_as(client, mechanic.username, "secret")

    response = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "q": "abc"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {
                "id": part.id,
                "component_id": component.id,
                "component_name": "Подвязка",
                "name": "ABC тяга",
                "article": " AbC-01 ",
                "is_active": True,
                "has_photo": False,
                "quantity": 0,
                "minimum_quantity": 0,
                "location": None,
                "stock_is_active": False,
            }
        ],
        "limit": 50,
        "offset": 0,
        "total": 1,
    }
    assert (
        client.get("/inventory/catalog/search", params={"park_id": foreign.id}).status_code == 403
    )


def test_catalog_search_filters_and_sorts_by_normalized_values(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "filter-mechanic", [seed_park_with_tracker])
    component, second = _catalog(db_session, mechanic, name="  яблоко", article="z-2")
    first = InventoryCatalogPart(
        component_id=component.id,
        name="Абрикос",
        normalized_name="абрикос",
        article="A-1",
        normalized_article="a-1",
        created_by=mechanic.id,
        updated_by=mechanic.id,
    )
    db_session.add(first)
    db_session.flush()
    db_session.add(
        InventoryParkStock(
            park_id=seed_park_with_tracker.id,
            catalog_part_id=first.id,
            quantity=1,
            minimum_quantity=2,
            location=None,
            updated_by=mechanic.id,
        )
    )
    db_session.commit()
    login_as(client, mechanic.username, "secret")

    all_rows = client.get(
        "/inventory/catalog/search", params={"park_id": seed_park_with_tracker.id}
    ).json()
    assert [row["id"] for row in all_rows["items"]] == [first.id, second.id]
    filtered = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "stock_filter": "below_minimum"},
    ).json()
    assert [row["id"] for row in filtered["items"]] == [first.id]


def test_catalog_archived_mode_is_admin_only_and_supports_restore(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "archive-browser-admin")
    mechanic = _user(db_session, "mechanic", "archive-browser-mechanic", [seed_park_with_tracker])
    component, part = _catalog(db_session, admin, article="ARCHIVE-FIND")
    login_as(client, admin.username, "secret")
    assert (
        client.patch(f"/inventory/catalog/parts/{part.id}", json={"is_active": False}).status_code
        == 200
    )

    active = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "q": "ARCHIVE-FIND"},
    )
    archived = client.get(
        "/inventory/catalog/search",
        params={
            "park_id": seed_park_with_tracker.id,
            "q": "ARCHIVE-FIND",
            "mode": "archived",
        },
    )
    all_rows = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "mode": "all"},
    )
    assert active.json()["items"] == []
    assert [row["id"] for row in archived.json()["items"]] == [part.id]
    assert part.id in [row["id"] for row in all_rows.json()["items"]]

    assert (
        client.patch(
            f"/inventory/catalog/components/{component.id}", json={"is_active": False}
        ).status_code
        == 200
    )
    assert [
        row["id"]
        for row in client.get(
            "/inventory/catalog/search",
            params={"park_id": seed_park_with_tracker.id, "mode": "archived"},
        ).json()["items"]
    ] == [part.id]

    login_as(client, mechanic.username, "secret")
    assert (
        client.get(
            "/inventory/catalog/search",
            params={"park_id": seed_park_with_tracker.id, "mode": "archived"},
        ).status_code
        == 403
    )

    login_as(client, admin.username, "secret")
    assert (
        client.patch(
            f"/inventory/catalog/components/{component.id}", json={"is_active": True}
        ).status_code
        == 200
    )
    restored = client.patch(f"/inventory/catalog/parts/{part.id}", json={"is_active": True})
    assert restored.status_code == 200, restored.text
    assert (
        client.get(
            "/inventory/catalog/search",
            params={"park_id": seed_park_with_tracker.id, "q": "ARCHIVE-FIND"},
        ).json()["items"][0]["id"]
        == part.id
    )


def test_catalog_component_metadata_and_exact_part_are_scoped_and_independent_of_search_page(
    client, db_session, seed_park_with_tracker
):
    foreign = Park(name="Foreign metadata", tag="Foreign-metadata", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    mechanic = _user(db_session, "mechanic", "metadata-mechanic", [seed_park_with_tracker])
    component, part = _catalog(db_session, mechanic)
    db_session.add(
        InventoryParkStock(
            park_id=seed_park_with_tracker.id,
            catalog_part_id=part.id,
            quantity=7,
            minimum_quantity=2,
            location="A-7",
            updated_by=mechanic.id,
        )
    )
    db_session.commit()
    login_as(client, mechanic.username, "secret")

    components = client.get(
        "/inventory/catalog/components",
        params={"park_id": seed_park_with_tracker.id, "limit": 1, "offset": 0},
    )
    assert components.status_code == 200, components.text
    assert components.json() == {
        "items": [{"id": component.id, "name": "Подвязка", "is_active": True, "has_photo": False}],
        "limit": 1,
        "offset": 0,
        "total": 1,
    }

    exact = client.get(
        f"/inventory/catalog/parts/{part.id}",
        params={"park_id": seed_park_with_tracker.id},
    )
    assert exact.status_code == 200, exact.text
    assert exact.json() == {
        "id": part.id,
        "component_id": component.id,
        "component_name": "Подвязка",
        "name": "Тяга",
        "article": "ABC-01",
        "is_active": True,
        "has_photo": False,
        "quantity": 7,
        "minimum_quantity": 2,
        "location": "A-7",
        "stock_is_active": True,
    }
    assert (
        client.get(
            f"/inventory/catalog/parts/{part.id}", params={"park_id": foreign.id}
        ).status_code
        == 403
    )


def test_mechanic_can_create_missing_catalog_but_only_admin_can_patch_existing(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "create-mechanic", [seed_park_with_tracker])
    admin = _user(db_session, "admin", "catalog-admin")
    login_as(client, mechanic.username, "secret")
    component_response = client.post(
        "/inventory/catalog/components",
        json={"park_id": seed_park_with_tracker.id, "name": "  Колесо  "},
    )
    assert component_response.status_code == 201, component_response.text
    assert component_response.json() == {
        "id": component_response.json()["id"],
        "name": "Колесо",
        "is_active": True,
        "has_photo": False,
    }
    part_response = client.post(
        "/inventory/catalog/parts",
        json={
            "park_id": seed_park_with_tracker.id,
            "component_id": component_response.json()["id"],
            "name": "Диск",
            "article": " WH-01 ",
        },
    )
    assert part_response.status_code == 201, part_response.text
    assert part_response.json() == {
        "id": part_response.json()["id"],
        "component_id": component_response.json()["id"],
        "name": "Диск",
        "article": "WH-01",
        "is_active": True,
        "has_photo": False,
    }
    part_id = part_response.json()["id"]
    assert (
        client.patch(f"/inventory/catalog/parts/{part_id}", json={"name": "Нет"}).status_code == 403
    )

    login_as(client, admin.username, "secret")
    patched = client.patch(
        f"/inventory/catalog/parts/{part_id}", json={"name": "  Исправлено  ", "is_active": False}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Исправлено"
    assert patched.json()["is_active"] is False

    component_id = component_response.json()["id"]
    component_patched = client.patch(
        f"/inventory/catalog/components/{component_id}",
        json={"name": "  Колесо в сборе  ", "is_active": False},
    )
    assert component_patched.status_code == 200, component_patched.text
    assert component_patched.json() == {
        "id": component_id,
        "name": "Колесо в сборе",
        "is_active": False,
        "has_photo": False,
    }


def test_duplicate_active_article_returns_existing_part(client, db_session, seed_park_with_tracker):
    mechanic = _user(db_session, "mechanic", "duplicate-mechanic", [seed_park_with_tracker])
    component, existing = _catalog(db_session, mechanic, article=" AbC-01 ")
    login_as(client, mechanic.username, "secret")

    response = client.post(
        "/inventory/catalog/parts",
        json={
            "park_id": seed_park_with_tracker.id,
            "component_id": component.id,
            "name": "Дубль",
            "article": "  abc-01  ",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "inventory_article_exists", "existing_part_id": existing.id}
    }


def test_catalog_api_accepts_max_length_values_whose_casefold_expands(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "expanding-casefold", [seed_park_with_tracker])
    login_as(client, mechanic.username, "secret")
    expanding = "ß" * 128
    component = client.post(
        "/inventory/catalog/components",
        json={"park_id": seed_park_with_tracker.id, "name": expanding},
    )
    assert component.status_code == 201, component.text
    part = client.post(
        "/inventory/catalog/parts",
        json={
            "park_id": seed_park_with_tracker.id,
            "component_id": component.json()["id"],
            "name": expanding,
            "article": expanding,
        },
    )
    assert part.status_code == 201, part.text
    db_session.expire_all()
    stored = db_session.get(InventoryCatalogPart, part.json()["id"])
    assert stored.normalized_name == "ss" * 128
    assert stored.normalized_article == "ss" * 128
    found = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "q": "SS" * 128},
    )
    assert [item["id"] for item in found.json()["items"]] == [part.json()["id"]]


def test_stock_settings_reject_mismatched_park_part_and_never_accept_quantity(
    client, db_session, seed_park_with_tracker
):
    mechanic = _user(db_session, "mechanic", "stock-mechanic", [seed_park_with_tracker])
    _, part = _catalog(db_session, mechanic)
    foreign = Park(name="Foreign", tag="Foreign", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    login_as(client, mechanic.username, "secret")

    unknown = client.put(
        f"/inventory/parks/{seed_park_with_tracker.id}/stocks/{part.id + 999}",
        json={"minimum_quantity": 2, "location": "A-1", "is_active": True},
    )
    assert unknown.status_code == 404
    assert (
        client.put(
            f"/inventory/parks/{foreign.id}/stocks/{part.id}",
            json={"minimum_quantity": 2, "location": "A-1", "is_active": True},
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/inventory/parks/{seed_park_with_tracker.id}/stocks/{part.id}",
            json={"quantity": 100},
        ).status_code
        == 422
    )
    updated = client.put(
        f"/inventory/parks/{seed_park_with_tracker.id}/stocks/{part.id}",
        json={"minimum_quantity": 2, "location": "  A-1  ", "is_active": True},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json() == {
        "park_id": seed_park_with_tracker.id,
        "catalog_part_id": part.id,
        "quantity": 0,
        "minimum_quantity": 2,
        "location": "A-1",
        "is_active": True,
        "version": 2,
    }


def test_archived_component_hides_parts_and_conflicting_restores_return_409(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "restore-admin")
    component, active = _catalog(db_session, admin, article="DUP-1")
    archived_component = InventoryCatalogComponent(
        name="  Подвязка  ",
        normalized_name="подвязка",
        is_active=False,
        created_by=admin.id,
        updated_by=admin.id,
    )
    archived_part = InventoryCatalogPart(
        component_id=component.id,
        name="Archived",
        normalized_name="archived",
        article=" dup-1 ",
        normalized_article="dup-1",
        is_active=False,
        created_by=admin.id,
        updated_by=admin.id,
    )
    hidden_component = InventoryCatalogComponent(
        name="Hidden component",
        normalized_name="hidden component",
        is_active=False,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db_session.add(hidden_component)
    db_session.flush()
    hidden_part = InventoryCatalogPart(
        component_id=hidden_component.id,
        name="Hidden part",
        normalized_name="hidden part",
        article="HIDDEN",
        normalized_article="hidden",
        created_by=admin.id,
        updated_by=admin.id,
    )
    db_session.add_all([archived_component, archived_part, hidden_part])
    db_session.commit()
    login_as(client, admin.username, "secret")

    searched = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "q": "HIDDEN"},
    )
    assert searched.status_code == 200
    assert searched.json()["items"] == []

    component_restore = client.patch(
        f"/inventory/catalog/components/{archived_component.id}", json={"is_active": True}
    )
    assert component_restore.status_code == 409
    assert component_restore.json()["detail"] == {
        "code": "inventory_component_exists",
        "existing_component_id": component.id,
    }
    part_restore = client.patch(
        f"/inventory/catalog/parts/{archived_part.id}", json={"is_active": True}
    )
    assert part_restore.status_code == 409
    assert part_restore.json()["detail"] == {
        "code": "inventory_article_exists",
        "existing_part_id": active.id,
    }


def test_merge_transfers_stock_and_movement_history_and_archives_source(
    client, db_session, seed_park_with_tracker
):
    other = Park(name="Other", tag="Other", is_active=True)
    db_session.add(other)
    db_session.commit()
    admin = _user(db_session, "admin", "merge-admin")
    component, target = _catalog(db_session, admin, name="Target", article="TARGET")
    source = InventoryCatalogPart(
        component_id=component.id,
        name="Source",
        normalized_name="source",
        article="SOURCE",
        normalized_article="source",
        created_by=admin.id,
        updated_by=admin.id,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add_all(
        [
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=target.id,
                quantity=2,
                minimum_quantity=1,
                location=None,
                is_active=False,
                updated_by=admin.id,
            ),
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=source.id,
                quantity=3,
                minimum_quantity=7,
                location="Source shelf",
                is_active=True,
                updated_by=admin.id,
            ),
            InventoryParkStock(
                park_id=other.id,
                catalog_part_id=target.id,
                quantity=0,
                minimum_quantity=9,
                location="Target B",
                updated_by=admin.id,
            ),
            InventoryParkStock(
                park_id=other.id,
                catalog_part_id=source.id,
                quantity=4,
                minimum_quantity=2,
                location="B",
                updated_by=admin.id,
            ),
            InventoryMovement(
                catalog_part_id=source.id,
                park_id=seed_park_with_tracker.id,
                actor_user_id=admin.id,
                kind="receipt",
                delta=3,
                balance_before=0,
                balance_after=3,
            ),
        ]
    )
    db_session.commit()
    login_as(client, admin.username, "secret")

    response = client.post(
        f"/inventory/catalog/parts/{source.id}/merge",
        json={"target_part_id": target.id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == target.id
    db_session.refresh(source)
    assert source.is_active is False
    stocks = {
        (row.park_id, row.catalog_part_id): (row.quantity, row.minimum_quantity, row.location)
        for row in db_session.scalars(select(InventoryParkStock))
    }
    assert stocks[(seed_park_with_tracker.id, target.id)] == (5, 7, "Source shelf")
    merged_target_stock = db_session.scalar(
        select(InventoryParkStock).where(
            InventoryParkStock.park_id == seed_park_with_tracker.id,
            InventoryParkStock.catalog_part_id == target.id,
        )
    )
    assert merged_target_stock.is_active is True
    assert stocks[(other.id, target.id)] == (4, 9, "Target B")
    archived = client.get(
        "/inventory/catalog/search",
        params={"park_id": seed_park_with_tracker.id, "mode": "archived"},
    )
    assert source.id not in [row["id"] for row in archived.json()["items"]]
    historical_receipt = db_session.scalar(
        select(InventoryMovement).where(InventoryMovement.kind == "receipt")
    )
    assert historical_receipt.catalog_part_id == source.id
    merge_audits = list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == "inventory.catalog.part.merged")
        )
    )
    assert {row.park_id for row in merge_audits} == {seed_park_with_tracker.id, other.id}
    assert all(row.actor_user_id == admin.id and row.actor_role == "admin" for row in merge_audits)
    assert all('"changed_fields"' in row.detail for row in merge_audits)


def test_postgresql_merge_acquires_exclusive_alias_lock_before_catalog_rows(monkeypatch):
    events = []

    class Bind:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

    class RecordingSession:
        def get_bind(self):
            return Bind()

        def execute(self, statement):
            events.append(str(statement))

    monkeypatch.setattr(
        inventory_catalog.inventory_access,
        "require_catalog_manage",
        lambda *_args: None,
    )

    def record_catalog_row_lock(_db, _part_ids):
        events.append("catalog-row-lock")
        return {}

    monkeypatch.setattr(inventory_catalog, "lock_catalog_parts", record_catalog_row_lock)

    with pytest.raises(LookupError, match="inventory_part_not_found"):
        inventory_catalog.merge_parts(RecordingSession(), object(), 10, 20)

    assert len(events) == 2
    assert "pg_advisory_xact_lock" in events[0]
    assert "pg_advisory_xact_lock_shared" not in events[0]
    assert events[1] == "catalog-row-lock"


def test_inventory_mutations_write_attributed_audit_entries(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "audit-admin")
    _, part = _catalog(db_session, admin)
    login_as(client, admin.username, "secret")

    assert (
        client.patch(f"/inventory/catalog/parts/{part.id}", json={"name": "Changed"}).status_code
        == 200
    )
    assert (
        client.put(
            f"/inventory/parks/{seed_park_with_tracker.id}/stocks/{part.id}",
            json={"minimum_quantity": 2, "location": "A", "is_active": True},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/inventory/parts/{part.id}/movements",
            json={
                "park_id": seed_park_with_tracker.id,
                "kind": "receipt",
                "quantity": 1,
                "note": "delivery",
            },
        ).status_code
        == 201
    )
    assert (
        client.patch(f"/inventory/catalog/parts/{part.id}", json={"is_active": False}).status_code
        == 200
    )

    rows = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.actor_user_id == admin.id,
                AuditLog.action.like("inventory.%"),
            )
        )
    )
    assert {row.action for row in rows} >= {
        "inventory.catalog.part.updated",
        "inventory.stock.configured",
        "inventory.stock.moved",
    }
    assert all(row.actor_role == "admin" for row in rows)
    assert all('"changed_fields"' in (row.detail or "") for row in rows)
    stock_rows = [row for row in rows if row.action != "inventory.catalog.part.updated"]
    assert all(row.park_id == seed_park_with_tracker.id for row in stock_rows)
    movement_row = next(row for row in rows if row.action == "inventory.stock.moved")
    assert json.loads(movement_row.detail)["changed_fields"] == [
        "kind",
        "note",
        "quantity",
    ]
    assert any(
        json.loads(row.detail)["changed_fields"] == ["is_active"]
        for row in rows
        if row.action == "inventory.catalog.part.updated"
    )


def test_concurrent_first_stock_deltas_are_not_lost(db_engine, db_session, seed_park_with_tracker):
    mechanic = _user(db_session, "mechanic", "concurrent-mechanic", [seed_park_with_tracker])
    _, part = _catalog(db_session, mechanic, article="CONCURRENT")
    mechanic_id = mechanic.id
    park_id = seed_park_with_tracker.id
    part_id = part.id

    def add_one(source_id):
        with Session(db_engine) as session:
            user = session.get(User, mechanic_id)
            inventory_stock.apply_stock_delta(
                session,
                user=user,
                park_id=park_id,
                catalog_part_id=part_id,
                delta=1,
                kind="adjustment",
                source_kind="manual",
                source_id=source_id,
                note=None,
            )
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(add_one, ["one", "two"]))

    db_session.expire_all()
    stock = db_session.scalar(select(InventoryParkStock))
    assert stock.quantity == 2
    movements = list(db_session.scalars(select(InventoryMovement)))
    assert sorted((row.balance_before, row.balance_after) for row in movements) == [
        (0, 1),
        (1, 2),
    ]


def test_merge_preserves_colliding_source_history_and_document_identity(
    client, db_session, seed_park_with_tracker
):
    admin = _user(db_session, "admin", "merge-source-admin")
    component, target = _catalog(db_session, admin, name="Target", article="MERGE-TARGET")
    source = InventoryCatalogPart(
        component_id=component.id,
        name="Source",
        normalized_name="source",
        article="MERGE-SOURCE",
        normalized_article="merge-source",
        created_by=admin.id,
        updated_by=admin.id,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add_all(
        [
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=target.id,
                quantity=2,
                updated_by=admin.id,
            ),
            InventoryParkStock(
                park_id=seed_park_with_tracker.id,
                catalog_part_id=source.id,
                quantity=3,
                updated_by=admin.id,
            ),
            InventoryMovement(
                catalog_part_id=target.id,
                park_id=seed_park_with_tracker.id,
                actor_user_id=admin.id,
                kind="receipt",
                delta=2,
                balance_before=0,
                balance_after=2,
                source_kind="receipt",
                source_id="receipt-line-7",
            ),
            InventoryMovement(
                catalog_part_id=source.id,
                park_id=seed_park_with_tracker.id,
                actor_user_id=admin.id,
                kind="receipt",
                delta=3,
                balance_before=0,
                balance_after=3,
                source_kind="receipt",
                source_id="receipt-line-7",
                note="x" * 490,
            ),
        ]
    )
    db_session.commit()
    historical = list(
        db_session.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.kind == "receipt")
            .order_by(InventoryMovement.id)
        )
    )
    before = {
        row.id: (
            row.catalog_part_id,
            row.source_kind,
            row.source_id,
            row.note,
            row.balance_before,
            row.balance_after,
        )
        for row in historical
    }
    login_as(client, admin.username, "secret")

    response = client.post(
        f"/inventory/catalog/parts/{source.id}/merge",
        json={"target_part_id": target.id},
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    stock = db_session.scalar(
        select(InventoryParkStock).where(InventoryParkStock.catalog_part_id == target.id)
    )
    assert stock.quantity == 5
    after = {
        row.id: (
            row.catalog_part_id,
            row.source_kind,
            row.source_id,
            row.note,
            row.balance_before,
            row.balance_after,
        )
        for row in db_session.scalars(
            select(InventoryMovement).where(InventoryMovement.id.in_(before))
        )
    }
    assert after == before
    document_movement = db_session.scalar(
        select(InventoryMovement).where(
            InventoryMovement.catalog_part_id == source.id,
            InventoryMovement.park_id == seed_park_with_tracker.id,
            InventoryMovement.source_kind == "receipt",
            InventoryMovement.source_id == "receipt-line-7",
        )
    )
    assert document_movement is not None
    history_response = client.get(
        "/inventory/movements", params={"park_id": seed_park_with_tracker.id}
    )
    assert history_response.status_code == 200, history_response.text
    assert any(
        row["id"] == document_movement.id
        and row["catalog_part_id"] == source.id
        and row["part_id"] == -target.id
        for row in history_response.json()
    )
    history = list(
        db_session.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.catalog_part_id.in_([source.id, target.id]))
            .order_by(InventoryMovement.id)
        )
    )
    assert len(history) == 4
    assert sum(row.delta for row in history if row.kind == "receipt") == 5
    assert all(row.balance_after - row.balance_before == row.delta for row in history)
    assert (
        sum(row.source_kind == "receipt" and row.source_id == "receipt-line-7" for row in history)
        == 2
    )


@pytest.mark.parametrize("kind", ["component", "part"])
def test_catalog_create_stale_uniqueness_precheck_returns_existing_id(
    db_session, seed_park_with_tracker, monkeypatch, kind
):
    admin = _user(db_session, "admin", f"race-{kind}-admin")
    component, part = _catalog(db_session, admin, article="RACE-1")
    original_scalar = db_session.scalar
    skipped = False

    def stale_precheck(statement, *args, **kwargs):
        nonlocal skipped
        sql = str(statement)
        marker = (
            "inventory_catalog_components.normalized_name"
            if kind == "component"
            else "inventory_catalog_parts.normalized_article"
        )
        if not skipped and marker in sql and "is_active" in sql:
            skipped = True
            return None
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", stale_precheck)
    with pytest.raises(inventory_stock.InventoryConflict) as raised:
        if kind == "component":
            inventory_catalog.create_component(
                db_session,
                admin,
                park_id=seed_park_with_tracker.id,
                name="  Подвязка ",
            )
        else:
            inventory_catalog.create_part(
                db_session,
                admin,
                park_id=seed_park_with_tracker.id,
                component_id=component.id,
                name="Duplicate",
                article=" race-1 ",
            )

    assert raised.value.code == (
        "inventory_component_exists" if kind == "component" else "inventory_article_exists"
    )
    assert (
        raised.value.existing_component_id if kind == "component" else raised.value.existing_part_id
    ) == (component.id if kind == "component" else part.id)


@pytest.mark.parametrize(
    ("kind", "operation"),
    [
        ("component", "rename"),
        ("component", "restore"),
        ("part", "rename"),
        ("part", "restore"),
    ],
)
def test_catalog_update_stale_uniqueness_precheck_returns_409_with_existing_id(
    client, db_session, seed_park_with_tracker, monkeypatch, kind, operation
):
    admin = _user(db_session, "admin", f"update-race-{kind}-{operation}")
    component, part = _catalog(db_session, admin, article="RACE-1")
    if kind == "component":
        conflicting = InventoryCatalogComponent(
            name="Source component",
            normalized_name=("source component" if operation == "rename" else "подвязка"),
            is_active=operation == "rename",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db_session.add(conflicting)
        db_session.commit()
        path = f"/inventory/catalog/components/{conflicting.id}"
        payload = {"name": "  Подвязка "} if operation == "rename" else {"is_active": True}
        marker = "inventory_catalog_components.normalized_name"
        expected_detail = {
            "code": "inventory_component_exists",
            "existing_component_id": component.id,
        }
    else:
        conflicting = InventoryCatalogPart(
            component_id=component.id,
            name="Source part",
            normalized_name="source part",
            article="RACE-2" if operation == "rename" else "race-1",
            normalized_article="race-2" if operation == "rename" else "race-1",
            is_active=operation == "rename",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db_session.add(conflicting)
        db_session.commit()
        path = f"/inventory/catalog/parts/{conflicting.id}"
        payload = {"article": " race-1 "} if operation == "rename" else {"is_active": True}
        marker = "inventory_catalog_parts.normalized_article"
        expected_detail = {
            "code": "inventory_article_exists",
            "existing_part_id": part.id,
        }

    login_as(client, admin.username, "secret")
    original_scalar = db_session.scalar
    skipped = False

    def stale_precheck(statement, *args, **kwargs):
        nonlocal skipped
        sql = str(statement)
        if not skipped and marker in sql and "is_active" in sql and "!=" in sql:
            skipped = True
            return None
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", stale_precheck)

    response = client.patch(path, json=payload)

    assert skipped is True
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == expected_detail


def test_postgresql_first_stock_creation_uses_conflict_safe_insert():
    statement = inventory_stock.stock_insert_if_missing_statement(
        "postgresql", park_id=7, catalog_part_id=11
    )

    compiled = str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert compiled == (
        "INSERT INTO inventory_park_stocks (park_id, catalog_part_id, quantity, "
        "minimum_quantity, is_active, version) VALUES (7, 11, 0, 0, true, 1) "
        "ON CONFLICT (park_id, catalog_part_id) DO NOTHING RETURNING "
        "inventory_park_stocks.id"
    )
