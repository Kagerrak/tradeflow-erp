from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TRADEFLOW_WORKER_",
        extra="ignore",
    )

    environment: Literal["development", "testing", "preview", "production"] = "development"
    redis_url: str = "redis://localhost:6380/0"
    database_url: str = "postgresql+asyncpg://tradeflow:tradeflow@localhost:5433/tradeflow"
    telemetry_enabled: bool = True


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
