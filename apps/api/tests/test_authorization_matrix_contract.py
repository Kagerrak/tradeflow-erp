from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def auth_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def auth_client(auth_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(auth_settings)
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
    settings: Settings, subject: str, idempotency_key: str, **extra: str
) -> dict[str, str]:
    return {
        "authorization": f"Bearer {_token(settings, subject, [])}",
        "idempotency-key": idempotency_key,
        **extra,
    }


def _cap_headers(
    settings: Settings,
    subject: str,
    capabilities: list[str],
    idempotency_key: str,
    **extra: str,
) -> dict[str, str]:
    return {
        "authorization": f"Bearer {_token(settings, subject, capabilities)}",
        "idempotency-key": idempotency_key,
        **extra,
    }


async def _bootstrap_organization(client: AsyncClient, settings: Settings) -> dict[str, Any]:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers=_cap_headers(
            settings,
            "bootstrapper",
            ["organization:bootstrap"],
            "auth-matrix-bootstrap",
        ),
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
                    "warehouses": [{"code": "MNL-01", "name": "Manila DC"}],
                },
                {
                    "code": "CEB",
                    "name": "Cebu",
                    "warehouses": [{"code": "CEB-01", "name": "Cebu DC"}],
                },
            ],
            "role_templates": [
                {
                    "code": "OPS_ADMIN",
                    "name": "Operations Administrator",
                    "capabilities": ["organization:admin"],
                },
                {
                    "code": "SALES",
                    "name": "Sales Representative",
                    "capabilities": [
                        "catalog:write",
                        "customer:read",
                        "customer:write",
                        "sales:order-read",
                        "sales:order-write",
                        "sales:pricing-write",
                        "sales:price-override",
                        "sales:discount-enter",
                    ],
                },
                {
                    "code": "COMMERCIAL_MGR",
                    "name": "Commercial Manager",
                    "capabilities": [
                        "sales:order-read",
                        "sales:commercial-approve",
                        "sales:below-floor-approve",
                        "sales:discount-approve",
                    ],
                },
            ],
            "users": [
                {
                    "subject": "operations-admin",
                    "display_name": "Operations Admin",
                    "is_operations_administrator": True,
                    "role_template_codes": ["OPS_ADMIN"],
                    "branch_codes": ["MNL", "CEB"],
                    "warehouse_codes": ["MNL-01", "CEB-01"],
                    "approval_authorities": [],
                },
                {
                    "subject": "sales-mnl",
                    "display_name": "Manila Sales",
                    "is_operations_administrator": False,
                    "role_template_codes": ["SALES"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                    "approval_authorities": [
                        {
                            "capability": "sales:discount-enter",
                            "branch_code": "MNL",
                            "maximum_amount": None,
                            "maximum_percentage": "0.050000",
                            "maker_checker_required": False,
                        }
                    ],
                },
                {
                    "subject": "commercial-mnl",
                    "display_name": "Manila Commercial Manager",
                    "is_operations_administrator": False,
                    "role_template_codes": ["COMMERCIAL_MGR"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                    "approval_authorities": [
                        {
                            "capability": "sales:discount-approve",
                            "branch_code": "MNL",
                            "maximum_amount": "1000000.00",
                            "maximum_percentage": "100.000000",
                            "maker_checker_required": True,
                        },
                        {
                            "capability": "sales:below-floor-approve",
                            "branch_code": "MNL",
                            "maximum_amount": "1000000.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        },
                    ],
                },
                {
                    "subject": "commercial-low-limit",
                    "display_name": "Low Limit Approver",
                    "is_operations_administrator": False,
                    "role_template_codes": ["COMMERCIAL_MGR"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                    "approval_authorities": [
                        {
                            "capability": "sales:below-floor-approve",
                            "branch_code": "MNL",
                            "maximum_amount": "1.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        },
                    ],
                },
                {
                    "subject": "commercial-ceb",
                    "display_name": "Cebu Commercial Manager",
                    "is_operations_administrator": False,
                    "role_template_codes": ["COMMERCIAL_MGR"],
                    "branch_codes": ["CEB"],
                    "warehouse_codes": ["CEB-01"],
                    "approval_authorities": [
                        {
                            "capability": "sales:below-floor-approve",
                            "branch_code": "CEB",
                            "maximum_amount": "1000000.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        },
                    ],
                },
                {
                    "subject": "commercial-no-authority",
                    "display_name": "Commercial Manager Without Authority",
                    "is_operations_administrator": False,
                    "role_template_codes": ["COMMERCIAL_MGR"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                    "approval_authorities": [],
                },
                {
                    "subject": "commercial-ceb-scoped",
                    "display_name": "Cebu Scoped Approver in Manila",
                    "is_operations_administrator": False,
                    "role_template_codes": ["COMMERCIAL_MGR"],
                    "branch_codes": ["MNL", "CEB"],
                    "warehouse_codes": ["MNL-01", "CEB-01"],
                    "approval_authorities": [
                        {
                            "capability": "sales:below-floor-approve",
                            "branch_code": "CEB",
                            "maximum_amount": "1000000.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        },
                    ],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


async def _create_customer(
    client: AsyncClient,
    settings: Settings,
    branch_id: str,
    account_number: str,
) -> dict[str, Any]:
    response = await client.post(
        "/v1/customers",
        headers=_headers(settings, "sales-mnl", f"customer-{account_number}"),
        json={
            "account_number": account_number,
            "branch_id": branch_id,
            "legal_name": "Authorization Matrix Retail",
            "status": "active",
            "payment_terms": "DUE_ON_RECEIPT",
            "payment_timing_policy": "prepaid",
            "credit_limit": None,
            "credit_hold": False,
            "contacts": [
                {
                    "name": "Buyer",
                    "role": "Purchasing",
                    "email": "buyer@example.test",
                    "phone": None,
                }
            ],
            "addresses": [
                {
                    "address_key": "DELIVERY",
                    "kind": "delivery",
                    "line_1": "100 Auth Street",
                    "line_2": None,
                    "city": "Manila",
                    "region": "NCR",
                    "postal_code": "1000",
                    "country_code": "PH",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


async def _configure_sku(
    client: AsyncClient,
    settings: Settings,
    sku_code: str,
    idempotency_key: str,
) -> dict[str, Any]:
    response = await client.post(
        "/v1/catalog/skus",
        headers=_headers(settings, "sales-mnl", idempotency_key),
        json={
            "product_code": "AUTH",
            "product_name": "Auth Product",
            "sku_code": sku_code,
            "sku_name": f"{sku_code} SKU",
            "base_stocking_unit": "EA",
            "tracking_policy": "untracked",
            "expiration_control": False,
            "conversions": [],
            "barcodes": [],
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


async def _configure_tax_code(client: AsyncClient, settings: Settings) -> dict[str, Any]:
    response = await client.post(
        "/v1/sales/tax-code-versions",
        headers=_headers(settings, "sales-mnl", "auth-tax-code"),
        json={
            "code": "VAT12",
            "name": "Value Added Tax 12%",
            "rate": "0.120000",
            "effective_from": date.today().isoformat(),
            "effective_to": None,
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


async def _configure_price_list(
    client: AsyncClient,
    settings: Settings,
    branch_id: str,
    sku_id: str,
    tax_code_version_id: str,
) -> dict[str, Any]:
    response = await client.post(
        "/v1/sales/price-list-versions",
        headers=_headers(settings, "sales-mnl", "auth-price-list"),
        json={
            "code": "MNL-AUTH",
            "branch_id": branch_id,
            "customer_id": None,
            "inclusion_mode": "exclusive",
            "effective_from": date.today().isoformat(),
            "effective_to": None,
            "items": [
                {
                    "sku_id": sku_id,
                    "unit_code": "EA",
                    "list_unit_price": "10.000000",
                    "floor_unit_price": "8.000000",
                    "tax_code_version_id": tax_code_version_id,
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


async def _seed_inventory(postgres_url: str, warehouse_id: str, sku_id: str) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        location_id = uuid4()
        await connection.execute(
            text(
                """
                INSERT INTO warehouse_stock_locations
                    (location_id, warehouse_id, code, name, custody, created_by)
                VALUES
                    (:location_id, :warehouse_id, 'AUTH-AVAIL',
                     'Auth Available', 'available', 'sales-mnl')
                """
            ),
            {"location_id": location_id, "warehouse_id": warehouse_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO inventory_availability
                    (sku_id, warehouse_id, location_id, identity_key, on_hand, reserved)
                VALUES (:sku_id, :warehouse_id, :location_id, '', '10.000000', 0)
                """
            ),
            {"sku_id": sku_id, "warehouse_id": warehouse_id, "location_id": location_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO inventory_valuation
                    (sku_id, warehouse_id, quantity_on_hand,
                     inventory_value, moving_average_unit_cost)
                VALUES (:sku_id, :warehouse_id, '10.000000', 0, 0)
                """
            ),
            {"sku_id": sku_id, "warehouse_id": warehouse_id},
        )
    await engine.dispose()


async def _create_below_floor_order(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], UUID]:
    org = await _bootstrap_organization(client, settings)
    branch_id = org["branches"][0]["branch_id"]
    warehouse_id = org["branches"][0]["warehouses"][0]["warehouse_id"]
    customer = await _create_customer(client, settings, branch_id, "AUTH-001")
    tax_code = await _configure_tax_code(client, settings)
    sku = await _configure_sku(client, settings, "AUTH-SKU", "auth-sku")
    price_list = await _configure_price_list(
        client, settings, branch_id, sku["sku_id"], tax_code["tax_code_version_id"]
    )
    await _seed_inventory(postgres_url, warehouse_id, sku["sku_id"])

    order_id = uuid4()
    response = await client.post(
        "/v1/sales/orders",
        headers=_headers(settings, "sales-mnl", "auth-below-floor-order"),
        json={
            "sales_order_id": str(order_id),
            "branch_id": branch_id,
            "customer_id": customer["customer_id"],
            "expected_customer_version": customer["version"],
            "expected_price_list_version_id": price_list["price_list_version_id"],
            "expected_pricing_date": date.today().isoformat(),
            "delivery_address_version_id": customer["addresses"][0]["address_version_id"],
            "payment_timing_policy": None,
            "payment_timing_override_reason": None,
            "order_discount_amount": "0.000000",
            "lines": [
                {
                    "line_id": str(uuid4()),
                    "sku_id": sku["sku_id"],
                    "expected_price_list_line_id": price_list["items"][0]["price_list_line_id"],
                    "expected_unit_conversion_id": None,
                    "expected_unit_conversion_version": None,
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "manual_override_unit_price": "5.000000",
                    "price_override_reason": "Competitive bid",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return org, customer, sku, order_id


@pytest.mark.asyncio
async def test_administrator_status_does_not_escalate_to_business_approval(
    auth_client: AsyncClient,
    auth_settings: Settings,
    postgres_url: str,
) -> None:
    org, _customer, _sku, order_id = await _create_below_floor_order(
        auth_client, auth_settings, postgres_url
    )
    warehouse_id = org["branches"][0]["warehouses"][0]["warehouse_id"]

    response = await auth_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=_cap_headers(
            auth_settings,
            "operations-admin",
            ["organization:admin"],
            "admin-approval-attempt",
            **{"if-match": "1"},
        ),
        json={
            "warehouse_id": warehouse_id,
            "exception_reason": "Admin attempting approval",
            "credit_override_reason": None,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "capability_required"


@pytest.mark.asyncio
async def test_approver_without_authority_row_is_denied(
    auth_client: AsyncClient,
    auth_settings: Settings,
    postgres_url: str,
) -> None:
    org, _customer, _sku, order_id = await _create_below_floor_order(
        auth_client, auth_settings, postgres_url
    )
    warehouse_id = org["branches"][0]["warehouses"][0]["warehouse_id"]

    response = await auth_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=_cap_headers(
            auth_settings,
            "commercial-no-authority",
            [
                "sales:order-read",
                "sales:order-write",
                "sales:commercial-approve",
                "sales:below-floor-approve",
            ],
            "unauthorized-approval-attempt",
            **{"if-match": "1"},
        ),
        json={
            "warehouse_id": warehouse_id,
            "exception_reason": "Lacks approval authority row",
            "credit_override_reason": None,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "approval_authority_required"


@pytest.mark.asyncio
async def test_cross_branch_approval_authority_is_denied(
    auth_client: AsyncClient,
    auth_settings: Settings,
    postgres_url: str,
) -> None:
    org, _customer, _sku, order_id = await _create_below_floor_order(
        auth_client, auth_settings, postgres_url
    )
    warehouse_id = org["branches"][0]["warehouses"][0]["warehouse_id"]

    response = await auth_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=_cap_headers(
            auth_settings,
            "commercial-ceb-scoped",
            [
                "sales:order-read",
                "sales:commercial-approve",
                "sales:below-floor-approve",
            ],
            "cross-branch-approval-attempt",
            **{"if-match": "1"},
        ),
        json={
            "warehouse_id": warehouse_id,
            "exception_reason": "Cross-branch approver",
            "credit_override_reason": None,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "approval_authority_required"


@pytest.mark.asyncio
async def test_approval_exceeding_amount_limit_is_denied(
    auth_client: AsyncClient,
    auth_settings: Settings,
    postgres_url: str,
) -> None:
    org, _customer, _sku, order_id = await _create_below_floor_order(
        auth_client, auth_settings, postgres_url
    )
    warehouse_id = org["branches"][0]["warehouses"][0]["warehouse_id"]

    response = await auth_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=_cap_headers(
            auth_settings,
            "commercial-low-limit",
            [
                "sales:order-read",
                "sales:commercial-approve",
                "sales:below-floor-approve",
            ],
            "over-limit-approval-attempt",
            **{"if-match": "1"},
        ),
        json={
            "warehouse_id": warehouse_id,
            "exception_reason": "Over limit approver",
            "credit_override_reason": None,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "approval_limit_exceeded"


@pytest.mark.asyncio
async def test_authorized_approval_with_valid_scope_and_limit_succeeds(
    auth_client: AsyncClient,
    auth_settings: Settings,
    postgres_url: str,
) -> None:
    org, _customer, _sku, order_id = await _create_below_floor_order(
        auth_client, auth_settings, postgres_url
    )
    warehouse_id = org["branches"][0]["warehouses"][0]["warehouse_id"]

    response = await auth_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=_cap_headers(
            auth_settings,
            "commercial-mnl",
            [
                "sales:order-read",
                "sales:commercial-approve",
                "sales:below-floor-approve",
            ],
            "valid-approval",
            **{"if-match": "1"},
        ),
        json={
            "warehouse_id": warehouse_id,
            "exception_reason": "Valid approval",
            "credit_override_reason": None,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "approved"
