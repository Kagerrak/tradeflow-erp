from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def payment_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def payment_client(payment_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(payment_settings)
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


async def bootstrap_payment_clearance(
    client: AsyncClient,
    settings: Settings,
) -> dict[str, object]:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers={
            "Authorization": (
                f"Bearer {token(settings, 'bootstrapper', ['organization:bootstrap'])}"
            ),
            "Idempotency-Key": "payment-clearance-bootstrap",
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
                        "sales:projection-rebuild",
                    ],
                },
                {
                    "code": "FINANCE_RECORDER",
                    "name": "Finance Payment Recorder",
                    "capabilities": [
                        "finance:payment-read",
                        "finance:payment-record",
                        "finance:cash-reconcile",
                    ],
                },
                {
                    "code": "FINANCE_VERIFIER",
                    "name": "Finance Payment Verifier",
                    "capabilities": [
                        "finance:payment-read",
                        "finance:payment-verify",
                        "finance:check-clear",
                    ],
                },
                {
                    "code": "FINANCE_REVERSER",
                    "name": "Finance Payment Reverser",
                    "capabilities": [
                        "finance:payment-read",
                        "finance:payment-reverse",
                    ],
                },
                {
                    "code": "WAREHOUSE",
                    "name": "Warehouse Controller",
                    "capabilities": [
                        "fulfillment:pick",
                        "fulfillment:pick-read",
                        "fulfillment:pick-reverse",
                        "fulfillment:pick-release",
                        "inventory:read",
                        "inventory:post",
                        "inventory:rebuild",
                        "inventory:reservation-retry",
                    ],
                },
                {
                    "code": "WAREHOUSE_PICKER",
                    "name": "Warehouse Picker",
                    "capabilities": [
                        "fulfillment:pick",
                        "fulfillment:pick-read",
                        "inventory:read",
                    ],
                },
                {
                    "code": "WAREHOUSE_SUPERVISOR",
                    "name": "Warehouse Supervisor",
                    "capabilities": [
                        "fulfillment:dispatch",
                        "fulfillment:pick",
                        "fulfillment:pick-read",
                        "fulfillment:pick-reverse",
                        "fulfillment:pick-release",
                        "fulfillment:pick-manual",
                        "fulfillment:fefo-override",
                        "fulfillment:return-receive",
                        "fulfillment:delivery-retry",
                        "fulfillment:delivery-correction-request",
                        "fulfillment:delivery-correction-authorize",
                        "inventory:read",
                        "inventory:post",
                        "inventory:rebuild",
                        "inventory:investigation-resolve",
                        "inventory:reservation-retry",
                    ],
                },
                {
                    "code": "DELIVERY_STAFF",
                    "name": "Delivery Staff",
                    "capabilities": [
                        "finance:payment-record",
                        "fulfillment:delivery-read",
                        "fulfillment:delivery-confirm",
                    ],
                },
                {
                    "code": "OPS_ADMIN",
                    "name": "Operations Administrator",
                    "capabilities": ["organization:admin"],
                },
                {
                    "code": "DEADLINE",
                    "name": "Payment Deadline Processor",
                    "capabilities": ["inventory:payment-deadline-process"],
                },
                {
                    "code": "COD_CREDIT_APPROVER",
                    "name": "COD Credit Approver",
                    "capabilities": [
                        "sales:cod-convert-on-account",
                        "sales:credit-override",
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
                    "subject": "finance-recorder",
                    "display_name": "Manila Finance Recorder",
                    "role_template_codes": ["FINANCE_RECORDER", "FINANCE_VERIFIER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "finance-verifier",
                    "display_name": "Manila Finance Verifier",
                    "role_template_codes": ["FINANCE_VERIFIER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "finance-reverser",
                    "display_name": "Manila Finance Reverser",
                    "role_template_codes": ["FINANCE_REVERSER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "warehouse-mnl",
                    "display_name": "Manila Warehouse Controller",
                    "role_template_codes": ["WAREHOUSE"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "warehouse-supervisor-mnl",
                    "display_name": "Manila Warehouse Supervisor",
                    "role_template_codes": ["WAREHOUSE_SUPERVISOR"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                    "approval_authorities": [
                        {
                            "capability": "inventory:investigation-resolve",
                            "branch_code": "MNL",
                            "maximum_amount": "1000.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        },
                        {
                            "capability": "fulfillment:delivery-correction-authorize",
                            "branch_code": "MNL",
                            "maximum_amount": "1000.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        },
                    ],
                },
                {
                    "subject": "delivery-correction-checker-mnl",
                    "display_name": "Manila Delivery Correction Checker",
                    "role_template_codes": ["WAREHOUSE_SUPERVISOR"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                    "approval_authorities": [
                        {
                            "capability": "fulfillment:delivery-correction-authorize",
                            "branch_code": "MNL",
                            "maximum_amount": "1000.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        }
                    ],
                },
                {
                    "subject": "delivery-correction-checker-low-mnl",
                    "display_name": "Low-Authority Manila Delivery Correction Checker",
                    "role_template_codes": ["WAREHOUSE_SUPERVISOR"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                    "approval_authorities": [
                        {
                            "capability": "fulfillment:delivery-correction-authorize",
                            "branch_code": "MNL",
                            "maximum_amount": "1.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        }
                    ],
                },
                {
                    "subject": "delivery-correction-checker-ceb",
                    "display_name": "Cebu Delivery Correction Checker",
                    "role_template_codes": ["WAREHOUSE_SUPERVISOR"],
                    "branch_codes": ["CEB"],
                    "warehouse_codes": ["CEB-01"],
                    "approval_authorities": [
                        {
                            "capability": "fulfillment:delivery-correction-authorize",
                            "branch_code": "CEB",
                            "maximum_amount": "1000.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        }
                    ],
                },
                {
                    "subject": "warehouse-picker-mnl",
                    "display_name": "Manila Warehouse Picker",
                    "role_template_codes": ["WAREHOUSE_PICKER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "delivery-mnl",
                    "display_name": "Manila Delivery Staff",
                    "role_template_codes": ["DELIVERY_STAFF"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                },
                {
                    "subject": "delivery-backup-mnl",
                    "display_name": "Backup Manila Delivery Staff",
                    "role_template_codes": ["DELIVERY_STAFF"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                },
                {
                    "subject": "ops-admin",
                    "display_name": "Scoped Operations Administrator",
                    "is_operations_administrator": True,
                    "role_template_codes": ["OPS_ADMIN"],
                    "branch_codes": ["MNL", "CEB"],
                    "warehouse_codes": ["MNL-01", "CEB-01"],
                },
                {
                    "subject": "warehouse-cross-scope",
                    "display_name": "Cross-Scope Warehouse Picker",
                    "role_template_codes": ["WAREHOUSE_PICKER"],
                    "branch_codes": ["CEB"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "deadline-mnl",
                    "display_name": "Manila Payment Deadline Processor",
                    "role_template_codes": ["DEADLINE"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "cod-credit-approver-mnl",
                    "display_name": "Manila COD Credit Approver",
                    "role_template_codes": ["COD_CREDIT_APPROVER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                    "approval_authorities": [
                        {
                            "capability": "sales:credit-override",
                            "branch_code": "MNL",
                            "maximum_amount": "1000.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        }
                    ],
                },
                {
                    "subject": "finance-ceb",
                    "display_name": "Cebu Finance Recorder",
                    "role_template_codes": ["FINANCE_RECORDER"],
                    "branch_codes": ["CEB"],
                    "warehouse_codes": ["CEB-01"],
                },
                {
                    "subject": "warehouse-ceb",
                    "display_name": "Cebu Warehouse Controller",
                    "role_template_codes": ["WAREHOUSE"],
                    "branch_codes": ["CEB"],
                    "warehouse_codes": ["CEB-01"],
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
    payment_timing_policy: str = "prepaid",
) -> dict[str, object]:
    response = await client.post(
        "/v1/customers",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": "payment-clearance-customer"},
        ),
        json={
            "account_number": "MNL-PREPAID-001",
            "branch_id": branch_id,
            "legal_name": "Prepaid Retail Customer",
            "status": "active",
            "payment_terms": (
                "NET30" if payment_timing_policy == "on_account" else "DUE_ON_RECEIPT"
            ),
            "payment_timing_policy": payment_timing_policy,
            "credit_limit": ("10000.00" if payment_timing_policy == "on_account" else None),
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
                    "line_1": "100 Payment Street",
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


async def create_sku(client: AsyncClient, settings: Settings) -> dict[str, object]:
    response = await client.post(
        "/v1/catalog/skus",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": "payment-clearance-sku"},
        ),
        json={
            "product_code": "PREPAID-GOODS",
            "product_name": "Prepaid Goods",
            "sku_code": "PREPAID-EA",
            "sku_name": "Prepaid Goods Each",
            "base_stocking_unit": "EA",
            "tracking_policy": "untracked",
            "expiration_control": False,
            "conversions": [],
            "barcodes": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_tax_code(client: AsyncClient, settings: Settings) -> dict[str, object]:
    response = await client.post(
        "/v1/sales/tax-code-versions",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": "payment-clearance-tax"},
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


async def create_price_list(
    client: AsyncClient,
    settings: Settings,
    *,
    branch_id: str,
    customer_id: str,
    sku_id: str,
    tax_code_version_id: str,
) -> dict[str, object]:
    response = await client.post(
        "/v1/sales/price-list-versions",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": "payment-clearance-price-list"},
        ),
        json={
            "code": "MNL-PREPAID",
            "branch_id": branch_id,
            "customer_id": customer_id,
            "inclusion_mode": "exclusive",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "items": [
                {
                    "sku_id": sku_id,
                    "unit_code": "EA",
                    "list_unit_price": "100.000000",
                    "floor_unit_price": "80.000000",
                    "tax_code_version_id": tax_code_version_id,
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def seed_available_stock(
    postgres_url: str,
    *,
    sku_id: str,
    warehouse_id: str,
    quantity: str,
) -> None:
    engine = create_async_engine(postgres_url)
    location_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO warehouse_stock_locations
                    (location_id, warehouse_id, code, name, custody, created_by)
                VALUES
                    (:location_id, :warehouse_id, 'PAYMENT-AVAILABLE',
                     'Payment Contract Available', 'available', 'sales-mnl')
                """
            ),
            {"location_id": location_id, "warehouse_id": warehouse_id},
        )
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
                "warehouse_id": warehouse_id,
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
            {"sku_id": sku_id, "warehouse_id": warehouse_id, "quantity": quantity},
        )
    await engine.dispose()


async def approved_prepaid_order(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
    *,
    payment_timing_policy: str = "prepaid",
) -> dict[str, object]:
    organization = await bootstrap_payment_clearance(client, settings)
    branch = organization["branches"][0]
    branch_id = branch["branch_id"]
    warehouse_id = branch["warehouses"][0]["warehouse_id"]
    customer = await create_customer(
        client,
        settings,
        branch_id,
        payment_timing_policy=payment_timing_policy,
    )
    sku = await create_sku(client, settings)
    tax = await create_tax_code(client, settings)
    price_list = await create_price_list(
        client,
        settings,
        branch_id=branch_id,
        customer_id=customer["customer_id"],
        sku_id=sku["sku_id"],
        tax_code_version_id=tax["tax_code_version_id"],
    )
    sales_order_id = str(uuid4())
    line_id = str(uuid4())
    command = {
        "sales_order_id": sales_order_id,
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
                "line_id": line_id,
                "sku_id": sku["sku_id"],
                "expected_price_list_line_id": price_list["items"][0]["price_list_line_id"],
                "expected_unit_conversion_id": None,
                "expected_unit_conversion_version": None,
                "quantity": "3.000000",
                "unit_code": "EA",
                "manual_override_unit_price": None,
                "price_override_reason": None,
            }
        ],
    }
    created = await client.post(
        "/v1/sales/orders",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": "payment-clearance-order"},
        ),
        json=command,
    )
    assert created.status_code == 201, created.text
    assert created.json()["grand_total"] == "336.00"
    await seed_available_stock(
        postgres_url,
        sku_id=sku["sku_id"],
        warehouse_id=warehouse_id,
        quantity="2.000000",
    )
    approved = await client.post(
        f"/v1/sales/orders/{sales_order_id}/commercial-approval",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": "payment-clearance-approval", "If-Match": "1"},
        ),
        json={
            "warehouse_id": warehouse_id,
            "exception_reason": None,
            "credit_override_reason": None,
        },
    )
    assert approved.status_code == 201, approved.text
    assert approved.json()["reserved_quantity_base"] == "2.000000"
    assert approved.json()["backorder_quantity_base"] == "1.000000"
    fulfillment = await client.get(
        "/v1/fulfillment/orders",
        headers=auth(settings, "warehouse-mnl"),
        params={"sales_order_id": sales_order_id},
    )
    assert fulfillment.status_code == 200, fulfillment.text
    fulfillment_orders = fulfillment.json()["items"]
    assert len(fulfillment_orders) == 1
    assert fulfillment_orders[0]["reserved_quantity_base"] == "2.000000"
    assert fulfillment_orders[0]["backorder_quantity_base"] == "1.000000"
    expected_payment = "224.00" if payment_timing_policy == "prepaid" else "0.00"
    assert fulfillment_orders[0]["payment_required"] == expected_payment
    return {
        "branch_id": branch_id,
        "command": command,
        "customer_id": customer["customer_id"],
        "fulfillment_order": fulfillment_orders[0],
        "line_id": line_id,
        "price_list": price_list,
        "sales_order_id": sales_order_id,
        "sku_id": sku["sku_id"],
        "warehouse_id": warehouse_id,
    }


def receipt_command(
    fixture: dict[str, object],
    *,
    payment_method: str,
    amount: str = "224.00",
    external_reference: str | None = None,
) -> dict[str, object]:
    return {
        "payment_receipt_id": str(uuid4()),
        "branch_id": fixture["branch_id"],
        "customer_id": fixture["customer_id"],
        "sales_order_id": fixture["sales_order_id"],
        "payment_method": payment_method,
        "amount": amount,
        "currency": "PHP",
        "received_at": "2026-07-29T01:00:00Z",
        "external_reference": external_reference,
        "evidence": (
            None
            if payment_method == "cash"
            else {
                "account_or_provider": "TradeFlow Clearing",
                "value_date": "2026-07-29",
                "document_url": "s3://payment-evidence/reference.pdf",
            }
        ),
    }


async def record_receipt(
    client: AsyncClient,
    settings: Settings,
    fixture: dict[str, object],
    *,
    payment_method: str,
    key: str,
    amount: str = "224.00",
    external_reference: str | None = None,
    actor: str = "finance-recorder",
) -> Response:
    return await client.post(
        "/v1/finance/payment-receipts",
        headers=auth(settings, actor, **{"Idempotency-Key": key}),
        json=receipt_command(
            fixture,
            payment_method=payment_method,
            amount=amount,
            external_reference=external_reference,
        ),
    )


@pytest.mark.asyncio
async def test_cash_clears_immediately_replays_and_requires_reconciliation(
    payment_client: AsyncClient,
    payment_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(payment_client, payment_settings, postgres_url)
    command = receipt_command(fixture, payment_method="cash")
    created = await payment_client.post(
        "/v1/finance/payment-receipts",
        headers=auth(
            payment_settings,
            "finance-recorder",
            **{"Idempotency-Key": "record-cash"},
        ),
        json=command,
    )
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert receipt["status"] == "cleared"
    assert receipt["cleared_amount"] == "224.00"
    assert receipt["unapplied_amount"] == "224.00"
    assert receipt["cash_reconciliation_status"] == "pending"
    assert receipt["recorded_by"] == "finance-recorder"

    replay = await payment_client.post(
        "/v1/finance/payment-receipts",
        headers=auth(
            payment_settings,
            "finance-recorder",
            **{"Idempotency-Key": "record-cash"},
        ),
        json=command,
    )
    assert replay.status_code == 200
    assert replay.json() == receipt

    reconciliation_command = {
        "cash_reconciliation_id": str(uuid4()),
        "counted_amount": "224.00",
        "reconciled_at": "2026-07-29T06:00:00Z",
        "reason": "End-of-shift cash count",
    }
    reconciliation_url = (
        f"/v1/finance/payment-receipts/{receipt['payment_receipt_id']}/cash-reconciliation"
    )
    reconciliation_headers = auth(
        payment_settings,
        "finance-recorder",
        **{"Idempotency-Key": "reconcile-cash"},
    )
    reconciled = await payment_client.post(
        reconciliation_url,
        headers=reconciliation_headers,
        json=reconciliation_command,
    )
    assert reconciled.status_code == 201, reconciled.text
    assert reconciled.json()["status"] == "reconciled"
    assert reconciled.json()["variance_amount"] == "0.00"
    assert reconciled.json()["payment_receipt_id"] == receipt["payment_receipt_id"]
    reconciliation_replay = await payment_client.post(
        reconciliation_url,
        headers=reconciliation_headers,
        json=reconciliation_command,
    )
    assert reconciliation_replay.status_code == 200
    assert reconciliation_replay.json() == reconciled.json()


@pytest.mark.asyncio
async def test_transfer_requires_different_verifier_and_unique_normalized_reference(
    payment_client: AsyncClient,
    payment_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(payment_client, payment_settings, postgres_url)
    created = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="bank_transfer",
        external_reference="  bank- 123  ",
        key="record-transfer",
    )
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert receipt["status"] == "pending_verification"
    assert receipt["cleared_amount"] == "0.00"
    assert receipt["external_reference_normalized"] == "BANK-123"

    queue = await payment_client.get(
        "/v1/finance/payment-receipts",
        headers=auth(payment_settings, "finance-verifier"),
        params={
            "branch_id": fixture["branch_id"],
            "status": "pending_verification",
        },
    )
    assert queue.status_code == 200, queue.text
    assert queue.json()["total"] == 1
    assert queue.json()["items"][0]["payment_receipt_id"] == receipt["payment_receipt_id"]

    maker_verification = await payment_client.post(
        f"/v1/finance/payment-receipts/{receipt['payment_receipt_id']}/verification",
        headers=auth(
            payment_settings,
            "finance-recorder",
            **{"Idempotency-Key": "maker-transfer-verification"},
        ),
        json={
            "decision": "cleared",
            "verified_at": "2026-07-29T02:00:00Z",
            "reason": "Bank evidence reviewed",
        },
    )
    assert maker_verification.status_code == 409
    assert maker_verification.json()["error"]["code"] == "maker_checker_violation"

    verified = await payment_client.post(
        f"/v1/finance/payment-receipts/{receipt['payment_receipt_id']}/verification",
        headers=auth(
            payment_settings,
            "finance-verifier",
            **{"Idempotency-Key": "verify-transfer"},
        ),
        json={
            "decision": "cleared",
            "verified_at": "2026-07-29T02:00:00Z",
            "reason": "Bank evidence reviewed",
        },
    )
    assert verified.status_code == 201, verified.text
    assert verified.json()["status"] == "cleared"
    assert verified.json()["verified_by"] == "finance-verifier"

    duplicate = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="bank_transfer",
        external_reference="BANK-123",
        key="duplicate-transfer-reference",
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "external_payment_reference_conflict"


@pytest.mark.asyncio
async def test_approved_provider_confirmation_clears_matching_electronic_reference(
    payment_client: AsyncClient,
    payment_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(payment_client, payment_settings, postgres_url)
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE payment_methods SET provider_confirmation_enabled = true "
                "WHERE kind = 'electronic'"
            )
        )
    await engine.dispose()
    recorded = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="electronic",
        external_reference="EWALLET-9001",
        key="record-provider-payment",
    )
    assert recorded.status_code == 201, recorded.text
    confirmed = await payment_client.post(
        f"/v1/finance/payment-receipts/{recorded.json()['payment_receipt_id']}"
        "/provider-confirmation",
        headers=auth(
            payment_settings,
            "finance-verifier",
            **{"Idempotency-Key": "provider-confirms-payment"},
        ),
        json={
            "confirmed_at": "2026-07-29T02:00:00Z",
            "provider_reference": " ewallet-9001 ",
            "reason": "Signed provider settlement callback accepted",
        },
    )
    assert confirmed.status_code == 201, confirmed.text

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO role_template_capabilities(role_template_id, capability_code)
                SELECT role_template_id, 'finance:payment-record'
                FROM role_templates WHERE code = 'FINANCE_VERIFIER'
                ON CONFLICT DO NOTHING
                """
            )
        )
    await engine.dispose()
    self_recorded = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="electronic",
        external_reference="EWALLET-SELF-CHECK",
        key="record-provider-payment-self-check",
        actor="finance-verifier",
    )
    assert self_recorded.status_code == 201, self_recorded.text
    self_confirmed = await payment_client.post(
        f"/v1/finance/payment-receipts/{self_recorded.json()['payment_receipt_id']}"
        "/provider-confirmation",
        headers=auth(
            payment_settings,
            "finance-verifier",
            **{"Idempotency-Key": "provider-self-confirms-payment"},
        ),
        json={
            "confirmed_at": "2026-07-29T02:05:00Z",
            "provider_reference": "EWALLET-SELF-CHECK",
            "reason": "Must be independently confirmed",
        },
    )
    assert self_confirmed.status_code == 409, self_confirmed.text
    assert self_confirmed.json()["error"]["code"] == "maker_checker_violation"
    assert confirmed.json()["status"] == "cleared"
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        events = list(
            (
                await connection.execute(
                    text(
                        "SELECT event_type FROM payment_receipt_events "
                        "WHERE payment_receipt_id = :receipt_id ORDER BY occurred_at, event_type"
                    ),
                    {"receipt_id": recorded.json()["payment_receipt_id"]},
                )
            ).scalars()
        )
    await engine.dispose()
    assert events == ["recorded", "cleared", "provider_confirmed"]


@pytest.mark.asyncio
async def test_check_verification_stays_pending_until_bank_clearance(
    payment_client: AsyncClient,
    payment_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(payment_client, payment_settings, postgres_url)
    created = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="check",
        external_reference="CHECK-9001",
        key="record-check",
    )
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert receipt["status"] == "pending_verification"

    verified = await payment_client.post(
        f"/v1/finance/payment-receipts/{receipt['payment_receipt_id']}/verification",
        headers=auth(
            payment_settings,
            "finance-verifier",
            **{"Idempotency-Key": "verify-check-evidence"},
        ),
        json={
            "decision": "evidence_verified",
            "verified_at": "2026-07-29T02:00:00Z",
            "reason": "Check image and account verified",
        },
    )
    assert verified.status_code == 201, verified.text
    assert verified.json()["status"] == "awaiting_bank_clearance"
    assert verified.json()["cleared_amount"] == "0.00"

    clearance_url = f"/v1/finance/payment-receipts/{receipt['payment_receipt_id']}/bank-clearance"
    clearance_headers = auth(
        payment_settings,
        "finance-verifier",
        **{"Idempotency-Key": "clear-check"},
    )
    clearance_command = {
        "cleared_at": "2026-07-30T01:00:00Z",
        "bank_reference": "MNL-CLEARING-551",
        "reason": "Cleared in bank statement",
    }
    cleared = await payment_client.post(
        clearance_url,
        headers=clearance_headers,
        json=clearance_command,
    )
    assert cleared.status_code == 201, cleared.text
    assert cleared.json()["status"] == "cleared"
    assert cleared.json()["cleared_amount"] == "224.00"
    clearance_replay = await payment_client.post(
        clearance_url,
        headers=clearance_headers,
        json=clearance_command,
    )
    assert clearance_replay.status_code == 200
    assert clearance_replay.json() == cleared.json()


@pytest.mark.asyncio
async def test_pick_release_prices_only_reserved_quantity_and_requires_exact_payment(
    payment_client: AsyncClient,
    payment_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(payment_client, payment_settings, postgres_url)
    fulfillment_order = fixture["fulfillment_order"]
    assert fulfillment_order["order_value"] == "336.00"
    assert fulfillment_order["payment_required"] == "224.00"
    assert fulfillment_order["reserved_quantity_base"] == "2.000000"
    assert fulfillment_order["backorder_quantity_base"] == "1.000000"

    short = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="cash",
        amount="223.99",
        key="record-one-cent-short",
    )
    assert short.status_code == 201, short.text
    blocked = await payment_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order['fulfillment_order_id']}/pick-release",
        headers=auth(
            payment_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "short-pick-release"},
        ),
        json={"reason": "Release paid reserved quantity"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "cleared_payment_insufficient"
    assert blocked.json()["error"]["details"] == {
        "cleared_payment": "223.99",
        "payment_required": "224.00",
        "shortfall": "0.01",
    }

    final_cent = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="cash",
        amount="0.01",
        key="record-final-cent",
    )
    assert final_cent.status_code == 201, final_cent.text
    released = await payment_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order['fulfillment_order_id']}/pick-release",
        headers=auth(
            payment_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "exact-pick-release"},
        ),
        json={"reason": "Release paid reserved quantity"},
    )
    assert released.status_code == 201, released.text
    assert released.json()["status"] == "released"
    assert released.json()["quantity_base"] == "2.000000"
    assert released.json()["payment_required"] == "224.00"
    assert released.json()["cleared_payment"] == "224.00"


@pytest.mark.asyncio
@pytest.mark.parametrize("payment_timing_policy", ["cash_on_delivery", "on_account"])
async def test_approved_non_prepaid_commitments_release_without_prepayment(
    payment_client: AsyncClient,
    payment_settings: Settings,
    postgres_url: str,
    payment_timing_policy: str,
) -> None:
    fixture = await approved_prepaid_order(
        payment_client,
        payment_settings,
        postgres_url,
        payment_timing_policy=payment_timing_policy,
    )
    fulfillment_order = fixture["fulfillment_order"]
    assert fulfillment_order["payment_timing_policy"] == payment_timing_policy
    assert fulfillment_order["warehouse_id"] == fixture["warehouse_id"]
    assert fulfillment_order["payment_required"] == "0.00"

    released = await payment_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order['fulfillment_order_id']}/pick-release",
        headers=auth(
            payment_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": f"{payment_timing_policy}-pick-release"},
        ),
        json={"reason": "Approved commitment is ready for warehouse picking"},
    )
    assert released.status_code == 201, released.text
    assert released.json()["status"] == "released"
    assert released.json()["quantity_base"] == "2.000000"
    assert released.json()["payment_required"] == "0.00"
    assert released.json()["cleared_payment"] == "0.00"


# Fixture payment deadlines are created relative to "now", so as_of must stay
# safely in the future regardless of when the suite runs.
AS_OF_PAST_DEADLINE = "2100-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_unpaid_deadline_replays_releases_to_hold_and_requires_reservation_retry(
    payment_client: AsyncClient,
    payment_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(payment_client, payment_settings, postgres_url)
    fulfillment_order = fixture["fulfillment_order"]
    command = {
        "fulfillment_order_id": fulfillment_order["fulfillment_order_id"],
        "as_of": AS_OF_PAST_DEADLINE,
    }
    expired = await payment_client.post(
        "/v1/fulfillment/payment-deadlines/process",
        headers=auth(
            payment_settings,
            "deadline-mnl",
            **{"Idempotency-Key": "expire-unpaid-prepaid"},
        ),
        json=command,
    )
    assert expired.status_code == 200, expired.text
    deadline = expired.json()
    assert deadline["status"] == "payment_hold"
    assert deadline["released_quantity_base"] == "2.000000"
    assert deadline["backorder_quantity_base"] == "3.000000"

    replay = await payment_client.post(
        "/v1/fulfillment/payment-deadlines/process",
        headers=auth(
            payment_settings,
            "deadline-mnl",
            **{"Idempotency-Key": "expire-unpaid-prepaid"},
        ),
        json=command,
    )
    assert replay.status_code == 200
    assert replay.json() == deadline

    late_payment = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="cash",
        key="late-prepayment",
    )
    assert late_payment.status_code == 201, late_payment.text
    blocked = await payment_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order['fulfillment_order_id']}/pick-release",
        headers=auth(
            payment_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "late-payment-pick"},
        ),
        json={"reason": "Late payment received"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "reservation_retry_required"

    retried = await payment_client.post(
        f"/v1/sales/orders/{fixture['sales_order_id']}/reservation-retry",
        headers=auth(
            payment_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "retry-reservation-after-payment"},
        ),
        json={"warehouse_id": fixture["warehouse_id"], "reason": "Late payment received"},
    )
    assert retried.status_code == 201, retried.text
    assert retried.json()["status"] == "approved"
    assert retried.json()["payment_hold"] is False
    assert retried.json()["reserved_quantity_base"] == "2.000000"
    retry_replay = await payment_client.post(
        f"/v1/sales/orders/{fixture['sales_order_id']}/reservation-retry",
        headers=auth(
            payment_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "retry-reservation-after-payment"},
        ),
        json={"warehouse_id": fixture["warehouse_id"], "reason": "Late payment received"},
    )
    assert retry_replay.status_code == 200
    assert retry_replay.json() == retried.json()

    refreshed = await payment_client.get(
        "/v1/fulfillment/orders",
        headers=auth(payment_settings, "warehouse-mnl"),
        params={"sales_order_id": fixture["sales_order_id"]},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["total"] == 2
    assert {item["reservation_generation"] for item in refreshed.json()["items"]} == {
        1,
        2,
    }
    assert {item["warehouse_id"] for item in refreshed.json()["items"]} == {fixture["warehouse_id"]}
    active = [item for item in refreshed.json()["items"] if item["status"] == "payment_ready"]
    assert len(active) == 1
    released = await payment_client.post(
        f"/v1/fulfillment/orders/{active[0]['fulfillment_order_id']}/pick-release",
        headers=auth(
            payment_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "pick-after-reservation-retry"},
        ),
        json={"reason": "Reservation restored after payment"},
    )
    assert released.status_code == 201, released.text


@pytest.mark.asyncio
async def test_payment_and_deadline_race_has_one_coherent_serialized_outcome(
    payment_client: AsyncClient,
    payment_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(payment_client, payment_settings, postgres_url)
    fulfillment_order = fixture["fulfillment_order"]

    payment_response, deadline_response = await asyncio.gather(
        record_receipt(
            payment_client,
            payment_settings,
            fixture,
            payment_method="cash",
            key="racing-payment",
        ),
        payment_client.post(
            "/v1/fulfillment/payment-deadlines/process",
            headers=auth(
                payment_settings,
                "deadline-mnl",
                **{"Idempotency-Key": "racing-deadline"},
            ),
            json={
                "fulfillment_order_id": fulfillment_order["fulfillment_order_id"],
                "as_of": AS_OF_PAST_DEADLINE,
            },
        ),
    )
    assert payment_response.status_code == 201, payment_response.text
    assert deadline_response.status_code == 200, deadline_response.text

    receipt = await payment_client.get(
        f"/v1/finance/payment-receipts/{payment_response.json()['payment_receipt_id']}",
        headers=auth(payment_settings, "finance-recorder"),
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "cleared"
    fulfillment = await payment_client.get(
        "/v1/fulfillment/orders",
        headers=auth(payment_settings, "warehouse-mnl"),
        params={"sales_order_id": fixture["sales_order_id"]},
    )
    assert fulfillment.status_code == 200, fulfillment.text
    current = fulfillment.json()["items"][-1]
    if deadline_response.json()["status"] == "payment_hold":
        assert current["reserved_quantity_base"] == "0.000000"
        assert current["payment_hold"] is True
        assert receipt.json()["unapplied_amount"] == "224.00"
    else:
        assert deadline_response.json()["status"] == "payment_satisfied"
        assert current["reserved_quantity_base"] == "2.000000"
        assert current["payment_hold"] is False
        released = await payment_client.post(
            f"/v1/fulfillment/orders/{current['fulfillment_order_id']}/pick-release",
            headers=auth(
                payment_settings,
                "warehouse-mnl",
                **{"Idempotency-Key": "pick-race-winner"},
            ),
            json={"reason": "Payment won deadline serialization"},
        )
        assert released.status_code == 201, released.text


@pytest.mark.asyncio
async def test_material_order_change_and_reversal_preserve_immutable_receipt_history(
    payment_client: AsyncClient,
    payment_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(payment_client, payment_settings, postgres_url)
    created = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="cash",
        key="payment-before-material-change",
    )
    assert created.status_code == 201, created.text
    original = created.json()

    update_command = deepcopy(fixture["command"])
    del update_command["sales_order_id"]
    update_command["lines"][0]["quantity"] = "1.000000"
    changed = await payment_client.put(
        f"/v1/sales/orders/{fixture['sales_order_id']}",
        headers=auth(
            payment_settings,
            "sales-mnl",
            **{"Idempotency-Key": "material-change-after-payment", "If-Match": "1"},
        ),
        json=update_command,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["status"] == "draft"
    preserved = await payment_client.get(
        f"/v1/finance/payment-receipts/{original['payment_receipt_id']}",
        headers=auth(payment_settings, "finance-recorder"),
    )
    assert preserved.status_code == 200, preserved.text
    for field in (
        "payment_receipt_id",
        "customer_id",
        "sales_order_id",
        "payment_method",
        "amount",
        "currency",
        "received_at",
        "recorded_by",
    ):
        assert preserved.json()[field] == original[field]
    assert preserved.json()["status"] == "cleared"
    assert preserved.json()["unapplied_amount"] == "224.00"

    reversal_url = f"/v1/finance/payment-receipts/{original['payment_receipt_id']}/reversal"
    reversal_headers = auth(
        payment_settings,
        "finance-reverser",
        **{"Idempotency-Key": "reverse-preserved-payment"},
    )
    reversal_command = {
        "payment_reversal_id": str(uuid4()),
        "reason": "Customer payment returned",
        "reversed_at": "2026-07-30T03:00:00Z",
    }
    reversed_response = await payment_client.post(
        reversal_url,
        headers=reversal_headers,
        json=reversal_command,
    )
    assert reversed_response.status_code == 201, reversed_response.text
    reversal = reversed_response.json()
    assert reversal["original_payment_receipt_id"] == original["payment_receipt_id"]
    assert reversal["amount"] == "-224.00"
    assert reversal["reason"] == "Customer payment returned"
    reversal_replay = await payment_client.post(
        reversal_url,
        headers=reversal_headers,
        json=reversal_command,
    )
    assert reversal_replay.status_code == 200
    assert reversal_replay.json() == reversal

    after_reversal = await payment_client.get(
        f"/v1/finance/payment-receipts/{original['payment_receipt_id']}",
        headers=auth(payment_settings, "finance-reverser"),
    )
    assert after_reversal.status_code == 200, after_reversal.text
    assert after_reversal.json()["payment_receipt_id"] == original["payment_receipt_id"]
    assert after_reversal.json()["amount"] == "224.00"
    assert after_reversal.json()["status"] == "reversed"
    assert after_reversal.json()["unapplied_amount"] == "0.00"
    assert after_reversal.json()["reversal_id"] == reversal["payment_reversal_id"]


@pytest.mark.asyncio
async def test_payment_and_pick_commands_enforce_capability_and_operational_scope(
    payment_client: AsyncClient,
    payment_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(payment_client, payment_settings, postgres_url)
    no_capability = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="cash",
        key="sales-cannot-record-payment",
        actor="sales-mnl",
    )
    assert no_capability.status_code == 403
    assert no_capability.json()["error"]["code"] == "capability_required"

    out_of_scope = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="cash",
        key="cebu-cannot-record-manila-payment",
        actor="finance-ceb",
    )
    assert out_of_scope.status_code == 403
    assert out_of_scope.json()["error"]["code"] == "operational_scope_required"

    paid = await record_receipt(
        payment_client,
        payment_settings,
        fixture,
        payment_method="cash",
        key="authorized-manila-payment",
    )
    assert paid.status_code == 201, paid.text
    fulfillment_order = fixture["fulfillment_order"]
    wrong_warehouse = await payment_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order['fulfillment_order_id']}/pick-release",
        headers=auth(
            payment_settings,
            "warehouse-ceb",
            **{"Idempotency-Key": "cebu-cannot-release-manila-pick"},
        ),
        json={"reason": "Attempt outside warehouse scope"},
    )
    assert wrong_warehouse.status_code == 403
    assert wrong_warehouse.json()["error"]["code"] == "operational_scope_required"
