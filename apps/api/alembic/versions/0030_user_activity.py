"""Keep only latest user activity and bounded approximate IP lookup state."""

import sqlalchemy as sa
from alembic import op

revision = "0030_user_activity"
down_revision = "0029_campaign_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_ip", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("last_device", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("last_location", sa.String(256), nullable=True))
    op.create_table(
        "ip_geo_cache",
        sa.Column("ip", sa.String(64), primary_key=True),
        sa.Column("location", sa.String(256), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ip_geo_cache_expires_at", "ip_geo_cache", ["expires_at"])
    op.create_table(
        "ip_geo_quota",
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("ip_geo_quota")
    op.drop_index("ix_ip_geo_cache_expires_at", table_name="ip_geo_cache")
    op.drop_table("ip_geo_cache")
    op.drop_column("users", "last_location")
    op.drop_column("users", "last_device")
    op.drop_column("users", "last_ip")
    op.drop_column("users", "last_seen_at")
