from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def gr_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def gr_client(gr_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(gr_settings)
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


async def bootstrap_procurement(
    client: AsyncClient,
    settings: Settings,
) -> dict[str, str]:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers={
            "Authorization": (
                f"Bearer {token(settings, 'bootstrapper', ['organization:bootstrap'])}"
            ),
            "Idempotency-Key": "goods-receipt-bootstrap",
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
                    "warehouses": [
                        {"code": "MNL-01", "name": "Manila DC"},
                        {"code": "MNL-02", "name": "Manila Receiving"},
                    ],
                },
                {
                    "code": "CEB",
                    "name": "Cebu",
                    "warehouses": [{"code": "CEB-01", "name": "Cebu DC"}],
                },
            ],
            "role_templates": [
                {
                    "code": "PROCUREMENT_MANAGER",
                    "name": "Procurement Manager",
                    "capabilities": [
                        "procurement:supplier-read",
                        "procurement:supplier-write",
                        "procurement:purchase-order-read",
                        "procurement:purchase-order-write",
                        "procurement:purchase-order-approve",
                        "procurement:goods-receipt-post",
                        "procurement:goods-receipt-approve-over-receipt",
                    ],
                },
                {
                    "code": "WAREHOUSE_RECEIVER",
                    "name": "Warehouse Receiver",
                    "capabilities": [
                        "procurement:goods-receipt-post",
                    ],
                },
                {
                    "code": "PROCUREMENT_BUYER",
                    "name": "Procurement Buyer",
                    "capabilities": [
                        "procurement:supplier-read",
                        "procurement:purchase-order-read",
                        "procurement:purchase-order-write",
                    ],
                },
            ],
            "users": [
                {
                    "subject": "procurement-mnl",
                    "display_name": "Manila Procurement Manager",
                    "role_template_codes": ["PROCUREMENT_MANAGER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01", "MNL-02"],
                },
                {
                    "subject": "receiver-mnl",
                    "display_name": "Manila Receiver",
                    "role_template_codes": ["WAREHOUSE_RECEIVER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "receiver-ceb",
                    "display_name": "Cebu Receiver",
                    "role_template_codes": ["WAREHOUSE_RECEIVER"],
                    "branch_codes": ["CEB"],
                    "warehouse_codes": ["CEB-01"],
                },
                {
                    "subject": "buyer-mnl",
                    "display_name": "Manila Buyer",
                    "role_template_codes": ["PROCUREMENT_BUYER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    warehouses: dict[str, str] = {}
    for branch in body["branches"]:
        for warehouse in branch["warehouses"]:
            warehouses[warehouse["code"]] = warehouse["warehouse_id"]
    return {
        "branch_id": str(body["branches"][0]["branch_id"]),
        "ceb_branch_id": str(body["branches"][1]["branch_id"]),
        "mnl_01_id": warehouses["MNL-01"],
        "mnl_02_id": warehouses["MNL-02"],
        "ceb_01_id": warehouses["CEB-01"],
    }


async def _seed_supplier(
    client: AsyncClient,
    settings: Settings,
    code: str = "ACME-001",
) -> str:
    response = await client.post(
        "/v1/procurement/suppliers",
        headers=auth(settings, "procurement-mnl"),
        json={
            "code": code,
            "legal_name": "ACME Supplies Inc.",
            "payment_terms": "Net 30",
            "default_currency": "PHP",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["supplier_id"]


async def _seed_sku(
    postgres_url: str,
    code: str = "SKU-001",
    tracking_policy: str = "untracked",
) -> str:
    engine = create_async_engine(postgres_url)
    sku_id = None
    async with engine.begin() as connection:
        company_id = await connection.scalar(text("SELECT company_id FROM companies LIMIT 1"))
        assert company_id is not None
        product_id = str(
            await connection.scalar(
                text(
                    "INSERT INTO products (product_id, code, name, created_by) "
                    "VALUES (gen_random_uuid(), :code, :name, 'procurement-mnl') "
                    "RETURNING product_id"
                ),
                {
                    "code": f"PROD-{code}",
                    "name": f"Test Product {code}",
                },
            )
        )
        sku_id = str(
            await connection.scalar(
                text(
                    """INSERT INTO skus (
                        sku_id,
                        product_id,
                        code,
                        name,
                        base_stocking_unit,
                        tracking_policy,
                        created_by
                    ) VALUES (
                        gen_random_uuid(),
                        :product_id,
                        :code,
                        :name,
                        :base_unit,
                        :tracking_policy,
                        'procurement-mnl'
                    ) RETURNING sku_id"""
                ),
                {
                    "product_id": product_id,
                    "code": code,
                    "name": f"Test SKU {code}",
                    "base_unit": "pcs",
                    "tracking_policy": tracking_policy,
                },
            )
        )
        await connection.execute(
            text(
                """INSERT INTO unit_conversions (
                    unit_conversion_id,
                    sku_id,
                    unit_code,
                    base_quantity,
                    effective_from,
                    created_by
                ) VALUES (
                    gen_random_uuid(),
                    :sku_id,
                    'box',
                    12,
                    CURRENT_DATE,
                    'procurement-mnl'
                )"""
            ),
            {"sku_id": sku_id},
        )
    await engine.dispose()
    assert sku_id is not None
    return sku_id


async def _seed_location(
    postgres_url: str,
    warehouse_id: str,
    code: str = "RCV-01",
) -> str:
    engine = create_async_engine(postgres_url)
    location_id = None
    async with engine.begin() as connection:
        location_id = str(
            await connection.scalar(
                text(
                    """INSERT INTO warehouse_stock_locations (
                        location_id,
                        warehouse_id,
                        code,
                        name,
                        custody,
                        created_by
                    ) VALUES (
                        gen_random_uuid(),
                        :warehouse_id,
                        :code,
                        :name,
                        'available',
                        'procurement-mnl'
                    ) RETURNING location_id"""
                ),
                {
                    "warehouse_id": warehouse_id,
                    "code": code,
                    "name": f"Receiving {code}",
                },
            )
        )
    await engine.dispose()
    assert location_id is not None
    return location_id


async def _seed_approval_authority(
    postgres_url: str,
    user_subject: str,
    capability_code: str,
    branch_id: str,
    warehouse_id: str | None = None,
    maximum_amount: str = "100000.00",
) -> str:
    engine = create_async_engine(postgres_url)
    authority_id = None
    async with engine.begin() as connection:
        authority_id = str(
            await connection.scalar(
                text(
                    """INSERT INTO approval_authorities (
                        approval_authority_id,
                        user_subject,
                        capability_code,
                        branch_id,
                        warehouse_id,
                        maximum_amount,
                        maker_checker_required
                    ) VALUES (
                        gen_random_uuid(),
                        :user_subject,
                        :capability_code,
                        :branch_id,
                        :warehouse_id,
                        :maximum_amount,
                        true
                    ) RETURNING approval_authority_id"""
                ),
                {
                    "user_subject": user_subject,
                    "capability_code": capability_code,
                    "branch_id": branch_id,
                    "warehouse_id": warehouse_id,
                    "maximum_amount": maximum_amount,
                },
            )
        )
    await engine.dispose()
    assert authority_id is not None
    return authority_id


async def _create_approved_purchase_order(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
    scope: dict[str, str],
    sku_id: str,
    code: str = "PO-GR-001",
    requested_quantity: str = "10",
) -> tuple[str, str]:
    supplier_id = await _seed_supplier(client, settings, f"SUP-{code}")
    created = await client.post(
        "/v1/procurement/purchase-orders",
        headers=auth(settings, "buyer-mnl"),
        json={
            "supplier_id": supplier_id,
            "branch_id": scope["branch_id"],
            "code": code,
            "currency": "PHP",
            "lines": [
                {
                    "sku_id": sku_id,
                    "requested_quantity": requested_quantity,
                    "unit_code": "box",
                    "unit_cost": "150.00",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    po_id = body["purchase_order_id"]
    line_id = body["lines"][0]["purchase_order_line_id"]
    approved = await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/approve",
        headers=auth(settings, "procurement-mnl"),
    )
    assert approved.status_code == 200, approved.text
    return po_id, line_id


class TestCreateGoodsReceipt:
    async def test_create_goods_receipt_returns_201(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-001",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "120",
                    }
                ],
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["purchase_order_id"] == po_id
        assert body["warehouse_id"] == scope["mnl_01_id"]
        assert body["location_id"] == location_id
        assert body["receipt_number"] == "GR-001"
        assert body["status"] == "posted"
        assert len(body["lines"]) == 1
        assert Decimal(body["lines"][0]["received_quantity_base"]) == Decimal("120")

        fetched = await gr_client.get(
            f"/v1/procurement/purchase-orders/{po_id}",
            headers=auth(gr_settings, "buyer-mnl"),
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["status"] == "received"

    async def test_partial_goods_receipt_sets_partially_received(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-002",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "60",
                    }
                ],
            },
        )

        assert response.status_code == 201, response.text

        fetched = await gr_client.get(
            f"/v1/procurement/purchase-orders/{po_id}",
            headers=auth(gr_settings, "buyer-mnl"),
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["status"] == "partially_received"

    async def test_goods_receipt_rejects_over_receipt_without_approval(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-003",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "121",
                    }
                ],
            },
        )

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "goods_receipt_over_receipt_approval_required"

    async def test_partial_receipt_with_rejected_quantity_updates_backorder(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-VAR-001",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "60",
                        "accepted_quantity_base": "48",
                        "rejected_quantity_base": "12",
                        "variance_reason": "Carton damage",
                    }
                ],
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert Decimal(body["lines"][0]["accepted_quantity_base"]) == Decimal("48")
        assert Decimal(body["lines"][0]["rejected_quantity_base"]) == Decimal("12")
        assert Decimal(body["lines"][0]["damaged_quantity_base"]) == Decimal("0")

        fetched = await gr_client.get(
            f"/v1/procurement/purchase-orders/{po_id}",
            headers=auth(gr_settings, "buyer-mnl"),
        )
        assert fetched.status_code == 200, fetched.text
        line = fetched.json()["lines"][0]
        assert Decimal(line["accepted_quantity_base"]) == Decimal("48")
        assert Decimal(line["received_quantity_base"]) == Decimal("60")
        assert Decimal(line["backorder_quantity_base"]) == Decimal("72")
        assert fetched.json()["status"] == "partially_received"

    async def test_over_receipt_with_approval_succeeds(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])
        authority_id = await _seed_approval_authority(
            postgres_url,
            user_subject="procurement-mnl",
            capability_code="procurement:goods-receipt-approve-over-receipt",
            branch_id=scope["branch_id"],
            warehouse_id=scope["mnl_01_id"],
            maximum_amount="1000.00",
        )

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-VAR-002",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "121",
                        "accepted_quantity_base": "121",
                        "variance_reason": "Supplier over-ship approved",
                        "approval_authority_id": authority_id,
                    }
                ],
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["lines"][0]["approval_authority_id"] == authority_id

        fetched = await gr_client.get(
            f"/v1/procurement/purchase-orders/{po_id}",
            headers=auth(gr_settings, "buyer-mnl"),
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["status"] == "received"

    async def test_self_approved_over_receipt_is_denied(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])
        authority_id = await _seed_approval_authority(
            postgres_url,
            user_subject="receiver-mnl",
            capability_code="procurement:goods-receipt-approve-over-receipt",
            branch_id=scope["branch_id"],
            warehouse_id=scope["mnl_01_id"],
        )

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-VAR-003",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "121",
                        "accepted_quantity_base": "121",
                        "variance_reason": "Self approval",
                        "approval_authority_id": authority_id,
                    }
                ],
            },
        )

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "maker_checker_violation"

    async def test_variance_imbalance_is_rejected(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-VAR-004",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "60",
                        "accepted_quantity_base": "50",
                        "rejected_quantity_base": "5",
                    }
                ],
            },
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "goods_receipt_variance_imbalance"

    async def test_goods_receipt_rejects_draft_purchase_order(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        supplier_id = await _seed_supplier(gr_client, gr_settings, "SUP-DRAFT")
        created = await gr_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(gr_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-DRAFT",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "1",
                        "unit_code": "box",
                        "unit_cost": "150.00",
                    }
                ],
            },
        )
        assert created.status_code == 201, created.text
        po_id = created.json()["purchase_order_id"]
        line_id = created.json()["lines"][0]["purchase_order_line_id"]
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-004",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "12",
                    }
                ],
            },
        )

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "purchase_order_not_receivable"

    async def test_goods_receipt_requires_post_capability(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "buyer-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-005",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "12",
                    }
                ],
            },
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "capability_required"

    async def test_goods_receipt_enforces_branch_scope(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-ceb"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-006",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "12",
                    }
                ],
            },
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "branch_scope_required"

    async def test_goods_receipt_enforces_warehouse_scope(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_02_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_02_id"],
                "location_id": location_id,
                "receipt_number": "GR-007",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "12",
                    }
                ],
            },
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "warehouse_scope_required"

    async def test_goods_receipt_rejects_duplicate_receipt_number(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])
        payload = {
            "warehouse_id": scope["mnl_01_id"],
            "location_id": location_id,
            "receipt_number": "GR-008",
            "lines": [
                {
                    "purchase_order_line_id": line_id,
                    "received_quantity_base": "60",
                }
            ],
        }

        first = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json=payload,
        )
        assert first.status_code == 201, first.text

        second = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json=payload,
        )
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "goods_receipt_number_duplicate"

    async def test_goods_receipt_rejects_lot_for_untracked_sku(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-009",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "12",
                        "lot_code": "LOT-A",
                    }
                ],
            },
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "tracking_identity_unexpected"

    async def test_lot_tracked_sku_requires_lot_code(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url, tracking_policy="lot")
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-010",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "12",
                    }
                ],
            },
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "lot_code_required"

    async def test_lot_tracked_sku_accepts_lot_code(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url, tracking_policy="lot")
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-011",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "12",
                        "lot_code": "LOT-A",
                    }
                ],
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["lines"][0]["lot_code"] == "LOT-A"

    async def test_serial_tracked_sku_requires_serial_count_match(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url, tracking_policy="serial")
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-012",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "3",
                        "serial_numbers": ["S1", "S2"],
                    }
                ],
            },
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "serial_count_mismatch"

    async def test_serial_tracked_sku_rejects_duplicate_serial_numbers(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url, tracking_policy="serial")
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-013",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "2",
                        "serial_numbers": ["S1", "S1"],
                    }
                ],
            },
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "serial_numbers_duplicate"

    async def test_serial_tracked_sku_accepts_serial_numbers(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url, tracking_policy="serial")
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id, requested_quantity="1"
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-014",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "1",
                        "serial_numbers": ["S1"],
                    }
                ],
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["lines"][0]["serial_numbers"] == ["S1"]

    async def test_goods_receipt_rejects_unknown_purchase_order(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])
        unknown_po = "123e4567-e89b-12d3-a456-426614174000"

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{unknown_po}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-015",
                "lines": [
                    {
                        "purchase_order_line_id": unknown_po,
                        "received_quantity_base": "1",
                    }
                ],
            },
        )

        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "purchase_order_not_found"

    async def test_goods_receipt_rejects_unknown_location(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        unknown_location = "123e4567-e89b-12d3-a456-426614174000"

        response = await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": unknown_location,
                "receipt_number": "GR-016",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "12",
                    }
                ],
            },
        )

        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "location_not_found"


class TestListGoodsReceipts:
    async def test_list_goods_receipts_returns_created_receipts(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-LIST-001",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "120",
                    }
                ],
            },
        )

        response = await gr_client.get(
            "/v1/procurement/purchase-orders/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["receipt_number"] == "GR-LIST-001"
        assert body["items"][0]["status"] == "posted"

    async def test_list_goods_receipts_returns_empty_for_other_branch(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
            json={
                "warehouse_id": scope["mnl_01_id"],
                "location_id": location_id,
                "receipt_number": "GR-LIST-002",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "120",
                    }
                ],
            },
        )

        response = await gr_client.get(
            "/v1/procurement/purchase-orders/receipts",
            headers=auth(gr_settings, "receiver-ceb"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 0
        assert body["items"] == []

    async def test_list_goods_receipts_respects_warehouse_scope(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_02_id"])

        await gr_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/receipts",
            headers=auth(gr_settings, "procurement-mnl"),
            json={
                "warehouse_id": scope["mnl_02_id"],
                "location_id": location_id,
                "receipt_number": "GR-LIST-003",
                "lines": [
                    {
                        "purchase_order_line_id": line_id,
                        "received_quantity_base": "120",
                    }
                ],
            },
        )

        response = await gr_client.get(
            "/v1/procurement/purchase-orders/receipts",
            headers=auth(gr_settings, "receiver-mnl"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 0

    async def test_list_goods_receipts_supports_pagination(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(gr_client, gr_settings)
        sku_id = await _seed_sku(postgres_url)
        po_id, line_id = await _create_approved_purchase_order(
            gr_client, gr_settings, postgres_url, scope, sku_id
        )
        location_id = await _seed_location(postgres_url, scope["mnl_01_id"])

        for index in range(3):
            await gr_client.post(
                f"/v1/procurement/purchase-orders/{po_id}/receipts",
                headers=auth(gr_settings, "receiver-mnl"),
                json={
                    "warehouse_id": scope["mnl_01_id"],
                    "location_id": location_id,
                    "receipt_number": f"GR-LIST-{index:03d}",
                    "lines": [
                        {
                            "purchase_order_line_id": line_id,
                            "received_quantity_base": "12",
                        }
                    ],
                },
            )

        response = await gr_client.get(
            "/v1/procurement/purchase-orders/receipts?limit=1&offset=1",
            headers=auth(gr_settings, "receiver-mnl"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 3
        assert len(body["items"]) == 1

    async def test_list_goods_receipts_requires_post_capability(
        self,
        gr_client: AsyncClient,
        gr_settings: Settings,
    ) -> None:
        await bootstrap_procurement(gr_client, gr_settings)
        response = await gr_client.get(
            "/v1/procurement/purchase-orders/receipts",
            headers=auth(gr_settings, "buyer-mnl"),
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "capability_required"
