from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx
import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.demo_reset import DEMO_SEED_VERSION

ALL_CAPABILITIES = [
    "catalog:write",
    "customer:credit-approve",
    "customer:read",
    "customer:write",
    "finance:cash-reconcile",
    "finance:check-clear",
    "finance:credit-note-approve",
    "finance:credit-note-read",
    "finance:credit-note-request",
    "finance:expense-category-create",
    "finance:expense-category-publish",
    "finance:expense-category-read",
    "finance:expense-policy-create",
    "finance:expense-policy-publish",
    "finance:expense-policy-read",
    "finance:invoice-post",
    "finance:invoice-read",
    "finance:invoice-void",
    "finance:payment-allocate",
    "finance:payment-read",
    "finance:payment-record",
    "finance:payment-refund",
    "finance:payment-reverse",
    "finance:payment-verify",
    "finance:projection-rebuild",
    "finance:statement-read",
    "fulfillment:delivery-confirm",
    "fulfillment:delivery-correction-authorize",
    "fulfillment:delivery-correction-request",
    "fulfillment:delivery-read",
    "fulfillment:delivery-retry",
    "fulfillment:dispatch",
    "fulfillment:pick",
    "fulfillment:pick-read",
    "fulfillment:pick-release",
    "fulfillment:pick-reverse",
    "fulfillment:return-receive",
    "inventory:adjustment-approve",
    "inventory:adjustment-read",
    "inventory:adjustment-request",
    "inventory:post",
    "inventory:read",
    "inventory:rebuild",
    "inventory:investigation-resolve",
    "inventory:payment-deadline-process",
    "inventory:reservation-retry",
    "inventory:transfer-read",
    "inventory:transfer-receive",
    "inventory:transfer-request",
    "notification:manage",
    "notification:read",
    "organization:admin",
    "procurement:purchase-order-approve",
    "procurement:purchase-order-read",
    "procurement:purchase-order-write",
    "procurement:purchase-request-approve",
    "procurement:purchase-request-read",
    "procurement:purchase-request-write",
    "procurement:supplier-read",
    "procurement:supplier-write",
    "procurement:goods-receipt-approve-over-receipt",
    "procurement:goods-receipt-post",
    "procurement:landed-cost-allocate",
    "sales:cod-convert-on-account",
    "sales:commercial-approve",
    "sales:discount-enter",
    "sales:order-read",
    "sales:order-cancel",
    "sales:order-write",
    "sales:pricing-write",
    "sales:projection-rebuild",
    "sales:quotation-approve",
    "sales:quotation-convert",
    "sales:quotation-write",
]


def stable_id(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"https://tradeflow.demo/{DEMO_SEED_VERSION}/{name}"))


def token(subject: str, name: str, capabilities: list[str] | None = None) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "aud": os.environ.get("TRADEFLOW_AUTH_AUDIENCE", "tradeflow-api"),
            "capabilities": capabilities or ["platform:read"],
            "exp": now + timedelta(hours=2),
            "iat": now,
            "iss": os.environ["TRADEFLOW_AUTH_ISSUER"],
            "name": name,
            "sub": subject,
        },
        os.environ["TRADEFLOW_AUTH_TEST_SECRET"],
        algorithm="HS256",
    )


async def seed_delivery_receipt_series(branch_id: str) -> None:
    """Install metadata that does not yet have a public configuration command."""
    engine = create_async_engine(os.environ["TRADEFLOW_DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO document_series "
                    "(document_series_id, branch_id, document_type, prefix, next_number) "
                    "VALUES (:series_id, :branch_id, 'delivery_receipt', 'DR-MNL', 1) "
                    "ON CONFLICT (branch_id, document_type) DO NOTHING"
                ),
                {
                    "series_id": stable_id("delivery-receipt-series"),
                    "branch_id": branch_id,
                },
            )
    finally:
        await engine.dispose()


class Seeder:
    def __init__(self) -> None:
        self.client = httpx.Client(
            base_url=os.environ.get("TRADEFLOW_DEMO_API_URL", "http://api:8000"), timeout=30
        )
        self.tokens = {
            "bootstrap": token("demo-bootstrap", "Demo Bootstrap", ["organization:bootstrap"]),
            "checker": token("demo-checker", "Demo Commercial Checker"),
            "maker": token("demo-maker", "Demo Order Maker"),
            "operator": token(
                "demo-operator", "Demo Operator", ["platform:read", "platform:write"]
            ),
        }
        self.manifest: dict[str, Any] = {"seed_version": DEMO_SEED_VERSION, "records": {}}

    def request(
        self,
        method: str,
        path: str,
        *,
        actor: str = "operator",
        key: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.tokens[actor]}",
            "X-TradeFlow-Demo-Reset": os.environ["TRADEFLOW_DEMO_RESET_TOKEN"],
        }
        if key is not None:
            headers["Idempotency-Key"] = f"demo-{DEMO_SEED_VERSION}-{key}"
        headers.update(kwargs.pop("headers", {}))
        response = self.client.request(method, path, headers=headers, **kwargs)
        if response.status_code not in {200, 201}:
            raise RuntimeError(
                f"Demo seed failed: {method} {path}: {response.status_code} {response.text}"
            )
        return response.json()

    def run(self) -> None:
        configured = self.request(
            "POST",
            "/v1/organization/bootstrap",
            actor="bootstrap",
            key="bootstrap",
            json={
                "company": {
                    "base_currency": "PHP",
                    "code": "TFD",
                    "name": "TradeFlow Demo Distribution",
                },
                "branches": [
                    {
                        "code": "MNL",
                        "name": "Manila",
                        "warehouses": [{"code": "MNL-01", "name": "Manila Distribution Center"}],
                    },
                    {
                        "code": "CEB",
                        "name": "Cebu",
                        "warehouses": [{"code": "CEB-01", "name": "Cebu Distribution Center"}],
                    },
                ],
                "role_templates": [
                    {
                        "capabilities": ALL_CAPABILITIES,
                        "code": "DEMO_OPERATOR",
                        "name": "Demo Operator",
                    },
                    {
                        "capabilities": [
                            "sales:order-read",
                            "sales:commercial-approve",
                            "customer:credit-approve",
                        ],
                        "code": "DEMO_CHECKER",
                        "name": "Commercial Checker",
                    },
                    {
                        "capabilities": [
                            "sales:order-read",
                            "sales:order-write",
                            "sales:discount-enter",
                        ],
                        "code": "DEMO_MAKER",
                        "name": "Commercial Maker",
                    },
                ],
                "users": [
                    {
                        "approval_authorities": [
                            {
                                "branch_code": "MNL",
                                "capability": "sales:commercial-approve",
                                "maker_checker_required": True,
                                "maximum_amount": "1000000.00",
                                "maximum_percentage": None,
                            }
                        ],
                        "branch_codes": ["MNL", "CEB"],
                        "display_name": "Demo Operator",
                        "role_template_codes": ["DEMO_OPERATOR"],
                        "subject": "demo-operator",
                        "warehouse_codes": ["MNL-01", "CEB-01"],
                    },
                    {
                        "branch_codes": ["MNL"],
                        "display_name": "Demo Order Maker",
                        "role_template_codes": ["DEMO_MAKER"],
                        "subject": "demo-maker",
                        "warehouse_codes": ["MNL-01"],
                    },
                    {
                        "approval_authorities": [
                            {
                                "branch_code": "MNL",
                                "capability": "sales:commercial-approve",
                                "maker_checker_required": True,
                                "maximum_amount": "1000000.00",
                                "maximum_percentage": None,
                            }
                        ],
                        "branch_codes": ["MNL"],
                        "display_name": "Demo Commercial Checker",
                        "role_template_codes": ["DEMO_CHECKER"],
                        "subject": "demo-checker",
                        "warehouse_codes": ["MNL-01"],
                    },
                ],
            },
        )
        branches = {item["code"]: item["branch_id"] for item in configured["branches"]}
        asyncio.run(seed_delivery_receipt_series(branches["MNL"]))
        scope = self.request("GET", "/v1/organization/scope")
        warehouse = next(item for item in scope["warehouses"] if item["code"] == "MNL-01")
        warehouse_id = warehouse["warehouse_id"]
        ceb_warehouse_id = next(
            item["warehouse_id"] for item in scope["warehouses"] if item["code"] == "CEB-01"
        )
        self.manifest["records"].update(
            {
                "branches": branches,
                "warehouses": {"MNL-01": warehouse_id},
            }
        )

        customers: dict[str, str] = {}
        for code, name, policy, terms in [
            ("HARBOR", "Harbor & Pine Retail", "on_account", "NET_30"),
            ("SUNWARD", "Sunward Neighborhood Markets", "prepaid", "DUE_ON_RECEIPT"),
            ("CORNER", "Cornerstone Grocers", "cash_on_delivery", "DUE_ON_RECEIPT"),
        ]:
            created = self.request(
                "POST",
                "/v1/customers",
                key=f"customer-{code}",
                json={
                    "account_number": f"MNL-{code}-001",
                    "addresses": [
                        {
                            "address_key": "DELIVERY",
                            "city": "Manila",
                            "country_code": "PH",
                            "kind": "delivery",
                            "line_1": f"{len(customers) + 10} Demo Commerce Avenue",
                            "line_2": None,
                            "postal_code": "1000",
                            "region": "NCR",
                        }
                    ],
                    "branch_id": branches["MNL"],
                    "contacts": [
                        {
                            "email": f"buyer@{code.lower()}.example",
                            "name": "Demo Buyer",
                            "phone": None,
                            "role": "Purchasing",
                        }
                    ],
                    "credit_hold": False,
                    "credit_limit": "500000.00",
                    "legal_name": name,
                    "payment_terms": terms,
                    "payment_timing_policy": policy,
                    "status": "active",
                },
            )
            customers[code] = created["customer_id"]

        sku = self.request(
            "POST",
            "/v1/catalog/skus",
            key="sku-coffee",
            json={
                "product_code": "TF-COFFEE",
                "product_name": "Highland Coffee",
                "sku_code": "COFFEE-500",
                "sku_name": "Highland Coffee 500 g",
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
                "barcodes": [{"barcode": "4800000000500", "unit_code": "EA"}],
            },
        )
        low_stock_sku = self.request(
            "POST",
            "/v1/catalog/skus",
            key="sku-thermal-labels",
            json={
                "product_code": "TF-LABELS",
                "product_name": "Warehouse Labels",
                "sku_code": "LABEL-THERMAL-100",
                "sku_name": "Thermal pallet labels, roll of 100",
                "base_stocking_unit": "ROLL",
                "tracking_policy": "untracked",
                "expiration_control": False,
                "conversions": [],
                "barcodes": [{"barcode": "4800000000609", "unit_code": "ROLL"}],
            },
        )
        location = self.request(
            "POST",
            "/v1/inventory/locations",
            key="location-available",
            json={
                "warehouse_id": warehouse_id,
                "code": "A-01",
                "name": "Available pick face",
                "custody": "available",
            },
        )
        ceb_location = self.request(
            "POST",
            "/v1/inventory/locations",
            key="location-ceb-available",
            json={
                "warehouse_id": ceb_warehouse_id,
                "code": "A-01",
                "name": "Cebu available pick face",
                "custody": "available",
            },
        )
        for index, quantity in enumerate(("120.000000", "80.000000"), start=1):
            self.request(
                "POST",
                "/v1/inventory/opening-stock",
                key=f"opening-{index}",
                json={
                    "sku_id": sku["sku_id"],
                    "warehouse_id": warehouse_id,
                    "location_id": location["location_id"],
                    "quantity": quantity,
                    "unit_code": "EA",
                    "unit_cost": "145.000000",
                    "source_reference": f"DEMO-OPEN-{index:02d}",
                    "lot_code": None,
                    "serial_numbers": [],
                    "expiration_date": None,
                },
            )
        self.request(
            "POST",
            "/v1/inventory/opening-stock",
            key="opening-thermal-labels",
            json={
                "sku_id": low_stock_sku["sku_id"],
                "warehouse_id": warehouse_id,
                "location_id": location["location_id"],
                "quantity": "4.000000",
                "unit_code": "ROLL",
                "unit_cost": "85.000000",
                "source_reference": "DEMO-OPEN-LABELS",
                "lot_code": None,
                "serial_numbers": [],
                "expiration_date": None,
            },
        )

        tax = self.request(
            "POST",
            "/v1/sales/tax-code-versions",
            key="vat12",
            json={
                "code": "VAT12",
                "effective_from": "2026-01-01",
                "effective_to": None,
                "name": "VAT 12%",
                "rate": "0.120000",
            },
        )
        self.request(
            "POST",
            "/v1/sales/price-list-versions",
            key="mnl-price",
            json={
                "branch_id": branches["MNL"],
                "code": "MNL-DEMO",
                "customer_id": None,
                "effective_from": "2026-01-01",
                "effective_to": None,
                "inclusion_mode": "exclusive",
                "items": [
                    {
                        "floor_unit_price": "180.000000",
                        "list_unit_price": "220.000000",
                        "sku_id": sku["sku_id"],
                        "tax_code_version_id": tax["tax_code_version_id"],
                        "unit_code": "EA",
                    }
                ],
            },
        )

        orders: dict[str, dict[str, Any]] = {}
        for stage in (
            "draft",
            "awaiting_approval",
            "approved",
            "ready_to_pick",
            "partially_picked",
            "ready_to_dispatch",
            "delivery_awaiting_confirmation",
            "posted_invoice",
        ):
            reference = self.request(
                "GET",
                f"/v1/sales/order-entry-reference?branch_id={branches['MNL']}&customer_id={customers['HARBOR']}",
            )
            sales_order_id = stable_id(f"sales-order-{stage}")
            line_id = stable_id(f"sales-line-{stage}")
            order = self.request(
                "POST",
                "/v1/sales/orders",
                actor="maker" if stage == "awaiting_approval" else "operator",
                key=f"order-{stage}",
                json={
                    "branch_id": branches["MNL"],
                    "customer_id": customers["HARBOR"],
                    "expected_customer_version": reference["customer_version"],
                    "expected_price_list_version_id": reference["price_list_version_id"],
                    "expected_pricing_date": reference["pricing_date"],
                    "delivery_address_version_id": reference["addresses"][0]["address_version_id"],
                    "lines": [
                        {
                            "line_id": line_id,
                            "expected_price_list_line_id": reference["items"][0][
                                "price_list_line_id"
                            ],
                            "expected_unit_conversion_id": None,
                            "expected_unit_conversion_version": None,
                            "manual_override_unit_price": None,
                            "price_override_reason": None,
                            "quantity": "8.000000",
                            "sku_id": sku["sku_id"],
                            "unit_code": "EA",
                        }
                    ],
                    "order_discount_amount": "0.00",
                    "payment_timing_override_reason": None,
                    "payment_timing_policy": None,
                    "sales_order_id": sales_order_id,
                },
            )
            orders[stage] = {
                "order_id": sales_order_id,
                "line_id": line_id,
                "status": order["status"],
            }
            if stage == "awaiting_approval":
                submitted = self.request(
                    "POST",
                    f"/v1/sales/orders/{sales_order_id}/submission",
                    actor="maker",
                    key="submit-awaiting-approval",
                    headers={"If-Match": "1"},
                )
                orders[stage]["status"] = submitted["status"]
            if stage not in {"draft", "awaiting_approval"}:
                approval = self.request(
                    "POST",
                    f"/v1/sales/orders/{sales_order_id}/commercial-approval",
                    actor="checker",
                    key=f"approve-{stage}",
                    headers={"If-Match": "1"},
                    json={
                        "warehouse_id": warehouse_id,
                        "exception_reason": None,
                        "credit_override_reason": None,
                    },
                )
                orders[stage]["status"] = approval["status"]
                fulfillment = self.request(
                    "GET", f"/v1/fulfillment/orders?sales_order_id={sales_order_id}"
                )["items"][0]
                orders[stage]["fulfillment_order_id"] = fulfillment["fulfillment_order_id"]
                if stage in {
                    "ready_to_pick",
                    "partially_picked",
                    "ready_to_dispatch",
                    "delivery_awaiting_confirmation",
                    "posted_invoice",
                }:
                    released = self.request(
                        "POST",
                        f"/v1/fulfillment/orders/{fulfillment['fulfillment_order_id']}/pick-release",
                        key=f"release-{stage}",
                        json={"reason": "Prepared for the live product demo"},
                    )
                    orders[stage]["status"] = (
                        "pick_released" if stage == "ready_to_pick" else released["status"]
                    )
                if stage in {
                    "partially_picked",
                    "ready_to_dispatch",
                    "delivery_awaiting_confirmation",
                    "posted_invoice",
                }:
                    picking_context = self.request(
                        "GET",
                        f"/v1/fulfillment/orders/{fulfillment['fulfillment_order_id']}/picking-context",
                    )
                    pick_id = stable_id(f"pick-{stage}")
                    quantity = "3.000000" if stage == "partially_picked" else "8.000000"
                    picked = self.request(
                        "POST",
                        f"/v1/fulfillment/orders/{fulfillment['fulfillment_order_id']}/picks",
                        key=f"pick-{stage}",
                        json={
                            "pick_id": pick_id,
                            "expected_fulfillment_version": picking_context["version"],
                            "lines": [
                                {
                                    "line_id": line_id,
                                    "quantity": quantity,
                                    "unit_code": "EA",
                                    "selections": [],
                                }
                            ],
                        },
                    )
                    orders[stage].update({"pick_id": pick_id, "status": picked["status"]})
                    if stage in {"delivery_awaiting_confirmation", "posted_invoice"}:
                        delivery_id = stable_id(f"delivery-{stage}")
                        delivery = self.request(
                            "POST",
                            f"/v1/fulfillment/orders/{fulfillment['fulfillment_order_id']}/dispatch",
                            key=f"dispatch-{stage}",
                            json={
                                "delivery_id": delivery_id,
                                "expected_fulfillment_version": picked["version"],
                                "assigned_to": "demo-operator",
                                "pick_ids": [pick_id],
                            },
                        )
                        orders[stage].update(
                            {"delivery_id": delivery_id, "status": delivery["status"]}
                        )
                        if stage == "posted_invoice":
                            evidence = b"tradeflow-demo-signature"
                            digest = hashlib.sha256(evidence).hexdigest()
                            evidence_id = stable_id("posted-invoice-evidence")
                            upload = self.request(
                                "POST",
                                f"/v1/deliveries/{delivery_id}/evidence/uploads",
                                json={
                                    "evidence_id": evidence_id,
                                    "kind": "signature",
                                    "content_type": "image/png",
                                    "size_bytes": len(evidence),
                                    "sha256": digest,
                                    "device_captured_at": "2026-08-24T08:00:00Z",
                                },
                            )
                            for part in upload["parts"]:
                                uploaded = httpx.put(
                                    part["upload_url"],
                                    content=evidence[part["start_byte"] : part["end_byte"]],
                                    headers=part["upload_headers"],
                                    timeout=30,
                                )
                                uploaded.raise_for_status()
                            self.request(
                                "POST",
                                f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
                            )
                            confirmed = self.request(
                                "POST",
                                f"/v1/deliveries/{delivery_id}/confirmations",
                                key="confirm-posted-invoice",
                                json={
                                    "confirmation_id": stable_id("posted-invoice-confirmation"),
                                    "expected_delivery_version": 1,
                                    "recipient_name": "Demo Receiving Clerk",
                                    "device_captured_at": "2026-08-24T08:00:00Z",
                                    "evidence_ids": [evidence_id],
                                    "lines": [
                                        {"line_id": line_id, "accepted_quantity_base": "8.000000"}
                                    ],
                                    "collection": None,
                                },
                            )
                            orders[stage].update(
                                {
                                    "confirmation_id": confirmed["confirmation_id"],
                                    "status": "confirmed",
                                }
                            )

        posted_order = orders["posted_invoice"]
        draft_invoice: dict[str, Any] | None = None
        for _attempt in range(30):
            invoices = self.request(
                "GET", f"/v1/finance/invoices?customer_id={customers['HARBOR']}"
            )["items"]
            draft_invoice = next(
                (
                    item
                    for item in invoices
                    if item["delivery_confirmation_id"] == posted_order["confirmation_id"]
                ),
                None,
            )
            if draft_invoice is not None:
                break
            time.sleep(1)
        if draft_invoice is None:
            raise RuntimeError("Demo invoice projection did not become ready.")
        posted = self.request(
            "POST",
            f"/v1/finance/invoices/{draft_invoice['draft_invoice_id']}/post",
            key="post-invoice",
            json={"posted_at": "2026-08-24T08:05:00Z"},
        )
        posted_order.update(
            {"invoice_id": draft_invoice["draft_invoice_id"], "status": posted["status"]}
        )

        payment = self.request(
            "POST",
            "/v1/finance/payment-receipts",
            key="payment-awaiting-verification",
            json={
                "payment_receipt_id": stable_id("payment-awaiting-verification"),
                "branch_id": branches["MNL"],
                "customer_id": customers["HARBOR"],
                "sales_order_id": orders["approved"]["order_id"],
                "payment_method": "bank_transfer",
                "amount": "500.00",
                "currency": "PHP",
                "received_at": "2026-08-24T08:10:00Z",
                "external_reference": "DEMO-BANK-001",
                "evidence": {
                    "account_or_provider": "TradeFlow Demo Bank",
                    "value_date": "2026-08-24",
                    "document_url": "s3://tradeflow-demo-evidence/payments/DEMO-BANK-001.pdf",
                },
            },
        )
        collected_payment = self.request(
            "POST",
            "/v1/finance/payment-receipts",
            key="payment-collected",
            json={
                "payment_receipt_id": stable_id("payment-collected"),
                "branch_id": branches["MNL"],
                "customer_id": customers["HARBOR"],
                "sales_order_id": posted_order["order_id"],
                "payment_method": "cash",
                "amount": "600.00",
                "currency": "PHP",
                "received_at": "2026-08-24T08:12:00Z",
                "external_reference": None,
                "evidence": None,
            },
        )
        allocation = self.request(
            "POST",
            f"/v1/finance/payment-receipts/{collected_payment['payment_receipt_id']}/allocations",
            key="allocate-collected-payment",
            json={
                "expected_version": collected_payment["balance_version"],
                "allocations": [
                    {
                        "invoice_id": draft_invoice["draft_invoice_id"],
                        "amount": "600.00",
                    }
                ],
            },
        )
        transfer = self.request(
            "POST",
            "/v1/inventory/transfers",
            key="inventory-transfer",
            json={
                "sku_id": low_stock_sku["sku_id"],
                "from_warehouse_id": warehouse_id,
                "to_warehouse_id": ceb_warehouse_id,
                "from_location_id": location["location_id"],
                "to_location_id": ceb_location["location_id"],
                "quantity": "4.000000",
                "unit_code": "ROLL",
                "reason": "Seeded inter-branch replenishment",
                "source_reference": "DEMO-TR-001",
                "lot_code": None,
            },
        )["transfer"]
        adjustment = self.request(
            "POST",
            "/v1/inventory/adjustments",
            key="inventory-adjustment",
            json={
                "sku_id": sku["sku_id"],
                "warehouse_id": warehouse_id,
                "location_id": location["location_id"],
                "kind": "shortage",
                "quantity": "1.000000",
                "unit_code": "EA",
                "reason": "Seeded cycle-count variance",
                "source_reference": "DEMO-ADJ-001",
                "lot_code": None,
            },
        )["adjustment"]

        supplier = self.request(
            "POST",
            "/v1/procurement/suppliers",
            json={
                "code": "PACIFIC-FOODS",
                "legal_name": "Pacific Food Supply Corporation",
                "tax_id": "DEMO-123-456",
                "payment_terms": "Net 30",
                "default_currency": "PHP",
            },
        )
        purchase_request = self.request(
            "POST",
            "/v1/procurement/purchase-requests",
            key="purchase-request",
            json={
                "supplier_id": supplier["supplier_id"],
                "branch_id": branches["MNL"],
                "code": "PR-DEMO-001",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku["sku_id"],
                        "requested_quantity": "24",
                        "unit_code": "CASE",
                        "unit_cost": "1740.00",
                    }
                ],
            },
        )
        purchase_order = self.request(
            "POST",
            "/v1/procurement/purchase-orders",
            json={
                "supplier_id": supplier["supplier_id"],
                "branch_id": branches["MNL"],
                "code": "PO-DEMO-001",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku["sku_id"],
                        "requested_quantity": "24",
                        "unit_code": "CASE",
                        "unit_cost": "1740.00",
                    }
                ],
            },
        )
        self.manifest["records"].update(
            {
                "customers": customers,
                "orders": orders,
                "sku": sku["sku_id"],
                "low_stock_sku": low_stock_sku["sku_id"],
                "supplier": supplier["supplier_id"],
                "purchase_request": purchase_request["purchase_request_id"],
                "purchase_order": purchase_order["purchase_order_id"],
                "payment": payment["payment_receipt_id"],
                "collected_payment": collected_payment["payment_receipt_id"],
                "payment_allocation": allocation[0]["allocation_id"],
                "inventory_transfer": transfer["transfer_id"],
                "inventory_adjustment": adjustment["adjustment_id"],
            }
        )
        self.write_state()

    def write_state(self) -> None:
        state_dir = Path(os.environ.get("TRADEFLOW_DEMO_STATE_DIR", "/demo-state"))
        state_dir.mkdir(parents=True, exist_ok=True)
        credential = state_dir / "credential"
        credential.write_text(self.tokens["operator"], encoding="utf-8")
        credential.chmod(0o400)
        os.chown(credential, int(os.environ.get("TRADEFLOW_DEMO_WEB_UID", "10001")), -1)
        temporary = state_dir / "manifest.tmp"
        temporary.write_text(
            json.dumps(self.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(state_dir / "manifest.json")
        if (
            self.manifest["seed_version"] != DEMO_SEED_VERSION
            or len(self.manifest["records"]["orders"]) < 6
        ):
            raise RuntimeError("Demo seed validation failed.")


if __name__ == "__main__":
    seeder = Seeder()
    try:
        seeder.run()
    finally:
        seeder.client.close()
