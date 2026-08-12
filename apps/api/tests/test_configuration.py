from __future__ import annotations

import pytest
from pydantic import ValidationError
from tradeflow_api.app import create_app
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


def test_picking_kill_switch_removes_new_workflow_routes() -> None:
    settings = Settings(
        environment="testing",
        auth_test_secret="test-secret-with-at-least-32-characters",
        picking_enabled=False,
        telemetry_enabled=False,
    )

    app = create_app(settings)
    paths = {path for route in app.routes if (path := getattr(route, "path", None))}

    assert "/v1/fulfillment/orders/{fulfillment_order_id}/picks" not in paths
    assert "/v1/fulfillment/orders/{fulfillment_order_id}/picking-context" not in paths
    assert "/v1/inventory/barcodes/resolve" not in paths
