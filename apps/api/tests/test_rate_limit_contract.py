from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


def make_token(
    settings: Settings,
    *,
    capabilities: list[str],
    subject: str = "rate-limit-tester",
) -> str:
    return jwt.encode(
        {
            "sub": subject,
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "name": "Rate Limit Tester",
            "capabilities": capabilities,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.auth_test_secret,
        algorithm="HS256",
    )


@pytest.fixture
def settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def limited_client(postgres_url: str) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
        rate_limit_enabled=True,
        rate_limit_requests_per_minute=2,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as http_client:
            yield http_client


@pytest.mark.asyncio
async def test_health_endpoints_are_exempt_from_rate_limit(
    limited_client: AsyncClient,
) -> None:
    for _ in range(5):
        response = await limited_client.get("/health/live")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_authenticated_requests_are_rate_limited_by_token(
    limited_client: AsyncClient,
    settings: Settings,
) -> None:
    headers = {"authorization": f"Bearer {make_token(settings, capabilities=['platform:read'])}"}
    first = await limited_client.get("/v1/session", headers=headers)
    second = await limited_client.get("/v1/session", headers=headers)
    third = await limited_client.get("/v1/session", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    payload = third.json()
    assert payload["error"]["code"] == "rate_limit_exceeded"
    assert "Retry-After" in third.headers
    assert int(third.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_unauthenticated_requests_are_rate_limited_by_ip(
    limited_client: AsyncClient,
) -> None:
    first = await limited_client.get("/v1/session")
    second = await limited_client.get("/v1/session")
    third = await limited_client.get("/v1/session")

    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_different_identities_have_independent_rate_limit_buckets(
    limited_client: AsyncClient,
    settings: Settings,
) -> None:
    token_a = make_token(settings, capabilities=["platform:read"], subject="rate-limit-a")
    token_b = make_token(settings, capabilities=["platform:read"], subject="rate-limit-b")

    for _ in range(2):
        response = await limited_client.get(
            "/v1/session",
            headers={"authorization": f"Bearer {token_a}"},
        )
        assert response.status_code == 200

    response = await limited_client.get(
        "/v1/session",
        headers={"authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_can_be_disabled_for_tests(
    postgres_url: str,
) -> None:
    settings = Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
        rate_limit_enabled=False,
        rate_limit_requests_per_minute=0,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as http_client:
            for _ in range(5):
                response = await http_client.get("/v1/session")
                assert response.status_code == 401


@pytest.mark.asyncio
async def test_production_configuration_requires_positive_rate_limit() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="requests_per_minute"):
        Settings(
            environment="production",
            auth_issuer="https://identity.example",
            auth_audience="tradeflow-api",
            auth_jwks_url="https://identity.example/.well-known/jwks.json",
            rate_limit_enabled=True,
            rate_limit_requests_per_minute=0,
        )
