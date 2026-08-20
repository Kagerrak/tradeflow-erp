from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings
from tradeflow_api.notification_outbox import (
    _is_recipient_authorized,
    create_notifications_for_event,
)


@pytest.fixture
def outbox_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def outbox_client(outbox_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(outbox_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as http_client:
            yield http_client


def _token(settings: Settings, subject: str, capabilities: list[str]) -> str:
    return jwt.encode(
        {
            "sub": subject,
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "name": subject.replace("-", " ").title(),
            "capabilities": capabilities,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.auth_test_secret,
        algorithm="HS256",
    )


def _headers(
    settings: Settings, subject: str, capabilities: list[str], **extra: str
) -> dict[str, str]:
    return {
        "authorization": f"Bearer {_token(settings, subject, capabilities)}",
        **extra,
    }


async def _bootstrap_organization(client: AsyncClient, settings: Settings) -> dict[str, Any]:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers={
            **_headers(settings, "bootstrapper", ["organization:bootstrap"]),
            "Idempotency-Key": "outbox-bootstrap",
        },
        json={
            "company": {
                "code": "OUTBOX",
                "name": "Outbox Test Co",
                "base_currency": "PHP",
            },
            "branches": [
                {
                    "code": "MNL",
                    "name": "Manila",
                    "warehouses": [{"code": "MNL-01", "name": "Manila DC"}],
                }
            ],
            "role_templates": [
                {
                    "code": "DELIVERY",
                    "name": "Delivery Staff",
                    "capabilities": ["fulfillment:delivery-read"],
                },
            ],
            "users": [
                {
                    "subject": "scoped-user",
                    "display_name": "Scoped User",
                    "is_operations_administrator": False,
                    "role_template_codes": ["DELIVERY"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                    "approval_authorities": [],
                },
                {
                    "subject": "unscoped-user",
                    "display_name": "Unscoped User",
                    "is_operations_administrator": False,
                    "role_template_codes": ["DELIVERY"],
                    "branch_codes": [],
                    "warehouse_codes": [],
                    "approval_authorities": [],
                },
                {
                    "subject": "no-cap-user",
                    "display_name": "No Cap User",
                    "is_operations_administrator": False,
                    "role_template_codes": [],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                    "approval_authorities": [],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_recipient_authorization_checks_capability_and_scope(
    outbox_client: AsyncClient,
    outbox_settings: Settings,
    postgres_url: str,
) -> None:
    org = await _bootstrap_organization(outbox_client, outbox_settings)
    branch_id = UUID(org["branches"][0]["branch_id"])
    warehouse_id = UUID(org["branches"][0]["warehouses"][0]["warehouse_id"])

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        session = AsyncSession(connection)
        assert await _is_recipient_authorized(
            session, "scoped-user", branch_id, warehouse_id, "fulfillment:delivery-read"
        )
        assert not await _is_recipient_authorized(
            session, "unscoped-user", branch_id, warehouse_id, "fulfillment:delivery-read"
        )
        assert not await _is_recipient_authorized(
            session, "no-cap-user", branch_id, warehouse_id, "fulfillment:delivery-read"
        )
        await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_notifications_for_event_is_idempotent(
    outbox_client: AsyncClient,
    outbox_settings: Settings,
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _bootstrap_organization(outbox_client, outbox_settings)
    event_id = uuid4()
    notification_id = uuid4()

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO outbox_events ("
                "outbox_event_id, aggregate_type, aggregate_id, event_type, payload, "
                "correlation_id, occurred_at"
                ") VALUES (:id, 'delivery', :aggregate_id, 'delivery.confirmed.v1', "
                ":payload, 'corr', now())"
            ),
            {
                "id": event_id,
                "aggregate_id": uuid4(),
                "payload": "{}",
            },
        )
        await connection.execute(
            text(
                "INSERT INTO outbox_processing_state ("
                "outbox_event_id, status, attempts, available_at"
                ") VALUES (:id, 'pending', 0, now())"
            ),
            {"id": event_id},
        )
    await engine.dispose()

    async def _fake_handler(_session: AsyncSession, _event: dict[str, Any]) -> UUID:
        return notification_id

    monkeypatch.setattr(
        "tradeflow_api.notification_outbox._handle_delivery_confirmed", _fake_handler
    )

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        session = AsyncSession(connection)
        result = await create_notifications_for_event(session, event_id)
        assert result == notification_id
        result2 = await create_notifications_for_event(session, event_id)
        assert result2 == notification_id
        await session.close()
    await engine.dispose()
