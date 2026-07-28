from __future__ import annotations

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

    environment: Literal["development", "testing", "preview", "production"] = "development"
    database_url: str = "postgresql+asyncpg://tradeflow:tradeflow@localhost:5432/tradeflow"
    auth_issuer: str = "https://identity.tradeflow.invalid"
    auth_audience: str = "tradeflow-api"
    auth_jwks_url: str | None = None
    auth_test_secret: str | None = Field(default=None, min_length=32, repr=False)
    telemetry_enabled: bool = True
    otlp_endpoint: str | None = None

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
