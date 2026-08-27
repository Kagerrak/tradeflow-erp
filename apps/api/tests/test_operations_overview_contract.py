from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def overview_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def overview_client(overview_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(overview_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


@pytest.mark.asyncio
async def test_operations_overview_requires_an_authenticated_operator(
    overview_client: AsyncClient,
) -> None:
    response = await overview_client.get("/v1/operations/overview")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
