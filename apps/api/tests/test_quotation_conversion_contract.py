from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def quotation_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def quotation_client(quotation_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(quotation_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


def token(settings: Settings, subject: str, capabilities: list[str] | None = None) -> str:
    return jwt.encode(
        {
            "sub": subject,
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "name": subject,
            "capabilities": capabilities or [],
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.auth_test_secret,
        algorithm="HS256",
    )


def auth(settings: Settings, subject: str, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(settings, subject)}", **headers}


async def bootstrap_quotation(client: AsyncClient, settings: Settings) -> dict[str, object]:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers={
            "Authorization": (
                f"Bearer {token(settings, 'bootstrapper', ['organization:bootstrap'])}"
            ),
            "Idempotency-Key": "quotation-bootstrap",
        },
        json={
            "company": {
                "code": "TF",
                "name": "TradeFlow Distribution",
                "base_currency": "PHP",
            },
            "branches": [
                {
                    "code": "MNL",
                    "name": "Manila",
                    "warehouses": [{"code": "MNL-01", "name": "Manila DC"}],
                },
            ],
            "role_templates": [
                {
                    "code": "ADMIN",
                    "name": "Administrator",
                    "capabilities": [
                        "organization:admin",
                        "organization:bootstrap",
                    ],
                },
                {
                    "code": "SALES",
                    "name": "Sales Representative",
                    "capabilities": [
                        "catalog:write",
                        "customer:write",
                        "customer:read",
                        "inventory:read",
                        "sales:order-write",
                        "sales:order-read",
                        "sales:pricing-write",
                        "sales:commercial-approve",
                        "sales:discount-enter",
                        "sales:price-override",
                        "sales:payment-timing-override",
                        "sales:quotation-write",
                        "sales:quotation-approve",
                        "sales:quotation-convert",
                    ],
                },
                {
                    "code": "SALES_NO_QUOTATION",
                    "name": "Sales without Quotation",
                    "capabilities": [
                        "sales:order-write",
                        "sales:order-read",
                    ],
                },
                {
                    "code": "COMMERCIAL_MANAGER",
                    "name": "Commercial Manager",
                    "capabilities": [
                        "sales:order-read",
                        "sales:order-write",
                        "sales:commercial-approve",
                        "sales:discount-approve",
                        "sales:below-floor-approve",
                        "sales:credit-override",
                        "sales:projection-rebuild",
                        "sales:quotation-approve",
                    ],
                },
            ],
            "users": [
                {
                    "subject": "admin-mnl",
                    "display_name": "Manila Admin",
                    "role_template_codes": ["ADMIN"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "sales-mnl",
                    "display_name": "Manila Sales",
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
                    "role_template_codes": ["COMMERCIAL_MANAGER"],
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
                    "subject": "sales-no-quotation",
                    "display_name": "Sales without Quotation",
                    "role_template_codes": ["SALES_NO_QUOTATION"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    organization = response.json()
    branch_id = organization["branches"][0]["branch_id"]
    series_response = await client.put(
        f"/v1/organization/branches/{branch_id}/document-series/quotation",
        headers={
            "Authorization": f"Bearer {token(settings, 'admin-mnl')}",
            "Idempotency-Key": "quotation-series",
            "If-Match": "0",
        },
        json={
            "prefix": "QT",
            "next_number": 1,
        },
    )
    assert series_response.status_code == 201, series_response.text
    return organization


async def create_customer(
    client: AsyncClient,
    settings: Settings,
    branch_id: str,
    *,
    account_number: str = "MNL-QT-001",
    idempotency_key: str = "quotation-customer",
) -> dict[str, object]:
    response = await client.post(
        "/v1/customers",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": idempotency_key},
        ),
        json={
            "account_number": account_number,
            "branch_id": branch_id,
            "legal_name": "Quotation Retail",
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
                    "line_1": "100 Quotation Street",
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
    return response.json()


async def configure_sku(
    client: AsyncClient,
    settings: Settings,
    *,
    product_code: str,
    sku_code: str,
    key: str,
) -> dict[str, object]:
    response = await client.post(
        "/v1/catalog/skus",
        headers=auth(settings, "sales-mnl", **{"Idempotency-Key": key}),
        json={
            "product_code": product_code,
            "product_name": f"{product_code} Product",
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
    return response.json()


async def configure_price_list(
    client: AsyncClient,
    settings: Settings,
    *,
    branch_id: str,
    items: list[dict[str, object]],
) -> dict[str, object]:
    response = await client.post(
        "/v1/sales/price-list-versions",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": "quotation-price-list"},
        ),
        json={
            "code": "MNL-QT-DEFAULT",
            "branch_id": branch_id,
            "customer_id": None,
            "inclusion_mode": "exclusive",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "items": items,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def configure_tax_code(client: AsyncClient, settings: Settings) -> dict[str, object]:
    response = await client.post(
        "/v1/sales/tax-code-versions",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": "quotation-tax-code-vat12"},
        ),
        json={
            "code": "VAT12",
            "name": "Value Added Tax 12%",
            "rate": "0.120000",
            "effective_from": "2026-01-01",
            "effective_to": None,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def quotation_fixture(client: AsyncClient, settings: Settings) -> dict[str, object]:
    organization = await bootstrap_quotation(client, settings)
    branch_id = organization["branches"][0]["branch_id"]
    customer = await create_customer(client, settings, branch_id)
    tax_code = await configure_tax_code(client, settings)
    cola = await configure_sku(
        client, settings, product_code="BEV", sku_code="COLA-330", key="quotation-cola"
    )
    price_list = await configure_price_list(
        client,
        settings,
        branch_id=branch_id,
        items=[
            {
                "sku_id": cola["sku_id"],
                "unit_code": "EA",
                "list_unit_price": "10.000000",
                "floor_unit_price": "8.000000",
                "tax_code_version_id": tax_code["tax_code_version_id"],
            }
        ],
    )
    return {
        "branch_id": branch_id,
        "customer": customer,
        "cola": cola,
        "price_list": price_list,
        "tax_code": tax_code,
    }


def quotation_draft_command(
    fixture: dict[str, object],
    *,
    expiry_date: date | None = None,
) -> dict[str, object]:
    customer = fixture["customer"]
    return {
        "branch_id": fixture["branch_id"],
        "customer_id": customer["customer_id"],
        "expected_customer_version": customer["version"],
        "expected_price_list_version_id": fixture["price_list"]["price_list_version_id"],
        "expected_pricing_date": date.today().isoformat(),
        "delivery_address_version_id": customer["addresses"][0]["address_version_id"],
        "expiry_date": (expiry_date or (date.today() + timedelta(days=7))).isoformat(),
        "payment_timing_policy": None,
        "payment_timing_override_reason": None,
        "order_discount_amount": "0.000000",
        "lines": [
            {
                "line_id": str(uuid4()),
                "sku_id": fixture["cola"]["sku_id"],
                "expected_price_list_line_id": fixture["price_list"]["items"][0][
                    "price_list_line_id"
                ],
                "expected_unit_conversion_id": None,
                "expected_unit_conversion_version": None,
                "quantity": "5.000000",
                "unit_code": "EA",
                "manual_override_unit_price": None,
                "price_override_reason": None,
            }
        ],
    }


@pytest.mark.asyncio
async def test_create_and_get_quotation(
    quotation_client: AsyncClient,
    quotation_settings: Settings,
) -> None:
    fixture = await quotation_fixture(quotation_client, quotation_settings)
    response = await quotation_client.post(
        "/v1/sales/quotations",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "create-quotation"},
        ),
        json=quotation_draft_command(fixture),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    quotation_id = body["quotation_id"]
    assert body["status"] == "draft"
    assert body["version"] == 1
    assert body["number"].startswith("QT-")
    assert body["grand_total"] == "56.00"

    fetched = await quotation_client.get(
        f"/v1/sales/quotations/{quotation_id}",
        headers=auth(quotation_settings, "sales-mnl"),
    )
    assert fetched.status_code == 200
    assert fetched.json()["number"] == body["number"]


@pytest.mark.asyncio
async def test_quotation_approval_and_conversion_creates_sales_order_draft(
    quotation_client: AsyncClient,
    quotation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await quotation_fixture(quotation_client, quotation_settings)
    create_response = await quotation_client.post(
        "/v1/sales/quotations",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "qt-approve-convert"},
        ),
        json=quotation_draft_command(fixture),
    )
    assert create_response.status_code == 201, create_response.text
    body = create_response.json()
    quotation_id = body["quotation_id"]
    version = body["version"]

    approval_response = await quotation_client.post(
        f"/v1/sales/quotations/{quotation_id}/approval",
        headers=auth(
            quotation_settings,
            "commercial-mnl",
            **{"Idempotency-Key": "qt-approval", "If-Match": str(version)},
        ),
        json={},
    )
    assert approval_response.status_code == 201, approval_response.text

    sales_order_id = uuid4()
    conversion_response = await quotation_client.post(
        f"/v1/sales/quotations/{quotation_id}/convert",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "qt-convert", "If-Match": str(version)},
        ),
        json={"sales_order_id": str(sales_order_id)},
    )
    assert conversion_response.status_code == 201, conversion_response.text
    body = conversion_response.json()
    assert body["quotation_id"] == str(quotation_id)
    assert body["sales_order_id"] == str(sales_order_id)

    sales_order = await quotation_client.get(
        f"/v1/sales/orders/{sales_order_id}",
        headers=auth(quotation_settings, "sales-mnl"),
    )
    assert sales_order.status_code == 200, sales_order.text
    assert sales_order.json()["status"] == "draft"
    assert sales_order.json()["grand_total"] == "56.00"
    assert sales_order.json()["lines"][0]["entered_quantity"] == "5.000000"

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        reservation_count = await connection.scalar(
            text("SELECT count(*) FROM inventory_reservation_events")
        )
        credit_count = await connection.scalar(
            text("SELECT count(*) FROM customer_credit_exposure")
        )
        conversion_count = await connection.scalar(
            text("SELECT count(*) FROM quotation_conversion_events WHERE quotation_id = :qid"),
            {"qid": quotation_id},
        )
    assert reservation_count == 0
    assert credit_count == 0
    assert conversion_count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_quotation_cannot_convert(
    quotation_client: AsyncClient,
    quotation_settings: Settings,
) -> None:
    fixture = await quotation_fixture(quotation_client, quotation_settings)
    create_response = await quotation_client.post(
        "/v1/sales/quotations",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "qt-expired-create"},
        ),
        json=quotation_draft_command(fixture, expiry_date=date.today()),
    )
    assert create_response.status_code == 201, create_response.text
    quotation_id = create_response.json()["quotation_id"]

    approval_response = await quotation_client.post(
        f"/v1/sales/quotations/{quotation_id}/approval",
        headers=auth(
            quotation_settings,
            "commercial-mnl",
            **{"Idempotency-Key": "qt-expired-approval", "If-Match": "1"},
        ),
        json={},
    )
    assert approval_response.status_code == 409
    assert approval_response.json()["error"]["code"] == "quotation_expired"


@pytest.mark.asyncio
async def test_superseded_quotation_cannot_convert(
    quotation_client: AsyncClient,
    quotation_settings: Settings,
) -> None:
    fixture = await quotation_fixture(quotation_client, quotation_settings)
    create_response = await quotation_client.post(
        "/v1/sales/quotations",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "qt-superseded-create"},
        ),
        json=quotation_draft_command(fixture),
    )
    assert create_response.status_code == 201, create_response.text
    quotation_id = create_response.json()["quotation_id"]

    approval_response = await quotation_client.post(
        f"/v1/sales/quotations/{quotation_id}/approval",
        headers=auth(
            quotation_settings,
            "commercial-mnl",
            **{"Idempotency-Key": "qt-superseded-approval", "If-Match": "1"},
        ),
        json={},
    )
    assert approval_response.status_code == 201, approval_response.text

    update_response = await quotation_client.put(
        f"/v1/sales/quotations/{quotation_id}",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "qt-superseded-update", "If-Match": "1"},
        ),
        json=quotation_draft_command(fixture),
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["status"] == "draft"
    assert update_response.json()["version"] == 2

    convert_response = await quotation_client.post(
        f"/v1/sales/quotations/{quotation_id}/convert",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "qt-superseded-convert", "If-Match": "1"},
        ),
        json={"sales_order_id": str(uuid4())},
    )
    assert convert_response.status_code == 409
    assert convert_response.json()["error"]["code"] == "optimistic_version_conflict"


@pytest.mark.asyncio
async def test_quotation_capability_guards(
    quotation_client: AsyncClient,
    quotation_settings: Settings,
) -> None:
    fixture = await quotation_fixture(quotation_client, quotation_settings)
    create_response = await quotation_client.post(
        "/v1/sales/quotations",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "qt-guard-create"},
        ),
        json=quotation_draft_command(fixture),
    )
    assert create_response.status_code == 201, create_response.text
    quotation_id = create_response.json()["quotation_id"]

    unauthorized_create = await quotation_client.post(
        "/v1/sales/quotations",
        headers=auth(
            quotation_settings,
            "sales-no-quotation",
            **{"Idempotency-Key": "qt-guard-unauth-create"},
        ),
        json=quotation_draft_command(fixture),
    )
    assert unauthorized_create.status_code == 403
    assert unauthorized_create.json()["error"]["code"] == "capability_required"

    unauthorized_approval = await quotation_client.post(
        f"/v1/sales/quotations/{quotation_id}/approval",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "qt-guard-unauth-approval", "If-Match": "1"},
        ),
        json={},
    )
    assert unauthorized_approval.status_code == 409
    assert unauthorized_approval.json()["error"]["code"] == "maker_checker_violation"


@pytest.mark.asyncio
async def test_quotation_conversion_is_idempotent(
    quotation_client: AsyncClient,
    quotation_settings: Settings,
) -> None:
    fixture = await quotation_fixture(quotation_client, quotation_settings)
    create_response = await quotation_client.post(
        "/v1/sales/quotations",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "qt-idempotent-create"},
        ),
        json=quotation_draft_command(fixture),
    )
    assert create_response.status_code == 201, create_response.text
    quotation_id = create_response.json()["quotation_id"]

    await quotation_client.post(
        f"/v1/sales/quotations/{quotation_id}/approval",
        headers=auth(
            quotation_settings,
            "commercial-mnl",
            **{"Idempotency-Key": "qt-idempotent-approval", "If-Match": "1"},
        ),
        json={},
    )

    sales_order_id = uuid4()
    first = await quotation_client.post(
        f"/v1/sales/quotations/{quotation_id}/convert",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "qt-idempotent-convert", "If-Match": "1"},
        ),
        json={"sales_order_id": str(sales_order_id)},
    )
    assert first.status_code == 201, first.text

    retry = await quotation_client.post(
        f"/v1/sales/quotations/{quotation_id}/convert",
        headers=auth(
            quotation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "qt-idempotent-convert", "If-Match": "1"},
        ),
        json={"sales_order_id": str(sales_order_id)},
    )
    assert retry.status_code == 200
    assert retry.json() == first.json()
