from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from the environment and optional `.env` file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    admin_ids: Annotated[tuple[int, ...], NoDecode] = ()
    database_url: str = "sqlite+aiosqlite:////data/tablegamesbot.db"
    audit_log_path: Path = Path("/data/audit/catalog.jsonl")
    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> tuple[int, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return tuple(int(item.strip()) for item in value.split(",") if item.strip())
        if isinstance(value, (list, tuple)):
            return tuple(int(item) for item in value)
        raise ValueError("ADMIN_IDS must be a comma-separated list of numeric Telegram IDs")


@lru_cache
def get_settings() -> Settings:
    return Settings()
