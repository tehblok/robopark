"""Persistent owner-bound terminal sessions and single-use TOTP grants."""

import sqlalchemy as sa
from alembic import op

revision = "0056_host_terminal"
down_revision = "0055_media_upload_park"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "privileged_reauthorizations",
        sa.Column("totp_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "terminal_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("auth_session_hash", sa.String(64), nullable=False),
        sa.Column("profile", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("broker_epoch", sa.String(36), nullable=False),
        sa.Column("descriptor", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("termination_reason", sa.String(64)),
        sa.Column("attachment_id", sa.String(36)),
        sa.Column("attachment_expires_at", sa.DateTime(timezone=True)),
        sa.Column("input_bytes", sa.BigInteger(), nullable=False),
        sa.Column("output_bytes", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("profile IN ('maintenance','root')", name="ck_terminal_profile"),
        sa.CheckConstraint(
            "state IN ('starting','detached','active','ended')", name="ck_terminal_state"
        ),
    )
    for col in ("owner_id", "auth_session_hash", "expires_at"):
        op.create_index(f"ix_terminal_sessions_{col}", "terminal_sessions", [col])
    op.create_table(
        "terminal_attach_tickets",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("terminal_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("auth_session_hash", sa.String(64), nullable=False),
        sa.Column("broker_epoch", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    for col in ("session_id", "expires_at"):
        op.create_index(f"ix_terminal_attach_tickets_{col}", "terminal_attach_tickets", [col])
    op.create_table(
        "terminal_session_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("terminal_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    for col in ("session_id", "created_at"):
        op.create_index(f"ix_terminal_session_events_{col}", "terminal_session_events", [col])


def downgrade():
    op.drop_table("terminal_session_events")
    op.drop_table("terminal_attach_tickets")
    op.drop_table("terminal_sessions")
    with op.batch_alter_table("privileged_reauthorizations") as batch:
        batch.drop_column("totp_only")
