from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def config_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def config_client(config_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(config_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as http_client:
            yield http_client


def _token(
    settings: Settings,
    *,
    subject: str,
    capabilities: list[str],
) -> str:
    return jwt.encode(
        {
            "sub": subject,
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "name": subject.replace("-", " ").title(),
            "capabilities": capabilities,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        cast(str, settings.auth_test_secret),
        algorithm="HS256",
    )


def _admin_bootstrap_headers(settings: Settings) -> dict[str, str]:
    return {
        "authorization": "Bearer "
        + _token(
            settings,
            subject="operations-admin",
            capabilities=["organization:bootstrap"],
        ),
        "idempotency-key": "bootstrap-config-contract",
    }


def _admin_headers(
    settings: Settings,
    *,
    idempotency_key: str,
) -> dict[str, str]:
    return {
        "authorization": "Bearer "
        + _token(
            settings,
            subject="operations-admin",
            capabilities=[],
        ),
        "idempotency-key": idempotency_key,
    }


def _scoped_admin_headers(
    settings: Settings,
    *,
    idempotency_key: str,
) -> dict[str, str]:
    return {
        "authorization": "Bearer "
        + _token(
            settings,
            subject="scoped-admin",
            capabilities=[],
        ),
        "idempotency-key": idempotency_key,
    }


async def _bootstrap_organization(
    client: AsyncClient,
    settings: Settings,
) -> dict[str, Any]:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers=_admin_bootstrap_headers(settings),
        json={
            "company": {
                "code": "ACME",
                "name": "Acme Distribution",
                "base_currency": "PHP",
            },
            "branches": [
                {
                    "code": "MNL",
                    "name": "Manila",
                    "warehouses": [{"code": "MNL-MAIN", "name": "Manila Main"}],
                },
                {
                    "code": "CEB",
                    "name": "Cebu",
                    "warehouses": [{"code": "CEB-MAIN", "name": "Cebu Main"}],
                },
            ],
            "role_templates": [
                {
                    "code": "OPS_ADMIN",
                    "name": "Operations Administrator",
                    "capabilities": ["organization:admin"],
                },
                {
                    "code": "SALES_REP",
                    "name": "Sales Representative",
                    "capabilities": ["customer:read", "customer:write"],
                },
            ],
            "users": [
                {
                    "subject": "operations-admin",
                    "display_name": "Operations Admin",
                    "is_operations_administrator": True,
                    "role_template_codes": ["OPS_ADMIN"],
                    "branch_codes": ["MNL", "CEB"],
                    "warehouse_codes": ["MNL-MAIN", "CEB-MAIN"],
                    "approval_authorities": [],
                },
                {
                    "subject": "sales-mnl",
                    "display_name": "Manila Sales",
                    "is_operations_administrator": False,
                    "role_template_codes": ["SALES_REP"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                    "approval_authorities": [],
                },
            ],
        },
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


async def _create_customer(
    client: AsyncClient,
    settings: Settings,
    branch_id: str,
    account_number: str,
) -> dict[str, Any]:
    response = await client.post(
        "/v1/customers",
        headers={
            "authorization": "Bearer "
            + _token(
                settings,
                subject="sales-mnl",
                capabilities=[],
            ),
            "idempotency-key": f"create-{account_number}",
        },
        json={
            "account_number": account_number,
            "branch_id": branch_id,
            "legal_name": "Config Contract Customer",
            "status": "active",
            "payment_terms": "NET_30",
            "payment_timing_policy": "on_account",
            "credit_limit": "25000.00",
            "credit_hold": True,
            "contacts": [
                {
                    "name": "Test Contact",
                    "role": "Purchasing",
                    "email": "test@customer.example",
                    "phone": "+63 917 555 0101",
                }
            ],
            "addresses": [
                {
                    "address_key": "BILLING",
                    "kind": "billing",
                    "line_1": "1 Test Road",
                    "line_2": None,
                    "city": "Manila",
                    "region": "NCR",
                    "postal_code": "1018",
                    "country_code": "PH",
                }
            ],
        },
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


@pytest.mark.asyncio
async def test_company_timezone_update_rejects_invalid_timezones(
    config_client: AsyncClient,
    config_settings: Settings,
) -> None:
    await _bootstrap_organization(config_client, config_settings)

    invalid = await config_client.patch(
        "/v1/organization/company",
        headers={
            **_admin_headers(config_settings, idempotency_key="timezone-invalid"),
            "if-match": "1",
        },
        json={
            "name": "Acme Distribution",
            "base_currency": "PHP",
            "timezone": "Mars/Colony",
        },
    )

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "request_validation_failed"

    valid = await config_client.patch(
        "/v1/organization/company",
        headers={
            **_admin_headers(config_settings, idempotency_key="timezone-valid"),
            "if-match": "1",
        },
        json={
            "name": "Acme Distribution",
            "base_currency": "PHP",
            "timezone": "Asia/Manila",
        },
    )

    assert valid.status_code == 200
    assert valid.json()["timezone"] == "Asia/Manila"
    assert valid.json()["version"] == 2


@pytest.mark.asyncio
async def test_base_currency_guard_blocks_change_after_customer_ledger_posting(
    config_client: AsyncClient,
    config_settings: Settings,
    postgres_url: str,
) -> None:
    organization = await _bootstrap_organization(config_client, config_settings)
    manila_branch = next(branch for branch in organization["branches"] if branch["code"] == "MNL")
    customer = await _create_customer(
        config_client,
        config_settings,
        branch_id=str(manila_branch["branch_id"]),
        account_number="CFG-0001",
    )

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO customer_ledger_entries (
                    entry_id, customer_id, entry_type, source_type, source_id,
                    amount, currency, branch_id, actor_subject, correlation_id,
                    idempotency_key, posted_at
                ) VALUES (
                    :entry_id, :customer_id, 'invoice', 'draft_invoice', :source_id,
                    :amount, 'PHP', :branch_id, 'operations-admin', :correlation_id,
                    :idempotency_key, now()
                )
                """
            ),
            {
                "entry_id": str(uuid4()),
                "customer_id": str(customer["customer_id"]),
                "source_id": str(uuid4()),
                "amount": Decimal("100.00"),
                "branch_id": str(manila_branch["branch_id"]),
                "correlation_id": "config-contract-ledger",
                "idempotency_key": "config-contract-ledger",
            },
        )
    await engine.dispose()

    blocked = await config_client.patch(
        "/v1/organization/company",
        headers={
            **_admin_headers(config_settings, idempotency_key="base-currency-blocked"),
            "if-match": "1",
        },
        json={
            "name": "Acme Distribution",
            "base_currency": "USD",
            "timezone": "UTC",
        },
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "base_currency_immutable"


@pytest.mark.asyncio
async def test_branch_settings_update_and_replay(
    config_client: AsyncClient,
    config_settings: Settings,
) -> None:
    organization = await _bootstrap_organization(config_client, config_settings)
    manila_branch = next(branch for branch in organization["branches"] if branch["code"] == "MNL")

    first = await config_client.patch(
        f"/v1/organization/branches/{manila_branch['branch_id']}/settings",
        headers={
            **_admin_headers(config_settings, idempotency_key="branch-settings-first"),
            "if-match": "1",
        },
        json={"name": "Manila Operations", "timezone": "Asia/Manila"},
    )

    assert first.status_code == 200
    assert first.json()["name"] == "Manila Operations"
    assert first.json()["timezone"] == "Asia/Manila"
    assert first.json()["version"] == 2
    assert first.headers["x-idempotency-replayed"] == "false"

    replay = await config_client.patch(
        f"/v1/organization/branches/{manila_branch['branch_id']}/settings",
        headers={
            **_admin_headers(config_settings, idempotency_key="branch-settings-first"),
            "if-match": "1",
        },
        json={"name": "Manila Operations", "timezone": "Asia/Manila"},
    )

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.headers["x-idempotency-replayed"] == "true"

    stale = await config_client.patch(
        f"/v1/organization/branches/{manila_branch['branch_id']}/settings",
        headers={
            **_admin_headers(config_settings, idempotency_key="branch-settings-stale"),
            "if-match": "1",
        },
        json={"name": "Stale", "timezone": "UTC"},
    )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "optimistic_version_conflict"


@pytest.mark.asyncio
async def test_document_series_create_update_version_and_regression(
    config_client: AsyncClient,
    config_settings: Settings,
) -> None:
    organization = await _bootstrap_organization(config_client, config_settings)
    manila_branch = next(branch for branch in organization["branches"] if branch["code"] == "MNL")

    created = await config_client.put(
        f"/v1/organization/branches/{manila_branch['branch_id']}/document-series/invoice",
        headers={
            **_admin_headers(config_settings, idempotency_key="series-create"),
            "if-match": "0",
        },
        json={"prefix": "INV-", "next_number": 1},
    )

    assert created.status_code == 201
    assert created.json()["prefix"] == "INV-"
    assert created.json()["next_number"] == 1
    assert created.json()["version"] == 1
    assert created.headers["x-idempotency-replayed"] == "false"

    replay = await config_client.put(
        f"/v1/organization/branches/{manila_branch['branch_id']}/document-series/invoice",
        headers={
            **_admin_headers(config_settings, idempotency_key="series-create"),
            "if-match": "0",
        },
        json={"prefix": "INV-", "next_number": 1},
    )

    assert replay.status_code == 200
    assert replay.json() == created.json()
    assert replay.headers["x-idempotency-replayed"] == "true"

    updated = await config_client.put(
        f"/v1/organization/branches/{manila_branch['branch_id']}/document-series/invoice",
        headers={
            **_admin_headers(config_settings, idempotency_key="series-update"),
            "if-match": "1",
        },
        json={"prefix": "INV-", "next_number": 100},
    )

    assert updated.status_code == 200
    assert updated.json()["next_number"] == 100
    assert updated.json()["version"] == 2

    regression = await config_client.put(
        f"/v1/organization/branches/{manila_branch['branch_id']}/document-series/invoice",
        headers={
            **_admin_headers(config_settings, idempotency_key="series-regression"),
            "if-match": "2",
        },
        json={"prefix": "INV-", "next_number": 50},
    )

    assert regression.status_code == 409
    assert regression.json()["error"]["code"] == "document_series_number_regression"

    listed = await config_client.get(
        f"/v1/organization/branches/{manila_branch['branch_id']}/document-series",
        headers=_admin_headers(config_settings, idempotency_key="series-list"),
    )

    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["version"] == 2


@pytest.mark.asyncio
async def test_document_series_scoped_admin_cannot_configure_outside_scope(
    config_client: AsyncClient,
    config_settings: Settings,
) -> None:
    organization = await _bootstrap_organization(config_client, config_settings)
    manila_branch = next(branch for branch in organization["branches"] if branch["code"] == "MNL")
    cebu_branch = next(branch for branch in organization["branches"] if branch["code"] == "CEB")

    create_scoped_admin = await config_client.put(
        "/v1/organization/users/scoped-admin",
        headers={
            **_admin_headers(config_settings, idempotency_key="create-scoped-admin"),
            "if-match": "0",
        },
        json={
            "display_name": "Scoped Admin",
            "is_operations_administrator": False,
            "is_active": True,
            "role_template_codes": ["OPS_ADMIN"],
            "branch_codes": ["MNL"],
            "warehouse_codes": [],
            "approval_authorities": [],
        },
    )

    assert create_scoped_admin.status_code == 201

    cebu_attempt = await config_client.put(
        f"/v1/organization/branches/{cebu_branch['branch_id']}/document-series/invoice",
        headers={
            **_scoped_admin_headers(config_settings, idempotency_key="scoped-cebu-series"),
            "if-match": "0",
        },
        json={"prefix": "CEB-INV-", "next_number": 1},
    )

    assert cebu_attempt.status_code == 403
    assert cebu_attempt.json()["error"]["code"] == "operational_scope_required"

    manila_attempt = await config_client.put(
        f"/v1/organization/branches/{manila_branch['branch_id']}/document-series/invoice",
        headers={
            **_scoped_admin_headers(config_settings, idempotency_key="scoped-mnl-series"),
            "if-match": "0",
        },
        json={"prefix": "MNL-INV-", "next_number": 1},
    )

    assert manila_attempt.status_code == 201


@pytest.mark.asyncio
async def test_company_document_template_versioned_and_preview_deterministic(
    config_client: AsyncClient,
    config_settings: Settings,
) -> None:
    await _bootstrap_organization(config_client, config_settings)

    first = await config_client.put(
        "/v1/organization/document-templates/invoice",
        headers=_admin_headers(config_settings, idempotency_key="template-v1"),
        json={
            "name": "Invoice Template v1",
            "template_body": "Invoice for {{ customer_name }}",
            "effective_from": str(date.today()),
            "is_active": True,
        },
    )

    assert first.status_code == 201
    assert first.json()["version"] == 1
    assert first.json()["branch_id"] is None
    assert first.headers["x-idempotency-replayed"] == "false"

    replay = await config_client.put(
        "/v1/organization/document-templates/invoice",
        headers=_admin_headers(config_settings, idempotency_key="template-v1"),
        json={
            "name": "Invoice Template v1",
            "template_body": "Invoice for {{ customer_name }}",
            "effective_from": str(date.today()),
            "is_active": True,
        },
    )

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.headers["x-idempotency-replayed"] == "true"

    second = await config_client.put(
        "/v1/organization/document-templates/invoice",
        headers=_admin_headers(config_settings, idempotency_key="template-v2"),
        json={
            "name": "Invoice Template v2",
            "template_body": "Invoice v2 for {{ customer_name }}",
            "effective_from": str(date.today()),
            "is_active": True,
        },
    )

    assert second.status_code == 201
    assert second.json()["version"] == 2

    listed = await config_client.get(
        "/v1/organization/document-templates/invoice",
        headers=_admin_headers(config_settings, idempotency_key="template-list"),
    )

    assert listed.status_code == 200
    assert [row["version"] for row in listed.json()] == [2, 1]

    template_id = first.json()["document_template_id"]
    preview_a = await config_client.post(
        f"/v1/organization/document-templates/{template_id}/preview",
        headers=_admin_headers(config_settings, idempotency_key="preview-a"),
        json={
            "context": {
                "z_last": "Zulu",
                "customer_name": "Acme",
                "a_first": "Alpha",
            }
        },
    )
    preview_b = await config_client.post(
        f"/v1/organization/document-templates/{template_id}/preview",
        headers=_admin_headers(config_settings, idempotency_key="preview-b"),
        json={
            "context": {
                "customer_name": "Acme",
                "a_first": "Alpha",
                "z_last": "Zulu",
            }
        },
    )

    assert preview_a.status_code == 200
    assert preview_b.status_code == 200
    assert preview_a.json()["rendered_body"] == preview_b.json()["rendered_body"]


@pytest.mark.asyncio
async def test_branch_document_template_scope_denial(
    config_client: AsyncClient,
    config_settings: Settings,
) -> None:
    organization = await _bootstrap_organization(config_client, config_settings)
    cebu_branch = next(branch for branch in organization["branches"] if branch["code"] == "CEB")

    await config_client.put(
        "/v1/organization/users/scoped-admin",
        headers={
            **_admin_headers(config_settings, idempotency_key="create-scoped-admin-template"),
            "if-match": "0",
        },
        json={
            "display_name": "Scoped Admin",
            "is_operations_administrator": False,
            "is_active": True,
            "role_template_codes": ["OPS_ADMIN"],
            "branch_codes": ["MNL"],
            "warehouse_codes": [],
            "approval_authorities": [],
        },
    )

    denied = await config_client.put(
        f"/v1/organization/branches/{cebu_branch['branch_id']}/document-templates/invoice",
        headers=_scoped_admin_headers(config_settings, idempotency_key="scoped-cebu-template"),
        json={
            "name": "Cebu Invoice",
            "template_body": "Cebu invoice",
            "effective_from": str(date.today()),
            "is_active": True,
        },
    )

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "operational_scope_required"
