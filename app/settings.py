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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
