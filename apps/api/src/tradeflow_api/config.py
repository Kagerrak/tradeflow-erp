from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TRADEFLOW_",
        extra="ignore",
    )

    environment: Literal["development", "testing", "demo", "preview", "production"] = "development"
    database_url: str = "postgresql+asyncpg://tradeflow:tradeflow@localhost:5432/tradeflow"
    auth_issuer: str = "https://identity.tradeflow.invalid"
    auth_audience: str = "tradeflow-api"
    auth_jwks_url: str | None = None
    auth_test_secret: str | None = Field(default=None, min_length=32, repr=False)
    picking_enabled: bool = True
    object_storage_endpoint_url: str | None = "http://localhost:9000"
    object_storage_public_endpoint_url: str | None = "http://localhost:9000"
    object_storage_access_key: str | None = "tradeflow"
    object_storage_secret_key: str | None = Field(default="tradeflow-local-only", repr=False)
    object_storage_bucket: str = "tradeflow-evidence"
    object_storage_url_expiry_seconds: int = Field(default=900, ge=60, le=3600)
    telemetry_enabled: bool = True
    otlp_endpoint: str | None = None
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_requests_per_minute: int = Field(default=120, ge=0)
    demo_mode: bool = False
    demo_database_name: str | None = None
    demo_seed_version: str | None = None
    demo_state_path: str | None = None
    demo_reset_token: str | None = Field(default=None, min_length=32, repr=False)
    demo_state_backend: Literal["file", "dynamodb"] = "file"
    demo_state_table: str | None = None
    demo_jobs_bucket: str | None = None
    demo_reset_interval_minutes: int = Field(default=45, ge=1)
    demo_dispatch_recovery_seconds: int = Field(default=300, ge=0)
    demo_dispatch_batch_size: int = Field(default=25, ge=1, le=200)
    aws_region: str | None = None
    lambda_runtime: bool = False
    alembic_ini: str | None = None

    @property
    def resolves_aws_region(self) -> str | None:
        return self.aws_region or os.environ.get("AWS_REGION")

    @model_validator(mode="after")
    def validate_rate_limit(self) -> Settings:
        if self.environment in {"development", "testing"}:
            return self
        if self.rate_limit_enabled and self.rate_limit_requests_per_minute <= 0:
            raise ValueError("Rate limiting is enabled but requests_per_minute is not positive.")
        return self

    @model_validator(mode="after")
    def validate_authentication(self) -> Settings:
        if self.environment in {"preview", "production"}:
            if self.auth_test_secret is not None:
                raise ValueError("Test token signing is forbidden outside development.")
            if self.auth_jwks_url is None:
                raise ValueError("TRADEFLOW_AUTH_JWKS_URL is required for deployment.")
        elif self.auth_test_secret is None:
            raise ValueError(
                "TRADEFLOW_AUTH_TEST_SECRET is required for local development and tests."
            )
        return self

    @model_validator(mode="after")
    def validate_demo_boundary(self) -> Settings:
        if self.environment != "demo":
            if self.demo_mode:
                raise ValueError("Demo mode is forbidden outside the demo environment.")
            return self

        if not self.demo_mode:
            raise ValueError("TRADEFLOW_DEMO_MODE must be enabled in the demo environment.")
        if (
            self.demo_database_name is None
            or re.fullmatch(
                r"tradeflow[-_]demo(?:[-_][a-z0-9]+)?", self.demo_database_name, re.IGNORECASE
            )
            is None
        ):
            raise ValueError(
                "TRADEFLOW_DEMO_DATABASE_NAME must explicitly identify a demo database."
            )
        configured_name = self.database_url.rsplit("/", 1)[-1].split("?", 1)[0]
        if configured_name != self.demo_database_name:
            raise ValueError("The configured database must match TRADEFLOW_DEMO_DATABASE_NAME.")
        if self.demo_seed_version is None:
            raise ValueError("TRADEFLOW_DEMO_SEED_VERSION is required in the demo environment.")
        if self.demo_state_backend == "file" and self.demo_state_path is None:
            raise ValueError("TRADEFLOW_DEMO_STATE_PATH is required for the file state backend.")
        if self.demo_state_backend == "dynamodb" and self.demo_state_table is None:
            raise ValueError("TRADEFLOW_DEMO_STATE_TABLE is required for the DynamoDB backend.")
        if self.demo_reset_token is None:
            raise ValueError("TRADEFLOW_DEMO_RESET_TOKEN is required in the demo environment.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
