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
def lc_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def lc_client(lc_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(lc_settings)
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
            "Idempotency-Key": "landed-cost-bootstrap",
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
                    "code": "PROCUREMENT_MANAGER",
                    "name": "Procurement Manager",
                    "capabilities": [
                        "procurement:supplier-read",
                        "procurement:supplier-write",
                        "procurement:purchase-order-read",
                        "procurement:purchase-order-write",
                        "procurement:purchase-order-approve",
                        "procurement:goods-receipt-post",
                        "procurement:landed-cost-allocate",
                    ],
                },
                {
                    "code": "LANDED_COST_ACCOUNTANT",
                    "name": "Landed Cost Accountant",
                    "capabilities": [
                        "procurement:landed-cost-allocate",
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
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "landed-cost-mnl",
                    "display_name": "Manila Landed Cost Accountant",
                    "role_template_codes": ["LANDED_COST_ACCOUNTANT"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "landed-cost-ceb",
                    "display_name": "Cebu Landed Cost Accountant",
                    "role_template_codes": ["LANDED_COST_ACCOUNTANT"],
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
        "ceb_01_id": warehouses["CEB-01"],
    }


async def _seed_supplier(
    client: AsyncClient,
    settings: Settings,
    code: str,
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


async def _seed_sku(postgres_url: str, code: str = "SKU-001") -> str:
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
                {"code": f"PROD-{code}", "name": f"Test Product {code}"},
            )
        )
        sku_id = str(
            await connection.scalar(
                text(
                    """INSERT INTO skus (
                        sku_id, product_id, code, name, base_stocking_unit,
                        tracking_policy, created_by
                    ) VALUES (
                        gen_random_uuid(), :product_id, :code, :name, 'pcs',
                        'untracked', 'procurement-mnl'
                    ) RETURNING sku_id"""
                ),
                {"product_id": product_id, "code": code, "name": f"Test SKU {code}"},
            )
        )
        await connection.execute(
            text(
                """INSERT INTO unit_conversions (
                    unit_conversion_id, sku_id, unit_code, base_quantity,
                    effective_from, created_by
                ) VALUES (
                    gen_random_uuid(), :sku_id, 'box', 12, CURRENT_DATE,
                    'procurement-mnl'
                )"""
            ),
            {"sku_id": sku_id},
        )
    await engine.dispose()
    assert sku_id is not None
    return sku_id


async def _seed_location(postgres_url: str, warehouse_id: str) -> str:
    engine = create_async_engine(postgres_url)
    location_id = None
    async with engine.begin() as connection:
        location_id = str(
            await connection.scalar(
                text(
                    """INSERT INTO warehouse_stock_locations (
                        location_id, warehouse_id, code, name, custody, created_by
                    ) VALUES (
                        gen_random_uuid(), :warehouse_id, 'RCV-01',
                        'Receiving RCV-01', 'available', 'procurement-mnl'
                    ) RETURNING location_id"""
                ),
                {"warehouse_id": warehouse_id},
            )
        )
    await engine.dispose()
    assert location_id is not None
    return location_id


async def _create_goods_receipt(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
    scope: dict[str, str],
    sku_id: str,
    code: str = "PO-LC-001",
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
    po_id = created.json()["purchase_order_id"]
    line_id = created.json()["lines"][0]["purchase_order_line_id"]
    approved = await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/approve",
        headers=auth(settings, "procurement-mnl"),
    )
    assert approved.status_code == 200, approved.text
    location_id = await _seed_location(postgres_url, scope["mnl_01_id"])
    receipt = await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/receipts",
        headers=auth(settings, "procurement-mnl"),
        json={
            "warehouse_id": scope["mnl_01_id"],
            "location_id": location_id,
            "receipt_number": f"GR-{code}",
            "lines": [
                {
                    "purchase_order_line_id": line_id,
                    "received_quantity_base": str(int(requested_quantity) * 12),
                }
            ],
        },
    )
    assert receipt.status_code == 201, receipt.text
    return po_id, receipt.json()["goods_receipt_id"]


class TestCreateLandedCost:
    async def test_allocate_landed_cost_by_line_value(
        self,
        lc_client: AsyncClient,
        lc_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(lc_client, lc_settings)
        sku_id = await _seed_sku(postgres_url)
        _, goods_receipt_id = await _create_goods_receipt(
            lc_client, lc_settings, postgres_url, scope, sku_id
        )

        response = await lc_client.post(
            f"/v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs",
            headers=auth(lc_settings, "landed-cost-mnl"),
            json={
                "charges": [
                    {"charge_type": "freight", "amount_base": "1200.00"},
                    {"charge_type": "insurance", "amount_base": "600.00"},
                ]
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["goods_receipt_id"] == goods_receipt_id
        assert body["base_currency"] == "PHP"
        assert len(body["charges"]) == 2
        total_allocated = sum(
            Decimal(allocation["allocated_amount_base"])
            for charge in body["charges"]
            for allocation in charge["allocations"]
        )
        assert total_allocated == Decimal("1800.00")

        line = body["lines"][0]
        assert Decimal(line["total_allocated_landed_cost"]) == Decimal("1800.00")
        assert Decimal(line["original_line_value"]) == Decimal("18000.00")

    async def test_allocate_landed_cost_requires_capability(
        self,
        lc_client: AsyncClient,
        lc_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(lc_client, lc_settings)
        sku_id = await _seed_sku(postgres_url)
        _, goods_receipt_id = await _create_goods_receipt(
            lc_client, lc_settings, postgres_url, scope, sku_id
        )

        response = await lc_client.post(
            f"/v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs",
            headers=auth(lc_settings, "buyer-mnl"),
            json={"charges": [{"charge_type": "freight", "amount_base": "100.00"}]},
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "capability_required"

    async def test_allocate_landed_cost_enforces_branch_scope(
        self,
        lc_client: AsyncClient,
        lc_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(lc_client, lc_settings)
        sku_id = await _seed_sku(postgres_url)
        _, goods_receipt_id = await _create_goods_receipt(
            lc_client, lc_settings, postgres_url, scope, sku_id
        )

        response = await lc_client.post(
            f"/v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs",
            headers=auth(lc_settings, "landed-cost-ceb"),
            json={"charges": [{"charge_type": "freight", "amount_base": "100.00"}]},
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "branch_scope_required"

    async def test_allocate_landed_cost_rejects_invalid_charge_type(
        self,
        lc_client: AsyncClient,
        lc_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(lc_client, lc_settings)
        sku_id = await _seed_sku(postgres_url)
        _, goods_receipt_id = await _create_goods_receipt(
            lc_client, lc_settings, postgres_url, scope, sku_id
        )

        response = await lc_client.post(
            f"/v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs",
            headers=auth(lc_settings, "landed-cost-mnl"),
            json={"charges": [{"charge_type": "discount", "amount_base": "100.00"}]},
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "landed_cost_charge_type_invalid"

    async def test_get_landed_costs_returns_allocated_totals(
        self,
        lc_client: AsyncClient,
        lc_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(lc_client, lc_settings)
        sku_id = await _seed_sku(postgres_url)
        _, goods_receipt_id = await _create_goods_receipt(
            lc_client, lc_settings, postgres_url, scope, sku_id
        )
        posted = await lc_client.post(
            f"/v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs",
            headers=auth(lc_settings, "landed-cost-mnl"),
            json={"charges": [{"charge_type": "customs", "amount_base": "300.00"}]},
        )
        assert posted.status_code == 201, posted.text

        response = await lc_client.get(
            f"/v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs",
            headers=auth(lc_settings, "landed-cost-mnl"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["goods_receipt_id"] == goods_receipt_id
        assert len(body["charges"]) == 1
        assert body["charges"][0]["charge_type"] == "customs"
        assert Decimal(body["lines"][0]["total_allocated_landed_cost"]) == Decimal("300.00")

    async def test_allocate_landed_cost_updates_valuation(
        self,
        lc_client: AsyncClient,
        lc_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(lc_client, lc_settings)
        sku_id = await _seed_sku(postgres_url)
        _, goods_receipt_id = await _create_goods_receipt(
            lc_client, lc_settings, postgres_url, scope, sku_id
        )

        response = await lc_client.post(
            f"/v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs",
            headers=auth(lc_settings, "landed-cost-mnl"),
            json={"charges": [{"charge_type": "freight", "amount_base": "1200.00"}]},
        )
        assert response.status_code == 201, response.text

        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            valuation = await connection.execute(
                text(
                    """SELECT quantity_on_hand, inventory_value,
                               moving_average_unit_cost
                          FROM inventory_valuation
                         WHERE sku_id = :sku_id
                           AND warehouse_id = :warehouse_id"""
                ),
                {"sku_id": sku_id, "warehouse_id": scope["mnl_01_id"]},
            )
            row = valuation.mappings().one()
        await engine.dispose()

        assert Decimal(row["quantity_on_hand"]) == Decimal("120")
        assert Decimal(row["inventory_value"]) == Decimal("19200.00")
        assert Decimal(row["moving_average_unit_cost"]) == Decimal("160.000000")
