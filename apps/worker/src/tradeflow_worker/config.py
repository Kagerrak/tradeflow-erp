from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
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
    object_storage_endpoint_url: str = "http://localhost:9000"
    object_storage_public_endpoint_url: str = "http://localhost:9000"
    object_storage_access_key: str = "tradeflow"
    object_storage_secret_key: str = Field(default="tradeflow-local-only", repr=False)
    object_storage_bucket: str = "tradeflow-evidence"
    object_storage_url_expiry_seconds: int = Field(default=900, ge=60, le=3600)
    telemetry_enabled: bool = True
    otlp_endpoint: str | None = None


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
