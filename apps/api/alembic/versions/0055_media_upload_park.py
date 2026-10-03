"""Bind resumable media reservations to a locally enforceable park scope."""

import sqlalchemy as sa
from alembic import op

revision = "0055_media_upload_park"
down_revision = "0054_park_coordinates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_upload_sessions") as batch:
        batch.add_column(sa.Column("park_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_media_upload_sessions_park_id",
            "parks",
            ["park_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_media_upload_sessions_park_id", ["park_id"])


def downgrade() -> None:
    with op.batch_alter_table("media_upload_sessions") as batch:
        batch.drop_index("ix_media_upload_sessions_park_id")
        batch.drop_constraint("fk_media_upload_sessions_park_id", type_="foreignkey")
        batch.drop_column("park_id")
