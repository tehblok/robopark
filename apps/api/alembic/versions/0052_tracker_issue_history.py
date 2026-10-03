"""Keep Tracker status evidence and the original repair SLA anchor."""

import sqlalchemy as sa
from alembic import op

revision = "0052_tracker_issue_history"
down_revision = "0051_park_timezone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracker_issue_history_state",
        sa.Column("issue_key", sa.String(128), primary_key=True),
        sa.Column("observed_park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="SET NULL")),
        sa.Column("anchor_park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="SET NULL")),
        sa.Column("anchor_timezone", sa.String(64)),
        sa.Column("first_queued_at", sa.DateTime(timezone=True)),
        sa.Column("latest_status_key", sa.String(128)),
        sa.Column("latest_status_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("history_state", sa.String(16), nullable=False),
        sa.Column("history_checked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_tracker_issue_history_state_observed_park_id", "tracker_issue_history_state", ["observed_park_id"])
    op.create_index("ix_tracker_issue_history_state_anchor_park_id", "tracker_issue_history_state", ["anchor_park_id"])
    op.create_index("ix_tracker_issue_history_state_history_state", "tracker_issue_history_state", ["history_state"])
    op.create_table(
        "tracker_issue_status_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("issue_key", sa.String(128), sa.ForeignKey("tracker_issue_history_state.issue_key", ondelete="CASCADE"), nullable=False),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="SET NULL")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_status_key", sa.String(128)),
        sa.Column("to_status_key", sa.String(128), nullable=False),
        sa.Column("to_status_display", sa.String(128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("issue_key", "event_key", name="uq_tracker_status_issue_event"),
    )
    op.create_index("ix_tracker_issue_status_events_issue_key", "tracker_issue_status_events", ["issue_key"])
    op.create_index("ix_tracker_issue_status_events_park_id", "tracker_issue_status_events", ["park_id"])
    op.create_index("ix_tracker_issue_status_events_occurred_at", "tracker_issue_status_events", ["occurred_at"])
    op.create_index("ix_tracker_status_issue_occurred", "tracker_issue_status_events", ["issue_key", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_tracker_status_issue_occurred", table_name="tracker_issue_status_events")
    op.drop_index("ix_tracker_issue_status_events_occurred_at", table_name="tracker_issue_status_events")
    op.drop_index("ix_tracker_issue_status_events_issue_key", table_name="tracker_issue_status_events")
    op.drop_index("ix_tracker_issue_status_events_park_id", table_name="tracker_issue_status_events")
    op.drop_table("tracker_issue_status_events")
    op.drop_index("ix_tracker_issue_history_state_history_state", table_name="tracker_issue_history_state")
    op.drop_index("ix_tracker_issue_history_state_anchor_park_id", table_name="tracker_issue_history_state")
    op.drop_index("ix_tracker_issue_history_state_observed_park_id", table_name="tracker_issue_history_state")
    op.drop_table("tracker_issue_history_state")
