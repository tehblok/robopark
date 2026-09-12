from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.models import (
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryMovement,
    InventoryParkStock,
    User,
)
from robopark_api.services import inventory_catalog, inventory_stock
from robopark_api.services.rbac_seed import ensure_rbac_catalog

API_DIR = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "target_quantity,legacy_target", [(None, False), (0, False), (2, False), (2, True)]
)
@pytest.mark.parametrize("transitive", [False, True])
@pytest.mark.parametrize("reuse_article", [False, True])
def test_runtime_merge_downgrade_preserves_total_and_legacy_history(
    sqlite_database_url, monkeypatch, target_quantity, legacy_target, transitive, reuse_article
):
    config, engine = _upgrade_legacy_inventory(sqlite_database_url, monkeypatch)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO inventory_components (id, park_id, name) VALUES (10, 1, 'Wheels')")
        )
        connection.execute(
            text(
                "INSERT INTO inventory_parts "
                "(id, park_id, component_id, name, article, quantity, location) "
                "VALUES (100, 1, 10, 'Source', 'SOURCE', 5, 'A-1')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO inventory_movements "
                "(id, part_id, park_id, actor_user_id, kind, delta, balance_after, note) "
                "VALUES (1000, 100, 1, 999, 'receipt', 5, 5, 'legacy source')"
            )
        )
        if legacy_target:
            connection.execute(
                text(
                    "INSERT INTO inventory_parts "
                    "(id, park_id, component_id, name, article, quantity, location) "
                    "VALUES (200, 1, 10, 'Target', 'TARGET', 2, 'B-1')"
                )
            )
    command.upgrade(config, "0026_global_inventory_workflows")
    with Session(engine) as session:
        ensure_rbac_catalog(session)
        actor = session.get(User, 999)
        source = session.scalar(
            select(InventoryCatalogPart).where(InventoryCatalogPart.article == "SOURCE")
        )
        source_id = source.id
        if legacy_target:
            target = session.scalar(
                select(InventoryCatalogPart).where(InventoryCatalogPart.article == "TARGET")
            )
        else:
            target = InventoryCatalogPart(
                component_id=source.component_id,
                name="Target",
                normalized_name="target",
                article="TARGET",
                normalized_article="target",
            )
            session.add(target)
            session.flush()
            if target_quantity is not None:
                session.add(
                    InventoryParkStock(
                        park_id=1,
                        catalog_part_id=target.id,
                        quantity=target_quantity,
                    )
                )
        session.commit()
        inventory_catalog.merge_parts(session, actor, source.id, target.id)
        if transitive:
            final = InventoryCatalogPart(
                component_id=source.component_id,
                name="Final",
                normalized_name="final",
                article="FINAL",
                normalized_article="final",
            )
            session.add(final)
            session.commit()
            inventory_catalog.merge_parts(session, actor, target.id, final.id)
            target = final
        # Runtime writes and renamed/reused articles must follow persistent bindings.
        inventory_catalog.update_part(session, actor, target.id, {"article": "RENAMED"})
        replacement = InventoryCatalogPart(
            component_id=source.component_id,
            name="Replacement",
            normalized_name="replacement",
            article="SOURCE" if reuse_article else "REPLACEMENT",
            normalized_article="source" if reuse_article else "replacement",
        )
        session.add(replacement)
        session.flush()
        inventory_stock.apply_stock_delta(
            session,
            user=actor,
            park_id=1,
            catalog_part_id=replacement.id,
            delta=4,
            kind="receipt",
            source_kind="receipt",
            source_id="new",
            note="replacement",
        )
        session.commit()
        movement_count = session.scalar(select(func.count(InventoryMovement.id)))
        assert session.get(InventoryCatalogPart, source_id).merged_into_part_id is not None
    command.downgrade(config, "0025_local_task_claims")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT SUM(quantity) FROM inventory_parts")
        ).scalar_one() == (9 + (target_quantity or 0))
        assert connection.execute(
            text("SELECT quantity, is_active FROM inventory_parts WHERE id = 100")
        ).one() == (0, False)
        assert connection.execute(
            text(
                "SELECT part_id, delta, balance_after, note FROM inventory_movements WHERE id = 1000"
            )
        ).one() == (100, 5, 5, "legacy source")
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM inventory_movements WHERE part_id IS NOT NULL")
            ).scalar_one()
            == movement_count
        )
    command.upgrade(config, "0026_global_inventory_workflows")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT SUM(quantity) FROM inventory_park_stocks")
        ).scalar_one() == 9 + (target_quantity or 0)


def _upgrade_legacy_inventory(database_url: str, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(API_DIR / "alembic.ini")
    command.upgrade(config, "0025_local_task_claims")
    engine = create_engine(database_url, future=True)
    with engine.begin() as connection:
        role_id = connection.execute(text("SELECT id FROM roles WHERE slug = 'admin'")).scalar_one()
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, password_hash, role_id, access_status, must_change_password, is_active) "
                "VALUES (999, 'migration-user', 'hash', :role_id, 'approved', 0, 1)"
            ),
            {"role_id": role_id},
        )
        connection.execute(
            text(
                "INSERT INTO parks (id, name, tag, is_active) VALUES "
                "(1, 'Park A', 'park-a', 1), (2, 'Park B', 'park-b', 1)"
            )
        )
    return config, engine


def test_downgrade_preserves_global_values_above_signed_int32(sqlite_database_url, monkeypatch):
    config, engine = _upgrade_legacy_inventory(sqlite_database_url, monkeypatch)
    command.upgrade(config, "0026_global_inventory_workflows")
    large_quantity = 2**31 + 17
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO inventory_catalog_components "
                "(id, name, normalized_name) VALUES (70, 'Large', 'large')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO inventory_catalog_parts "
                "(id, component_id, name, normalized_name, article, normalized_article) "
                "VALUES (70, 70, 'Large part', 'large part', 'LARGE-70', 'large-70')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO inventory_park_stocks "
                "(id, park_id, catalog_part_id, quantity, minimum_quantity, version) "
                "VALUES (70, 1, 70, :quantity, :minimum, 1)"
            ),
            {"quantity": large_quantity, "minimum": large_quantity - 1},
        )
        connection.execute(
            text(
                "INSERT INTO inventory_movements "
                "(part_id, catalog_part_id, park_id, actor_user_id, kind, delta, balance_before, "
                "balance_after) VALUES (NULL, 70, 1, 999, 'receipt', :quantity, 0, :quantity)"
            ),
            {"quantity": large_quantity},
        )

    command.downgrade(config, "0025_local_task_claims")

    with engine.connect() as connection:
        part_types = {
            column["name"]: type(column["type"]).__name__.upper()
            for column in inspect(connection).get_columns("inventory_parts")
        }
        movement_types = {
            column["name"]: type(column["type"]).__name__.upper()
            for column in inspect(connection).get_columns("inventory_movements")
        }
        assert part_types["quantity"] in {"BIGINT", "BIGINTEGER"}
        assert part_types["minimum_quantity"] in {"BIGINT", "BIGINTEGER"}
        assert movement_types["delta"] in {"BIGINT", "BIGINTEGER"}
        assert movement_types["balance_after"] in {"BIGINT", "BIGINTEGER"}
        assert connection.execute(
            text(
                "SELECT quantity, minimum_quantity FROM inventory_parts WHERE article = 'LARGE-70'"
            )
        ).one() == (large_quantity, large_quantity - 1)
        assert connection.execute(
            text("SELECT delta, balance_after FROM inventory_movements WHERE delta = :quantity"),
            {"quantity": large_quantity},
        ).one() == (large_quantity, large_quantity)

    command.upgrade(config, "0026_global_inventory_workflows")
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT stock.quantity, stock.minimum_quantity "
                "FROM inventory_park_stocks AS stock "
                "JOIN inventory_catalog_parts AS part ON part.id = stock.catalog_part_id "
                "WHERE part.article = 'LARGE-70'"
            )
        ).one() == (large_quantity, large_quantity - 1)


def _insert_catalog_stock(connection, *, quantity: int = 4, location: str = "C-3") -> None:
    connection.execute(
        text(
            "INSERT INTO inventory_catalog_components "
            "(id, name, normalized_name) VALUES (1, 'Brakes', 'brakes')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO inventory_catalog_parts "
            "(id, component_id, name, normalized_name, article, normalized_article) "
            "VALUES (1, 1, 'Brake pad', 'brake pad', 'BP-1', 'bp-1')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO inventory_park_stocks "
            "(id, park_id, catalog_part_id, quantity, minimum_quantity, location) "
            "VALUES (1, 1, 1, :quantity, 1, :location)"
        ),
        {"quantity": quantity, "location": location},
    )


def test_upgrade_deduplicates_articles_and_preserves_stock_and_movements(
    sqlite_database_url, monkeypatch
):
    config, engine = _upgrade_legacy_inventory(sqlite_database_url, monkeypatch)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO inventory_components "
                "(id, park_id, name, photo_storage_key, created_at) VALUES "
                "(10, 1, 'Wheels', 'component-first', :first), "
                "(20, 2, '  wheels  ', 'component-later', :later)"
            ),
            {"first": datetime(2026, 1, 1), "later": datetime(2026, 1, 2)},
        )
        connection.execute(
            text(
                "INSERT INTO inventory_parts "
                "(id, park_id, component_id, name, article, quantity, minimum_quantity, "
                "location, photo_storage_key, photo_filename, photo_content_type, created_at) "
                "VALUES "
                "(100, 1, 10, 'Front wheel', ' ART-42 ', 2, 1, 'A-1', "
                "'part-first', 'first.jpg', 'image/jpeg', :first), "
                "(200, 2, 20, 'Front wheel', 'art-42', 7, 3, 'B-4', "
                "'part-later', 'later.png', 'image/png', :later)"
            ),
            {"first": datetime(2026, 1, 1), "later": datetime(2026, 1, 2)},
        )
        connection.execute(
            text(
                "INSERT INTO inventory_movements "
                "(id, part_id, park_id, actor_user_id, kind, delta, balance_after, note, created_at) "
                "VALUES (1000, 100, 1, 999, 'receipt', 2, 2, 'legacy A', :first), "
                "(2000, 200, 2, 999, 'receipt', 7, 7, 'legacy B', :later)"
            ),
            {"first": datetime(2026, 1, 1), "later": datetime(2026, 1, 2)},
        )

    command.upgrade(config, "0026_global_inventory_workflows")

    with Session(engine) as session:
        assert session.scalar(select(func.count(InventoryCatalogComponent.id))) == 1
        assert session.scalar(select(func.count(InventoryCatalogPart.id))) == 1
        catalog_part = session.scalar(select(InventoryCatalogPart))
        assert catalog_part is not None
        assert catalog_part.photo_storage_key == "part-first"
        assert catalog_part.photo_filename == "first.jpg"
        assert catalog_part.photo_content_type == "image/jpeg"
        stocks = session.scalars(
            select(InventoryParkStock).order_by(InventoryParkStock.park_id)
        ).all()
        assert [(row.quantity, row.location) for row in stocks] == [(2, "A-1"), (7, "B-4")]
        assert set(session.scalars(select(InventoryMovement.catalog_part_id))) == {catalog_part.id}
        assert set(session.scalars(select(InventoryMovement.part_id))) == {100, 200}
        assert list(
            session.execute(text("SELECT id, catalog_part_id FROM inventory_parts ORDER BY id"))
        ) == [(100, catalog_part.id), (200, catalog_part.id)]


def test_upgrade_normalizes_blank_locations_and_expanding_unicode_keys(
    sqlite_database_url, monkeypatch
):
    config, engine = _upgrade_legacy_inventory(sqlite_database_url, monkeypatch)
    expanding = "ß" * 128
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO inventory_components (id, park_id, name) VALUES (10, 1, :name)"),
            {"name": expanding},
        )
        connection.execute(
            text(
                "INSERT INTO inventory_parts "
                "(id, park_id, component_id, name, article, quantity, minimum_quantity, location) "
                "VALUES (100, 1, 10, :name, :article, 1, 0, '   ')"
            ),
            {"name": expanding, "article": expanding},
        )

    command.upgrade(config, "0026_global_inventory_workflows")

    with Session(engine) as session:
        ensure_rbac_catalog(session)
        stock = session.scalar(select(InventoryParkStock))
        part = session.scalar(select(InventoryCatalogPart))
        actor = session.get(User, 999)
        assert stock.location is None
        assert part.normalized_name == "ss" * 128
        assert part.normalized_article == "ss" * 128
        filtered = inventory_catalog.search_catalog(
            session,
            actor,
            park_id=1,
            query="SS" * 128,
            component_id=None,
            stock_filter="without_location",
            limit=50,
            offset=0,
        )
        assert [item["id"] for item in filtered["items"]] == [part.id]


def test_upgrade_uses_earliest_metadata_and_records_conflicts(sqlite_database_url, monkeypatch):
    config, engine = _upgrade_legacy_inventory(sqlite_database_url, monkeypatch)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO inventory_components (id, park_id, name, created_at) VALUES "
                "(20, 2, 'Later component', :later), (10, 1, 'First component', :first)"
            ),
            {"first": datetime(2026, 1, 1), "later": datetime(2026, 1, 2)},
        )
        connection.execute(
            text(
                "INSERT INTO inventory_parts "
                "(id, park_id, component_id, name, article, quantity, minimum_quantity, "
                "location, created_at) VALUES "
                "(200, 2, 20, 'Later name', 'DUP-1', 7, 0, 'B-4', :later), "
                "(100, 1, 10, 'First name', ' dup-1 ', 2, 0, 'A-1', :first)"
            ),
            {"first": datetime(2026, 1, 1), "later": datetime(2026, 1, 2)},
        )

    command.upgrade(config, "0026_global_inventory_workflows")

    with Session(engine) as session:
        catalog_part = session.scalar(select(InventoryCatalogPart))
        assert catalog_part is not None
        assert catalog_part.name == "First name"
        assert catalog_part.component.name == "First component"
        conflicts = session.execute(
            text(
                "SELECT field_name, canonical_value, conflicting_value "
                "FROM inventory_migration_conflicts ORDER BY field_name"
            )
        ).all()
        assert conflicts == [
            ("component", "First component", "Later component"),
            ("name", "First name", "Later name"),
        ]


def test_downgrade_keeps_legacy_inventory_readable(sqlite_database_url, monkeypatch):
    config, engine = _upgrade_legacy_inventory(sqlite_database_url, monkeypatch)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO inventory_components (id, park_id, name) VALUES (10, 1, 'Wheels')")
        )
        connection.execute(
            text(
                "INSERT INTO inventory_parts "
                "(id, park_id, component_id, name, article, quantity, minimum_quantity, location) "
                "VALUES (100, 1, 10, 'Front wheel', 'ART-42', 2, 1, 'A-1')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO inventory_movements "
                "(id, part_id, park_id, actor_user_id, kind, delta, balance_after) "
                "VALUES (1000, 100, 1, 999, 'receipt', 2, 2)"
            )
        )

    command.upgrade(config, "0026_global_inventory_workflows")
    command.downgrade(config, "0025_local_task_claims")

    assert {
        "inventory_components",
        "inventory_parts",
        "inventory_movements",
    } <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT name, quantity, location FROM inventory_parts WHERE id = 100")
        ).one() == ("Front wheel", 2, "A-1")
        assert connection.execute(
            text("SELECT part_id, delta, balance_after FROM inventory_movements WHERE id = 1000")
        ).one() == (100, 2, 2)


def test_downgrade_materializes_legacy_part_for_catalog_only_movement(
    sqlite_database_url, monkeypatch
):
    config, engine = _upgrade_legacy_inventory(sqlite_database_url, monkeypatch)
    command.upgrade(config, "0026_global_inventory_workflows")
    with engine.begin() as connection:
        _insert_catalog_stock(connection)
        connection.execute(
            text(
                "INSERT INTO inventory_movements "
                "(id, part_id, catalog_part_id, park_id, actor_user_id, kind, delta, "
                "balance_before, balance_after, source_kind, source_id, note) "
                "VALUES (1000, NULL, 1, 1, 999, 'receipt', 4, 0, 4, "
                "'receipt', 'new-1', 'catalog-only')"
            )
        )

    command.downgrade(config, "0025_local_task_claims")

    with engine.connect() as connection:
        legacy_part = connection.execute(
            text(
                "SELECT inventory_parts.id, inventory_components.name, inventory_parts.name, "
                "inventory_parts.article, inventory_parts.quantity, inventory_parts.location "
                "FROM inventory_parts JOIN inventory_components "
                "ON inventory_components.id = inventory_parts.component_id"
            )
        ).one()
        assert legacy_part[1:] == ("Brakes", "Brake pad", "BP-1", 4, "C-3")
        assert connection.execute(
            text(
                "SELECT part_id, delta, balance_after, note "
                "FROM inventory_movements WHERE id = 1000"
            )
        ).one() == (legacy_part.id, 4, 4, "catalog-only")


def test_upgrade_rebases_same_park_duplicate_movement_history(sqlite_database_url, monkeypatch):
    config, engine = _upgrade_legacy_inventory(sqlite_database_url, monkeypatch)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO inventory_components (id, park_id, name) VALUES "
                "(10, 1, 'Wheels'), (20, 1, 'Tyres')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO inventory_parts "
                "(id, park_id, component_id, name, article, quantity, minimum_quantity, "
                "location, created_at) VALUES "
                "(100, 1, 10, 'First', 'DUP-2', 2, 0, 'L1', :first), "
                "(200, 1, 20, 'Second', ' dup-2 ', 3, 0, 'L2', :later)"
            ),
            {"first": datetime(2026, 1, 1), "later": datetime(2026, 1, 2)},
        )
        connection.execute(
            text(
                "INSERT INTO inventory_movements "
                "(id, part_id, park_id, actor_user_id, kind, delta, balance_after, created_at) "
                "VALUES (2000, 200, 1, 999, 'receipt', 3, 3, :movement_time), "
                "(1000, 100, 1, 999, 'receipt', 2, 2, :movement_time)"
            ),
            {"movement_time": datetime(2026, 1, 3)},
        )

    command.upgrade(config, "0026_global_inventory_workflows")

    with engine.connect() as connection:
        history = connection.execute(
            text(
                "SELECT balance_before, balance_after, delta FROM inventory_movements "
                "ORDER BY created_at, id"
            )
        ).all()
        assert history == [(0, 2, 2), (2, 5, 3)]
        assert all(after - before == delta for before, after, delta in history)
        assert (
            connection.execute(text("SELECT quantity FROM inventory_park_stocks")).scalar_one()
            == history[-1].balance_after
        )


def test_movement_source_identity_is_idempotent_but_manual_sources_are_repeatable(
    sqlite_database_url, monkeypatch
):
    config, engine = _upgrade_legacy_inventory(sqlite_database_url, monkeypatch)
    command.upgrade(config, "0026_global_inventory_workflows")
    with engine.begin() as connection:
        _insert_catalog_stock(connection)
        connection.execute(
            text(
                "INSERT INTO inventory_movements "
                "(part_id, catalog_part_id, park_id, actor_user_id, kind, delta, "
                "balance_before, balance_after, source_kind, source_id) "
                "VALUES (NULL, 1, 1, 999, 'receipt', 4, 0, 4, 'receipt', 'receipt-1')"
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO inventory_movements "
                "(part_id, catalog_part_id, park_id, actor_user_id, kind, delta, "
                "balance_before, balance_after, source_kind, source_id) "
                "VALUES (NULL, 1, 1, 999, 'receipt', 4, 0, 4, "
                "'receipt', 'receipt-1')"
            )
        )

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO inventory_movements "
                "(part_id, catalog_part_id, park_id, actor_user_id, kind, delta, "
                "balance_before, balance_after, source_kind, source_id) VALUES "
                "(NULL, 1, 1, 999, 'manual', 0, 4, 4, 'manual', NULL), "
                "(NULL, 1, 1, 999, 'manual', 0, 4, 4, 'manual', NULL)"
            )
        )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM inventory_movements WHERE source_id IS NULL")
            ).scalar_one()
            == 2
        )


def test_upgrade_records_same_park_location_conflict(sqlite_database_url, monkeypatch):
    config, engine = _upgrade_legacy_inventory(sqlite_database_url, monkeypatch)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO inventory_components (id, park_id, name) VALUES "
                "(10, 1, 'Wheels'), (20, 1, ' wheels ')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO inventory_parts "
                "(id, park_id, component_id, name, article, quantity, minimum_quantity, "
                "location, created_at) VALUES "
                "(100, 1, 10, 'Wheel', 'LOC-1', 2, 0, 'L1', :first), "
                "(200, 1, 20, 'Wheel', ' loc-1 ', 3, 0, 'L2', :later)"
            ),
            {"first": datetime(2026, 1, 1), "later": datetime(2026, 1, 2)},
        )

    command.upgrade(config, "0026_global_inventory_workflows")

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT quantity, location FROM inventory_park_stocks")
        ).one() == (5, "L1")
        assert connection.execute(
            text(
                "SELECT canonical_legacy_part_id, conflicting_legacy_part_id, "
                "canonical_value, conflicting_value FROM inventory_migration_conflicts "
                "WHERE field_name = 'location'"
            )
        ).one() == (100, 200, "L1", "L2")


def test_round_trip_reconciles_same_park_legacy_duplicates_without_losing_history(
    sqlite_database_url, monkeypatch
):
    config, engine = _upgrade_legacy_inventory(sqlite_database_url, monkeypatch)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO inventory_components (id, park_id, name) VALUES "
                "(10, 1, 'Wheels'), (20, 1, 'Tyres')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO inventory_parts "
                "(id, park_id, component_id, name, article, quantity, minimum_quantity, "
                "location, is_active, created_at) VALUES "
                "(100, 1, 10, 'First', 'DUP-3', 2, 0, 'L1', 1, :first), "
                "(200, 1, 20, 'Second', ' dup-3 ', 3, 0, 'L2', 1, :later)"
            ),
            {"first": datetime(2026, 1, 1), "later": datetime(2026, 1, 2)},
        )
        connection.execute(
            text(
                "INSERT INTO inventory_movements "
                "(id, part_id, park_id, actor_user_id, kind, delta, balance_after, created_at) "
                "VALUES (1000, 100, 1, 999, 'receipt', 2, 2, :first), "
                "(2000, 200, 1, 999, 'receipt', 3, 3, :later)"
            ),
            {"first": datetime(2026, 1, 1), "later": datetime(2026, 1, 2)},
        )

    command.upgrade(config, "0026_global_inventory_workflows")
    command.downgrade(config, "0025_local_task_claims")

    with engine.connect() as connection:
        legacy_parts = connection.execute(
            text("SELECT article, quantity, is_active FROM inventory_parts ORDER BY id")
        ).all()
        duplicates = [
            row
            for row in legacy_parts
            if " ".join(row.article.strip().casefold().split()) == "dup-3"
        ]
        assert sum(row.quantity for row in duplicates) == 5
        assert sum(row.is_active for row in duplicates) == 1
        assert (
            connection.execute(text("SELECT count(*) FROM inventory_movements")).scalar_one() == 2
        )

    command.upgrade(config, "0026_global_inventory_workflows")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT quantity FROM inventory_park_stocks")).scalar_one() == 5
        )
        assert (
            connection.execute(text("SELECT count(*) FROM inventory_movements")).scalar_one() == 2
        )
