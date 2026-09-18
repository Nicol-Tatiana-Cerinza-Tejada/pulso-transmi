from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql://academy_api:development@localhost:5432/pulso_transmi"
    scheduler_poll_seconds: int = 30
    scheduler_instance: str = "primary"
    skip_db_startup: bool = False
    starter_data_dir: Path = Path("data/starter")
    submission_max_bytes: int = 65_536
    submission_max_attempts: int = 3
    submission_rate_limit_per_minute: int = 10
    release_interval_minutes: int = 30
    submission_window_minutes: int = 25
    portal_identity_pepper: str = "development-only-change-me"
    portal_session_hours: int = 12
    portal_login_rate_limit_per_minute: int = 120
    portal_key_issuance_limit_per_hour: int = 4

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
