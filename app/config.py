from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://mm70:mm70@postgres:5432/mm70"
    secret_key: str = Field(default="dev-only-change-me")
    secure_cookies: bool = True
    session_cookie: str = "mm70_session"
    session_days: int = 14
    trusted_proxy_cidrs: str = ""
    auto_import_bundle: str = ""
    public_base_url: str = "http://127.0.0.1:8000"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MM70_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
