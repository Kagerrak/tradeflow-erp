from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def pr_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def pr_client(pr_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(pr_settings)
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


async def bootstrap_purchase_requests(
    client: AsyncClient,
    settings: Settings,
) -> dict[str, str]:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers={
            "Authorization": (
                f"Bearer {token(settings, 'bootstrapper', ['organization:bootstrap'])}"
            ),
            "Idempotency-Key": "purchase-request-bootstrap",
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
                        "procurement:purchase-request-read",
                        "procurement:purchase-request-write",
                        "procurement:purchase-request-approve",
                        "procurement:purchase-order-read",
                        "procurement:purchase-order-write",
                    ],
                },
                {
                    "code": "PROCUREMENT_BUYER",
                    "name": "Procurement Buyer",
                    "capabilities": [
                        "procurement:supplier-read",
                        "procurement:purchase-request-read",
                        "procurement:purchase-request-write",
                    ],
                },
                {
                    "code": "PROCUREMENT_READER",
                    "name": "Procurement Reader",
                    "capabilities": ["procurement:purchase-request-read"],
                },
            ],
            "users": [
                {
                    "subject": "manager-mnl",
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
                    "subject": "reader-mnl",
                    "display_name": "Manila Reader",
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
        headers=auth(settings, "manager-mnl"),
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
                    "VALUES (gen_random_uuid(), :code, :name, 'manager-mnl') "
                    "RETURNING product_id"
                ),
                {"code": f"PROD-{code}", "name": f"Test Product {code}"},
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
                        'manager-mnl'
                    ) RETURNING sku_id"""
                ),
                {
                    "product_id": product_id,
                    "code": code,
                    "name": f"Test SKU {code}",
                    "base_unit": "pcs",
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
                    'manager-mnl'
                )"""
            ),
            {"sku_id": sku_id},
        )
    await engine.dispose()
    assert sku_id is not None
    return sku_id


async def _seed_approval_authority(
    postgres_url: str,
    user_subject: str,
    branch_id: str,
    maximum_amount: str | None = None,
) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """INSERT INTO approval_authorities (
                    approval_authority_id,
                    user_subject,
                    capability_code,
                    branch_id,
                    maximum_amount
                ) VALUES (
                    gen_random_uuid(),
                    :user_subject,
                    'procurement:purchase-request-approve',
                    :branch_id,
                    :maximum_amount
                )"""
            ),
            {
                "user_subject": user_subject,
                "branch_id": UUID(branch_id),
                "maximum_amount": Decimal(maximum_amount) if maximum_amount else None,
            },
        )
    await engine.dispose()


async def _create_request(
    client: AsyncClient,
    settings: Settings,
    branch_id: str,
    supplier_id: str,
    sku_id: str,
    code: str = "PR-001",
    quantity: str = "10",
    idempotency_key: str = "pr-create-001",
    actor_subject: str = "buyer-mnl",
) -> dict[str, object]:
    response = await client.post(
        "/v1/procurement/purchase-requests",
        headers=auth(
            settings,
            actor_subject,
            **{"Idempotency-Key": idempotency_key},
        ),
        json={
            "supplier_id": supplier_id,
            "branch_id": branch_id,
            "code": code,
            "currency": "PHP",
            "lines": [
                {
                    "sku_id": sku_id,
                    "requested_quantity": quantity,
                    "unit_code": "box",
                    "unit_cost": "150.00",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestCreatePurchaseRequest:
    async def test_create_returns_201(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)

        response = await pr_client.post(
            "/v1/procurement/purchase-requests",
            headers=auth(pr_settings, "buyer-mnl", **{"Idempotency-Key": "pr-create-001"}),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PR-001",
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
        assert body["code"] == "PR-001"
        assert body["status"] == "draft"
        assert body["version"] == 1
        assert Decimal(body["lines"][0]["base_quantity"]) == Decimal("120")
        assert body["lines"][0]["open_quantity"] == "10"

    async def test_create_rejects_duplicate_code(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        payload = {
            "supplier_id": supplier_id,
            "branch_id": scope["branch_id"],
            "code": "PR-002",
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
        first = await pr_client.post(
            "/v1/procurement/purchase-requests",
            headers=auth(pr_settings, "buyer-mnl", **{"Idempotency-Key": "pr-dup-1"}),
            json=payload,
        )
        assert first.status_code == 201

        second = await pr_client.post(
            "/v1/procurement/purchase-requests",
            headers=auth(pr_settings, "buyer-mnl", **{"Idempotency-Key": "pr-dup-2"}),
            json=payload,
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "purchase_request_code_duplicate"

    async def test_create_requires_write_capability(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)

        response = await pr_client.post(
            "/v1/procurement/purchase-requests",
            headers=auth(pr_settings, "reader-mnl", **{"Idempotency-Key": "pr-unauth"}),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PR-003",
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
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "capability_required"

    async def test_create_enforces_branch_scope(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)

        response = await pr_client.post(
            "/v1/procurement/purchase-requests",
            headers=auth(pr_settings, "buyer-ceb", **{"Idempotency-Key": "pr-scope"}),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "code": "PR-004",
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
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "branch_scope_required"

    async def test_create_replays_idempotent_request(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        payload = {
            "supplier_id": supplier_id,
            "branch_id": scope["branch_id"],
            "code": "PR-005",
            "currency": "PHP",
            "lines": [
                {
                    "sku_id": sku_id,
                    "requested_quantity": "2",
                    "unit_code": "box",
                    "unit_cost": "50.00",
                }
            ],
        }
        first = await pr_client.post(
            "/v1/procurement/purchase-requests",
            headers=auth(pr_settings, "buyer-mnl", **{"Idempotency-Key": "pr-idem"}),
            json=payload,
        )
        assert first.status_code == 201
        pr_id = first.json()["purchase_request_id"]

        second = await pr_client.post(
            "/v1/procurement/purchase-requests",
            headers=auth(pr_settings, "buyer-mnl", **{"Idempotency-Key": "pr-idem"}),
            json=payload,
        )
        assert second.status_code == 200
        assert second.headers["x-idempotency-replayed"] == "true"
        assert second.json()["purchase_request_id"] == pr_id


class TestListAndGetPurchaseRequest:
    async def test_list_and_get_by_id(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
        )

        list_response = await pr_client.get(
            "/v1/procurement/purchase-requests",
            headers=auth(pr_settings, "reader-mnl"),
        )
        assert list_response.status_code == 200
        body = list_response.json()
        assert body["total"] == 1
        assert body["items"][0]["code"] == "PR-001"

        get_response = await pr_client.get(
            f"/v1/procurement/purchase-requests/{created['purchase_request_id']}",
            headers=auth(pr_settings, "reader-mnl"),
        )
        assert get_response.status_code == 200
        assert get_response.json()["code"] == "PR-001"

    async def test_list_filters_by_status(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            code="PR-FILTER",
            idempotency_key="pr-filter",
        )

        response = await pr_client.get(
            "/v1/procurement/purchase-requests?status=approved",
            headers=auth(pr_settings, "reader-mnl"),
        )
        assert response.status_code == 200
        assert response.json()["total"] == 0

    async def test_get_enforces_branch_scope(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-get-scope",
        )

        response = await pr_client.get(
            f"/v1/procurement/purchase-requests/{created['purchase_request_id']}",
            headers=auth(pr_settings, "buyer-ceb"),
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "branch_scope_required"


class TestRevisePurchaseRequest:
    async def test_revise_replaces_lines_and_increments_version(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-revise",
        )
        pr_id = created["purchase_request_id"]

        response = await pr_client.put(
            f"/v1/procurement/purchase-requests/{pr_id}",
            headers=auth(pr_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "currency": "PHP",
                "exchange_rate": "1",
                "expected_version": 1,
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "20",
                        "unit_code": "box",
                        "unit_cost": "200.00",
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["version"] == 2
        assert body["status"] == "draft"
        assert body["lines"][0]["requested_quantity"] == "20"
        assert Decimal(body["lines"][0]["base_quantity"]) == Decimal("240")

    async def test_revise_rejects_version_conflict(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-revise-conflict",
        )
        pr_id = created["purchase_request_id"]

        response = await pr_client.put(
            f"/v1/procurement/purchase-requests/{pr_id}",
            headers=auth(pr_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "currency": "PHP",
                "exchange_rate": "1",
                "expected_version": 2,
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "1",
                        "unit_code": "box",
                        "unit_cost": "1.00",
                    }
                ],
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "purchase_request_version_conflict"


class TestApprovePurchaseRequest:
    async def test_approve_transitions_status(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-approve",
        )
        pr_id = created["purchase_request_id"]
        await _seed_approval_authority(postgres_url, "manager-mnl", scope["branch_id"], "2000")

        response = await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/approve",
            headers=auth(pr_settings, "manager-mnl"),
            json={"expected_version": 1},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "approved"
        assert body["version"] == 2
        assert body["approved_by"] == "manager-mnl"

    async def test_approve_rejects_maker_checker(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-maker",
            actor_subject="manager-mnl",
        )
        pr_id = created["purchase_request_id"]
        await _seed_approval_authority(postgres_url, "manager-mnl", scope["branch_id"], "2000")

        response = await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/approve",
            headers=auth(pr_settings, "manager-mnl"),
            json={"expected_version": 1},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "purchase_request_maker_checker"

    async def test_approve_enforces_authority_limit(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-limit",
        )
        pr_id = created["purchase_request_id"]
        await _seed_approval_authority(postgres_url, "manager-mnl", scope["branch_id"], "1000")

        response = await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/approve",
            headers=auth(pr_settings, "manager-mnl"),
            json={"expected_version": 1},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "approval_amount_exceeded"


class TestRejectPurchaseRequest:
    async def test_reject_transitions_status(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-reject",
        )
        pr_id = created["purchase_request_id"]

        response = await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/reject",
            headers=auth(pr_settings, "manager-mnl"),
            json={"expected_version": 1},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "rejected"
        assert body["rejected_by"] == "manager-mnl"

    async def test_rejected_request_can_be_revised(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-reject-revise",
        )
        pr_id = created["purchase_request_id"]
        await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/reject",
            headers=auth(pr_settings, "manager-mnl"),
            json={"expected_version": 1},
        )

        response = await pr_client.put(
            f"/v1/procurement/purchase-requests/{pr_id}",
            headers=auth(pr_settings, "buyer-mnl"),
            json={
                "supplier_id": supplier_id,
                "branch_id": scope["branch_id"],
                "currency": "PHP",
                "exchange_rate": "1",
                "expected_version": 2,
                "lines": [
                    {
                        "sku_id": sku_id,
                        "requested_quantity": "3",
                        "unit_code": "box",
                        "unit_cost": "30.00",
                    }
                ],
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "draft"


class TestConvertPurchaseRequest:
    async def test_full_conversion_creates_draft_po(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-convert-full",
        )
        pr_id = created["purchase_request_id"]
        await _seed_approval_authority(postgres_url, "manager-mnl", scope["branch_id"], "2000")
        await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/approve",
            headers=auth(pr_settings, "manager-mnl"),
            json={"expected_version": 1},
        )

        response = await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/conversions",
            headers=auth(
                pr_settings,
                "manager-mnl",
                **{"Idempotency-Key": "pr-convert-full-po"},
            ),
            json={
                "purchase_order_code": "PO-FULL-001",
                "expected_version": 2,
                "lines": [
                    {
                        "purchase_request_line_id": created["lines"][0]["purchase_request_line_id"],
                        "requested_quantity": "10",
                    }
                ],
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["purchase_order_code"] == "PO-FULL-001"
        assert body["status"] == "draft"

        request_response = await pr_client.get(
            f"/v1/procurement/purchase-requests/{pr_id}",
            headers=auth(pr_settings, "reader-mnl"),
        )
        request_body = request_response.json()
        assert request_body["status"] == "fully_converted"
        assert request_body["lines"][0]["converted_quantity"] == "10"
        assert request_body["lines"][0]["open_quantity"] == "0"

    async def test_partial_conversion(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-convert-partial",
        )
        pr_id = created["purchase_request_id"]
        line_id = created["lines"][0]["purchase_request_line_id"]
        await _seed_approval_authority(postgres_url, "manager-mnl", scope["branch_id"], "2000")
        await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/approve",
            headers=auth(pr_settings, "manager-mnl"),
            json={"expected_version": 1},
        )

        first = await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/conversions",
            headers=auth(
                pr_settings,
                "manager-mnl",
                **{"Idempotency-Key": "pr-convert-partial-1"},
            ),
            json={
                "purchase_order_code": "PO-PART-001",
                "expected_version": 2,
                "lines": [
                    {
                        "purchase_request_line_id": line_id,
                        "requested_quantity": "4",
                    }
                ],
            },
        )
        assert first.status_code == 201

        second = await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/conversions",
            headers=auth(
                pr_settings,
                "manager-mnl",
                **{"Idempotency-Key": "pr-convert-partial-2"},
            ),
            json={
                "purchase_order_code": "PO-PART-002",
                "expected_version": 3,
                "lines": [
                    {
                        "purchase_request_line_id": line_id,
                        "requested_quantity": "6",
                    }
                ],
            },
        )
        assert second.status_code == 201

        request_response = await pr_client.get(
            f"/v1/procurement/purchase-requests/{pr_id}",
            headers=auth(pr_settings, "reader-mnl"),
        )
        request_body = request_response.json()
        assert request_body["status"] == "fully_converted"
        assert request_body["lines"][0]["converted_quantity"] == "10"

    async def test_conversion_rejects_over_conversion(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-convert-over",
        )
        pr_id = created["purchase_request_id"]
        line_id = created["lines"][0]["purchase_request_line_id"]
        await _seed_approval_authority(postgres_url, "manager-mnl", scope["branch_id"], "2000")
        await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/approve",
            headers=auth(pr_settings, "manager-mnl"),
            json={"expected_version": 1},
        )

        response = await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/conversions",
            headers=auth(
                pr_settings,
                "manager-mnl",
                **{"Idempotency-Key": "pr-convert-over-po"},
            ),
            json={
                "purchase_order_code": "PO-OVER-001",
                "expected_version": 2,
                "lines": [
                    {
                        "purchase_request_line_id": line_id,
                        "requested_quantity": "11",
                    }
                ],
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "purchase_request_overconverted"

    async def test_conversion_replays_idempotent_request(
        self,
        pr_client: AsyncClient,
        pr_settings: Settings,
        postgres_url: str,
    ) -> None:
        scope = await bootstrap_purchase_requests(pr_client, pr_settings)
        supplier_id = await _seed_supplier(pr_client, pr_settings)
        sku_id = await _seed_sku(postgres_url)
        created = await _create_request(
            pr_client,
            pr_settings,
            scope["branch_id"],
            supplier_id,
            sku_id,
            idempotency_key="pr-convert-idem-req",
        )
        pr_id = created["purchase_request_id"]
        line_id = created["lines"][0]["purchase_request_line_id"]
        await _seed_approval_authority(postgres_url, "manager-mnl", scope["branch_id"], "2000")
        await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/approve",
            headers=auth(pr_settings, "manager-mnl"),
            json={"expected_version": 1},
        )
        payload = {
            "purchase_order_code": "PO-IDEM-001",
            "expected_version": 2,
            "lines": [
                {
                    "purchase_request_line_id": line_id,
                    "requested_quantity": "2",
                }
            ],
        }
        first = await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/conversions",
            headers=auth(
                pr_settings,
                "manager-mnl",
                **{"Idempotency-Key": "pr-convert-idem"},
            ),
            json=payload,
        )
        assert first.status_code == 201
        po_id = first.json()["purchase_order_id"]

        second = await pr_client.post(
            f"/v1/procurement/purchase-requests/{pr_id}/conversions",
            headers=auth(
                pr_settings,
                "manager-mnl",
                **{"Idempotency-Key": "pr-convert-idem"},
            ),
            json=payload,
        )
        assert second.status_code == 200
        assert second.headers["x-idempotency-replayed"] == "true"
        assert second.json()["purchase_order_id"] == po_id
