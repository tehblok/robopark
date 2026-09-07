"""Collect bounded unknown diagnostic examples and sampled sightings."""

import sqlalchemy as sa
from alembic import op

revision = "0020_diagnostic_unknowns"
down_revision = "0019_diagnostic_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_unknowns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("identity", sa.String(64), nullable=False, unique=True),
        sa.Column("source_path", sa.String(256), nullable=False),
        sa.Column("source_segments_json", sa.Text(), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observations", sa.Integer(), nullable=False),
        sa.Column("last_robot", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("diagnostic_rules.id"), nullable=True),
        sa.CheckConstraint(
            "state IN ('new', 'mapped', 'ignored')", name="ck_diagnostic_unknowns_state"
        ),
        sa.CheckConstraint("observations >= 1", name="ck_diagnostic_unknowns_observations"),
    )
    op.create_index(
        "ix_diagnostic_unknowns_state_last_seen",
        "diagnostic_unknowns",
        ["state", "last_seen_at", "id"],
    )
    op.create_table(
        "diagnostic_unknown_sightings",
        sa.Column(
            "unknown_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_unknowns.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("robot", sa.String(128), primary_key=True),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("diagnostic_unknown_sightings")
    op.drop_index("ix_diagnostic_unknowns_state_last_seen", table_name="diagnostic_unknowns")
    op.drop_table("diagnostic_unknowns")
