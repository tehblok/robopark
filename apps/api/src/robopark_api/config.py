from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from robopark_api.services.tracker_api import NOTIFICATION_SEARCH_OPERATION_TIMEOUT_SEC

_API_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/robopark.db"
    session_cookie_name: str = "robopark_session"
    #: Sliding idle timeout: no authenticated request for this long → re-login.
    session_idle_seconds: int = Field(default=60 * 60 * 24 * 3, gt=0)
    #: Hard cap from login (also Max-Age when remember_me is set).
    session_absolute_ttl_seconds: int = Field(default=60 * 60 * 24 * 30, gt=0)
    #: Do not rewrite expires_at on every request (SQLite write load).
    session_slide_min_interval_seconds: int = Field(default=60 * 60, ge=0)
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cors_origins: str = "http://localhost:5173"
    operator_shared_password: str | None = None
    seed_username: str | None = None
    seed_password: str | None = None
    seed_role: str = "royal"
    #: When true, create documented local demo users and a demo park on startup.
    #: Local development only — keep false in production.
    dev_seed: bool = False

    #: Master key for encrypting integration secrets at rest (Tracker token,
    #: Emergency cookie). Required to write secrets — empty key fails closed.
    secret_key: str | None = None

    #: Expose FastAPI /docs and /openapi.json (local only; keep false in deploy).
    openapi_enabled: bool = False

    #: Auto-fail stuck ops jobs older than this (seconds). 0 disables.
    ops_job_ttl_seconds: int = 2 * 60 * 60

    # --- Password policy -------------------------------------------------
    password_min_length: int = Field(default=12, ge=8, le=128)
    password_require_complexity: bool = True

    # --- Brute-force protection -----------------------------------------
    #: Failed attempts (per username+IP) before temporary lockout.
    login_max_attempts: int = Field(default=5, ge=1, le=1_000)
    #: Failed logins across all usernames from one IP (shared NAT gets a wider budget).
    login_ip_max_attempts: int = Field(default=30, ge=1, le=10_000)
    #: Rolling window in which failures are counted.
    login_attempt_window_seconds: int = Field(default=300, gt=0)
    #: Lockout duration once the threshold is reached.
    login_lockout_seconds: int = Field(default=900, gt=0)
    #: Registration attempts allowed per IP inside the same window.
    register_max_attempts: int = Field(default=10, ge=1, le=1_000)

    # --- Session hygiene --------------------------------------------------
    #: How often expired sessions are purged from the database.
    session_cleanup_interval_seconds: int = 60 * 60

    # --- Emergency cache --------------------------------------------------
    #: Maximum simultaneous Emergency HTTP requests in one API worker.
    emergency_max_concurrency: int = 8

    # --- Approximate IP geolocation --------------------------------------
    #: Disabled unless the operator explicitly opts into the external service.
    ip_geo_provider: Literal["off", "ipwhois"] = "off"

    # --- Web Push delivery ------------------------------------------------
    #: Maximum simultaneous Web Push requests in one API worker.
    push_max_concurrency: int = Field(default=4, ge=1, le=64)
    #: Maximum subscriptions attempted for one emitted event.
    push_delivery_batch_size: int = Field(default=100, ge=1, le=10_000)
    #: Total wall-clock budget for delivering one emitted event.
    push_delivery_deadline_seconds: float = Field(default=10.0, gt=0, le=120.0)

    # --- Tracker task notifications --------------------------------------
    #: Delay between bounded searches for newly created Tracker tasks.
    tracker_notification_interval_seconds: float = Field(default=60.0, gt=0, le=3600.0)
    #: Maximum tasks inspected in one cursor page (Tracker API caps pages at 50).
    tracker_notification_page_size: int = Field(default=20, ge=1, le=50)
    #: Wall-clock budget for one poll before it stops starting new deliveries.
    tracker_notification_poll_deadline_seconds: float = Field(default=45.0, gt=30.0, le=300.0)
    #: Cross-worker cursor lease, renewed after every processed task.
    tracker_notification_lease_seconds: float = Field(default=300.0, ge=30.0, le=3600.0)

    # --- Royal ops (snapshot / restore / ZIP update) -----------------------
    #: Directory for job state, staging, and snapshot artifacts.
    ops_dir: str | None = None
    #: Installed host bridge mount; unset for manual Compose installations.
    ops_host_root: str | None = None
    ai_runtime_state_path: str = "/ops/ai-public/ai-runtime.json"
    ai_broker_socket: str = "/run/robopark-ai/broker.sock"
    terminal_broker_socket: str | None = None
    terminal_allowed_origins: str | None = None
    terminal_allow_loopback_http: bool = False
    #: Cross-process live-merge blobs (default: ``<sqlite-dir>/live-merge``).
    live_merge_dir: str | None = None
    #: Staged Tracker upload copies (production: under the measured /data mount).
    staged_attachments_dir: str | None = None
    #: Tree that a successful release is copied onto (repo root in local/dev).
    ops_apply_root: str | None = None
    #: Optional path to deploy/host.env (or a test stand-in) packed into snapshots.
    ops_host_env_path: str | None = None
    #: When true, ops jobs finish inside the request (tests). When false, they
    #: run after the response so the royal UI can poll.
    ops_sync: bool = False
    ops_max_upload_bytes: int = 512 * 1024 * 1024
    #: Report UI snapshots, device photos, and client logs.
    report_attachments_dir: str | None = None
    #: Component and spare-part catalog photos.
    inventory_photos_dir: str | None = None
    #: Actual application data mount and sanitized host-only health projection.
    host_data_path: str = "/data"
    host_health_path: str = "/ops/host-health.json"

    @model_validator(mode="after")
    def resolve_sqlite_database_path(self) -> "Settings":
        """Anchor relative SQLite paths to ``apps/api`` so daemon starts work."""
        url = self.database_url
        if not url.startswith("sqlite:///"):
            return self
        raw_path = url.removeprefix("sqlite:///")
        if raw_path == ":memory:":
            return self
        path = Path(raw_path)
        if path.is_absolute():
            path.parent.mkdir(parents=True, exist_ok=True)
            return self
        resolved = (_API_ROOT / path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        object.__setattr__(self, "database_url", f"sqlite:///{resolved}")
        return self

    @model_validator(mode="after")
    def validate_tracker_notification_timing(self) -> "Settings":
        max_operation = max(
            NOTIFICATION_SEARCH_OPERATION_TIMEOUT_SEC,
            self.push_delivery_deadline_seconds,
        )
        required_lease = self.tracker_notification_poll_deadline_seconds + max_operation + 5.0
        if self.tracker_notification_lease_seconds < required_lease:
            raise ValueError("tracker_notification_lease_too_short")
        return self


@lru_cache(maxsize=1)
def _cached_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    """Return process-wide settings.

    Cached on purpose: this runs as a FastAPI dependency on most endpoints and
    an uncached ``Settings()`` re-read the `.env` file from disk on *every*
    request.
    """
    return _cached_settings()


def reset_settings_cache() -> None:
    """Drop the cached settings (used by tests and after config changes)."""
    _cached_settings.cache_clear()
