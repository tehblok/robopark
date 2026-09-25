"""Add shared notification and authentication throttle state."""

import sqlalchemy as sa
from alembic import op

revision = "0036_audit_remediation_state"
down_revision = "0035_schedules_and_push"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracker_notification_cursors",
        sa.Column("scope_key", sa.String(128), primary_key=True),
        sa.Column("cursor_value", sa.String(256)),
        sa.Column("lease_owner", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(256)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_until IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_tracker_notification_lease_pair",
        ),
    )
    op.create_index(
        "ix_tracker_notification_lease",
        "tracker_notification_cursors",
        ["lease_until", "scope_key"],
    )

    op.create_table(
        "system_incident_occurrences",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("incident_key", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("last_seen_at >= started_at", name="ck_system_incident_seen_range"),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= started_at",
            name="ck_system_incident_resolved_range",
        ),
    )
    op.create_index(
        "uq_system_incident_active_key",
        "system_incident_occurrences",
        ["incident_key"],
        unique=True,
        sqlite_where=sa.text("resolved_at IS NULL"),
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_index(
        "ix_system_incident_cleanup",
        "system_incident_occurrences",
        ["resolved_at", "id"],
    )

    op.create_table(
        "auth_throttle_states",
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_started_at", sa.DateTime(timezone=True)),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("length(key_hash) = 64", name="ck_auth_throttle_key_hash"),
        sa.CheckConstraint("failure_count >= 0", name="ck_auth_throttle_failure_count"),
    )
    op.create_index("ix_auth_throttle_expiry", "auth_throttle_states", ["expires_at", "key_hash"])


def downgrade() -> None:
    op.drop_index("ix_auth_throttle_expiry", table_name="auth_throttle_states")
    op.drop_table("auth_throttle_states")
    op.drop_index("ix_system_incident_cleanup", table_name="system_incident_occurrences")
    op.drop_index("uq_system_incident_active_key", table_name="system_incident_occurrences")
    op.drop_table("system_incident_occurrences")
    op.drop_index("ix_tracker_notification_lease", table_name="tracker_notification_cursors")
    op.drop_table("tracker_notification_cursors")
