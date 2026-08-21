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


def get_settings() -> Settings:
    return Settings()
