from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
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
                        "sales:order-write",
                        "sales:order-read",
                        "sales:pricing-write",
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
            ],
            "users": [
                {
                    "subject": "sales-mnl",
                    "display_name": "Manila Sales",
                    "role_template_codes": ["SALES"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
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
) -> dict[str, object]:
    organization = await bootstrap_sales(client, settings)
    branch_id = organization["branches"][0]["branch_id"]
    customer = await create_customer(client, settings, branch_id)
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
