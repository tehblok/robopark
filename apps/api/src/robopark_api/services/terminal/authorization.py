"""Fail-closed terminal access; neither heartbeat nor output renews login TTL."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from robopark_api.models import AuthSession, PrivilegedCredential, User
from robopark_api.services.ops import host_bridge
from robopark_api.services.ops.maintenance import host_maintenance_active


class TerminalError(ValueError):
    def __init__(self, reason, status_code=409):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def authorize_terminal(db, *, auth_session_hash, owner_id, settings, generation=None):
    row = db.scalar(
        select(AuthSession)
        .options(joinedload(AuthSession.user).joinedload(User.role_ref))
        .where(AuthSession.token_hash == auth_session_hash)
        .execution_options(populate_existing=True)
    )
    now = datetime.now(UTC)
    if (
        row is None
        or row.user_id != owner_id
        or aware(row.expires_at) <= now
        or aware(row.created_at) + timedelta(seconds=settings.session_absolute_ttl_seconds) <= now
    ):
        raise TerminalError("terminal_login_expired", 401)
    actor = row.user
    if (
        not actor
        or not actor.is_active
        or actor.must_change_password
        or actor.access_status != "approved"
        or actor.role != "royal"
    ):
        raise TerminalError("terminal_forbidden", 403)
    credential = db.scalar(
        select(PrivilegedCredential)
        .where(PrivilegedCredential.user_id == owner_id)
        .execution_options(populate_existing=True)
    )
    if credential is None or credential.enrolled_at is None:
        raise TerminalError("privileged_enrollment_required", 403)
    if generation is not None and generation != credential.credential_generation:
        raise TerminalError("terminal_credentials_changed", 403)
    if host_maintenance_active(settings):
        raise TerminalError("terminal_host_busy")
    return actor


def require_origin(headers, settings):
    origin = headers.get("origin", "")
    origins = settings.terminal_allowed_origins or settings.cors_origins
    allowed = set()
    for candidate in origins.split(","):
        candidate = candidate.strip()
        parsed = urlsplit(candidate)
        secure = parsed.scheme == "https" or (
            settings.terminal_allow_loopback_http
            and parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1", "testserver"}
        )
        if (
            secure
            and parsed.netloc
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        ):
            allowed.add(candidate)
    if origin not in allowed or headers.get("host", "") != urlsplit(origin).netloc:
        raise TerminalError("terminal_origin_rejected", 403)


def capabilities(settings):
    if not settings.terminal_broker_socket:
        raise TerminalError("terminal_unavailable", 503)
    try:
        root = host_bridge.host_root(settings)
    except host_bridge.BridgeError:
        raise TerminalError("terminal_unavailable", 503) from None
    value = host_bridge.read_json(root / "public/terminal-capabilities.json", limit=4096)
    try:
        from hashlib import sha256
        from uuid import UUID

        now = datetime.now(UTC)
        generated = datetime.fromisoformat(value["generated_at"])
        if generated.tzinfo is None or not -5 <= (now - generated).total_seconds() < 30:
            raise ValueError()
        if (
            value["schema"] != 1
            or value["valid_for_seconds"] != 30
            or value["profiles"] != ["maintenance", "root"]
            or type(value["active_sessions"]) is not int
            or not 0 <= value["active_sessions"] <= 2
        ):
            raise ValueError()
        boot, epoch = value["boot_id"], value["broker_epoch"]
        if str(UUID(boot)) != boot or str(UUID(epoch)) != epoch:
            raise ValueError()
        revision = sha256(f"terminal-v1:{boot}:{epoch}".encode()).hexdigest()
        if revision != value["capability_revision"]:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise TerminalError("terminal_unavailable", 503) from None
    if host_maintenance_active(settings):
        raise TerminalError("terminal_host_busy")
    return value


def validate_grant_context(settings, operation_kind, operation_id, revision):
    from uuid import UUID

    try:
        if str(UUID(operation_id)) != operation_id:
            raise ValueError()
    except (ValueError, TypeError):
        raise TerminalError("terminal_invalid_id", 422) from None
    if operation_kind not in {"terminal.open.maintenance", "terminal.open.root"}:
        raise TerminalError("terminal_invalid_profile", 422)
    if capabilities(settings)["capability_revision"] != revision:
        raise TerminalError("terminal_capabilities_changed")
