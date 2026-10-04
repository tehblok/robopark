"""Track shipped knowledge without overwriting operator decisions.

Revision ID: 0058_ai_bundle_receipts
Revises: 0057_local_ai
"""

import sqlalchemy as sa
from alembic import op

revision = "0058_ai_bundle_receipts"
down_revision = "0057_local_ai"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ai_events",
        sa.Column("payload_retained", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index(
        "ix_ai_events_retention", "ai_events", ["processed", "payload_retained", "occurred_at"]
    )
    op.create_index("ix_ai_runs_event_state", "ai_runs", ["event_key", "state"])
    op.create_index(
        "ix_ai_documents_source_ref_park_id",
        "ai_documents",
        ["source_ref", "park_id"],
        unique=False,
    )
    op.create_table(
        "ai_bundle_documents",
        sa.Column("source_ref", sa.String(length=400), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("applied_document_revision", sa.Integer(), nullable=False),
        sa.Column("applied_signature", sa.String(length=64), nullable=False),
        sa.Column("applied_bundle_revision", sa.String(length=64), nullable=False),
        sa.Column("seen_bundle_revision", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["ai_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("source_ref"),
        sa.UniqueConstraint("document_id", name="uq_ai_bundle_documents_document_id"),
    )
    op.create_index(
        "ix_ai_bundle_documents_seen_bundle_revision",
        "ai_bundle_documents",
        ["seen_bundle_revision"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_ai_runs_event_state", table_name="ai_runs")
    op.drop_index("ix_ai_events_retention", table_name="ai_events")
    op.drop_column("ai_events", "payload_retained")
    op.drop_table("ai_bundle_documents")
    op.drop_index("ix_ai_documents_source_ref_park_id", table_name="ai_documents")
