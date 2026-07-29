from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TRADEFLOW_REAL_STACK") != "1",
    reason="Runs only against the migrated real-stack acceptance environment.",
)


def auth(token: str, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **headers}


def test_external_priced_sales_order_draft_contract() -> None:
    base_url = os.environ.get("TRADEFLOW_REAL_STACK_API_URL", "http://127.0.0.1:8000")
    token = os.environ["TRADEFLOW_REAL_STACK_SALES_TOKEN"]
    commercial_token = os.environ["TRADEFLOW_REAL_STACK_CREDIT_TOKEN"]
    with httpx.Client(base_url=base_url, timeout=10) as client:
        scope = client.get("/v1/organization/scope", headers=auth(token))
        customer_search = client.get("/v1/customers?query=Real", headers=auth(token))
        inventory = client.get("/v1/inventory/availability?query=REAL-COLA", headers=auth(token))
        assert scope.status_code == customer_search.status_code == inventory.status_code == 200
        branch_id = scope.json()["branches"][0]["branch_id"]
        customer = customer_search.json()["items"][0]
        sku = inventory.json()["items"][0]

        tax = client.post(
            "/v1/sales/tax-code-versions",
            headers=auth(token, **{"Idempotency-Key": f"real-tax-{uuid4()}"}),
            json={
                "code": "REAL-VAT12",
                "effective_from": "2026-01-01",
                "effective_to": None,
                "name": "Real Stack VAT 12%",
                "rate": "0.120000",
            },
        )
        assert tax.status_code == 201, tax.text
        price_list = client.post(
            "/v1/sales/price-list-versions",
            headers=auth(token, **{"Idempotency-Key": f"real-price-{uuid4()}"}),
            json={
                "branch_id": branch_id,
                "code": "REAL-MNL-DEFAULT",
                "customer_id": None,
                "effective_from": "2026-01-01",
                "effective_to": None,
                "inclusion_mode": "exclusive",
                "items": [
                    {
                        "floor_unit_price": "8.000000",
                        "list_unit_price": "10.000000",
                        "sku_id": sku["sku_id"],
                        "tax_code_version_id": tax.json()["tax_code_version_id"],
                        "unit_code": "EA",
                    }
                ],
            },
        )
        assert price_list.status_code == 201, price_list.text
        reference = client.get(
            (
                "/v1/sales/order-entry-reference"
                f"?branch_id={branch_id}&customer_id={customer['customer_id']}"
            ),
            headers=auth(token),
        )
        assert reference.status_code == 200, reference.text
        assert reference.json()["price_list_code"] == "REAL-MNL-DEFAULT"
        address_id = reference.json()["addresses"][0]["address_version_id"]
        command = {
            "branch_id": branch_id,
            "customer_id": customer["customer_id"],
            "expected_customer_version": reference.json()["customer_version"],
            "expected_price_list_version_id": reference.json()["price_list_version_id"],
            "expected_pricing_date": reference.json()["pricing_date"],
            "delivery_address_version_id": address_id,
            "lines": [
                {
                    "line_id": str(uuid4()),
                    "expected_price_list_line_id": reference.json()["items"][0][
                        "price_list_line_id"
                    ],
                    "expected_unit_conversion_id": None,
                    "expected_unit_conversion_version": None,
                    "manual_override_unit_price": None,
                    "price_override_reason": None,
                    "quantity": "35.000000",
                    "sku_id": sku["sku_id"],
                    "unit_code": "EA",
                }
            ],
            "order_discount_amount": "0.030000",
            "payment_timing_override_reason": None,
            "payment_timing_policy": None,
            "sales_order_id": str(uuid4()),
        }
        key = f"real-sales-order-{uuid4()}"
        created = client.post(
            "/v1/sales/orders",
            headers=auth(token, **{"Idempotency-Key": key}),
            json=command,
        )
        replay = client.post(
            "/v1/sales/orders",
            headers=auth(token, **{"Idempotency-Key": key}),
            json=command,
        )
        assert created.status_code == 201, created.text
        assert replay.status_code == 200
        assert replay.json() == created.json()
        assert created.json()["subtotal"] == "350.00"
        assert created.json()["discount_total"] == "0.03"
        assert created.json()["tax_total"] == "42.00"
        assert created.json()["grand_total"] == "391.97"
        assert created.json()["payment_timing_policy"] == "on_account"

        warehouse_id = scope.json()["warehouses"][0]["warehouse_id"]
        approval_command = {
            "warehouse_id": warehouse_id,
            "exception_reason": "Real-stack discount review",
            "credit_override_reason": None,
        }
        approval_key = f"real-commercial-approval-{uuid4()}"
        approved = client.post(
            f"/v1/sales/orders/{command['sales_order_id']}/commercial-approval",
            headers=auth(
                commercial_token,
                **{"Idempotency-Key": approval_key, "If-Match": "1"},
            ),
            json=approval_command,
        )
        approval_replay = client.post(
            f"/v1/sales/orders/{command['sales_order_id']}/commercial-approval",
            headers=auth(
                commercial_token,
                **{"Idempotency-Key": approval_key, "If-Match": "1"},
            ),
            json=approval_command,
        )
        assert approved.status_code == 201, approved.text
        assert approval_replay.status_code == 200
        assert approval_replay.json() == approved.json()
        assert approved.json()["reserved_quantity_base"] == "30.000000"
        assert approved.json()["backorder_quantity_base"] == "5.000000"
        assert approved.json()["required_exceptions"] == []

        availability_after = client.get(
            "/v1/inventory/availability?query=REAL-COLA",
            headers=auth(token),
        )
        assert availability_after.status_code == 200
        assert availability_after.json()["items"][0]["commercial_reserved"] == "30.000000"
        assert availability_after.json()["items"][0]["warehouse_available"] == "0.000000"
