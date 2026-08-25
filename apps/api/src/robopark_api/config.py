from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/robopark.db"
    session_cookie_name: str = "robopark_session"
    session_ttl_seconds: int = 60 * 60 * 24 * 14
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cors_origins: str = "http://localhost:5173"
    operator_shared_password: str | None = None
    seed_username: str | None = None
    seed_password: str | None = None
    seed_role: str = "royal"

    #: Master key for encrypting integration secrets at rest (Tracker token,
    #: Emergency cookie). When unset, secrets fall back to plaintext storage and
    #: the API logs a warning — set it in `.env` / `host.env`.
    secret_key: str | None = None

    # --- Password policy -------------------------------------------------
    password_min_length: int = 12
    password_require_complexity: bool = True

    # --- Brute-force protection -----------------------------------------
    #: Failed attempts (per username+IP) before temporary lockout.
    login_max_attempts: int = 5
    #: Rolling window in which failures are counted.
    login_attempt_window_seconds: int = 300
    #: Lockout duration once the threshold is reached.
    login_lockout_seconds: int = 900
    #: Registration attempts allowed per IP inside the same window.
    register_max_attempts: int = 10

    # --- Session hygiene --------------------------------------------------
    #: How often expired sessions are purged from the database.
    session_cleanup_interval_seconds: int = 60 * 60


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
