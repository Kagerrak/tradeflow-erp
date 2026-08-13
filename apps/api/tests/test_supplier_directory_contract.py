from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def supplier_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def supplier_client(supplier_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(supplier_settings)
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


async def bootstrap_supplier_directory(
    client: AsyncClient,
    settings: Settings,
) -> None:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers={
            "Authorization": (
                f"Bearer {token(settings, 'bootstrapper', ['organization:bootstrap'])}"
            ),
            "Idempotency-Key": "supplier-directory-bootstrap",
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
                }
            ],
            "role_templates": [
                {
                    "code": "SALES",
                    "name": "Sales Representative",
                    "capabilities": [
                        "customer:read",
                        "sales:order-read",
                    ],
                },
                {
                    "code": "PROCUREMENT_MANAGER",
                    "name": "Procurement Manager",
                    "capabilities": [
                        "procurement:supplier-read",
                        "procurement:supplier-write",
                    ],
                },
                {
                    "code": "PROCUREMENT_READER",
                    "name": "Procurement Reader",
                    "capabilities": [
                        "procurement:supplier-read",
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
                },
                {
                    "subject": "procurement-mnl",
                    "display_name": "Manila Procurement Manager",
                    "role_template_codes": ["PROCUREMENT_MANAGER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "procurement-reader",
                    "display_name": "Manila Procurement Reader",
                    "role_template_codes": ["PROCUREMENT_READER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text


class TestCreateSupplier:
    async def test_create_supplier_returns_201(
        self,
        supplier_client: AsyncClient,
        supplier_settings: Settings,
    ) -> None:
        await bootstrap_supplier_directory(supplier_client, supplier_settings)

        response = await supplier_client.post(
            "/v1/procurement/suppliers",
            headers=auth(supplier_settings, "procurement-mnl"),
            json={
                "code": "ACME-001",
                "legal_name": "ACME Supplies Inc.",
                "tax_id": "123-456-789",
                "payment_terms": "Net 30",
                "default_currency": "PHP",
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["code"] == "ACME-001"
        assert body["legal_name"] == "ACME Supplies Inc."
        assert body["tax_id"] == "123-456-789"
        assert body["payment_terms"] == "Net 30"
        assert body["default_currency"] == "PHP"
        assert body["is_active"] is True
        assert body["version"] == 1
        assert "supplier_id" in body

    async def test_create_supplier_rejects_duplicate_code(
        self,
        supplier_client: AsyncClient,
        supplier_settings: Settings,
    ) -> None:
        await bootstrap_supplier_directory(supplier_client, supplier_settings)
        await supplier_client.post(
            "/v1/procurement/suppliers",
            headers=auth(supplier_settings, "procurement-mnl"),
            json={
                "code": "ACME-001",
                "legal_name": "ACME Supplies Inc.",
                "payment_terms": "Net 30",
                "default_currency": "PHP",
            },
        )

        response = await supplier_client.post(
            "/v1/procurement/suppliers",
            headers=auth(supplier_settings, "procurement-mnl"),
            json={
                "code": "ACME-001",
                "legal_name": "ACME Supplies Duplicate",
                "payment_terms": "Net 15",
                "default_currency": "PHP",
            },
        )

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "supplier_code_duplicate"

    async def test_create_supplier_requires_write_capability(
        self,
        supplier_client: AsyncClient,
        supplier_settings: Settings,
    ) -> None:
        await bootstrap_supplier_directory(supplier_client, supplier_settings)

        response = await supplier_client.post(
            "/v1/procurement/suppliers",
            headers=auth(supplier_settings, "procurement-reader"),
            json={
                "code": "ACME-002",
                "legal_name": "ACME Reader Blocked",
                "payment_terms": "Net 30",
                "default_currency": "PHP",
            },
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "capability_required"

    async def test_create_supplier_rejects_missing_fields(
        self,
        supplier_client: AsyncClient,
        supplier_settings: Settings,
    ) -> None:
        await bootstrap_supplier_directory(supplier_client, supplier_settings)

        response = await supplier_client.post(
            "/v1/procurement/suppliers",
            headers=auth(supplier_settings, "procurement-mnl"),
            json={"code": "ACME-003"},
        )

        assert response.status_code == 422, response.text


class TestListSuppliers:
    async def test_list_suppliers_is_empty_initially(
        self,
        supplier_client: AsyncClient,
        supplier_settings: Settings,
    ) -> None:
        await bootstrap_supplier_directory(supplier_client, supplier_settings)

        response = await supplier_client.get(
            "/v1/procurement/suppliers",
            headers=auth(supplier_settings, "procurement-reader"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_list_suppliers_returns_created_items(
        self,
        supplier_client: AsyncClient,
        supplier_settings: Settings,
    ) -> None:
        await bootstrap_supplier_directory(supplier_client, supplier_settings)
        created = await supplier_client.post(
            "/v1/procurement/suppliers",
            headers=auth(supplier_settings, "procurement-mnl"),
            json={
                "code": "BETA-001",
                "legal_name": "Beta Trading Ltd.",
                "payment_terms": "Net 15",
                "default_currency": "USD",
            },
        )
        assert created.status_code == 201

        response = await supplier_client.get(
            "/v1/procurement/suppliers",
            headers=auth(supplier_settings, "procurement-reader"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["code"] == "BETA-001"
        assert body["items"][0]["legal_name"] == "Beta Trading Ltd."
        assert body["items"][0]["default_currency"] == "USD"

    async def test_list_suppliers_searches_by_code(
        self,
        supplier_client: AsyncClient,
        supplier_settings: Settings,
    ) -> None:
        await bootstrap_supplier_directory(supplier_client, supplier_settings)
        for code, legal_name in [
            ("GAMMA-001", "Gamma Goods"),
            ("GAMMA-002", "Gamma Services"),
            ("DELTA-001", "Delta Distributors"),
        ]:
            created = await supplier_client.post(
                "/v1/procurement/suppliers",
                headers=auth(supplier_settings, "procurement-mnl"),
                json={
                    "code": code,
                    "legal_name": legal_name,
                    "payment_terms": "Net 30",
                    "default_currency": "PHP",
                },
            )
            assert created.status_code == 201

        response = await supplier_client.get(
            "/v1/procurement/suppliers?query=GAMMA",
            headers=auth(supplier_settings, "procurement-reader"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        assert {item["code"] for item in body["items"]} == {"GAMMA-001", "GAMMA-002"}

    async def test_list_suppliers_searches_by_legal_name(
        self,
        supplier_client: AsyncClient,
        supplier_settings: Settings,
    ) -> None:
        await bootstrap_supplier_directory(supplier_client, supplier_settings)
        for code, legal_name in [
            ("EPS-001", "Epsilon Supplies"),
            ("ZET-001", "Zeta Wholesale"),
        ]:
            created = await supplier_client.post(
                "/v1/procurement/suppliers",
                headers=auth(supplier_settings, "procurement-mnl"),
                json={
                    "code": code,
                    "legal_name": legal_name,
                    "payment_terms": "Net 30",
                    "default_currency": "PHP",
                },
            )
            assert created.status_code == 201

        response = await supplier_client.get(
            "/v1/procurement/suppliers?query=wholesale",
            headers=auth(supplier_settings, "procurement-reader"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["code"] == "ZET-001"

    async def test_list_suppliers_paginates(
        self,
        supplier_client: AsyncClient,
        supplier_settings: Settings,
    ) -> None:
        await bootstrap_supplier_directory(supplier_client, supplier_settings)
        for index in range(3):
            created = await supplier_client.post(
                "/v1/procurement/suppliers",
                headers=auth(supplier_settings, "procurement-mnl"),
                json={
                    "code": f"PAG-{index:03d}",
                    "legal_name": f"Pagination Supplier {index}",
                    "payment_terms": "Net 30",
                    "default_currency": "PHP",
                },
            )
            assert created.status_code == 201

        response = await supplier_client.get(
            "/v1/procurement/suppliers?limit=2&offset=0",
            headers=auth(supplier_settings, "procurement-reader"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2

        offset_response = await supplier_client.get(
            "/v1/procurement/suppliers?limit=2&offset=2",
            headers=auth(supplier_settings, "procurement-reader"),
        )

        assert offset_response.status_code == 200
        offset_body = offset_response.json()
        assert len(offset_body["items"]) == 1

    async def test_list_suppliers_requires_read_capability(
        self,
        supplier_client: AsyncClient,
        supplier_settings: Settings,
    ) -> None:
        await bootstrap_supplier_directory(supplier_client, supplier_settings)

        response = await supplier_client.get(
            "/v1/procurement/suppliers",
            headers=auth(supplier_settings, "sales-mnl"),
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "capability_required"

    async def test_unauthenticated_requests_are_rejected(
        self,
        supplier_client: AsyncClient,
        supplier_settings: Settings,
    ) -> None:
        await bootstrap_supplier_directory(supplier_client, supplier_settings)

        response = await supplier_client.get("/v1/procurement/suppliers")
        assert response.status_code == 401

        post_response = await supplier_client.post(
            "/v1/procurement/suppliers",
            json={
                "code": "UNAUTH-001",
                "legal_name": "Unauthenticated Supplier",
                "payment_terms": "Net 30",
                "default_currency": "PHP",
            },
        )
        assert post_response.status_code == 401
