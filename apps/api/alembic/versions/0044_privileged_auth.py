"""Add privileged authentication credentials, grants, and immutable audit."""

import sqlalchemy as sa
from alembic import op

revision = "0044_privileged_auth"
down_revision = "0043_schedule_series_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "privileged_credentials",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("totp_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("last_totp_counter", sa.BigInteger(), nullable=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "privileged_recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "code_hash", name="uq_recovery_user_code"),
    )
    op.create_index("ix_privileged_recovery_codes_user_id", "privileged_recovery_codes", ["user_id"])
    op.create_table(
        "privileged_reauthorizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_token_hash", sa.String(64), nullable=False),
        sa.Column("operation_kind", sa.String(64), nullable=False),
        sa.Column("operation_id", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_privileged_reauthorizations_token_hash", "privileged_reauthorizations", ["token_hash"], unique=True)
    op.create_index("ix_privileged_reauthorizations_expires_at", "privileged_reauthorizations", ["expires_at"])
    op.create_table(
        "privileged_auth_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_username", sa.String(64), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(128), nullable=True),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("device", sa.String(256), nullable=True),
        sa.Column("operation_kind", sa.String(64), nullable=True),
        sa.Column("operation_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_privileged_auth_audit_action", "privileged_auth_audit", ["action"])
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER privileged_auth_audit_no_update BEFORE UPDATE ON privileged_auth_audit "
            "BEGIN SELECT RAISE(ABORT, 'privileged_auth_audit_immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER privileged_auth_audit_no_delete BEFORE DELETE ON privileged_auth_audit "
            "BEGIN SELECT RAISE(ABORT, 'privileged_auth_audit_immutable'); END"
        )
    elif op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION deny_privileged_auth_audit_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'privileged_auth_audit_immutable'; END $$"
        )
        op.execute(
            "CREATE TRIGGER privileged_auth_audit_no_mutation BEFORE UPDATE OR DELETE "
            "ON privileged_auth_audit FOR EACH ROW EXECUTE FUNCTION deny_privileged_auth_audit_mutation()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER privileged_auth_audit_no_delete")
        op.execute("DROP TRIGGER privileged_auth_audit_no_update")
    elif op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER privileged_auth_audit_no_mutation ON privileged_auth_audit")
        op.execute("DROP FUNCTION deny_privileged_auth_audit_mutation()")
    op.drop_index("ix_privileged_auth_audit_action", table_name="privileged_auth_audit")
    op.drop_table("privileged_auth_audit")
    op.drop_index("ix_privileged_reauthorizations_expires_at", table_name="privileged_reauthorizations")
    op.drop_index("ix_privileged_reauthorizations_token_hash", table_name="privileged_reauthorizations")
    op.drop_table("privileged_reauthorizations")
    op.drop_index("ix_privileged_recovery_codes_user_id", table_name="privileged_recovery_codes")
    op.drop_table("privileged_recovery_codes")
    op.drop_table("privileged_credentials")
