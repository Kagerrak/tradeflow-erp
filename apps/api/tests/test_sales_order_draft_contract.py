from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def sales_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def sales_client(sales_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(sales_settings)
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


async def bootstrap_sales(client: AsyncClient, settings: Settings) -> dict[str, object]:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers={
            "Authorization": (
                f"Bearer {token(settings, 'bootstrapper', ['organization:bootstrap'])}"
            ),
            "Idempotency-Key": "sales-bootstrap",
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
                {
                    "code": "CEB",
                    "name": "Cebu",
                    "warehouses": [{"code": "CEB-01", "name": "Cebu DC"}],
                },
            ],
            "role_templates": [
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
                    ],
                },
                {
                    "code": "SALES_READ",
                    "name": "Sales Reader",
                    "capabilities": ["sales:order-read"],
                },
                {
                    "code": "SALES_NO_OVERRIDE",
                    "name": "Sales without Payment Override",
                    "capabilities": ["sales:order-read", "sales:order-write"],
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
                    ],
                },
            ],
            "users": [
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
                    "subject": "sales-ceb",
                    "display_name": "Cebu Sales",
                    "role_template_codes": ["SALES_READ"],
                    "branch_codes": ["CEB"],
                    "warehouse_codes": ["CEB-01"],
                },
                {
                    "subject": "sales-mnl-basic",
                    "display_name": "Manila Sales Basic",
                    "role_template_codes": ["SALES_NO_OVERRIDE"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
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
                        {
                            "capability": "sales:credit-override",
                            "branch_code": "MNL",
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
    return response.json()


async def create_customer(
    client: AsyncClient,
    settings: Settings,
    branch_id: str,
    *,
    account_number: str = "MNL-SALES-001",
    idempotency_key: str = "sales-customer",
    payment_terms: str = "DUE_ON_RECEIPT",
    payment_timing_policy: str = "prepaid",
    credit_limit: str | None = None,
    credit_hold: bool = False,
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
            "legal_name": "Draft Order Retail",
            "status": "active",
            "payment_terms": payment_terms,
            "payment_timing_policy": payment_timing_policy,
            "credit_limit": credit_limit,
            "credit_hold": credit_hold,
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
                    "line_1": "100 Draft Street",
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
            "conversions": [
                {
                    "unit_code": "CASE",
                    "base_quantity": "12.000000",
                    "effective_from": "2026-01-01",
                    "effective_to": None,
                }
            ],
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
    code: str,
    items: list[dict[str, object]],
    customer_id: str | None = None,
    inclusion_mode: str = "exclusive",
) -> dict[str, object]:
    response = await client.post(
        "/v1/sales/price-list-versions",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": f"price-list-{code}"},
        ),
        json={
            "code": code,
            "branch_id": branch_id,
            "customer_id": customer_id,
            "inclusion_mode": inclusion_mode,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "items": items,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def configure_tax_code(
    client: AsyncClient,
    settings: Settings,
) -> dict[str, object]:
    response = await client.post(
        "/v1/sales/tax-code-versions",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": "tax-code-vat12"},
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


async def sales_fixture(
    client: AsyncClient,
    settings: Settings,
    *,
    inclusion_mode: str = "exclusive",
    cola_branch_price: str = "10.000000",
    cola_customer_price: str = "9.500000",
    water_price: str = "5.000000",
    payment_timing_policy: str = "prepaid",
    credit_limit: str | None = None,
) -> dict[str, object]:
    organization = await bootstrap_sales(client, settings)
    branch_id = organization["branches"][0]["branch_id"]
    customer = await create_customer(
        client,
        settings,
        branch_id,
        payment_terms="NET_30" if payment_timing_policy == "on_account" else "DUE_ON_RECEIPT",
        payment_timing_policy=payment_timing_policy,
        credit_limit=credit_limit,
    )
    tax_code = await configure_tax_code(client, settings)
    cola = await configure_sku(
        client,
        settings,
        product_code="BEV",
        sku_code="COLA-330",
        key="sales-cola",
    )
    water = await configure_sku(
        client,
        settings,
        product_code="WATER",
        sku_code="WATER-500",
        key="sales-water",
    )
    branch_items = [
        {
            "sku_id": cola["sku_id"],
            "unit_code": "EA",
            "list_unit_price": cola_branch_price,
            "floor_unit_price": "8.000000",
            "tax_code_version_id": tax_code["tax_code_version_id"],
        },
        {
            "sku_id": cola["sku_id"],
            "unit_code": "CASE",
            "list_unit_price": "114.000000",
            "floor_unit_price": "96.000000",
            "tax_code_version_id": tax_code["tax_code_version_id"],
        },
        {
            "sku_id": water["sku_id"],
            "unit_code": "EA",
            "list_unit_price": water_price,
            "floor_unit_price": "4.000000",
            "tax_code_version_id": tax_code["tax_code_version_id"],
        },
    ]
    branch_price_list = await configure_price_list(
        client,
        settings,
        branch_id=branch_id,
        code="MNL-DEFAULT",
        items=branch_items,
        inclusion_mode=inclusion_mode,
    )
    customer_price_list = await configure_price_list(
        client,
        settings,
        branch_id=branch_id,
        customer_id=customer["customer_id"],
        code="MNL-CUSTOMER",
        items=[
            {
                **branch_items[0],
                "list_unit_price": cola_customer_price,
                "floor_unit_price": "7.500000",
            },
            {
                **branch_items[1],
                "list_unit_price": "110.000000",
                "floor_unit_price": "100.000000",
            },
            branch_items[2],
        ],
        inclusion_mode=inclusion_mode,
    )
    return {
        "branch_id": branch_id,
        "warehouse_id": organization["branches"][0]["warehouses"][0]["warehouse_id"],
        "branch_price_list": branch_price_list,
        "cola": cola,
        "customer": customer,
        "customer_price_list": customer_price_list,
        "water": water,
    }


def draft_command(
    fixture: dict[str, object],
    *,
    sales_order_id: UUID,
) -> dict[str, object]:
    customer = fixture["customer"]
    return {
        "sales_order_id": str(sales_order_id),
        "branch_id": fixture["branch_id"],
        "customer_id": customer["customer_id"],
        "expected_customer_version": customer["version"],
        "expected_price_list_version_id": fixture["customer_price_list"]["price_list_version_id"],
        "expected_pricing_date": date.today().isoformat(),
        "delivery_address_version_id": customer["addresses"][0]["address_version_id"],
        "payment_timing_policy": None,
        "payment_timing_override_reason": None,
        "order_discount_amount": "0.030000",
        "lines": [
            {
                "line_id": str(uuid4()),
                "sku_id": fixture["cola"]["sku_id"],
                "expected_price_list_line_id": fixture["customer_price_list"]["items"][0][
                    "price_list_line_id"
                ],
                "expected_unit_conversion_id": None,
                "expected_unit_conversion_version": None,
                "quantity": "3.000000",
                "unit_code": "EA",
                "manual_override_unit_price": None,
                "price_override_reason": None,
            },
            {
                "line_id": str(uuid4()),
                "sku_id": fixture["water"]["sku_id"],
                "expected_price_list_line_id": fixture["customer_price_list"]["items"][2][
                    "price_list_line_id"
                ],
                "expected_unit_conversion_id": None,
                "expected_unit_conversion_version": None,
                "quantity": "1.000000",
                "unit_code": "EA",
                "manual_override_unit_price": None,
                "price_override_reason": None,
            },
        ],
    }


@pytest.mark.asyncio
async def test_draft_uses_customer_pricing_and_deterministic_line_calculations(
    sales_client: AsyncClient,
    sales_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await sales_fixture(sales_client, sales_settings)
    reference = await sales_client.get(
        (
            "/v1/sales/order-entry-reference"
            f"?branch_id={fixture['branch_id']}"
            f"&customer_id={fixture['customer']['customer_id']}"
        ),
        headers=auth(sales_settings, "sales-mnl"),
    )
    assert reference.status_code == 200, reference.text
    assert reference.json()["price_list_code"] == "MNL-CUSTOMER"
    assert reference.json()["price_list_version"] == 1
    assert [item["sku_code"] for item in reference.json()["items"]] == [
        "COLA-330",
        "COLA-330",
        "WATER-500",
    ]
    order_id = uuid4()
    command = draft_command(fixture, sales_order_id=order_id)
    created = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "create-priced-draft"},
        ),
        json=command,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["sales_order_id"] == str(order_id)
    assert body["status"] == "draft"
    assert body["version"] == 1
    assert body["currency"] == "PHP"
    assert body["price_inclusion_mode"] == "exclusive"
    assert body["payment_timing_policy"] == "prepaid"
    assert body["delivery_address_snapshot"]["line_1"] == "100 Draft Street"
    assert body["subtotal"] == "33.50"
    assert body["discount_total"] == "0.03"
    assert body["taxable_total"] == "33.47"
    assert body["tax_total"] == "4.02"
    assert body["grand_total"] == "37.49"
    assert body["lines"][0]["price_list_code"] == "MNL-CUSTOMER"
    assert body["lines"][0]["price_list_version"] == 1
    assert body["lines"][0]["price_source"] == "customer"
    assert body["lines"][0]["list_unit_price"] == "9.500000"
    assert body["lines"][0]["allocated_discount"] == "0.03"
    assert body["lines"][0]["tax_amount"] == "3.42"
    assert body["lines"][0]["line_total"] == "31.89"
    assert body["lines"][1]["price_list_code"] == "MNL-CUSTOMER"
    assert body["lines"][1]["allocated_discount"] == "0.00"
    assert body["lines"][1]["tax_amount"] == "0.60"
    assert body["lines"][1]["line_total"] == "5.60"
    assert body["lines"][0]["conversion_snapshot"]["base_quantity"] == "3.000000"
    assert body["lines"][0]["tax_snapshot"]["tax_code"] == "VAT12"
    assert body["lines"][0]["calculation_snapshot"]["line_total"] == "31.89"

    replay = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "create-priced-draft"},
        ),
        json=command,
    )
    assert replay.status_code == 200
    assert replay.json() == body

    ceb = await sales_client.get(
        "/v1/sales/orders",
        headers=auth(sales_settings, "sales-ceb"),
    )
    assert ceb.json() == {"items": [], "total": 0}

    address_update = await sales_client.put(
        f"/v1/customers/{fixture['customer']['customer_id']}/addresses/DELIVERY",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "update-order-customer-address", "If-Match": "1"},
        ),
        json={
            "kind": "delivery",
            "line_1": "200 New Draft Street",
            "line_2": None,
            "city": "Manila",
            "region": "NCR",
            "postal_code": "1000",
            "country_code": "PH",
        },
    )
    assert address_update.status_code == 200, address_update.text
    delayed_replay = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "create-priced-draft"},
        ),
        json=command,
    )
    assert delayed_replay.status_code == 200
    assert delayed_replay.json() == body
    stale_reference_command = {
        **command,
        "sales_order_id": str(uuid4()),
    }
    stale_reference = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "stale-reference-draft"},
        ),
        json=stale_reference_command,
    )
    assert stale_reference.status_code == 409
    assert stale_reference.json()["error"]["code"] == "reference_data_conflict"
    fetched = await sales_client.get(
        f"/v1/sales/orders/{order_id}",
        headers=auth(sales_settings, "sales-mnl"),
    )
    assert fetched.status_code == 200
    assert fetched.json()["delivery_address_snapshot"]["line_1"] == "100 Draft Street"

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        revision_count = await connection.scalar(
            text(
                "SELECT count(*) FROM sales_order_revisions WHERE sales_order_id = :sales_order_id"
            ),
            {"sales_order_id": order_id},
        )
        inventory_rows = await connection.scalar(
            text("SELECT count(*) FROM inventory_availability")
        )
        credit_rows = await connection.scalar(
            text("SELECT count(*) FROM customer_credit_approvals")
        )
    assert revision_count == 1
    assert inventory_rows == 0
    assert credit_rows == 0
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE sales_order_revisions SET subtotal = 0 "
                    "WHERE sales_order_id = :sales_order_id"
                ),
                {"sales_order_id": order_id},
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_branch_fallback_converted_unit_and_authorized_price_override(
    sales_client: AsyncClient,
    sales_settings: Settings,
) -> None:
    fixture = await sales_fixture(sales_client, sales_settings)
    branch_customer = await create_customer(
        sales_client,
        sales_settings,
        fixture["branch_id"],
        account_number="MNL-SALES-002",
        idempotency_key="sales-branch-price-customer",
    )
    reference = await sales_client.get(
        (
            "/v1/sales/order-entry-reference"
            f"?branch_id={fixture['branch_id']}"
            f"&customer_id={branch_customer['customer_id']}"
        ),
        headers=auth(sales_settings, "sales-mnl"),
    )
    assert reference.status_code == 200, reference.text
    assert reference.json()["price_list_code"] == "MNL-DEFAULT"

    order_id = uuid4()
    command = draft_command(fixture, sales_order_id=order_id)
    command["order_discount_amount"] = "0.000000"
    command["lines"] = [
        {
            "line_id": str(uuid4()),
            "sku_id": fixture["cola"]["sku_id"],
            "expected_price_list_line_id": fixture["customer_price_list"]["items"][1][
                "price_list_line_id"
            ],
            "expected_unit_conversion_id": fixture["cola"]["conversions"][0]["unit_conversion_id"],
            "expected_unit_conversion_version": fixture["cola"]["conversions"][0]["version"],
            "quantity": "2.000000",
            "unit_code": "CASE",
            "manual_override_unit_price": "99.000000",
            "price_override_reason": "Approved case promotion",
        }
    ]
    unauthorized = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl-basic",
            **{"Idempotency-Key": "unauthorized-case-override"},
        ),
        json=command,
    )
    assert unauthorized.status_code == 403
    assert unauthorized.json()["error"]["code"] == "price_override_required"

    created = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "authorized-case-override"},
        ),
        json=command,
    )
    assert created.status_code == 201, created.text
    line = created.json()["lines"][0]
    assert line["entered_unit"] == "CASE"
    assert line["quantity_base"] == "24.000000"
    assert line["conversion_snapshot"]["base_quantity_per_unit"] == "12.000000"
    assert line["list_unit_price"] == "110.000000"
    assert line["effective_unit_price"] == "99.000000"
    assert line["price_override_reason"] == "Approved case promotion"
    assert line["below_floor"] is True
    assert created.json()["subtotal"] == "198.00"
    assert created.json()["tax_total"] == "23.76"
    assert created.json()["grand_total"] == "221.76"

    zero_command = {
        **command,
        "sales_order_id": str(uuid4()),
        "lines": [
            {
                **command["lines"][0],
                "line_id": str(uuid4()),
                "manual_override_unit_price": "0.000000",
                "price_override_reason": "Authorized free sample",
                "quantity": "1.000000",
            }
        ],
    }
    zero = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "authorized-zero-price-override"},
        ),
        json=zero_command,
    )
    assert zero.status_code == 201, zero.text
    assert zero.json()["lines"][0]["effective_unit_price"] == "0.000000"
    assert zero.json()["grand_total"] == "0.00"

    overflow_command = {
        **command,
        "sales_order_id": str(uuid4()),
        "lines": [
            {
                **command["lines"][0],
                "line_id": str(uuid4()),
                "manual_override_unit_price": None,
                "price_override_reason": None,
                "quantity": "999999999999.999999",
            }
        ],
    }
    overflow = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "converted-quantity-overflow"},
        ),
        json=overflow_command,
    )
    assert overflow.status_code == 422
    assert overflow.json()["error"]["code"] == "calculated_quantity_overflow"


@pytest.mark.asyncio
async def test_draft_edit_is_optimistic_and_payment_override_is_audited(
    sales_client: AsyncClient,
    sales_settings: Settings,
) -> None:
    fixture = await sales_fixture(sales_client, sales_settings)
    order_id = uuid4()
    command = draft_command(fixture, sales_order_id=order_id)
    created = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "create-editable-draft"},
        ),
        json=command,
    )
    assert created.status_code == 201, created.text
    update_command = {key: value for key, value in command.items() if key != "sales_order_id"}
    update_command["payment_timing_policy"] = "cash_on_delivery"
    update_command["payment_timing_override_reason"] = "Customer requested COD"

    unauthorized = await sales_client.put(
        f"/v1/sales/orders/{order_id}",
        headers=auth(
            sales_settings,
            "sales-mnl-basic",
            **{"Idempotency-Key": "unauthorized-payment-override", "If-Match": "1"},
        ),
        json=update_command,
    )
    assert unauthorized.status_code == 403
    assert unauthorized.json()["error"]["code"] == "payment_timing_override_required"

    async def edit(key: str) -> tuple[int, dict[str, object]]:
        response = await sales_client.put(
            f"/v1/sales/orders/{order_id}",
            headers=auth(
                sales_settings,
                "sales-mnl",
                **{"Idempotency-Key": key, "If-Match": "1"},
            ),
            json=update_command,
        )
        return response.status_code, response.json()

    results = await asyncio.gather(edit("edit-draft-1"), edit("edit-draft-2"))
    assert sorted(result[0] for result in results) == [200, 409]
    winner = next(result[1] for result in results if result[0] == 200)
    assert winner["version"] == 2
    assert winner["payment_timing_policy"] == "cash_on_delivery"
    assert winner["payment_timing_override_reason"] == "Customer requested COD"
    assert winner["payment_timing_overridden_by"] == "sales-mnl"


@pytest.mark.asyncio
async def test_maker_submits_current_draft_for_authoritative_commercial_approval(
    sales_client: AsyncClient,
    sales_settings: Settings,
) -> None:
    fixture = await sales_fixture(sales_client, sales_settings)
    order_id = uuid4()
    created = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "create-submittable-draft"},
        ),
        json=draft_command(fixture, sales_order_id=order_id),
    )
    assert created.status_code == 201, created.text

    submitted = await sales_client.post(
        f"/v1/sales/orders/{order_id}/submission",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "submit-for-commercial-approval", "If-Match": "1"},
        ),
    )

    assert submitted.status_code == 201, submitted.text
    assert submitted.json()["status"] == "awaiting_approval"
    assert submitted.json()["version"] == 1

    listed = await sales_client.get(
        "/v1/sales/orders",
        headers=auth(sales_settings, "commercial-mnl"),
    )
    assert listed.status_code == 200, listed.text
    assert (
        next(item for item in listed.json()["items"] if item["sales_order_id"] == str(order_id))[
            "status"
        ]
        == "awaiting_approval"
    )


@pytest.mark.asyncio
async def test_commercial_approval_partially_reserves_replays_and_material_edit_releases(
    sales_client: AsyncClient,
    sales_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await sales_fixture(sales_client, sales_settings)
    order_id = uuid4()
    command = draft_command(fixture, sales_order_id=order_id)
    command["lines"][0]["manual_override_unit_price"] = "7.000000"
    command["lines"][0]["price_override_reason"] = "Competitive bid"
    created = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "approval-draft"},
        ),
        json=command,
    )
    assert created.status_code == 201, created.text

    location_id = uuid4()
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO warehouse_stock_locations
                    (location_id, warehouse_id, code, name, custody, created_by)
                VALUES
                    (:location_id, :warehouse_id, 'APPROVAL-AVAILABLE',
                     'Approval Available', 'available', 'sales-mnl')
                """
            ),
            {
                "location_id": location_id,
                "warehouse_id": fixture["warehouse_id"],
            },
        )
        for sku_id, quantity in (
            (fixture["cola"]["sku_id"], "2.000000"),
            (fixture["water"]["sku_id"], "1.000000"),
        ):
            await connection.execute(
                text(
                    """
                    INSERT INTO inventory_availability
                        (sku_id, warehouse_id, location_id, identity_key, on_hand, reserved)
                    VALUES (:sku_id, :warehouse_id, :location_id, '', :quantity, 0)
                    """
                ),
                {
                    "sku_id": sku_id,
                    "warehouse_id": fixture["warehouse_id"],
                    "location_id": location_id,
                    "quantity": quantity,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO inventory_valuation
                        (sku_id, warehouse_id, quantity_on_hand,
                         inventory_value, moving_average_unit_cost)
                    VALUES (:sku_id, :warehouse_id, :quantity, 0, 0)
                    """
                ),
                {
                    "sku_id": sku_id,
                    "warehouse_id": fixture["warehouse_id"],
                    "quantity": quantity,
                },
            )

    approval_command = {
        "warehouse_id": fixture["warehouse_id"],
        "exception_reason": "Commercial discount reviewed",
        "credit_override_reason": None,
    }
    review = await sales_client.get(
        f"/v1/sales/orders/{order_id}/commercial-review",
        headers=auth(sales_settings, "commercial-mnl"),
        params={"warehouse_id": fixture["warehouse_id"]},
    )
    assert review.status_code == 200, review.text
    evidence = review.json()
    UUID(evidence["sales_order_revision_id"])
    assert evidence["customer_name"] == fixture["customer"]["legal_name"]
    assert evidence["customer_status"] == "active"
    assert evidence["customer_snapshot_current"] is True
    assert evidence["payment_terms"] == "DUE_ON_RECEIPT"
    assert [item["exception_type"] for item in evidence["required_exceptions"]] == [
        "discount",
        "below_floor",
    ]
    assert evidence["lines"][0]["warehouse_on_hand_base"] == "2.000000"
    assert evidence["lines"][0]["reservable_quantity_base"] == "2.000000"
    assert evidence["lines"][0]["backorder_quantity_base"] == "1.000000"
    assert evidence["lines"][0]["conversion_snapshot"]["entered_unit"] == "EA"
    assert evidence["lines"][0]["tax_snapshot"]["tax_code"] == "VAT12"
    blank_reason = await sales_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=auth(
            sales_settings,
            "commercial-mnl",
            **{"Idempotency-Key": "blank-approval-reason", "If-Match": "1"},
        ),
        json={**approval_command, "exception_reason": "   "},
    )
    assert blank_reason.status_code == 422
    maker_attempt = await sales_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "maker-approve-order", "If-Match": "1"},
        ),
        json=approval_command,
    )
    assert maker_attempt.status_code == 409
    assert maker_attempt.json()["error"]["code"] == "maker_checker_violation"
    approved = await sales_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=auth(
            sales_settings,
            "commercial-mnl",
            **{"Idempotency-Key": "approve-order", "If-Match": "1"},
        ),
        json=approval_command,
    )
    assert approved.status_code == 201, approved.text
    approval = approved.json()
    assert approval["status"] == "approved"
    assert approval["sales_order_revision_id"] == evidence["sales_order_revision_id"]
    assert approval["required_exceptions"] == ["discount", "below_floor"]
    assert approval["reserved_quantity_base"] == "3.000000"
    assert approval["backorder_quantity_base"] == "1.000000"
    assert approval["reservations"][0]["ordered_quantity_base"] == "3.000000"
    assert approval["reservations"][0]["reserved_quantity_base"] == "2.000000"
    inventory_after_approval = await sales_client.get(
        "/v1/inventory/availability?query=COLA",
        headers=auth(sales_settings, "sales-mnl"),
    )
    assert inventory_after_approval.status_code == 200
    assert inventory_after_approval.json()["items"][0]["commercial_reserved"] == "2.000000"
    assert inventory_after_approval.json()["items"][0]["warehouse_available"] == "0.000000"

    replay = await sales_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=auth(
            sales_settings,
            "commercial-mnl",
            **{"Idempotency-Key": "approve-order", "If-Match": "1"},
        ),
        json=approval_command,
    )
    assert replay.status_code == 200
    assert replay.json() == approval
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "DELETE FROM user_warehouse_scopes "
                "WHERE user_subject = 'commercial-mnl' AND warehouse_id = :warehouse_id"
            ),
            {"warehouse_id": fixture["warehouse_id"]},
        )
    revoked_replay = await sales_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=auth(
            sales_settings,
            "commercial-mnl",
            **{"Idempotency-Key": "approve-order", "If-Match": "1"},
        ),
        json=approval_command,
    )
    assert revoked_replay.status_code == 403
    assert revoked_replay.json()["error"]["code"] == "operational_scope_required"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO user_warehouse_scopes (user_subject, warehouse_id) "
                "VALUES ('commercial-mnl', :warehouse_id)"
            ),
            {"warehouse_id": fixture["warehouse_id"]},
        )

    annotated = await sales_client.patch(
        f"/v1/sales/orders/{order_id}/non-material",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "annotate-approved", "If-Match": "1"},
        ),
        json={
            "notes": "Call before arrival",
            "delivery_instructions": "Use receiving gate",
        },
    )
    assert annotated.status_code == 200, annotated.text
    assert annotated.json()["commercial_approval_id"] == approval["commercial_approval_id"]
    assert annotated.json()["commercial_version"] == 1
    assert annotated.json()["version"] == 2
    stale_annotation = await sales_client.patch(
        f"/v1/sales/orders/{order_id}/non-material",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "stale-annotation", "If-Match": "1"},
        ),
        json={"notes": "Stale note", "delivery_instructions": None},
    )
    assert stale_annotation.status_code == 409

    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM inventory_reserved_by_sku_warehouse"))
        await connection.execute(text("DELETE FROM sales_order_line_commitments"))
    rebuilt = await sales_client.post(
        "/v1/sales/projections/rebuild",
        headers=auth(sales_settings, "commercial-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json() == {
        "credit_customers": 0,
        "line_commitments": 2,
        "reservation_items": 2,
    }

    update_command = {key: value for key, value in command.items() if key != "sales_order_id"}
    update_command["lines"][0]["quantity"] = "1.000000"
    revised = await sales_client.put(
        f"/v1/sales/orders/{order_id}",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "material-revision", "If-Match": "1"},
        ),
        json=update_command,
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["status"] == "draft"
    assert revised.json()["version"] == 2

    async with engine.begin() as connection:
        invalidations = await connection.scalar(
            text(
                "SELECT count(*) FROM commercial_approval_invalidations "
                "WHERE commercial_approval_id = :approval_id"
            ),
            {"approval_id": UUID(approval["commercial_approval_id"])},
        )
        active_commitments = await connection.scalar(
            text(
                "SELECT count(*) FROM sales_order_line_commitments "
                "WHERE sales_order_id = :sales_order_id"
            ),
            {"sales_order_id": order_id},
        )
        reserved_projection = await connection.scalar(
            text(
                "SELECT coalesce(sum(reserved_quantity_base), 0) "
                "FROM inventory_reserved_by_sku_warehouse "
                "WHERE warehouse_id = :warehouse_id"
            ),
            {"warehouse_id": fixture["warehouse_id"]},
        )
        release_events = await connection.scalar(
            text(
                "SELECT count(*) FROM inventory_reservation_events "
                "WHERE commercial_approval_id = :approval_id AND event_type = 'released'"
            ),
            {"approval_id": UUID(approval["commercial_approval_id"])},
        )
    assert invalidations == 1
    assert active_commitments == 0
    assert reserved_projection == 0
    assert release_events == 2
    inventory_after_release = await sales_client.get(
        "/v1/inventory/availability?query=COLA",
        headers=auth(sales_settings, "sales-mnl"),
    )
    assert inventory_after_release.json()["items"][0]["commercial_reserved"] == "0.000000"
    assert inventory_after_release.json()["items"][0]["warehouse_available"] == "2.000000"

    reapproved = await sales_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=auth(
            sales_settings,
            "commercial-mnl",
            **{"Idempotency-Key": "reapprove-order", "If-Match": "2"},
        ),
        json=approval_command,
    )
    assert reapproved.status_code == 201, reapproved.text
    assert reapproved.json()["sales_order_revision_id"] != approval["sales_order_revision_id"]
    assert reapproved.json()["reserved_quantity_base"] == "2.000000"
    assert reapproved.json()["backorder_quantity_base"] == "0.000000"
    async with engine.begin() as connection:
        approval_count = await connection.scalar(
            text(
                "SELECT count(*) FROM commercial_approvals WHERE sales_order_id = :sales_order_id"
            ),
            {"sales_order_id": order_id},
        )
        active_commitments = await connection.scalar(
            text(
                "SELECT count(*) FROM sales_order_line_commitments "
                "WHERE sales_order_id = :sales_order_id"
            ),
            {"sales_order_id": order_id},
        )
    await engine.dispose()
    assert approval_count == 2
    assert active_commitments == 2


@pytest.mark.asyncio
async def test_inclusive_tax_and_equal_remainder_discount_tie_use_line_order(
    sales_client: AsyncClient,
    sales_settings: Settings,
) -> None:
    fixture = await sales_fixture(
        sales_client,
        sales_settings,
        inclusion_mode="inclusive",
        cola_branch_price="1.000000",
        cola_customer_price="1.000000",
        water_price="1.000000",
    )
    command = draft_command(fixture, sales_order_id=uuid4())
    command["order_discount_amount"] = "0.010000"
    command["lines"][0]["quantity"] = "1.000000"
    response = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "inclusive-tie-draft"},
        ),
        json=command,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["price_inclusion_mode"] == "inclusive"
    assert body["subtotal"] == "2.00"
    assert body["discount_total"] == "0.01"
    assert body["taxable_total"] == "1.77"
    assert body["tax_total"] == "0.22"
    assert body["grand_total"] == "1.99"
    assert body["lines"][0]["allocated_discount"] == "0.01"
    assert body["lines"][0]["taxable_amount"] == "0.88"
    assert body["lines"][0]["tax_amount"] == "0.11"
    assert body["lines"][0]["line_total"] == "0.99"
    assert body["lines"][1]["allocated_discount"] == "0.00"
    assert body["lines"][1]["taxable_amount"] == "0.89"
    assert body["lines"][1]["tax_amount"] == "0.11"
    assert body["lines"][1]["line_total"] == "1.00"


@pytest.mark.asyncio
async def test_concurrent_commercial_approvals_never_overreserve_final_unit(
    sales_client: AsyncClient,
    sales_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await sales_fixture(sales_client, sales_settings)
    order_ids = [uuid4(), uuid4()]
    for position, order_id in enumerate(order_ids, start=1):
        command = draft_command(fixture, sales_order_id=order_id)
        command["order_discount_amount"] = "0.000000"
        command["lines"] = [{**command["lines"][0], "quantity": "1.000000"}]
        created = await sales_client.post(
            "/v1/sales/orders",
            headers=auth(
                sales_settings,
                "sales-mnl",
                **{"Idempotency-Key": f"final-unit-draft-{position}"},
            ),
            json=command,
        )
        assert created.status_code == 201, created.text

    location_id = uuid4()
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO warehouse_stock_locations
                    (location_id, warehouse_id, code, name, custody, created_by)
                VALUES
                    (:location_id, :warehouse_id, 'FINAL-UNIT',
                     'Final Unit', 'available', 'sales-mnl')
                """
            ),
            {"location_id": location_id, "warehouse_id": fixture["warehouse_id"]},
        )
        await connection.execute(
            text(
                """
                INSERT INTO inventory_availability
                    (sku_id, warehouse_id, location_id, identity_key, on_hand, reserved)
                VALUES (:sku_id, :warehouse_id, :location_id, '', 1.000000, 0)
                """
            ),
            {
                "sku_id": fixture["cola"]["sku_id"],
                "warehouse_id": fixture["warehouse_id"],
                "location_id": location_id,
            },
        )

    async def approve(order_id: UUID, position: int) -> tuple[int, dict[str, object]]:
        response = await sales_client.post(
            f"/v1/sales/orders/{order_id}/commercial-approval",
            headers=auth(
                sales_settings,
                "commercial-mnl",
                **{
                    "Idempotency-Key": f"final-unit-approval-{position}",
                    "If-Match": "1",
                },
            ),
            json={
                "warehouse_id": fixture["warehouse_id"],
                "exception_reason": None,
                "credit_override_reason": None,
            },
        )
        return response.status_code, response.json()

    results = await asyncio.gather(
        *(approve(order_id, position) for position, order_id in enumerate(order_ids, start=1))
    )
    assert [result[0] for result in results] == [201, 201]
    assert sorted(result[1]["reserved_quantity_base"] for result in results) == [
        "0.000000",
        "1.000000",
    ]
    assert sorted(result[1]["backorder_quantity_base"] for result in results) == [
        "0.000000",
        "1.000000",
    ]
    async with engine.begin() as connection:
        total_reserved = await connection.scalar(
            text(
                "SELECT reserved_quantity_base "
                "FROM inventory_reserved_by_sku_warehouse "
                "WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id"
            ),
            {
                "sku_id": fixture["cola"]["sku_id"],
                "warehouse_id": fixture["warehouse_id"],
            },
        )
    await engine.dispose()
    assert total_reserved == 1


@pytest.mark.asyncio
async def test_discount_within_maker_threshold_needs_no_exception_evidence(
    sales_client: AsyncClient,
    sales_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await sales_fixture(sales_client, sales_settings)
    order_id = uuid4()
    command = draft_command(fixture, sales_order_id=order_id)
    command["order_discount_amount"] = "0.010000"
    created = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "within-threshold-order"},
        ),
        json=command,
    )
    assert created.status_code == 201, created.text
    approved = await sales_client.post(
        f"/v1/sales/orders/{order_id}/commercial-approval",
        headers=auth(
            sales_settings,
            "commercial-mnl",
            **{"Idempotency-Key": "within-threshold-approval", "If-Match": "1"},
        ),
        json={
            "warehouse_id": fixture["warehouse_id"],
            "exception_reason": None,
            "credit_override_reason": None,
        },
    )
    assert approved.status_code == 201, approved.text
    assert approved.json()["required_exceptions"] == []

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE approval_authorities SET maximum_percentage = 0.125000 "
                "WHERE user_subject = 'sales-mnl' "
                "AND capability_code = 'sales:discount-enter'"
            )
        )
    floor_order_id = uuid4()
    floor_command = draft_command(fixture, sales_order_id=floor_order_id)
    floor_command["lines"] = [{**floor_command["lines"][0], "quantity": "1.000000"}]
    floor_command["order_discount_amount"] = "2.010000"
    floor_draft = await sales_client.post(
        "/v1/sales/orders",
        headers=auth(
            sales_settings,
            "sales-mnl",
            **{"Idempotency-Key": "discount-induced-floor-order"},
        ),
        json=floor_command,
    )
    assert floor_draft.status_code == 201, floor_draft.text
    floor_approval = await sales_client.post(
        f"/v1/sales/orders/{floor_order_id}/commercial-approval",
        headers=auth(
            sales_settings,
            "commercial-mnl",
            **{
                "Idempotency-Key": "discount-induced-floor-approval",
                "If-Match": "1",
            },
        ),
        json={
            "warehouse_id": fixture["warehouse_id"],
            "exception_reason": "Discount and net floor reviewed",
            "credit_override_reason": None,
        },
    )
    assert floor_approval.status_code == 201, floor_approval.text
    assert floor_approval.json()["required_exceptions"] == [
        "discount",
        "below_floor",
    ]
    async with engine.begin() as connection:
        evidence_amount = await connection.scalar(
            text(
                "SELECT exception_amount FROM commercial_exception_approvals "
                "WHERE commercial_approval_id = :approval_id "
                "AND exception_type = 'discount'"
            ),
            {"approval_id": floor_approval.json()["commercial_approval_id"]},
        )
    await engine.dispose()
    assert evidence_amount == Decimal("2.00")


@pytest.mark.asyncio
async def test_concurrent_on_account_approval_serializes_credit_exposure(
    sales_client: AsyncClient,
    sales_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await sales_fixture(
        sales_client,
        sales_settings,
        payment_timing_policy="on_account",
        credit_limit="50.00",
    )
    engine = create_async_engine(postgres_url)
    open_balance_source_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO credit_exposure_entries
                    (entry_id, customer_id, commercial_approval_id, sales_order_id,
                     component, amount_delta, source_type, source_id, actor_subject,
                     correlation_id, idempotency_key)
                VALUES
                    (:entry_id, :customer_id, NULL, NULL, 'posted_open_balance',
                     10.00, 'posted_invoice', :source_id, 'commercial-mnl',
                     'posted-open-balance-test', 'posted-open-balance-test')
                """
            ),
            {
                "entry_id": uuid4(),
                "customer_id": fixture["customer"]["customer_id"],
                "source_id": open_balance_source_id,
            },
        )
    rebuilt = await sales_client.post(
        "/v1/sales/projections/rebuild",
        headers=auth(sales_settings, "commercial-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    order_ids = [uuid4(), uuid4()]
    for position, order_id in enumerate(order_ids, start=1):
        command = draft_command(fixture, sales_order_id=order_id)
        command["order_discount_amount"] = "0.000000"
        created = await sales_client.post(
            "/v1/sales/orders",
            headers=auth(
                sales_settings,
                "sales-mnl",
                **{"Idempotency-Key": f"credit-order-{position}"},
            ),
            json=command,
        )
        assert created.status_code == 201, created.text

    async def approve(order_id: UUID, position: int) -> tuple[int, dict[str, object]]:
        response = await sales_client.post(
            f"/v1/sales/orders/{order_id}/commercial-approval",
            headers=auth(
                sales_settings,
                "commercial-mnl",
                **{
                    "Idempotency-Key": f"credit-approval-{position}",
                    "If-Match": "1",
                },
            ),
            json={
                "warehouse_id": fixture["warehouse_id"],
                "exception_reason": None,
                "credit_override_reason": None,
            },
        )
        return response.status_code, response.json()

    results = await asyncio.gather(
        *(approve(order_id, position) for position, order_id in enumerate(order_ids, start=1))
    )
    assert sorted(result[0] for result in results) == [201, 422]
    failed = next(result[1] for result in results if result[0] == 422)
    assert failed["error"]["code"] == "credit_override_reason_required"
    failed_position = next(position for position, result in enumerate(results) if result[0] == 422)
    overridden = await sales_client.post(
        f"/v1/sales/orders/{order_ids[failed_position]}/commercial-approval",
        headers=auth(
            sales_settings,
            "commercial-mnl",
            **{
                "Idempotency-Key": "credit-override-approval",
                "If-Match": "1",
            },
        ),
        json={
            "warehouse_id": fixture["warehouse_id"],
            "exception_reason": None,
            "credit_override_reason": "Temporary order-specific headroom",
        },
    )
    assert overridden.status_code == 201, overridden.text
    assert overridden.json()["required_exceptions"] == ["credit_override"]
    assert overridden.json()["credit"]["open_balance"] == "10.000000"
    assert overridden.json()["credit"]["approved_excess"] == "35.040000"

    async with engine.begin() as connection:
        exposure = await connection.execute(
            text(
                "SELECT open_balance, approved_uninvoiced "
                "FROM customer_credit_exposure WHERE customer_id = :customer_id"
            ),
            {"customer_id": fixture["customer"]["customer_id"]},
        )
        exposure_row = exposure.one()
        ledger_entries = await connection.scalar(
            text(
                "SELECT count(*) FROM credit_exposure_entries "
                "WHERE customer_id = :customer_id AND amount_delta > 0"
            ),
            {"customer_id": fixture["customer"]["customer_id"]},
        )
    await engine.dispose()
    assert exposure_row.open_balance == Decimal("10.00")
    assert exposure_row.approved_uninvoiced == Decimal("75.04")
    assert ledger_entries == 3


@pytest.mark.asyncio
async def test_commercial_ledgers_reject_cross_aggregate_ownership(
    sales_client: AsyncClient,
    sales_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await sales_fixture(sales_client, sales_settings)
    alternate_customer = await create_customer(
        sales_client,
        sales_settings,
        fixture["branch_id"],
        account_number="MNL-SALES-ALT",
        idempotency_key="aggregate-ownership-customer",
    )
    orders: list[dict[str, object]] = []
    approvals: list[dict[str, object]] = []
    for position in (1, 2):
        order_id = uuid4()
        command = draft_command(fixture, sales_order_id=order_id)
        command["order_discount_amount"] = "0.000000"
        created = await sales_client.post(
            "/v1/sales/orders",
            headers=auth(
                sales_settings,
                "sales-mnl",
                **{"Idempotency-Key": f"aggregate-ownership-order-{position}"},
            ),
            json=command,
        )
        assert created.status_code == 201, created.text
        orders.append(created.json())
        approved = await sales_client.post(
            f"/v1/sales/orders/{order_id}/commercial-approval",
            headers=auth(
                sales_settings,
                "commercial-mnl",
                **{
                    "Idempotency-Key": f"aggregate-ownership-approval-{position}",
                    "If-Match": "1",
                },
            ),
            json={
                "warehouse_id": fixture["warehouse_id"],
                "exception_reason": None,
                "credit_override_reason": None,
            },
        )
        assert approved.status_code == 201, approved.text
        approvals.append(approved.json())

    first_order, second_order = orders
    first_approval, second_approval = approvals
    first_line, second_line = first_order["lines"][:2]
    credit_statement = """
        INSERT INTO credit_exposure_entries
            (entry_id, customer_id, commercial_approval_id, sales_order_id,
             component, amount_delta, source_type, source_id, actor_subject,
             correlation_id, idempotency_key)
        VALUES
            (:entry_id, :customer_id, :commercial_approval_id, :sales_order_id,
             'approved_uninvoiced', 1, 'ownership-test', :source_id, 'sales-mnl',
             'ownership-test', :idempotency_key)
    """
    reservation_statement = """
        INSERT INTO inventory_reservation_events
            (reservation_event_id, commercial_approval_id, sales_order_id,
             sales_order_revision_id, line_id, sku_id, warehouse_id, event_type,
             quantity_base, reason, actor_subject, correlation_id, idempotency_key)
        VALUES
            (:reservation_event_id, :commercial_approval_id, :sales_order_id,
             :sales_order_revision_id, :line_id, :sku_id, :warehouse_id, 'reserved',
             1, 'ownership-test', 'sales-mnl', 'ownership-test', :idempotency_key)
    """
    commitment_statement = """
        INSERT INTO sales_order_line_commitments
            (sales_order_id, line_id, commercial_approval_id,
             sales_order_revision_id, sku_id, warehouse_id, ordered_quantity_base,
             reserved_quantity_base, picked_quantity_base, backorder_quantity_base,
             cancelled_quantity_base)
        VALUES
            (:sales_order_id, :line_id, :commercial_approval_id,
             :sales_order_revision_id, :sku_id, :warehouse_id, 1, 0, 0, 1, 0)
    """

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        alternate_warehouse_id = await connection.scalar(
            text(
                "SELECT warehouse_id FROM warehouses "
                "WHERE warehouse_id <> :warehouse_id ORDER BY warehouse_id LIMIT 1"
            ),
            {"warehouse_id": UUID(str(fixture["warehouse_id"]))},
        )
        assert alternate_warehouse_id is not None
        await connection.execute(text("DELETE FROM sales_order_line_commitments"))

        credit_values = {
            "entry_id": uuid4(),
            "customer_id": UUID(str(fixture["customer"]["customer_id"])),
            "commercial_approval_id": UUID(str(first_approval["commercial_approval_id"])),
            "sales_order_id": UUID(str(first_order["sales_order_id"])),
            "source_id": uuid4(),
            "idempotency_key": "aggregate-ownership-credit",
        }
        reservation_values = {
            "reservation_event_id": uuid4(),
            "commercial_approval_id": UUID(str(first_approval["commercial_approval_id"])),
            "sales_order_id": UUID(str(first_order["sales_order_id"])),
            "sales_order_revision_id": UUID(str(first_approval["sales_order_revision_id"])),
            "line_id": UUID(str(first_line["line_id"])),
            "sku_id": UUID(str(first_line["sku_id"])),
            "warehouse_id": UUID(str(first_approval["warehouse_id"])),
            "idempotency_key": "aggregate-ownership-reservation",
        }
        commitment_values = {
            key: value
            for key, value in reservation_values.items()
            if key not in {"reservation_event_id", "idempotency_key"}
        }
        cases = [
            (
                credit_statement,
                {
                    **credit_values,
                    "commercial_approval_id": UUID(str(second_approval["commercial_approval_id"])),
                },
                "fk_credit_exposure_entries_approval_ownership",
            ),
            (
                credit_statement,
                {
                    **credit_values,
                    "customer_id": UUID(str(alternate_customer["customer_id"])),
                },
                "fk_credit_exposure_entries_approval_ownership",
            ),
            (
                credit_statement,
                {
                    **credit_values,
                    "sales_order_id": UUID(str(second_order["sales_order_id"])),
                },
                "fk_credit_exposure_entries_approval_ownership",
            ),
            (
                reservation_statement,
                {
                    **reservation_values,
                    "commercial_approval_id": UUID(str(second_approval["commercial_approval_id"])),
                },
                "fk_inventory_reservation_events_approval_ownership",
            ),
            (
                reservation_statement,
                {
                    **reservation_values,
                    "sales_order_id": UUID(str(second_order["sales_order_id"])),
                },
                "fk_inventory_reservation_events_approval_ownership",
            ),
            (
                reservation_statement,
                {
                    **reservation_values,
                    "sales_order_revision_id": UUID(
                        str(second_approval["sales_order_revision_id"])
                    ),
                },
                "fk_inventory_reservation_events_approval_ownership",
            ),
            (
                reservation_statement,
                {**reservation_values, "warehouse_id": alternate_warehouse_id},
                "fk_inventory_reservation_events_approval_ownership",
            ),
            (
                reservation_statement,
                {**reservation_values, "line_id": UUID(str(second_line["line_id"]))},
                "fk_inventory_reservation_events_line_ownership",
            ),
            (
                reservation_statement,
                {**reservation_values, "sku_id": UUID(str(second_line["sku_id"]))},
                "fk_inventory_reservation_events_line_ownership",
            ),
            (
                commitment_statement,
                {
                    **commitment_values,
                    "commercial_approval_id": UUID(str(second_approval["commercial_approval_id"])),
                },
                "fk_sales_order_line_commitments_approval_ownership",
            ),
            (
                commitment_statement,
                {
                    **commitment_values,
                    "sku_id": UUID(str(second_line["sku_id"])),
                },
                "fk_sales_order_line_commitments_line_ownership",
            ),
        ]
        for statement, values, expected_constraint in cases:
            savepoint = await connection.begin_nested()
            with pytest.raises(DBAPIError) as captured:
                await connection.execute(text(statement), values)
            await savepoint.rollback()
            assert expected_constraint in str(captured.value.orig)
    await engine.dispose()
