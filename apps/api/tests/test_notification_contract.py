from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def notification_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def notification_client(notification_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(notification_settings)
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
        headers=_headers(
            settings,
            "bootstrapper",
            ["organization:bootstrap"],
            **{"Idempotency-Key": "notification-bootstrap"},
        ),
        json={
            "company": {
                "code": "NOTIFY",
                "name": "Notification Test Co",
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
                    "code": "NOTIFY_USER",
                    "name": "Notification User",
                    "capabilities": ["notification:read", "notification:manage"],
                },
                {
                    "code": "DELIVERY",
                    "name": "Delivery Staff",
                    "capabilities": ["fulfillment:delivery-read"],
                },
            ],
            "users": [
                {
                    "subject": "notify-user",
                    "display_name": "Notify User",
                    "is_operations_administrator": False,
                    "role_template_codes": ["NOTIFY_USER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                    "approval_authorities": [],
                },
                {
                    "subject": "delivery-user",
                    "display_name": "Delivery User",
                    "is_operations_administrator": False,
                    "role_template_codes": ["DELIVERY", "NOTIFY_USER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                    "approval_authorities": [],
                },
                {
                    "subject": "other-branch-user",
                    "display_name": "Other Branch User",
                    "is_operations_administrator": False,
                    "role_template_codes": ["NOTIFY_USER", "DELIVERY"],
                    "branch_codes": [],
                    "warehouse_codes": [],
                    "approval_authorities": [],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_device_registration_requires_manage_capability(
    notification_client: AsyncClient,
    notification_settings: Settings,
) -> None:
    response = await notification_client.post(
        "/v1/notifications/devices",
        headers={"authorization": f"Bearer {_token(notification_settings, 'no-cap', [])}"},
        json={"device_token": "token-1", "platform": "ios"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_device_registration_and_listing(
    notification_client: AsyncClient,
    notification_settings: Settings,
) -> None:
    await _bootstrap_organization(notification_client, notification_settings)
    response = await notification_client.post(
        "/v1/notifications/devices",
        headers=_headers(
            notification_settings,
            "notify-user",
            ["notification:manage"],
            **{"Idempotency-Key": "register-device-1"},
        ),
        json={
            "device_token": "device-token-abc",
            "platform": "ios",
            "app_version": "1.0.0",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["user_subject"] == "notify-user"
    assert data["platform"] == "ios"
    assert data["device_token_summary"].startswith("devi")
    device_id = data["device_registration_id"]

    list_response = await notification_client.get(
        "/v1/notifications/devices",
        headers=_headers(notification_settings, "notify-user", ["notification:read"]),
    )
    assert list_response.status_code == 200
    devices = list_response.json()
    assert len(devices) == 1
    assert devices[0]["device_registration_id"] == device_id

    delete_response = await notification_client.delete(
        f"/v1/notifications/devices/{device_id}",
        headers=_headers(notification_settings, "notify-user", ["notification:manage"]),
    )
    assert delete_response.status_code == 204


@pytest.mark.asyncio
async def test_preferences_defaults_and_update(
    notification_client: AsyncClient,
    notification_settings: Settings,
) -> None:
    await _bootstrap_organization(notification_client, notification_settings)
    get_response = await notification_client.get(
        "/v1/notifications/preferences",
        headers=_headers(notification_settings, "notify-user", ["notification:read"]),
    )
    assert get_response.status_code == 200
    preferences = get_response.json()
    categories = {p["category"] for p in preferences}
    assert "delivery_confirmation" in categories
    assert "approval_required" in categories

    put_response = await notification_client.put(
        "/v1/notifications/preferences/delivery_confirmation",
        headers=_headers(notification_settings, "notify-user", ["notification:manage"]),
        json={
            "push_enabled": False,
            "inbox_enabled": True,
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
        },
    )
    assert put_response.status_code == 200
    updated = put_response.json()
    assert updated["push_enabled"] is False
    assert updated["quiet_hours_start"] == "22:00"


@pytest.mark.asyncio
async def test_inbox_is_scoped_to_recipient(
    postgres_url: str,
    notification_client: AsyncClient,
    notification_settings: Settings,
) -> None:
    org = await _bootstrap_organization(notification_client, notification_settings)
    branch_id = UUID(org["branches"][0]["branch_id"])
    warehouse_id = UUID(org["branches"][0]["warehouses"][0]["warehouse_id"])
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO operational_notifications ("
                "notification_id, source_event_id, source_type, source_id, "
                "recipient_subject, notification_type, title, body, deep_link_path, "
                "deep_link_token, branch_id, warehouse_id, status, correlation_id"
                ") VALUES (:id, NULL, 'test', :source_id, :recipient, 'test_type', "
                "'Title', 'Body', '/test', 'token', :branch_id, :warehouse_id, 'pending', 'corr')"
            ),
            {
                "id": uuid4(),
                "source_id": uuid4(),
                "recipient": "notify-user",
                "branch_id": branch_id,
                "warehouse_id": warehouse_id,
            },
        )
    await engine.dispose()

    inbox_response = await notification_client.get(
        "/v1/notifications/inbox",
        headers=_headers(notification_settings, "notify-user", ["notification:read"]),
    )
    assert inbox_response.status_code == 200
    items = inbox_response.json()["items"]
    assert len(items) == 1
    assert items[0]["recipient_subject"] == "notify-user"

    other_response = await notification_client.get(
        "/v1/notifications/inbox",
        headers=_headers(notification_settings, "delivery-user", ["notification:read"]),
    )
    assert other_response.status_code == 200
    assert len(other_response.json()["items"]) == 0


@pytest.mark.asyncio
async def test_read_and_revoke_notification(
    postgres_url: str,
    notification_client: AsyncClient,
    notification_settings: Settings,
) -> None:
    org = await _bootstrap_organization(notification_client, notification_settings)
    branch_id = UUID(org["branches"][0]["branch_id"])
    warehouse_id = UUID(org["branches"][0]["warehouses"][0]["warehouse_id"])
    notification_id = uuid4()
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO operational_notifications ("
                "notification_id, source_event_id, source_type, source_id, "
                "recipient_subject, notification_type, title, body, deep_link_path, "
                "deep_link_token, branch_id, warehouse_id, status, correlation_id"
                ") VALUES (:id, NULL, 'test', :source_id, :recipient, 'test_type', "
                "'Title', 'Body', '/test', 'token', :branch_id, :warehouse_id, 'pending', 'corr')"
            ),
            {
                "id": notification_id,
                "source_id": uuid4(),
                "recipient": "notify-user",
                "branch_id": branch_id,
                "warehouse_id": warehouse_id,
            },
        )
    await engine.dispose()

    read_response = await notification_client.post(
        f"/v1/notifications/{notification_id}/read",
        headers=_headers(
            notification_settings,
            "notify-user",
            ["notification:read"],
            **{"Idempotency-Key": "read-1"},
        ),
        json={},
    )
    assert read_response.status_code == 204

    read_response_2 = await notification_client.post(
        f"/v1/notifications/{notification_id}/read",
        headers=_headers(
            notification_settings,
            "notify-user",
            ["notification:read"],
            **{"Idempotency-Key": "read-1"},
        ),
        json={},
    )
    assert read_response_2.status_code == 204

    inbox_response = await notification_client.get(
        "/v1/notifications/inbox?status=read",
        headers=_headers(notification_settings, "notify-user", ["notification:read"]),
    )
    assert inbox_response.status_code == 200
    assert len(inbox_response.json()["items"]) == 1

    revoke_response = await notification_client.post(
        f"/v1/notifications/{notification_id}/revoke",
        headers=_headers(
            notification_settings,
            "notify-user",
            ["notification:manage"],
            **{"Idempotency-Key": "revoke-1"},
        ),
        json={"reason": "No longer relevant"},
    )
    assert revoke_response.status_code == 204

    inbox_after_revoke = await notification_client.get(
        "/v1/notifications/inbox",
        headers=_headers(notification_settings, "notify-user", ["notification:read"]),
    )
    assert inbox_after_revoke.status_code == 200
    items = inbox_after_revoke.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "revoked"
    assert items[0]["title"] == "Notification removed"


@pytest.mark.asyncio
async def test_deep_link_authorization(
    postgres_url: str,
    notification_client: AsyncClient,
    notification_settings: Settings,
) -> None:
    org = await _bootstrap_organization(notification_client, notification_settings)
    branch_id = UUID(org["branches"][0]["branch_id"])
    warehouse_id = UUID(org["branches"][0]["warehouses"][0]["warehouse_id"])
    notification_id = uuid4()
    deep_link_token = str(
        uuid5(NAMESPACE_URL, f"tradeflow:notification-deep-link:{notification_id}")
    )
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO operational_notifications ("
                "notification_id, source_event_id, source_type, source_id, "
                "recipient_subject, notification_type, title, body, deep_link_path, "
                "deep_link_token, branch_id, warehouse_id, required_capability, "
                "status, correlation_id"
                ") VALUES (:id, NULL, 'test', :source_id, :recipient, 'test_type', "
                "'Title', 'Body', '/test', :token, :branch_id, :warehouse_id, "
                "'fulfillment:delivery-read', 'pending', 'corr')"
            ),
            {
                "id": notification_id,
                "source_id": uuid4(),
                "recipient": "delivery-user",
                "token": deep_link_token,
                "branch_id": branch_id,
                "warehouse_id": warehouse_id,
            },
        )
    await engine.dispose()

    ok_response = await notification_client.get(
        f"/v1/notifications/deep-links/{deep_link_token}",
        headers=_headers(notification_settings, "delivery-user", ["notification:read"]),
    )
    assert ok_response.status_code == 200
    assert ok_response.json()["authorized_path"] == "/test"

    wrong_user_response = await notification_client.get(
        f"/v1/notifications/deep-links/{deep_link_token}",
        headers=_headers(notification_settings, "notify-user", ["notification:read"]),
    )
    assert wrong_user_response.status_code == 403

    missing_scope_response = await notification_client.get(
        f"/v1/notifications/deep-links/{deep_link_token}",
        headers=_headers(
            notification_settings,
            "other-branch-user",
            ["notification:read", "fulfillment:delivery-read"],
        ),
    )
    assert missing_scope_response.status_code == 403

    missing_capability_response = await notification_client.get(
        f"/v1/notifications/deep-links/{deep_link_token}",
        headers=_headers(notification_settings, "notify-user", ["notification:read"]),
    )
    assert missing_capability_response.status_code == 403


@pytest.mark.asyncio
async def test_unknown_deep_link_returns_404(
    notification_client: AsyncClient,
    notification_settings: Settings,
) -> None:
    await _bootstrap_organization(notification_client, notification_settings)
    response = await notification_client.get(
        "/v1/notifications/deep-links/no-such-token",
        headers=_headers(notification_settings, "notify-user", ["notification:read"]),
    )
    assert response.status_code == 404
