"""Version independent recovery-code hashes."""

import sqlalchemy as sa
from alembic import op

revision = "0045_privileged_recovery_hashes"
down_revision = "0044_privileged_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "privileged_recovery_codes",
        sa.Column(
            "hash_version",
            sa.String(32),
            nullable=False,
            server_default="legacy-hmac-v1",
        ),
    )


def downgrade() -> None:
    op.drop_column("privileged_recovery_codes", "hash_version")
