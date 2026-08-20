from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


def _token(settings: Settings, subject: str, capabilities: list[str] | None = None) -> str:
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


def _auth(settings: Settings, subject: str, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(settings, subject)}", **headers}


@pytest.fixture
def adjustment_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def adjustment_client(adjustment_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(adjustment_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def _bootstrap_adjustment_environment(
    client: AsyncClient,
    settings: Settings,
) -> dict[str, object]:
    bootstrap = await client.post(
        "/v1/organization/bootstrap",
        headers={
            "Authorization": (
                f"Bearer {_token(settings, 'bootstrapper', ['organization:bootstrap'])}"
            ),
            "Idempotency-Key": "inventory-adjustment-bootstrap",
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
            ],
            "role_templates": [
                {
                    "code": "ADJUSTER",
                    "name": "Inventory Adjuster",
                    "capabilities": [
                        "catalog:write",
                        "inventory:read",
                        "inventory:post",
                        "inventory:rebuild",
                        "inventory:adjustment-request",
                        "inventory:adjustment-approve",
                    ],
                },
                {
                    "code": "ADJUSTMENT_APPROVER",
                    "name": "Inventory Adjustment Approver",
                    "capabilities": [
                        "inventory:read",
                        "inventory:adjustment-approve",
                    ],
                },
            ],
            "users": [
                {
                    "subject": "adjuster-mnl",
                    "display_name": "Manila Adjuster",
                    "role_template_codes": ["ADJUSTER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "approver-mnl",
                    "display_name": "Manila Adjustment Approver",
                    "role_template_codes": ["ADJUSTMENT_APPROVER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "approver-low-limit",
                    "display_name": "Low Limit Approver",
                    "role_template_codes": ["ADJUSTMENT_APPROVER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "adjuster-no-scope",
                    "display_name": "No Scope Adjuster",
                    "role_template_codes": ["ADJUSTER"],
                    "branch_codes": [],
                    "warehouse_codes": [],
                },
            ],
        },
    )
    assert bootstrap.status_code == 201, bootstrap.text
    scope = await client.get(
        "/v1/organization/scope",
        headers=_auth(settings, "adjuster-mnl"),
    )
    assert scope.status_code == 200, scope.text
    scope_data = scope.json()
    warehouse = next(w for w in scope_data["warehouses"] if w["code"] == "MNL-01")
    branch = next(b for b in scope_data["branches"] if b["code"] == "MNL")

    sku = await client.post(
        "/v1/catalog/skus",
        headers=_auth(
            settings,
            "adjuster-mnl",
            **{"Idempotency-Key": f"adjustment-sku-{uuid4()}"},
        ),
        json={
            "product_code": "ADJ-BEV",
            "product_name": "Adjustment Beverages",
            "sku_code": "ADJ-COLA-330",
            "sku_name": "Adjustment Cola 330 mL",
            "base_stocking_unit": "EA",
            "tracking_policy": "untracked",
            "expiration_control": False,
            "conversions": [],
            "barcodes": [],
        },
    )
    assert sku.status_code == 201, sku.text
    sku_data = sku.json()

    location = await client.post(
        "/v1/inventory/locations",
        headers=_auth(
            settings,
            "adjuster-mnl",
            **{"Idempotency-Key": f"adjustment-loc-{uuid4()}"},
        ),
        json={
            "warehouse_id": warehouse["warehouse_id"],
            "code": "MNL-AVAILABLE",
            "name": "Manila Available",
            "custody": "available",
        },
    )
    assert location.status_code == 201, location.text
    location_data = location.json()

    opening = await client.post(
        "/v1/inventory/opening-stock",
        headers=_auth(
            settings,
            "adjuster-mnl",
            **{"Idempotency-Key": f"adjustment-opening-{uuid4()}"},
        ),
        json={
            "sku_id": sku_data["sku_id"],
            "warehouse_id": warehouse["warehouse_id"],
            "location_id": location_data["location_id"],
            "quantity": "100.000000",
            "unit_code": "EA",
            "unit_cost": "10.000000",
            "source_reference": "OPENING",
        },
    )
    assert opening.status_code == 201, opening.text

    engine = create_async_engine(str(settings.database_url))
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO approval_authorities(
                  approval_authority_id, user_subject, capability_code,
                  branch_id, warehouse_id, maximum_amount, maker_checker_required
                ) VALUES (:authority_id, 'approver-mnl', 'inventory:adjustment-approve',
                  :branch_id, :warehouse_id, 100000.00, true)
                """
            ),
            {
                "authority_id": uuid4(),
                "branch_id": branch["branch_id"],
                "warehouse_id": warehouse["warehouse_id"],
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO approval_authorities(
                  approval_authority_id, user_subject, capability_code,
                  branch_id, warehouse_id, maximum_amount, maker_checker_required
                ) VALUES (:authority_id, 'approver-low-limit', 'inventory:adjustment-approve',
                  :branch_id, :warehouse_id, 1.00, true)
                """
            ),
            {
                "authority_id": uuid4(),
                "branch_id": branch["branch_id"],
                "warehouse_id": warehouse["warehouse_id"],
            },
        )
    await engine.dispose()

    return {
        "client": client,
        "settings": settings,
        "sku_id": UUID(sku_data["sku_id"]),
        "warehouse_id": UUID(warehouse["warehouse_id"]),
        "location_id": UUID(location_data["location_id"]),
        "branch_id": UUID(branch["branch_id"]),
    }


@pytest.fixture
async def adjustment_env(
    adjustment_client: AsyncClient, adjustment_settings: Settings
) -> dict[str, object]:
    return await _bootstrap_adjustment_environment(adjustment_client, adjustment_settings)


async def _request_adjustment(
    client: AsyncClient,
    settings: Settings,
    env: dict[str, object],
    key: str,
    kind: str = "shortage",
    quantity: str = "5.000000",
) -> object:
    response = await client.post(
        "/v1/inventory/adjustments",
        headers=_auth(
            settings,
            "adjuster-mnl",
            **{"Idempotency-Key": key},
        ),
        json={
            "sku_id": str(env["sku_id"]),
            "warehouse_id": str(env["warehouse_id"]),
            "location_id": str(env["location_id"]),
            "kind": kind,
            "quantity": quantity,
            "unit_code": "EA",
            "reason": "Fixture adjustment.",
            "source_reference": "FIXTURE-ADJUSTMENT",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["adjustment"]


@pytest.mark.asyncio
async def test_adjustment_request_and_post_surplus(adjustment_env: dict[str, object]) -> None:
    client = adjustment_env["client"]
    settings = adjustment_env["settings"]

    request_key = f"adjustment-request-{uuid4()}"
    request = await client.post(
        "/v1/inventory/adjustments",
        headers=_auth(
            settings,
            "adjuster-mnl",
            **{"Idempotency-Key": request_key},
        ),
        json={
            "sku_id": str(adjustment_env["sku_id"]),
            "warehouse_id": str(adjustment_env["warehouse_id"]),
            "location_id": str(adjustment_env["location_id"]),
            "kind": "surplus",
            "quantity": "10.000000",
            "unit_code": "EA",
            "reason": "Counted surplus.",
            "source_reference": "COUNT-001",
        },
    )
    assert request.status_code == 201, request.text
    data = request.json()["adjustment"]
    assert data["status"] == "pending_authorization"
    assert data["kind"] == "surplus"
    assert data["quantity_base"] == "10.000000"
    assert data["value_delta"] == "100.000000"
    adjustment_id = data["adjustment_id"]

    replay = await client.post(
        "/v1/inventory/adjustments",
        headers=_auth(
            settings,
            "adjuster-mnl",
            **{"Idempotency-Key": request_key},
        ),
        json={
            "sku_id": str(adjustment_env["sku_id"]),
            "warehouse_id": str(adjustment_env["warehouse_id"]),
            "location_id": str(adjustment_env["location_id"]),
            "kind": "surplus",
            "quantity": "10.000000",
            "unit_code": "EA",
            "reason": "Counted surplus.",
            "source_reference": "COUNT-001",
        },
    )
    assert replay.status_code == 200
    assert replay.headers["X-Idempotency-Replayed"] == "true"

    post_key = f"adjustment-post-{uuid4()}"
    post = await client.post(
        f"/v1/inventory/adjustments/{adjustment_id}/post",
        headers=_auth(
            settings,
            "approver-mnl",
            **{"Idempotency-Key": post_key},
        ),
        json={"expected_version": 1},
    )
    assert post.status_code == 201, post.text
    posted = post.json()["adjustment"]
    assert posted["status"] == "posted"
    assert posted["version"] == 2
    assert posted["posted_by"] == "approver-mnl"
    assert posted["posted_movement_group_id"] is not None


@pytest.mark.asyncio
async def test_adjustment_shortage_and_reverse(adjustment_env: dict[str, object]) -> None:
    client = adjustment_env["client"]
    settings = adjustment_env["settings"]

    adjustment = await _request_adjustment(
        client, settings, adjustment_env, f"adjustment-request-{uuid4()}", kind="shortage"
    )
    adjustment_id = adjustment["adjustment_id"]

    post = await client.post(
        f"/v1/inventory/adjustments/{adjustment_id}/post",
        headers=_auth(
            settings,
            "approver-mnl",
            **{"Idempotency-Key": f"adjustment-post-{uuid4()}"},
        ),
        json={"expected_version": 1},
    )
    assert post.status_code == 201, post.text

    reverse = await client.post(
        f"/v1/inventory/adjustments/{adjustment_id}/reverse",
        headers=_auth(
            settings,
            "approver-mnl",
            **{"Idempotency-Key": f"adjustment-reverse-{uuid4()}"},
        ),
        json={"expected_version": 2, "reason": "Count corrected."},
    )
    assert reverse.status_code == 201, reverse.text
    reversed_data = reverse.json()["adjustment"]
    assert reversed_data["status"] == "reversed"
    assert reversed_data["version"] == 3
    assert reversed_data["reversed_by"] == "approver-mnl"
    assert reversed_data["reversal_reason"] == "Count corrected."


@pytest.mark.asyncio
async def test_adjustment_authorization_matrix(adjustment_env: dict[str, object]) -> None:
    client = adjustment_env["client"]
    settings = adjustment_env["settings"]

    adjustment = await _request_adjustment(
        client, settings, adjustment_env, f"adjustment-request-{uuid4()}", kind="shortage"
    )
    adjustment_id = adjustment["adjustment_id"]

    self_post = await client.post(
        f"/v1/inventory/adjustments/{adjustment_id}/post",
        headers=_auth(
            settings,
            "adjuster-mnl",
            **{"Idempotency-Key": f"adjustment-self-post-{uuid4()}"},
        ),
        json={"expected_version": 1},
    )
    assert self_post.status_code == 403, self_post.text
    assert self_post.json()["error"]["code"] == "maker_checker_violation"

    low_limit = await client.post(
        f"/v1/inventory/adjustments/{adjustment_id}/post",
        headers=_auth(
            settings,
            "approver-low-limit",
            **{"Idempotency-Key": f"adjustment-low-limit-{uuid4()}"},
        ),
        json={"expected_version": 1},
    )
    assert low_limit.status_code == 403, low_limit.text
    assert low_limit.json()["error"]["code"] == "approval_limit_exceeded"


@pytest.mark.asyncio
async def test_adjustment_scope_denial(adjustment_env: dict[str, object]) -> None:
    client = adjustment_env["client"]
    settings = adjustment_env["settings"]

    response = await client.post(
        "/v1/inventory/adjustments",
        headers=_auth(
            settings,
            "adjuster-no-scope",
            **{"Idempotency-Key": f"adjustment-scope-{uuid4()}"},
        ),
        json={
            "sku_id": str(adjustment_env["sku_id"]),
            "warehouse_id": str(adjustment_env["warehouse_id"]),
            "location_id": str(adjustment_env["location_id"]),
            "kind": "shortage",
            "quantity": "1.000000",
            "unit_code": "EA",
            "reason": "Scope test.",
            "source_reference": "SCOPE",
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "operational_scope_required"


@pytest.mark.asyncio
async def test_adjustment_list_and_get(adjustment_env: dict[str, object]) -> None:
    client = adjustment_env["client"]
    settings = adjustment_env["settings"]

    adjustment = await _request_adjustment(
        client, settings, adjustment_env, f"adjustment-list-{uuid4()}"
    )

    list_response = await client.get(
        "/v1/inventory/adjustments",
        headers=_auth(settings, "adjuster-mnl"),
    )
    assert list_response.status_code == 200, list_response.text
    data = list_response.json()
    assert data["total"] >= 1
    assert any(a["adjustment_id"] == adjustment["adjustment_id"] for a in data["items"])

    detail = await client.get(
        f"/v1/inventory/adjustments/{adjustment['adjustment_id']}",
        headers=_auth(settings, "adjuster-mnl"),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["adjustment"]["adjustment_id"] == adjustment["adjustment_id"]
