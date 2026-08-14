from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
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
def transfer_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def transfer_client(transfer_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(transfer_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def _bootstrap_transfer_environment(
    client: AsyncClient,
    settings: Settings,
) -> dict[str, object]:
    bootstrap = await client.post(
        "/v1/organization/bootstrap",
        headers={
            "Authorization": (
                f"Bearer {_token(settings, 'bootstrapper', ['organization:bootstrap'])}"
            ),
            "Idempotency-Key": "inventory-transfer-bootstrap",
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
                    "code": "WAREHOUSE",
                    "name": "Warehouse Controller",
                    "capabilities": [
                        "catalog:write",
                        "inventory:read",
                        "inventory:post",
                        "inventory:rebuild",
                        "inventory:transfer-request",
                        "inventory:transfer-receive",
                    ],
                },
                {
                    "code": "WAREHOUSE_SOURCE_ONLY",
                    "name": "Source Warehouse Controller",
                    "capabilities": [
                        "inventory:read",
                        "inventory:transfer-request",
                    ],
                },
            ],
            "users": [
                {
                    "subject": "warehouse-cross",
                    "display_name": "Cross Warehouse Controller",
                    "role_template_codes": ["WAREHOUSE"],
                    "branch_codes": ["MNL", "CEB"],
                    "warehouse_codes": ["MNL-01", "CEB-01"],
                },
                {
                    "subject": "warehouse-mnl-only",
                    "display_name": "Manila Warehouse Controller",
                    "role_template_codes": ["WAREHOUSE_SOURCE_ONLY"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
            ],
        },
    )
    assert bootstrap.status_code == 201, bootstrap.text
    scope = await client.get(
        "/v1/organization/scope",
        headers=_auth(settings, "warehouse-cross"),
    )
    assert scope.status_code == 200, scope.text
    scope_data = scope.json()
    mnl_warehouse = next(w for w in scope_data["warehouses"] if w["code"] == "MNL-01")
    ceb_warehouse = next(w for w in scope_data["warehouses"] if w["code"] == "CEB-01")

    sku = await client.post(
        "/v1/catalog/skus",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-sku-{uuid4()}"},
        ),
        json={
            "product_code": "TRANS-BEV",
            "product_name": "Transfer Beverages",
            "sku_code": "TRANS-COLA-330",
            "sku_name": "Transfer Cola 330 mL",
            "base_stocking_unit": "EA",
            "tracking_policy": "untracked",
            "expiration_control": False,
            "conversions": [],
            "barcodes": [],
        },
    )
    assert sku.status_code == 201, sku.text
    sku_data = sku.json()

    mnl_location = await client.post(
        "/v1/inventory/locations",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-loc-mnl-{uuid4()}"},
        ),
        json={
            "warehouse_id": mnl_warehouse["warehouse_id"],
            "code": "MNL-AVAILABLE",
            "name": "Manila Available",
            "custody": "available",
        },
    )
    assert mnl_location.status_code == 201, mnl_location.text
    ceb_location = await client.post(
        "/v1/inventory/locations",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-loc-ceb-{uuid4()}"},
        ),
        json={
            "warehouse_id": ceb_warehouse["warehouse_id"],
            "code": "CEB-AVAILABLE",
            "name": "Cebu Available",
            "custody": "available",
        },
    )
    assert ceb_location.status_code == 201, ceb_location.text

    opening = await client.post(
        "/v1/inventory/opening-stock",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-opening-{uuid4()}"},
        ),
        json={
            "sku_id": sku_data["sku_id"],
            "warehouse_id": mnl_warehouse["warehouse_id"],
            "location_id": mnl_location.json()["location_id"],
            "quantity": "100.000000",
            "unit_code": "EA",
            "unit_cost": "10.000000",
            "source_reference": "TRANSFER-OPENING-001",
        },
    )
    assert opening.status_code == 201, opening.text

    return {
        "settings": settings,
        "client": client,
        "sku_id": sku_data["sku_id"],
        "mnl_warehouse_id": mnl_warehouse["warehouse_id"],
        "ceb_warehouse_id": ceb_warehouse["warehouse_id"],
        "mnl_location_id": mnl_location.json()["location_id"],
        "ceb_location_id": ceb_location.json()["location_id"],
    }


async def _availability_for(
    client: AsyncClient,
    settings: Settings,
    sku_id: UUID,
    warehouse_id: UUID,
    location_code: str,
) -> dict[str, object] | None:
    response = await client.get(
        "/v1/inventory/availability",
        headers=_auth(settings, "warehouse-cross"),
        params={"limit": 100},
    )
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        if (
            item["sku_id"] == str(sku_id)
            and item["warehouse_id"] == str(warehouse_id)
            and item["location_code"] == location_code
        ):
            return item
    return None


async def _valuation_for(
    client: AsyncClient,
    settings: Settings,
    sku_id: UUID,
    warehouse_id: UUID,
) -> dict[str, object] | None:
    response = await client.get(
        "/v1/inventory/availability",
        headers=_auth(settings, "warehouse-cross"),
        params={"limit": 100},
    )
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        if item["sku_id"] == str(sku_id) and item["warehouse_id"] == str(warehouse_id):
            return {
                "quantity_on_hand": item["warehouse_on_hand"],
                "inventory_value": item["warehouse_inventory_value"],
                "moving_average_unit_cost": item["moving_average_unit_cost"],
            }
    return None


async def _create_released_transfer(
    client: AsyncClient,
    settings: Settings,
    env: dict[str, object],
    idempotency_key: str,
    quantity: str = "10.000000",
) -> str:
    response = await client.post(
        "/v1/inventory/transfers",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": idempotency_key},
        ),
        json={
            "sku_id": str(env["sku_id"]),
            "from_warehouse_id": str(env["mnl_warehouse_id"]),
            "to_warehouse_id": str(env["ceb_warehouse_id"]),
            "from_location_id": str(env["mnl_location_id"]),
            "to_location_id": str(env["ceb_location_id"]),
            "quantity": quantity,
            "unit_code": "EA",
            "reason": "Fixture transfer.",
            "source_reference": "FIXTURE-TRANSFER",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["transfer"]["transfer_id"]


@pytest.fixture
async def transfer_env(transfer_client: AsyncClient, transfer_settings: Settings):
    env = await _bootstrap_transfer_environment(transfer_client, transfer_settings)
    env["transfer_id"] = await _create_released_transfer(
        transfer_client,
        transfer_settings,
        env,
        f"transfer-env-fixture-{uuid4()}",
    )
    return env


@pytest.mark.asyncio
async def test_transfer_request_and_receive_happy_path(transfer_env: dict[str, object]) -> None:
    client = transfer_env["client"]
    settings = transfer_env["settings"]
    sku_id = transfer_env["sku_id"]
    from_warehouse_id = transfer_env["mnl_warehouse_id"]
    to_warehouse_id = transfer_env["ceb_warehouse_id"]
    from_location_id = transfer_env["mnl_location_id"]
    to_location_id = transfer_env["ceb_location_id"]

    request_key = f"transfer-request-{uuid4()}"
    request = await client.post(
        "/v1/inventory/transfers",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": request_key},
        ),
        json={
            "sku_id": str(sku_id),
            "from_warehouse_id": str(from_warehouse_id),
            "to_warehouse_id": str(to_warehouse_id),
            "from_location_id": str(from_location_id),
            "to_location_id": str(to_location_id),
            "quantity": "40.000000",
            "unit_code": "EA",
            "reason": "Replenishment.",
            "source_reference": "REPLENISH-001",
        },
    )
    assert request.status_code == 201, request.text
    data = request.json()["transfer"]
    assert data["status"] == "released"
    assert data["version"] == 1
    assert data["quantity_base"] == "40.000000"
    assert data["unit_cost"] == "10.000000"
    assert data["base_currency"] == "PHP"
    transfer_id = data["transfer_id"]

    replay = await client.post(
        "/v1/inventory/transfers",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": request_key},
        ),
        json={
            "sku_id": str(sku_id),
            "from_warehouse_id": str(from_warehouse_id),
            "to_warehouse_id": str(to_warehouse_id),
            "from_location_id": str(from_location_id),
            "to_location_id": str(to_location_id),
            "quantity": "40.000000",
            "unit_code": "EA",
            "reason": "Replenishment.",
            "source_reference": "REPLENISH-001",
        },
    )
    assert replay.status_code == 200
    assert replay.headers["X-Idempotency-Replayed"] == "true"
    assert replay.json() == request.json()

    source_before_receive = await _valuation_for(client, settings, sku_id, from_warehouse_id)
    assert source_before_receive is not None
    assert Decimal(source_before_receive["quantity_on_hand"]) == Decimal("50")
    assert Decimal(source_before_receive["inventory_value"]) == Decimal("1000")

    receive_key = f"transfer-receive-{uuid4()}"
    receive = await client.post(
        f"/v1/inventory/transfers/{transfer_id}/receive",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": receive_key},
        ),
        json={"expected_version": 1},
    )
    assert receive.status_code == 201, receive.text
    received = receive.json()["transfer"]
    assert received["status"] == "received"
    assert received["version"] == 2
    assert received["received_by"] == "warehouse-cross"
    assert received["receive_movement_group_id"] is not None

    receive_replay = await client.post(
        f"/v1/inventory/transfers/{transfer_id}/receive",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": receive_key},
        ),
        json={"expected_version": 1},
    )
    assert receive_replay.status_code == 200
    assert receive_replay.headers["X-Idempotency-Replayed"] == "true"
    assert receive_replay.json() == receive.json()

    source_after = await _valuation_for(client, settings, sku_id, from_warehouse_id)
    dest_after = await _valuation_for(client, settings, sku_id, to_warehouse_id)
    assert source_after is not None
    assert dest_after is not None
    assert Decimal(source_after["quantity_on_hand"]) == Decimal("50")
    assert Decimal(source_after["inventory_value"]) == Decimal("600")
    assert Decimal(dest_after["quantity_on_hand"]) == Decimal("40")
    assert Decimal(dest_after["inventory_value"]) == Decimal("400")
    assert Decimal(dest_after["moving_average_unit_cost"]) == Decimal("10")

    detail = await client.get(
        f"/v1/inventory/transfers/{transfer_id}",
        headers=_auth(settings, "warehouse-cross"),
    )
    assert detail.status_code == 200
    assert detail.json()["transfer"]["status"] == "received"
    assert detail.json()["transfer"]["version"] == 2

    rebuild = await client.post(
        "/v1/inventory/projections/rebuild",
        headers=_auth(settings, "warehouse-cross"),
    )
    assert rebuild.status_code == 200, rebuild.text
    rebuilt_source = await _valuation_for(client, settings, sku_id, from_warehouse_id)
    rebuilt_destination = await _valuation_for(client, settings, sku_id, to_warehouse_id)
    assert rebuilt_source == source_after
    assert rebuilt_destination == dest_after


@pytest.mark.asyncio
async def test_transfer_request_requires_idempotency_key(transfer_env: dict[str, object]) -> None:
    client = transfer_env["client"]
    settings = transfer_env["settings"]
    response = await client.post(
        "/v1/inventory/transfers",
        headers=_auth(settings, "warehouse-cross"),
        json={
            "sku_id": str(transfer_env["sku_id"]),
            "from_warehouse_id": str(transfer_env["mnl_warehouse_id"]),
            "to_warehouse_id": str(transfer_env["ceb_warehouse_id"]),
            "from_location_id": str(transfer_env["mnl_location_id"]),
            "to_location_id": str(transfer_env["ceb_location_id"]),
            "quantity": "1.000000",
            "unit_code": "EA",
            "reason": "Test.",
            "source_reference": "TEST",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"


@pytest.mark.asyncio
async def test_transfer_request_rejects_same_warehouse(transfer_env: dict[str, object]) -> None:
    client = transfer_env["client"]
    settings = transfer_env["settings"]
    response = await client.post(
        "/v1/inventory/transfers",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-same-warehouse-{uuid4()}"},
        ),
        json={
            "sku_id": str(transfer_env["sku_id"]),
            "from_warehouse_id": str(transfer_env["mnl_warehouse_id"]),
            "to_warehouse_id": str(transfer_env["mnl_warehouse_id"]),
            "from_location_id": str(transfer_env["mnl_location_id"]),
            "to_location_id": str(transfer_env["mnl_location_id"]),
            "quantity": "1.000000",
            "unit_code": "EA",
            "reason": "Test.",
            "source_reference": "TEST",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_transfer_request_rejects_insufficient_stock(
    transfer_env: dict[str, object],
) -> None:
    client = transfer_env["client"]
    settings = transfer_env["settings"]
    response = await client.post(
        "/v1/inventory/transfers",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-insufficient-{uuid4()}"},
        ),
        json={
            "sku_id": str(transfer_env["sku_id"]),
            "from_warehouse_id": str(transfer_env["mnl_warehouse_id"]),
            "to_warehouse_id": str(transfer_env["ceb_warehouse_id"]),
            "from_location_id": str(transfer_env["mnl_location_id"]),
            "to_location_id": str(transfer_env["ceb_location_id"]),
            "quantity": "1000.000000",
            "unit_code": "EA",
            "reason": "Test.",
            "source_reference": "TEST",
        },
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_transfer_request_rejects_destination_scope(transfer_env: dict[str, object]) -> None:
    client = transfer_env["client"]
    settings = transfer_env["settings"]
    response = await client.post(
        "/v1/inventory/transfers",
        headers=_auth(
            settings,
            "warehouse-mnl-only",
            **{"Idempotency-Key": f"transfer-scope-{uuid4()}"},
        ),
        json={
            "sku_id": str(transfer_env["sku_id"]),
            "from_warehouse_id": str(transfer_env["mnl_warehouse_id"]),
            "to_warehouse_id": str(transfer_env["ceb_warehouse_id"]),
            "from_location_id": str(transfer_env["mnl_location_id"]),
            "to_location_id": str(transfer_env["ceb_location_id"]),
            "quantity": "1.000000",
            "unit_code": "EA",
            "reason": "Test.",
            "source_reference": "TEST",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_transfer_receive_rejects_already_received(
    transfer_env: dict[str, object],
) -> None:
    client = transfer_env["client"]
    settings = transfer_env["settings"]
    request = await client.post(
        "/v1/inventory/transfers",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-already-{uuid4()}"},
        ),
        json={
            "sku_id": str(transfer_env["sku_id"]),
            "from_warehouse_id": str(transfer_env["mnl_warehouse_id"]),
            "to_warehouse_id": str(transfer_env["ceb_warehouse_id"]),
            "from_location_id": str(transfer_env["mnl_location_id"]),
            "to_location_id": str(transfer_env["ceb_location_id"]),
            "quantity": "5.000000",
            "unit_code": "EA",
            "reason": "Test.",
            "source_reference": "TEST",
        },
    )
    assert request.status_code == 201
    transfer_id = request.json()["transfer"]["transfer_id"]

    stale = await client.post(
        f"/v1/inventory/transfers/{transfer_id}/receive",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"receive-stale-{uuid4()}"},
        ),
        json={"expected_version": 2},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "transfer_version_conflict"

    receive1 = await client.post(
        f"/v1/inventory/transfers/{transfer_id}/receive",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"receive-already-{uuid4()}"},
        ),
        json={"expected_version": 1},
    )
    assert receive1.status_code == 201

    receive2 = await client.post(
        f"/v1/inventory/transfers/{transfer_id}/receive",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"receive-already-2-{uuid4()}"},
        ),
        json={"expected_version": 1},
    )
    assert receive2.status_code == 409


@pytest.mark.asyncio
async def test_transfer_list_is_scoped(transfer_env: dict[str, object]) -> None:
    client = transfer_env["client"]
    settings = transfer_env["settings"]
    await client.post(
        "/v1/inventory/transfers",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-list-{uuid4()}"},
        ),
        json={
            "sku_id": str(transfer_env["sku_id"]),
            "from_warehouse_id": str(transfer_env["mnl_warehouse_id"]),
            "to_warehouse_id": str(transfer_env["ceb_warehouse_id"]),
            "from_location_id": str(transfer_env["mnl_location_id"]),
            "to_location_id": str(transfer_env["ceb_location_id"]),
            "quantity": "2.000000",
            "unit_code": "EA",
            "reason": "Test.",
            "source_reference": "TEST",
        },
    )
    list_response = await client.get(
        "/v1/inventory/transfers",
        headers=_auth(settings, "warehouse-cross"),
    )
    assert list_response.status_code == 200
    assert list_response.json()["total"] >= 1

    empty_list = await client.get(
        "/v1/inventory/transfers",
        headers=_auth(settings, "warehouse-mnl-only"),
    )
    assert empty_list.status_code == 200
    assert empty_list.json()["total"] == 0


async def _setup_transfer_with_lot(
    postgres_url: str,
    client: AsyncClient,
    settings: Settings,
) -> dict[str, object]:
    sku = await client.post(
        "/v1/catalog/skus",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-lot-sku-{uuid4()}"},
        ),
        json={
            "product_code": "TRANS-LOT",
            "product_name": "Transfer Lot Product",
            "sku_code": "TRANS-LOT-001",
            "sku_name": "Transfer Lot SKU",
            "base_stocking_unit": "EA",
            "tracking_policy": "lot",
            "expiration_control": True,
            "conversions": [],
            "barcodes": [],
        },
    )
    assert sku.status_code == 201, sku.text
    scope = await client.get(
        "/v1/organization/scope",
        headers=_auth(settings, "warehouse-cross"),
    )
    scope_data = scope.json()
    mnl_warehouse = next(w for w in scope_data["warehouses"] if w["code"] == "MNL-01")
    mnl_location = await client.post(
        "/v1/inventory/locations",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-lot-loc-{uuid4()}"},
        ),
        json={
            "warehouse_id": mnl_warehouse["warehouse_id"],
            "code": "MNL-LOT-AVAILABLE",
            "name": "Manila Lot Available",
            "custody": "available",
        },
    )
    assert mnl_location.status_code == 201, mnl_location.text
    opening = await client.post(
        "/v1/inventory/opening-stock",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-lot-opening-{uuid4()}"},
        ),
        json={
            "sku_id": sku.json()["sku_id"],
            "warehouse_id": mnl_warehouse["warehouse_id"],
            "location_id": mnl_location.json()["location_id"],
            "quantity": "50.000000",
            "unit_code": "EA",
            "unit_cost": "8.000000",
            "source_reference": "LOT-OPENING",
            "lot_code": "LOT-A",
            "expiration_date": "2027-12-31",
        },
    )
    assert opening.status_code == 201, opening.text
    return {
        "sku_id": sku.json()["sku_id"],
        "warehouse_id": mnl_warehouse["warehouse_id"],
        "location_id": mnl_location.json()["location_id"],
    }


@pytest.mark.asyncio
async def test_transfer_lot_identity_is_carried(
    transfer_env: dict[str, object],
) -> None:
    client = transfer_env["client"]
    settings = transfer_env["settings"]
    lot_fixture = await _setup_transfer_with_lot(str(settings.database_url), client, settings)
    ceb_warehouse_id = transfer_env["ceb_warehouse_id"]
    ceb_location_id = transfer_env["ceb_location_id"]

    request = await client.post(
        "/v1/inventory/transfers",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-lot-req-{uuid4()}"},
        ),
        json={
            "sku_id": str(lot_fixture["sku_id"]),
            "from_warehouse_id": str(lot_fixture["warehouse_id"]),
            "to_warehouse_id": str(ceb_warehouse_id),
            "from_location_id": str(lot_fixture["location_id"]),
            "to_location_id": str(ceb_location_id),
            "quantity": "20.000000",
            "unit_code": "EA",
            "reason": "Lot replenishment.",
            "source_reference": "LOT-REPLENISH",
            "lot_code": "LOT-A",
        },
    )
    assert request.status_code == 201, request.text
    transfer_id = request.json()["transfer"]["transfer_id"]

    receive = await client.post(
        f"/v1/inventory/transfers/{transfer_id}/receive",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-lot-rec-{uuid4()}"},
        ),
        json={"expected_version": 1},
    )
    assert receive.status_code == 201, receive.text

    dest_item = await _availability_for(
        client, settings, lot_fixture["sku_id"], ceb_warehouse_id, "CEB-AVAILABLE"
    )
    assert dest_item is not None
    assert dest_item["lot_code"] == "LOT-A"
    assert dest_item["expiration_date"] == "2027-12-31"
    assert Decimal(dest_item["on_hand"]) == Decimal("20")

    rebuild = await client.post(
        "/v1/inventory/projections/rebuild",
        headers=_auth(settings, "warehouse-cross"),
    )
    assert rebuild.status_code == 200, rebuild.text
    rebuilt_dest_item = await _availability_for(
        client, settings, lot_fixture["sku_id"], ceb_warehouse_id, "CEB-AVAILABLE"
    )
    assert rebuilt_dest_item is not None
    assert rebuilt_dest_item["lot_code"] == "LOT-A"
    assert rebuilt_dest_item["expiration_date"] == "2027-12-31"
    assert Decimal(rebuilt_dest_item["on_hand"]) == Decimal("20")


@pytest.mark.asyncio
async def test_transfer_rejects_serial_tracked_sku(
    transfer_env: dict[str, object],
) -> None:
    client = transfer_env["client"]
    settings = transfer_env["settings"]
    sku = await client.post(
        "/v1/catalog/skus",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-serial-sku-{uuid4()}"},
        ),
        json={
            "product_code": "TRANS-SERIAL",
            "product_name": "Transfer Serial Product",
            "sku_code": "TRANS-SERIAL-001",
            "sku_name": "Transfer Serial SKU",
            "base_stocking_unit": "EA",
            "tracking_policy": "serial",
            "expiration_control": False,
            "conversions": [],
            "barcodes": [],
        },
    )
    assert sku.status_code == 201, sku.text
    response = await client.post(
        "/v1/inventory/transfers",
        headers=_auth(
            settings,
            "warehouse-cross",
            **{"Idempotency-Key": f"transfer-serial-req-{uuid4()}"},
        ),
        json={
            "sku_id": sku.json()["sku_id"],
            "from_warehouse_id": str(transfer_env["mnl_warehouse_id"]),
            "to_warehouse_id": str(transfer_env["ceb_warehouse_id"]),
            "from_location_id": str(transfer_env["mnl_location_id"]),
            "to_location_id": str(transfer_env["ceb_location_id"]),
            "quantity": "1.000000",
            "unit_code": "EA",
            "reason": "Test.",
            "source_reference": "TEST",
        },
    )
    assert response.status_code == 422
