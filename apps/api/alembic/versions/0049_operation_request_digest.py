"""Bind operation UUID receipts to canonical typed request digests."""

import sqlalchemy as sa
from alembic import op

revision = "0049_operation_request_digest"
down_revision = "0048_host_operation_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("host_operation_status") as batch:
        batch.add_column(
            sa.Column(
                "request_digest", sa.String(64), nullable=False,
                server_default="0" * 64,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("host_operation_status") as batch:
        batch.drop_column("request_digest")
