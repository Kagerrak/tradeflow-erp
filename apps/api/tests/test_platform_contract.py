from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


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
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as http_client:
            yield http_client


def make_token(
    settings: Settings,
    *,
    capabilities: list[str],
    expires_at: datetime | None = None,
) -> str:
    return jwt.encode(
        {
            "sub": "user-123",
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "name": "Platform Tester",
            "capabilities": capabilities,
            "exp": expires_at or datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.auth_test_secret,
        algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_live_health_is_public_and_correlated(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"service": "tradeflow-api", "status": "ok"}
    UUID(response.headers["x-correlation-id"])


@pytest.mark.asyncio
async def test_ready_health_checks_real_database_and_is_correlated(
    client: AsyncClient,
) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "database": "ready",
        "service": "tradeflow-api",
        "status": "ready",
    }
    UUID(response.headers["x-correlation-id"])


@pytest.mark.asyncio
async def test_session_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/v1/session")

    assert response.status_code == 401
    payload = response.json()
    assert payload["error"]["code"] == "authentication_required"
    assert payload["error"]["message"] == "A valid bearer token is required."
    assert payload["error"]["correlation_id"] == response.headers["x-correlation-id"]


@pytest.mark.asyncio
async def test_session_requires_platform_capability(
    client: AsyncClient,
    settings: Settings,
) -> None:
    response = await client.get(
        "/v1/session",
        headers={"authorization": f"Bearer {make_token(settings, capabilities=[])}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "capability_required"


@pytest.mark.asyncio
async def test_session_rejects_an_expired_token(
    client: AsyncClient,
    settings: Settings,
) -> None:
    response = await client.get(
        "/v1/session",
        headers={
            "authorization": (
                "Bearer "
                + make_token(
                    settings,
                    capabilities=["platform:read"],
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


@pytest.mark.asyncio
async def test_session_rejects_a_token_without_expiration(
    client: AsyncClient,
    settings: Settings,
) -> None:
    token = jwt.encode(
        {
            "sub": "user-123",
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "capabilities": ["platform:read"],
        },
        settings.auth_test_secret,
        algorithm="HS256",
    )

    response = await client.get(
        "/v1/session",
        headers={"authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


@pytest.mark.asyncio
async def test_authenticated_session_checks_real_database(
    client: AsyncClient,
    settings: Settings,
) -> None:
    response = await client.get(
        "/v1/session",
        headers={
            "authorization": (f"Bearer {make_token(settings, capabilities=['platform:read'])}")
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "database": "ready",
        "service": "tradeflow-api",
        "user": {
            "capabilities": ["platform:read"],
            "display_name": "Platform Tester",
            "subject": "user-123",
        },
    }


@pytest.mark.asyncio
async def test_ping_command_replays_the_original_result(
    client: AsyncClient,
    settings: Settings,
) -> None:
    headers = {
        "authorization": (f"Bearer {make_token(settings, capabilities=['platform:write'])}"),
        "idempotency-key": f"platform-check-{uuid4()}",
    }

    first = await client.post(
        "/v1/platform/ping",
        headers=headers,
        json={"message": "hello TradeFlow"},
    )
    replay = await client.post(
        "/v1/platform/ping",
        headers=headers,
        json={"message": "hello TradeFlow"},
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["message"] == "hello TradeFlow"
    UUID(first.json()["command_id"])
    assert first.headers["x-idempotency-replayed"] == "false"
    assert replay.headers["x-idempotency-replayed"] == "true"


@pytest.mark.asyncio
async def test_validation_errors_use_the_stable_error_contract(
    client: AsyncClient,
    settings: Settings,
) -> None:
    response = await client.post(
        "/v1/platform/ping",
        headers={
            "authorization": (f"Bearer {make_token(settings, capabilities=['platform:write'])}"),
            "idempotency-key": "invalid-platform-check",
        },
        json={"message": ""},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_failed"
    assert response.json()["error"]["correlation_id"] == response.headers["x-correlation-id"]


@pytest.mark.asyncio
async def test_unknown_routes_use_the_stable_error_contract(
    client: AsyncClient,
) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "route_not_found",
        "correlation_id": response.headers["x-correlation-id"],
        "message": "The requested API route does not exist.",
    }


@pytest.mark.asyncio
async def test_unexpected_errors_are_stable_and_do_not_expose_details(
    caplog: pytest.LogCaptureFixture,
    settings: Settings,
) -> None:
    caplog.set_level(logging.ERROR, logger="tradeflow_api.request")
    app = create_app(settings)

    @app.get("/testing/unexpected")
    async def unexpected() -> None:
        raise RuntimeError("sensitive implementation detail")

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as http_client:
            response = await http_client.get("/testing/unexpected")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "internal_error",
        "correlation_id": response.headers["x-correlation-id"],
        "message": "The API could not complete the request.",
    }
    assert "sensitive implementation detail" not in response.text
    assert any(
        '"event": "api_request_failed"' in record.message
        and response.headers["x-correlation-id"] in record.message
        and "sensitive implementation detail" not in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_api_startup_refuses_an_unmigrated_database(
    postgres_url: str,
    settings: Settings,
) -> None:
    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    await asyncio.to_thread(command.downgrade, config, "base")
    app = create_app(settings)

    try:
        with pytest.raises(RuntimeError, match="migrations are not current"):
            async with app.router.lifespan_context(app):
                pass
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
