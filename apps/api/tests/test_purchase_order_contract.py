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
def po_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def po_client(po_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(po_settings)
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
            "Idempotency-Key": "purchase-order-bootstrap",
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
                {
                    "code": "PROCUREMENT_READER",
                    "name": "Procurement Reader",
                    "capabilities": [
                        "procurement:purchase-order-read",
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
                    "subject": "buyer-mnl",
                    "display_name": "Manila Buyer",
                    "role_template_codes": ["PROCUREMENT_BUYER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "buyer-ceb",
                    "display_name": "Cebu Buyer",
                    "role_template_codes": ["PROCUREMENT_BUYER"],
                    "branch_codes": ["CEB"],
                    "warehouse_codes": ["CEB-01"],
                },
                {
                    "subject": "procurement-reader",
                    "display_name": "Procurement Reader",
                    "role_template_codes": ["PROCUREMENT_READER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {
        "branch_id": str(body["branches"][0]["branch_id"]),
        "ceb_branch_id": str(body["branches"][1]["branch_id"]),
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
    base_stocking_unit: str = "pcs",
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
                        'untracked',
                        'procurement-mnl'
                    ) RETURNING sku_id"""
                ),
                {
                    "product_id": product_id,
                    "code": code,
                    "name": f"Test SKU {code}",
                    "base_unit": base_stocking_unit,
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


class TestCreatePurchaseOrder:
    async def test_create_purchase_order_returns_201(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)

        response = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-001",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "10",
                        "unit_code": "box",
                        "unit_cost": "150.00",
                    }
                ],
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["code"] == "PO-001"
        assert body["currency"] == "PHP"
        assert body["status"] == "draft"
        assert body["version"] == 1
        assert body["supplier_id"] == supplier_id
        assert body["branch_id"] == scope["branch_id"]
        assert len(body["lines"]) == 1
        assert body["lines"][0]["unit_code"] == "box"
        assert Decimal(body["lines"][0]["base_quantity"]) == Decimal("120")

    async def test_create_purchase_order_rejects_duplicate_code(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)
        payload = {
            "supplier_id": supplier_id,
            "branch_id": scope["branch_id"],
            "code": "PO-002",
            "currency": "PHP",
            "lines": [
                {
                    "sku_id": sku_id,
                    "requested_quantity": "5",
                    "unit_code": "box",
                    "unit_cost": "100.00",
                }
            ],
        }
        first = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json=payload,
        )
        assert first.status_code == 201

        second = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json=payload,
        )

        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "purchase_order_code_duplicate"

    async def test_create_purchase_order_requires_write_capability(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)

        response = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "procurement-reader"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-003",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "1",
                        "unit_code": "box",
                        "unit_cost": "10.00",
                    }
                ],
            },
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "capability_required"

    async def test_create_purchase_order_enforces_branch_scope(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)

        response = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-ceb"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-004",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "1",
                        "unit_code": "box",
                        "unit_cost": "10.00",
                    }
                ],
            },
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "branch_scope_required"

    async def test_create_purchase_order_rejects_missing_unit_conversion(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)

        response = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-005",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "1",
                        "unit_code": "pallet",
                        "unit_cost": "10.00",
                    }
                ],
            },
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "unit_conversion_missing"


class TestApprovePurchaseOrder:
    async def test_approve_purchase_order_transitions_status(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-010",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "10",
                        "unit_code": "box",
                        "unit_cost": "150.00",
                    }
                ],
            },
        )
        assert created.status_code == 201
        po_id = created.json()["purchase_order_id"]

        response = await po_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/approve",
            headers=auth(po_settings, "procurement-mnl"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "approved"
        assert body["version"] == 2

    async def test_approve_purchase_order_requires_approver_capability(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-011",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "10",
                        "unit_code": "box",
                        "unit_cost": "150.00",
                    }
                ],
            },
        )
        assert created.status_code == 201
        po_id = created.json()["purchase_order_id"]

        response = await po_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/approve",
            headers=auth(po_settings, "buyer-mnl"),
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "capability_required"

    async def test_approve_purchase_order_rejects_non_draft(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-012",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "10",
                        "unit_code": "box",
                        "unit_cost": "150.00",
                    }
                ],
            },
        )
        assert created.status_code == 201
        po_id = created.json()["purchase_order_id"]
        approved = await po_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/approve",
            headers=auth(po_settings, "procurement-mnl"),
        )
        assert approved.status_code == 200

        second = await po_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/approve",
            headers=auth(po_settings, "procurement-mnl"),
        )

        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "purchase_order_not_draft"


class TestListAndFetchPurchaseOrder:
    async def test_list_purchase_orders_is_branch_scoped(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)
        await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-020",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "10",
                        "unit_code": "box",
                        "unit_cost": "150.00",
                    }
                ],
            },
        )

        response = await po_client.get(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["code"] == "PO-020"

    async def test_list_purchase_orders_hides_other_branches(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)
        await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-021",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "10",
                        "unit_code": "box",
                        "unit_cost": "150.00",
                    }
                ],
            },
        )

        response = await po_client.get(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-ceb"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 0

    async def test_fetch_purchase_order_returns_lines(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-022",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "10",
                        "unit_code": "box",
                        "unit_cost": "150.00",
                    }
                ],
            },
        )
        assert created.status_code == 201
        po_id = created.json()["purchase_order_id"]

        response = await po_client.get(
            f"/v1/procurement/purchase-orders/{po_id}",
            headers=auth(po_settings, "buyer-mnl"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["code"] == "PO-022"
        assert len(body["lines"]) == 1

    async def test_fetch_purchase_order_enforces_branch_scope(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-023",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "10",
                        "unit_code": "box",
                        "unit_cost": "150.00",
                    }
                ],
            },
        )
        assert created.status_code == 201
        po_id = created.json()["purchase_order_id"]

        response = await po_client.get(
            f"/v1/procurement/purchase-orders/{po_id}",
            headers=auth(po_settings, "buyer-ceb"),
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "branch_scope_required"

    async def test_list_purchase_orders_filters_by_status(
        self,
        po_client: AsyncClient,
        po_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_procurement(po_client, po_settings)
        supplier_id = await _seed_supplier(po_client, po_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await po_client.post(
            "/v1/procurement/purchase-orders",
            headers=auth(po_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PO-024",
                "currency": "PHP",
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "10",
                        "unit_code": "box",
                        "unit_cost": "150.00",
                    }
                ],
            },
        )
        assert created.status_code == 201
        po_id = created.json()["purchase_order_id"]
        await po_client.post(
            f"/v1/procurement/purchase-orders/{po_id}/approve",
            headers=auth(po_settings, "procurement-mnl"),
        )

        response = await po_client.get(
            "/v1/procurement/purchase-orders?status=approved",
            headers=auth(po_settings, "buyer-mnl"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "approved"
