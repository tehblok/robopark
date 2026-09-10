"""Service-company and wrapping campaigns."""

import sqlalchemy as sa
from alembic import op

revision = "0023_campaigns"
down_revision = "0022_tracker_collaboration"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("tracker_tag", sa.String(128), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('service_company', 'wrapping')", name="ck_campaigns_kind"),
        sa.CheckConstraint("due_on >= starts_on", name="ck_campaigns_date_range"),
    )
    op.create_index("ix_campaigns_kind", "campaigns", ["kind"])
    op.create_index("ix_campaigns_tracker_tag", "campaigns", ["tracker_tag"])
    op.create_index("ix_campaigns_created_by", "campaigns", ["created_by"])
    op.create_index("ix_campaigns_active_due", "campaigns", ["is_active", "due_on", "id"])
    op.create_table(
        "campaign_parks",
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "park_id", sa.Integer(), sa.ForeignKey("parks.id", ondelete="CASCADE"), primary_key=True
        ),
    )
    op.create_table(
        "campaign_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("issue_key", sa.String(128), nullable=False),
        sa.Column("park_id", sa.Integer(), sa.ForeignKey("parks.id"), nullable=False),
        sa.Column("robot", sa.String(64)),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "report_id", sa.Integer(), sa.ForeignKey("reports.id"), nullable=False, unique=True
        ),
        sa.Column("tracker_transition", sa.String(32)),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("campaign_id", "issue_key", name="uq_campaign_submission_issue"),
    )
    op.create_index("ix_campaign_submissions_campaign_id", "campaign_submissions", ["campaign_id"])
    op.create_index("ix_campaign_submissions_park_id", "campaign_submissions", ["park_id"])
    op.create_index(
        "ix_campaign_submissions_author_user_id", "campaign_submissions", ["author_user_id"]
    )
    op.create_index(
        "ix_campaign_submissions_campaign_completed",
        "campaign_submissions",
        ["campaign_id", "completed_at"],
    )


def downgrade():
    op.drop_table("campaign_submissions")
    op.drop_table("campaign_parks")
    op.drop_table("campaigns")
