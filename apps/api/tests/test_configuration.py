from __future__ import annotations

import pytest
from pydantic import ValidationError
from tradeflow_api.config import Settings


def test_local_configuration_requires_a_test_signing_secret() -> None:
    with pytest.raises(ValidationError, match="TRADEFLOW_AUTH_TEST_SECRET"):
        Settings(
            environment="development",
            auth_test_secret=None,
        )


def test_production_configuration_rejects_a_test_signing_secret() -> None:
    with pytest.raises(ValidationError, match="forbidden outside development"):
        Settings(
            environment="production",
            auth_jwks_url="https://identity.example/.well-known/jwks.json",
            auth_test_secret="must-never-be-accepted-in-production",
        )


def test_production_configuration_requires_a_jwks_endpoint() -> None:
    with pytest.raises(ValidationError, match="TRADEFLOW_AUTH_JWKS_URL"):
        Settings(
            environment="production",
            auth_test_secret=None,
        )


def test_production_configuration_accepts_oidc_verification_settings() -> None:
    settings = Settings(
        environment="production",
        auth_issuer="https://identity.example",
        auth_audience="tradeflow-api",
        auth_jwks_url="https://identity.example/.well-known/jwks.json",
        auth_test_secret=None,
    )

    assert settings.environment == "production"
    assert settings.auth_test_secret is None
