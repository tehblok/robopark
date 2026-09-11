"""Global inventory catalog, park stocks, receipts, and counts."""

from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "0026_global_inventory_workflows"
down_revision = "0025_local_task_claims"
branch_labels = None
depends_on = None


def normalize_inventory_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _create_catalog_tables() -> None:
    op.create_table(
        "inventory_catalog_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("normalized_name", sa.String(128), nullable=False),
        sa.Column("photo_storage_key", sa.String(128)),
        sa.Column("photo_filename", sa.String(240)),
        sa.Column("photo_content_type", sa.String(64)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "uq_inventory_catalog_components_active_name",
        "inventory_catalog_components",
        ["normalized_name"],
        unique=True,
        sqlite_where=sa.text("is_active = 1 AND normalized_name <> ''"),
        postgresql_where=sa.text("is_active AND normalized_name <> ''"),
    )
    op.create_table(
        "inventory_catalog_parts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "component_id",
            sa.Integer(),
            sa.ForeignKey("inventory_catalog_components.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("normalized_name", sa.String(128), nullable=False),
        sa.Column("article", sa.String(128), nullable=False),
        sa.Column("normalized_article", sa.String(128), nullable=False),
        sa.Column("photo_storage_key", sa.String(128)),
        sa.Column("photo_filename", sa.String(240)),
        sa.Column("photo_content_type", sa.String(64)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_inventory_catalog_parts_component_id", "inventory_catalog_parts", ["component_id"]
    )
    op.create_index(
        "ix_inventory_catalog_parts_normalized_article",
        "inventory_catalog_parts",
        ["normalized_article"],
    )
    op.create_index(
        "ix_inventory_catalog_parts_component_name",
        "inventory_catalog_parts",
        ["component_id", "normalized_name"],
    )
    op.create_index(
        "uq_inventory_catalog_parts_active_article",
        "inventory_catalog_parts",
        ["normalized_article"],
        unique=True,
        sqlite_where=sa.text("is_active = 1 AND normalized_article <> ''"),
        postgresql_where=sa.text("is_active AND normalized_article <> ''"),
    )


def _create_workflow_tables() -> None:
    op.create_table(
        "inventory_park_stocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "catalog_part_id",
            sa.Integer(),
            sa.ForeignKey("inventory_catalog_parts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("minimum_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("location", sa.String(256)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_inventory_park_stock_quantity"),
        sa.CheckConstraint("minimum_quantity >= 0", name="ck_inventory_park_stock_minimum"),
        sa.UniqueConstraint("park_id", "catalog_part_id", name="uq_inventory_park_stock_part"),
    )
    op.create_index("ix_inventory_park_stocks_park_id", "inventory_park_stocks", ["park_id"])
    op.create_index(
        "ix_inventory_park_stocks_catalog_part_id",
        "inventory_park_stocks",
        ["catalog_part_id"],
    )

    op.create_table(
        "inventory_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("supplier", sa.String(256)),
        sa.Column("document_number", sa.String(128)),
        sa.Column("receipt_date", sa.Date(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("posted_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('draft', 'posted', 'cancelled')", name="ck_receipt_status"),
    )
    op.create_index("ix_inventory_receipts_park_id", "inventory_receipts", ["park_id"])
    op.create_index("ix_inventory_receipts_created_by", "inventory_receipts", ["created_by"])
    op.create_table(
        "inventory_receipt_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "receipt_id",
            sa.Integer(),
            sa.ForeignKey("inventory_receipts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "catalog_part_id",
            sa.Integer(),
            sa.ForeignKey("inventory_catalog_parts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500)),
        sa.CheckConstraint("quantity > 0", name="ck_inventory_receipt_line_quantity"),
        sa.UniqueConstraint("receipt_id", "catalog_part_id", name="uq_inventory_receipt_line_part"),
    )
    op.create_index(
        "ix_inventory_receipt_lines_receipt_id", "inventory_receipt_lines", ["receipt_id"]
    )
    op.create_index(
        "ix_inventory_receipt_lines_catalog_part_id",
        "inventory_receipt_lines",
        ["catalog_part_id"],
    )

    op.create_table(
        "inventory_counts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("posted_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('draft', 'posted', 'cancelled')", name="ck_count_status"),
    )
    op.create_index("ix_inventory_counts_park_id", "inventory_counts", ["park_id"])
    op.create_index("ix_inventory_counts_created_by", "inventory_counts", ["created_by"])
    op.create_table(
        "inventory_count_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "count_id",
            sa.Integer(),
            sa.ForeignKey("inventory_counts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "catalog_part_id",
            sa.Integer(),
            sa.ForeignKey("inventory_catalog_parts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("expected_quantity", sa.Integer(), nullable=False),
        sa.Column("actual_quantity", sa.Integer()),
        sa.Column("difference", sa.Integer()),
        sa.Column("comment", sa.String(500)),
        sa.CheckConstraint("expected_quantity >= 0", name="ck_inventory_count_line_expected"),
        sa.CheckConstraint(
            "actual_quantity IS NULL OR actual_quantity >= 0",
            name="ck_inventory_count_line_actual",
        ),
        sa.UniqueConstraint("count_id", "catalog_part_id", name="uq_inventory_count_line_part"),
    )
    op.create_index("ix_inventory_count_lines_count_id", "inventory_count_lines", ["count_id"])
    op.create_index(
        "ix_inventory_count_lines_catalog_part_id",
        "inventory_count_lines",
        ["catalog_part_id"],
    )

    op.create_table(
        "inventory_migration_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("normalized_article", sa.String(128), nullable=False),
        sa.Column("canonical_legacy_part_id", sa.Integer(), nullable=False),
        sa.Column("conflicting_legacy_part_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(32), nullable=False),
        sa.Column("canonical_value", sa.String(256), nullable=False),
        sa.Column("conflicting_value", sa.String(256), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_inventory_migration_conflicts_normalized_article",
        "inventory_migration_conflicts",
        ["normalized_article"],
    )


def _legacy_tables():
    metadata = sa.MetaData()
    components = sa.Table("inventory_components", metadata, autoload_with=op.get_bind())
    parts = sa.Table("inventory_parts", metadata, autoload_with=op.get_bind())
    movements = sa.Table("inventory_movements", metadata, autoload_with=op.get_bind())
    return components, parts, movements


def _new_tables():
    metadata = sa.MetaData()
    names = (
        "inventory_catalog_components",
        "inventory_catalog_parts",
        "inventory_park_stocks",
        "inventory_migration_conflicts",
    )
    return tuple(sa.Table(name, metadata, autoload_with=op.get_bind()) for name in names)


def _backfill_catalog() -> None:
    bind = op.get_bind()
    legacy_components, legacy_parts, legacy_movements = _legacy_tables()
    catalog_components, catalog_parts, park_stocks, conflicts = _new_tables()

    component_rows = list(
        bind.execute(
            sa.select(legacy_components).order_by(
                legacy_components.c.created_at, legacy_components.c.id
            )
        ).mappings()
    )
    component_by_id = {row["id"]: row for row in component_rows}
    component_ids: dict[int, int] = {}
    catalog_component_by_key: dict[str, int] = {}
    for row in component_rows:
        normalized_name = normalize_inventory_key(row["name"])
        key = normalized_name or f"legacy:{row['id']}"
        catalog_id = catalog_component_by_key.get(key)
        if catalog_id is None:
            result = bind.execute(
                catalog_components.insert().values(
                    name=row["name"],
                    normalized_name=normalized_name,
                    photo_storage_key=row["photo_storage_key"],
                    photo_filename=row["photo_filename"],
                    photo_content_type=row["photo_content_type"],
                    created_at=row["created_at"],
                    updated_at=row["created_at"],
                )
            )
            catalog_id = result.inserted_primary_key[0]
            catalog_component_by_key[key] = catalog_id
        component_ids[row["id"]] = catalog_id

    part_rows = list(
        bind.execute(
            sa.select(legacy_parts).order_by(legacy_parts.c.created_at, legacy_parts.c.id)
        ).mappings()
    )
    groups: dict[str, list] = defaultdict(list)
    for row in part_rows:
        normalized_article = normalize_inventory_key(row["article"])
        key = normalized_article or f"legacy:{row['id']}"
        groups[key].append(row)

    part_ids: dict[int, int] = {}
    stock_values: dict[tuple[int, int], dict] = {}
    for rows in groups.values():
        canonical = rows[0]
        normalized_article = normalize_inventory_key(canonical["article"])
        result = bind.execute(
            catalog_parts.insert().values(
                component_id=component_ids[canonical["component_id"]],
                name=canonical["name"],
                normalized_name=normalize_inventory_key(canonical["name"]),
                article=canonical["article"].strip(),
                normalized_article=normalized_article,
                photo_storage_key=canonical["photo_storage_key"],
                photo_filename=canonical["photo_filename"],
                photo_content_type=canonical["photo_content_type"],
                is_active=any(row["is_active"] for row in rows),
                created_at=canonical["created_at"],
                updated_at=canonical["updated_at"],
            )
        )
        catalog_part_id = result.inserted_primary_key[0]
        canonical_component = component_by_id[canonical["component_id"]]["name"]
        for row in rows:
            part_ids[row["id"]] = catalog_part_id
            row_component = component_by_id[row["component_id"]]["name"]
            for field_name, canonical_value, conflicting_value in (
                ("name", canonical["name"], row["name"]),
                ("component", canonical_component, row_component),
            ):
                if normalize_inventory_key(canonical_value) != normalize_inventory_key(
                    conflicting_value
                ):
                    bind.execute(
                        conflicts.insert().values(
                            normalized_article=normalized_article,
                            canonical_legacy_part_id=canonical["id"],
                            conflicting_legacy_part_id=row["id"],
                            field_name=field_name,
                            canonical_value=canonical_value,
                            conflicting_value=conflicting_value,
                        )
                    )

            stock_key = (row["park_id"], catalog_part_id)
            current = stock_values.get(stock_key)
            if current is None:
                stock_values[stock_key] = {
                    "park_id": row["park_id"],
                    "catalog_part_id": catalog_part_id,
                    "quantity": row["quantity"],
                    "minimum_quantity": row["minimum_quantity"],
                    "location": row["location"],
                    "is_active": row["is_active"],
                    "updated_at": row["updated_at"],
                }
            else:
                current["quantity"] += row["quantity"]
                current["minimum_quantity"] = max(
                    current["minimum_quantity"], row["minimum_quantity"]
                )
                current["is_active"] = current["is_active"] or row["is_active"]

    if stock_values:
        bind.execute(park_stocks.insert(), list(stock_values.values()))

    for row in bind.execute(sa.select(legacy_movements)).mappings():
        bind.execute(
            legacy_movements.update()
            .where(legacy_movements.c.id == row["id"])
            .values(
                catalog_part_id=part_ids[row["part_id"]],
                balance_before=row["balance_after"] - row["delta"],
            )
        )


def upgrade():
    _create_catalog_tables()
    _create_workflow_tables()
    with op.batch_alter_table("inventory_movements") as batch_op:
        batch_op.add_column(sa.Column("catalog_part_id", sa.Integer()))
        batch_op.add_column(sa.Column("source_kind", sa.String(32)))
        batch_op.add_column(sa.Column("source_id", sa.String(128)))
        batch_op.add_column(sa.Column("balance_before", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_inventory_movements_catalog_part_id",
            "inventory_catalog_parts",
            ["catalog_part_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.alter_column("part_id", existing_type=sa.Integer(), nullable=True)
    op.create_index(
        "ix_inventory_movements_catalog_part_id", "inventory_movements", ["catalog_part_id"]
    )
    op.create_index(
        "ix_inventory_movements_catalog_part_created",
        "inventory_movements",
        ["catalog_part_id", "created_at"],
    )
    _backfill_catalog()


def downgrade():
    op.drop_index("ix_inventory_movements_catalog_part_created", table_name="inventory_movements")
    op.drop_index("ix_inventory_movements_catalog_part_id", table_name="inventory_movements")
    with op.batch_alter_table("inventory_movements") as batch_op:
        batch_op.drop_constraint("fk_inventory_movements_catalog_part_id", type_="foreignkey")
        batch_op.drop_column("balance_before")
        batch_op.drop_column("source_id")
        batch_op.drop_column("source_kind")
        batch_op.drop_column("catalog_part_id")
        batch_op.alter_column("part_id", existing_type=sa.Integer(), nullable=False)

    op.drop_table("inventory_migration_conflicts")
    op.drop_table("inventory_count_lines")
    op.drop_table("inventory_counts")
    op.drop_table("inventory_receipt_lines")
    op.drop_table("inventory_receipts")
    op.drop_table("inventory_park_stocks")
    op.drop_table("inventory_catalog_parts")
    op.drop_table("inventory_catalog_components")
