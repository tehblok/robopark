"""Add loss-safe recovery reset packages and credential generations."""

import sqlalchemy as sa
from alembic import op

revision = "0046_privileged_generation"
down_revision = "0045_privileged_recovery_hashes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "privileged_credentials",
        sa.Column("credential_generation", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute(
        "UPDATE privileged_credentials SET credential_generation = 1 WHERE enrolled_at IS NOT NULL"
    )
    op.add_column(
        "privileged_reauthorizations",
        sa.Column("credential_generation", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute(
        "UPDATE privileged_reauthorizations SET credential_generation = COALESCE("
        "(SELECT credential_generation FROM privileged_credentials "
        "WHERE privileged_credentials.user_id = privileged_reauthorizations.user_id), 0)"
    )
    op.create_table(
        "privileged_recovery_resets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "selected_recovery_code_id",
            sa.Integer(),
            sa.ForeignKey("privileged_recovery_codes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("totp_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_privileged_recovery_resets_user_id",
        "privileged_recovery_resets",
        ["user_id"],
        unique=True,
    )
    op.create_index(
        "ix_privileged_recovery_resets_expires_at",
        "privileged_recovery_resets",
        ["expires_at"],
    )
    op.create_table(
        "privileged_recovery_reset_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reset_id",
            sa.String(64),
            sa.ForeignKey("privileged_recovery_resets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("hash_version", sa.String(32), server_default="scrypt-v1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("reset_id", "code_hash", name="uq_recovery_reset_code"),
    )
    op.create_index(
        "ix_privileged_recovery_reset_codes_reset_id",
        "privileged_recovery_reset_codes",
        ["reset_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_privileged_recovery_reset_codes_reset_id",
        table_name="privileged_recovery_reset_codes",
    )
    op.drop_table("privileged_recovery_reset_codes")
    op.drop_index(
        "ix_privileged_recovery_resets_expires_at",
        table_name="privileged_recovery_resets",
    )
    op.drop_index(
        "ix_privileged_recovery_resets_user_id",
        table_name="privileged_recovery_resets",
    )
    op.drop_table("privileged_recovery_resets")
    op.drop_column("privileged_reauthorizations", "credential_generation")
    op.drop_column("privileged_credentials", "credential_generation")
