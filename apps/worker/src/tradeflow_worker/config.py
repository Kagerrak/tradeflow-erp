from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """Background worker configuration.

    The worker runs as an SQS-triggered Lambda in the demo deployment and as a
    small polling process during local development.  Both use the same
    settings, so behaviour cannot drift between the two.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TRADEFLOW_WORKER_",
        extra="ignore",
    )

    environment: Literal["development", "testing", "demo", "preview", "production"] = "development"
    database_url: str = "postgresql+asyncpg://tradeflow:tradeflow@localhost:5433/tradeflow"
    object_storage_endpoint_url: str | None = "http://localhost:9000"
    object_storage_public_endpoint_url: str | None = "http://localhost:9000"
    object_storage_access_key: str | None = "tradeflow"
    object_storage_secret_key: str | None = Field(default="tradeflow-local-only", repr=False)
    object_storage_bucket: str = "tradeflow-evidence"
    object_storage_url_expiry_seconds: int = Field(default=900, ge=60, le=3600)
    telemetry_enabled: bool = True
    otlp_endpoint: str | None = None
    aws_region: str | None = None
    lambda_runtime: bool = False
    local_poll_seconds: float = Field(default=5.0, ge=0.5)
    outbox_batch_size: int = Field(default=25, ge=1, le=200)

    demo_mode: bool = False
    demo_database_name: str = "tradeflow_demo"
    demo_seed_version: str = "2026.08.24.2"
    demo_reset_interval_minutes: int = Field(default=45, ge=1)
    demo_state_backend: Literal["file", "dynamodb"] = "file"
    demo_state_path: str | None = None
    demo_state_table: str | None = None
    demo_state_dir: str = "/demo-state"
    demo_web_credential_parameter: str | None = None
    demo_reset_api_port: int = Field(default=8000, ge=1024, le=65535)

    @property
    def resolves_aws_region(self) -> str | None:
        return self.aws_region or os.environ.get("AWS_REGION")


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
