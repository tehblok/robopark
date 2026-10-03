"""Store optional, validated park coordinates for the shift map."""

import sqlalchemy as sa
from alembic import op

revision = "0054_park_coordinates"
down_revision = "0053_tracker_history_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("parks") as batch:
        batch.add_column(sa.Column("latitude", sa.Float(), nullable=True))
        batch.add_column(sa.Column("longitude", sa.Float(), nullable=True))
        batch.create_check_constraint(
            "ck_parks_coordinates",
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL AND "
            "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
        )


def downgrade() -> None:
    with op.batch_alter_table("parks") as batch:
        batch.drop_constraint("ck_parks_coordinates", type_="check")
        batch.drop_column("longitude")
        batch.drop_column("latitude")
