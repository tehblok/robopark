from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates


class Base(DeclarativeBase):
    pass


INVENTORY_INT64_MIN = -(2**63)
INVENTORY_INT64_MAX = 2**63 - 1


class AccessStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(16), default="nav")
    label: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(512), default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    permissions: Mapped[list[Permission]] = relationship(
        secondary="role_permissions",
        order_by=Permission.sort_order,
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class UserPermission(Base):
    """Per-user grant/deny on top of the assigned role."""

    __tablename__ = "user_permissions"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )
    granted: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    access_status: Mapped[str] = mapped_column(String(32), default=AccessStatus.approved.value)
    tracker_login: Mapped[str | None] = mapped_column(String(128), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    role_ref: Mapped[Role] = relationship(lazy="joined")
    sessions: Mapped[list[AuthSession]] = relationship(back_populates="user")
    parks: Mapped[list[Park]] = relationship(secondary="user_parks")
    park_requests: Mapped[list[ParkRequest]] = relationship(
        back_populates="user",
        foreign_keys="ParkRequest.user_id",
    )

    @property
    def role(self) -> str:
        return self.role_ref.slug if self.role_ref is not None else ""


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="sessions")


class Park(Base):
    __tablename__ = "parks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    tag: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    tracker_queue: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tracker_priority: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracker_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feature_reports: Mapped[bool] = mapped_column(Boolean, default=True)
    feature_blockers: Mapped[bool] = mapped_column(Boolean, default=True)
    feature_sla_repair: Mapped[bool] = mapped_column(Boolean, default=True)
    feature_backlog_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    blocker_history: Mapped[list[ParkBlockerHistory]] = relationship(
        back_populates="park",
        cascade="all, delete-orphan",
    )


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('service_company', 'wrapping')",
            name="ck_campaigns_kind",
        ),
        CheckConstraint("due_on >= starts_on", name="ck_campaigns_date_range"),
        Index("ix_campaigns_active_due", "is_active", "due_on", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    tracker_tag: Mapped[str] = mapped_column(String(128), index=True)
    starts_on: Mapped[date] = mapped_column()
    due_on: Mapped[date] = mapped_column()
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    parks: Mapped[list[Park]] = relationship(secondary="campaign_parks")


class CampaignPark(Base):
    __tablename__ = "campaign_parks"

    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    park_id: Mapped[int] = mapped_column(
        ForeignKey("parks.id", ondelete="CASCADE"), primary_key=True
    )


class CampaignSubmission(Base):
    __tablename__ = "campaign_submissions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "issue_key", name="uq_campaign_submission_issue"),
        Index("ix_campaign_submissions_campaign_completed", "campaign_id", "completed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    issue_key: Mapped[str] = mapped_column(String(128))
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id"), index=True)
    robot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str] = mapped_column(Text)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), unique=True)
    tracker_transition: Mapped[str | None] = mapped_column(String(32), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InventoryComponent(Base):
    __tablename__ = "inventory_components"
    __table_args__ = (UniqueConstraint("park_id", "name", name="uq_inventory_component_park_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    photo_storage_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    photo_filename: Mapped[str | None] = mapped_column(String(240), nullable=True)
    photo_content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InventoryPart(Base):
    __tablename__ = "inventory_parts"
    __table_args__ = (
        UniqueConstraint("park_id", "article", name="uq_inventory_part_park_article"),
        CheckConstraint("quantity >= 0", name="ck_inventory_part_quantity"),
        CheckConstraint("minimum_quantity >= 0", name="ck_inventory_part_minimum"),
        Index("ix_inventory_parts_park_component", "park_id", "component_id", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    component_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_components.id", ondelete="RESTRICT"), index=True
    )
    catalog_part_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_catalog_parts.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128))
    article: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    minimum_quantity: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    location: Mapped[str] = mapped_column(String(256))
    photo_storage_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    photo_filename: Mapped[str | None] = mapped_column(String(240), nullable=True)
    photo_content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InventoryCatalogComponent(Base):
    __tablename__ = "inventory_catalog_components"
    __table_args__ = (
        Index(
            "uq_inventory_catalog_components_active_name",
            "normalized_name",
            unique=True,
            sqlite_where=text("is_active = 1 AND normalized_name <> ''"),
            postgresql_where=text("is_active AND normalized_name <> ''"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(384))
    photo_storage_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    photo_filename: Mapped[str | None] = mapped_column(String(240), nullable=True)
    photo_content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InventoryCatalogPart(Base):
    __tablename__ = "inventory_catalog_parts"
    __table_args__ = (
        Index(
            "uq_inventory_catalog_parts_active_article",
            "normalized_article",
            unique=True,
            sqlite_where=text("is_active = 1 AND normalized_article <> ''"),
            postgresql_where=text("is_active AND normalized_article <> ''"),
        ),
        Index("ix_inventory_catalog_parts_component_name", "component_id", "normalized_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    component_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_catalog_components.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(384))
    article: Mapped[str] = mapped_column(String(128))
    normalized_article: Mapped[str] = mapped_column(String(384), index=True)
    merged_into_part_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_catalog_parts.id", ondelete="RESTRICT"), nullable=True
    )
    photo_storage_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    photo_filename: Mapped[str | None] = mapped_column(String(240), nullable=True)
    photo_content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    component: Mapped[InventoryCatalogComponent] = relationship(lazy="joined")


class InventoryParkStock(Base):
    __tablename__ = "inventory_park_stocks"
    __table_args__ = (
        UniqueConstraint("park_id", "catalog_part_id", name="uq_inventory_park_stock_part"),
        CheckConstraint("quantity >= 0", name="ck_inventory_park_stock_quantity"),
        CheckConstraint("minimum_quantity >= 0", name="ck_inventory_park_stock_minimum"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    catalog_part_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_catalog_parts.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    minimum_quantity: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    version: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InventoryReceipt(Base):
    __tablename__ = "inventory_receipts"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'posted', 'cancelled')", name="ck_receipt_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    supplier: Mapped[str | None] = mapped_column(String(256), nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    receipt_date: Mapped[date] = mapped_column()
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    posted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InventoryReceiptLine(Base):
    __tablename__ = "inventory_receipt_lines"
    __table_args__ = (
        UniqueConstraint("receipt_id", "catalog_part_id", name="uq_inventory_receipt_line_part"),
        CheckConstraint("quantity > 0", name="ck_inventory_receipt_line_quantity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    receipt_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_receipts.id", ondelete="CASCADE"), index=True
    )
    catalog_part_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_catalog_parts.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[int] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class InventoryCount(Base):
    __tablename__ = "inventory_counts"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'posted', 'cancelled')", name="ck_count_status"),
        Index(
            "ix_inventory_counts_park_name_key_id",
            "park_id",
            "normalized_name_key",
            "id",
        ),
        Index("ix_inventory_counts_park_created_id", "park_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(384))
    normalized_name_key: Mapped[bytes] = mapped_column(LargeBinary(512))
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    posted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @validates("name")
    def normalize_name(self, _key: str, value: str) -> str:
        self.normalized_name = value.casefold()
        self.normalized_name_key = self.normalized_name.encode("utf-8")
        return value


class InventoryCountLine(Base):
    __tablename__ = "inventory_count_lines"
    __table_args__ = (
        UniqueConstraint("count_id", "catalog_part_id", name="uq_inventory_count_line_part"),
        CheckConstraint("expected_quantity >= 0", name="ck_inventory_count_line_expected"),
        CheckConstraint(
            "actual_quantity IS NULL OR actual_quantity >= 0",
            name="ck_inventory_count_line_actual",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    count_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_counts.id", ondelete="CASCADE"), index=True
    )
    catalog_part_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_catalog_parts.id", ondelete="RESTRICT"), index=True
    )
    expected_quantity: Mapped[int] = mapped_column(BigInteger)
    actual_quantity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    difference: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)


class InventoryMigrationConflict(Base):
    __tablename__ = "inventory_migration_conflicts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    normalized_article: Mapped[str] = mapped_column(String(384), index=True)
    canonical_legacy_part_id: Mapped[int] = mapped_column(Integer)
    conflicting_legacy_part_id: Mapped[int] = mapped_column(Integer)
    field_name: Mapped[str] = mapped_column(String(32))
    canonical_value: Mapped[str] = mapped_column(String(256))
    conflicting_value: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        Index("ix_inventory_movements_part_created", "part_id", "created_at"),
        Index("ix_inventory_movements_catalog_part_created", "catalog_part_id", "created_at"),
        Index(
            "uq_inventory_movements_source_identity",
            "source_kind",
            "source_id",
            "park_id",
            "catalog_part_id",
            unique=True,
            sqlite_where=text(
                "source_kind IS NOT NULL AND source_id IS NOT NULL AND catalog_part_id IS NOT NULL"
            ),
            postgresql_where=text(
                "source_kind IS NOT NULL AND source_id IS NOT NULL AND catalog_part_id IS NOT NULL"
            ),
        ),
        Index(
            "uq_inventory_movements_idempotency_key",
            "idempotency_key",
            unique=True,
            sqlite_where=text("idempotency_key IS NOT NULL"),
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    part_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_parts.id", ondelete="CASCADE"), index=True, nullable=True
    )
    catalog_part_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_catalog_parts.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    delta: Mapped[int] = mapped_column(BigInteger)
    balance_before: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    source_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    issue_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ParkBlockerHistory(Base):
    __tablename__ = "park_blocker_history"
    __table_args__ = (
        UniqueConstraint(
            "park_id",
            "bucket_start",
            name="uq_park_blocker_history_park_bucket",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    arrived_count: Mapped[int] = mapped_column(Integer, default=0)
    departed_count: Mapped[int] = mapped_column(Integer, default=0)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Unversioned/manual inserts are legacy until explicitly recomputed.
    definition_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    park: Mapped[Park] = relationship(back_populates="blocker_history")


class AnalyticsSnapshot(Base):
    """A complete, successful source read, including a measured empty park."""

    __tablename__ = "analytics_snapshots"

    park_id: Mapped[int] = mapped_column(
        ForeignKey("parks.id", ondelete="CASCADE"), primary_key=True
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    target_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AnalyticsObservation(Base):
    __tablename__ = "analytics_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["park_id", "bucket_start"],
            ["analytics_snapshots.park_id", "analytics_snapshots.bucket_start"],
            ondelete="CASCADE",
        ),
    )

    park_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    issue_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(128), primary_key=True)
    status_bucket: Mapped[str] = mapped_column(String(32))
    # Exact workflow classification shared with task/card authorization. Unknown
    # statuses fail closed for restricted roles, independently of display hints.
    authorization_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    age_hours: Mapped[float | None] = mapped_column(Float, nullable=True)


class DiagnosticRule(Base):
    __tablename__ = "diagnostic_rules"
    __table_args__ = (
        CheckConstraint(
            "match_kind IN ('exact', 'regex')",
            name="ck_diagnostic_rules_match_kind",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_diagnostic_rules_severity",
        ),
        CheckConstraint(
            "preferred_view IN ('top', 'front', 'rear', 'left', 'right', 'isometric')",
            name="ck_diagnostic_rules_preferred_view",
        ),
        CheckConstraint(
            "indicator IN ('point', 'outline', 'zone')",
            name="ck_diagnostic_rules_indicator",
        ),
        CheckConstraint("x = x AND x >= 0.0 AND x <= 1.0", name="ck_diagnostic_rules_x"),
        CheckConstraint("y = y AND y >= 0.0 AND y <= 1.0", name="ck_diagnostic_rules_y"),
        UniqueConstraint(
            "source_path",
            "match_kind",
            "pattern",
            name="uq_diagnostic_rules_source_match_pattern",
        ),
        Index("ix_diagnostic_rules_sort_order_id", "sort_order", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_path: Mapped[str] = mapped_column(String(256))
    match_kind: Mapped[str] = mapped_column(String(16))
    pattern: Mapped[str] = mapped_column(String(512))
    example: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16))
    part: Mapped[str] = mapped_column(String(128))
    preferred_view: Mapped[str] = mapped_column(String(16))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    indicator: Mapped[str] = mapped_column(String(16))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserPark(Base):
    __tablename__ = "user_parks"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    park_id: Mapped[int] = mapped_column(
        ForeignKey("parks.id", ondelete="CASCADE"), primary_key=True
    )


class ParkRequest(Base):
    __tablename__ = "park_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), default=AccessStatus.pending.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    user: Mapped[User] = relationship(
        back_populates="park_requests",
        foreign_keys=[user_id],
    )


class EmergencySection(Base):
    __tablename__ = "emergency_sections"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    formatter: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    fields: Mapped[list[EmergencyField]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )
    roles: Mapped[list[EmergencySectionRole]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )


class EmergencyField(Base):
    __tablename__ = "emergency_fields"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("emergency_sections.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String(256))
    label: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    section: Mapped[EmergencySection] = relationship(back_populates="fields")


class EmergencySectionRole(Base):
    __tablename__ = "emergency_section_roles"

    section_id: Mapped[str] = mapped_column(
        ForeignKey("emergency_sections.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), primary_key=True)

    section: Mapped[EmergencySection] = relationship(back_populates="roles")


class EmergencyReading(Base):
    __tablename__ = "emergency_readings"
    __table_args__ = (
        CheckConstraint(
            "display_kind IN ('text', 'number', 'percent', 'distance', 'current', 'state')",
            name="ck_emergency_readings_display_kind",
        ),
        CheckConstraint(
            "view IN ('top', 'front', 'rear', 'left', 'right', 'isometric')",
            name="ck_emergency_readings_view",
        ),
        CheckConstraint(
            "label_direction IN ('auto', 'left', 'right', 'top', 'bottom')",
            name="ck_emergency_readings_label_direction",
        ),
        CheckConstraint("x = x AND x >= 0.0 AND x <= 1.0", name="ck_emergency_readings_x"),
        CheckConstraint("y = y AND y >= 0.0 AND y <= 1.0", name="ck_emergency_readings_y"),
        CheckConstraint(
            "precision >= 0 AND precision <= 4", name="ck_emergency_readings_precision"
        ),
        UniqueConstraint("section_id", "path", name="uq_emergency_readings_section_path"),
        Index("ix_emergency_readings_sort_order_id", "sort_order", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    section_id: Mapped[str] = mapped_column(
        ForeignKey("emergency_sections.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(256))
    label: Mapped[str] = mapped_column(String(128))
    display_kind: Mapped[str] = mapped_column(String(16))
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    precision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    enabled_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    no_data_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    warning_below: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning_above: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_below: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_above: Mapped[float | None] = mapped_column(Float, nullable=True)
    view: Mapped[str] = mapped_column(String(16))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    label_direction: Mapped[str] = mapped_column(String(16), default="auto", server_default="auto")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class AuditLog(Base):
    """Append-only record of security- and Tracker-relevant actions.

    The platform talks to Tracker through a single service OAuth token, so in
    Tracker every change looks like the same account. Without this table there
    was no way to answer "who closed that ticket".
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_log_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    #: Nullable: failed logins are recorded before a user is identified.
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    park_id: Mapped[int | None] = mapped_column(
        ForeignKey("parks.id", ondelete="SET NULL"), nullable=True
    )
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), default="success")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_target_role_status_park_id", "target_role", "status", "park_id"),
        Index("ix_reports_author_user_id_created_at", "author_user_id", "created_at"),
        Index(
            "ix_reports_tracker_key",
            "tracker_key",
            sqlite_where=text("tracker_key IS NOT NULL"),
            postgresql_where=text("tracker_key IS NOT NULL"),
        ),
        Index(
            "uq_reports_open_close_review_park_tracker",
            "park_id",
            "tracker_key",
            unique=True,
            sqlite_where=text("kind = 'ticket_close_review' AND status = 'open'"),
            postgresql_where=text("kind = 'ticket_close_review' AND status = 'open'"),
        ),
        Index(
            "uq_reports_open_emergency_cookie_stale",
            "kind",
            unique=True,
            sqlite_where=text("kind = 'emergency_cookie_stale' AND status = 'open'"),
            postgresql_where=text("kind = 'emergency_cookie_stale' AND status = 'open'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="open")
    park_id: Mapped[int | None] = mapped_column(ForeignKey("parks.id"), index=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_role: Mapped[str] = mapped_column(String(32), index=True)
    tracker_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tracker_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, default="")
    parent_report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"), nullable=True)
    return_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attachments: Mapped[list[ReportAttachment]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ReportAttachment.id",
    )


class ReportAttachment(Base):
    __tablename__ = "report_attachments"
    __table_args__ = (
        UniqueConstraint("report_id", "kind", name="uq_report_attachments_report_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(256))
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    report: Mapped[Report] = relationship(back_populates="attachments")


class DiagnosticUnknown(Base):
    __tablename__ = "diagnostic_unknowns"
    __table_args__ = (
        CheckConstraint(
            "state IN ('new', 'mapped', 'ignored')", name="ck_diagnostic_unknowns_state"
        ),
        CheckConstraint("observations >= 1", name="ck_diagnostic_unknowns_observations"),
        Index("ix_diagnostic_unknowns_state_last_seen", "state", "last_seen_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    source_path: Mapped[str] = mapped_column(String(256))
    source_segments_json: Mapped[str] = mapped_column(Text)
    raw_json: Mapped[str] = mapped_column(Text)
    original_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observations: Mapped[int] = mapped_column(Integer, default=1)
    last_robot: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16), default="new")
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("diagnostic_rules.id"), nullable=True)


class DiagnosticUnknownSighting(Base):
    """Last counted observation per robot/error; prevents polling from inflating counts."""

    __tablename__ = "diagnostic_unknown_sightings"

    unknown_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostic_unknowns.id", ondelete="CASCADE"), primary_key=True
    )
    robot: Mapped[str] = mapped_column(String(128), primary_key=True)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
