from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings
from tradeflow_api.money import currency_quantum


@pytest.fixture
def inventory_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def inventory_client(inventory_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(inventory_settings)
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


async def bootstrap_inventory(client: AsyncClient, settings: Settings) -> dict[str, object]:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers={
            "Authorization": (
                f"Bearer {token(settings, 'bootstrapper', ['organization:bootstrap'])}"
            ),
            "Idempotency-Key": "inventory-bootstrap",
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
                    "code": "INVENTORY",
                    "name": "Inventory Controller",
                    "capabilities": [
                        "catalog:write",
                        "inventory:post",
                        "inventory:read",
                        "inventory:rebuild",
                    ],
                },
                {
                    "code": "INVENTORY_READ",
                    "name": "Inventory Reader",
                    "capabilities": ["inventory:read"],
                },
            ],
            "users": [
                {
                    "subject": "inventory-mnl",
                    "display_name": "Manila Inventory",
                    "role_template_codes": ["INVENTORY"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": ["MNL-01"],
                },
                {
                    "subject": "inventory-ceb",
                    "display_name": "Cebu Inventory",
                    "role_template_codes": ["INVENTORY_READ"],
                    "branch_codes": ["CEB"],
                    "warehouse_codes": ["CEB-01"],
                },
                {
                    "subject": "inventory-all",
                    "display_name": "Network Inventory",
                    "role_template_codes": ["INVENTORY"],
                    "branch_codes": ["MNL", "CEB"],
                    "warehouse_codes": ["MNL-01", "CEB-01"],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def sku_command(
    *,
    product_code: str = "BEV",
    sku_code: str = "COLA-330",
    tracking_policy: str = "untracked",
    expiration_control: bool = False,
    barcode: str = "480000000001",
) -> dict[str, object]:
    return {
        "product_code": product_code,
        "product_name": f"{product_code} Product",
        "sku_code": sku_code,
        "sku_name": f"{sku_code} SKU",
        "base_stocking_unit": "EA",
        "tracking_policy": tracking_policy,
        "expiration_control": expiration_control,
        "conversions": [
            {
                "unit_code": "CASE",
                "base_quantity": "12.000000",
                "effective_from": "2026-01-01",
                "effective_to": None,
            }
        ],
        "barcodes": [{"barcode": barcode, "unit_code": "CASE"}],
    }


async def configure_sku(
    client: AsyncClient,
    settings: Settings,
    command: dict[str, object],
    key: str,
) -> dict[str, object]:
    response = await client.post(
        "/v1/catalog/skus",
        headers=auth(settings, "inventory-mnl", **{"Idempotency-Key": key}),
        json=command,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_opening_stock_is_idempotent_scoped_and_rebuildable(
    inventory_client: AsyncClient,
    inventory_settings: Settings,
    postgres_url: str,
) -> None:
    organization = await bootstrap_inventory(inventory_client, inventory_settings)
    warehouses = {
        warehouse["code"]: warehouse["warehouse_id"]
        for branch in organization["branches"]
        for warehouse in branch["warehouses"]
    }
    configured = await configure_sku(
        inventory_client,
        inventory_settings,
        sku_command(),
        "configure-cola",
    )
    replayed_configuration = await inventory_client.post(
        "/v1/catalog/skus",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "configure-cola"},
        ),
        json=sku_command(),
    )
    assert replayed_configuration.status_code == 200
    assert replayed_configuration.json() == configured

    location = await inventory_client.post(
        "/v1/inventory/locations",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "location-mnl-available"},
        ),
        json={
            "warehouse_id": warehouses["MNL-01"],
            "code": "AVAILABLE",
            "name": "Available Stock",
            "custody": "available",
        },
    )
    assert location.status_code == 201
    location_replay = await inventory_client.post(
        "/v1/inventory/locations",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "location-mnl-available"},
        ),
        json={
            "warehouse_id": warehouses["MNL-01"],
            "code": "AVAILABLE",
            "name": "Available Stock",
            "custody": "available",
        },
    )
    assert location_replay.status_code == 200
    assert location_replay.json() == location.json()
    location_conflict = await inventory_client.post(
        "/v1/inventory/locations",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "location-mnl-available"},
        ),
        json={
            "warehouse_id": warehouses["MNL-01"],
            "code": "OTHER",
            "name": "Other Stock",
            "custody": "available",
        },
    )
    assert location_conflict.status_code == 409
    assert location_conflict.json()["error"]["code"] == "idempotency_conflict"

    unused = await configure_sku(
        inventory_client,
        inventory_settings,
        sku_command(
            product_code="WATER",
            sku_code="WATER-500",
            barcode="480000000099",
        ),
        "configure-water",
    )
    unused_conversion = unused["conversions"][0]

    async def update_unused_conversion(key: str, base_quantity: str) -> int:
        response = await inventory_client.put(
            (
                f"/v1/catalog/skus/{unused['sku_id']}/unit-conversions/"
                f"{unused_conversion['unit_conversion_id']}"
            ),
            headers=auth(
                inventory_settings,
                "inventory-mnl",
                **{"If-Match": "1", "Idempotency-Key": key},
            ),
            json={
                "base_quantity": base_quantity,
                "effective_from": "2026-01-01",
                "effective_to": None,
            },
        )
        return response.status_code

    assert sorted(
        await asyncio.gather(
            update_unused_conversion("update-water-1", "20.000000"),
            update_unused_conversion("update-water-2", "24.000000"),
        )
    ) == [200, 409]
    opening = {
        "sku_id": configured["sku_id"],
        "warehouse_id": warehouses["MNL-01"],
        "location_id": location.json()["location_id"],
        "quantity": "2.500000",
        "unit_code": "CASE",
        "unit_cost": "10.000000",
        "source_reference": "OPEN-001",
        "lot_code": None,
        "serial_numbers": [],
        "expiration_date": None,
    }
    first = await inventory_client.post(
        "/v1/inventory/opening-stock",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "opening-cola-1"},
        ),
        json=opening,
    )
    replay = await inventory_client.post(
        "/v1/inventory/opening-stock",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "opening-cola-1"},
        ),
        json=opening,
    )
    assert first.status_code == 201, first.text
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["quantity_base"] == "30.000000"
    assert first.json()["value_delta"] == "300.00"

    changed_replay = await inventory_client.post(
        "/v1/inventory/opening-stock",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "opening-cola-1"},
        ),
        json={**opening, "quantity": "3.000000"},
    )
    assert changed_replay.status_code == 409
    assert changed_replay.json()["error"]["code"] == "idempotency_conflict"

    async def post(key: str, quantity: str, cost: str) -> int:
        response = await inventory_client.post(
            "/v1/inventory/opening-stock",
            headers=auth(
                inventory_settings,
                "inventory-mnl",
                **{"Idempotency-Key": key},
            ),
            json={
                **opening,
                "quantity": quantity,
                "unit_code": "EA",
                "unit_cost": cost,
                "source_reference": key,
            },
        )
        assert response.status_code == 201, response.text
        return response.status_code

    assert await asyncio.gather(
        post("opening-cola-2", "10.000000", "20.000000"),
        post("opening-cola-3", "10.000000", "30.000000"),
    ) == [201, 201]

    mnl = await inventory_client.get(
        "/v1/inventory/availability?query=COLA",
        headers=auth(inventory_settings, "inventory-mnl"),
    )
    ceb = await inventory_client.get(
        "/v1/inventory/availability?query=COLA",
        headers=auth(inventory_settings, "inventory-ceb"),
    )
    assert mnl.status_code == 200
    assert mnl.json()["items"][0]["on_hand"] == "50.000000"
    assert mnl.json()["items"][0]["available"] == "50.000000"
    assert mnl.json()["items"][0]["base_currency"] == "PHP"
    assert mnl.json()["items"][0]["warehouse_inventory_value"] == "800.000000"
    assert mnl.json()["items"][0]["moving_average_unit_cost"] == "16.000000"
    assert ceb.json() == {"items": [], "total": 0}
    barcode_match = await inventory_client.get(
        "/v1/inventory/availability?query=480000000001",
        headers=auth(inventory_settings, "inventory-mnl"),
    )
    assert barcode_match.status_code == 200
    assert barcode_match.json()["items"][0]["sku_code"] == "COLA-330"

    conversion = configured["conversions"][0]
    immutable_conversion = await inventory_client.put(
        (
            f"/v1/catalog/skus/{configured['sku_id']}/unit-conversions/"
            f"{conversion['unit_conversion_id']}"
        ),
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{
                "If-Match": "1",
                "Idempotency-Key": "update-used-cola-conversion",
            },
        ),
        json={
            "base_quantity": "24.000000",
            "effective_from": "2026-01-01",
            "effective_to": None,
        },
    )
    assert immutable_conversion.status_code == 409
    assert immutable_conversion.json()["error"]["code"] == "unit_conversion_in_use"

    rebuilt = await inventory_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(inventory_settings, "inventory-mnl"),
    )
    assert rebuilt.json() == {"availability_rows": 1, "valuation_rows": 1}
    after_rebuild = await inventory_client.get(
        "/v1/inventory/availability?query=COLA",
        headers=auth(inventory_settings, "inventory-mnl"),
    )
    assert after_rebuild.json() == mnl.json()

    engine = create_async_engine(postgres_url)
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE stock_movements SET quantity_base = 1 WHERE movement_id = :movement_id"
                ),
                {"movement_id": first.json()["movement_id"]},
            )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE skus SET base_stocking_unit = 'CASE' WHERE sku_id = :sku_id"),
                {"sku_id": configured["sku_id"]},
            )
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM user_warehouse_scopes WHERE user_subject = 'inventory-mnl'")
        )
    await engine.dispose()
    revoked_replay = await inventory_client.post(
        "/v1/inventory/opening-stock",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "opening-cola-1"},
        ),
        json=opening,
    )
    assert revoked_replay.status_code == 403
    assert revoked_replay.json()["error"]["code"] == "operational_scope_required"


@pytest.mark.asyncio
async def test_tracking_expiration_barcode_and_serial_constraints(
    inventory_client: AsyncClient,
    inventory_settings: Settings,
) -> None:
    organization = await bootstrap_inventory(inventory_client, inventory_settings)
    warehouses = {
        warehouse["code"]: warehouse["warehouse_id"]
        for branch in organization["branches"]
        for warehouse in branch["warehouses"]
    }
    warehouse_id = warehouses["MNL-01"]
    available = await inventory_client.post(
        "/v1/inventory/locations",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "location-tracking-available"},
        ),
        json={
            "warehouse_id": warehouse_id,
            "code": "AVAILABLE",
            "name": "Available",
            "custody": "available",
        },
    )
    assert available.status_code == 201
    ceb_available = await inventory_client.post(
        "/v1/inventory/locations",
        headers=auth(
            inventory_settings,
            "inventory-all",
            **{"Idempotency-Key": "location-ceb-available"},
        ),
        json={
            "warehouse_id": warehouses["CEB-01"],
            "code": "AVAILABLE",
            "name": "Available",
            "custody": "available",
        },
    )
    assert ceb_available.status_code == 201

    impossible_expiration = await inventory_client.post(
        "/v1/catalog/skus",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "impossible-expiration"},
        ),
        json=sku_command(
            product_code="IMPOSSIBLE",
            sku_code="IMPOSSIBLE-EXPIRY",
            tracking_policy="untracked",
            expiration_control=True,
            barcode="480000000098",
        ),
    )
    assert impossible_expiration.status_code == 422

    lot = await configure_sku(
        inventory_client,
        inventory_settings,
        sku_command(
            product_code="MED",
            sku_code="MED-LOT",
            tracking_policy="lot",
            expiration_control=True,
            barcode="480000000002",
        ),
        "configure-med-lot",
    )
    base_post = {
        "sku_id": lot["sku_id"],
        "warehouse_id": warehouse_id,
        "location_id": available.json()["location_id"],
        "quantity": "1.000000",
        "unit_code": "EA",
        "unit_cost": "5.000000",
        "source_reference": "LOT-OPEN",
        "lot_code": "LOT-A",
        "serial_numbers": [],
        "expiration_date": None,
    }
    missing_expiration = await inventory_client.post(
        "/v1/inventory/opening-stock",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "missing-expiration"},
        ),
        json=base_post,
    )
    assert missing_expiration.status_code == 422
    assert missing_expiration.json()["error"]["code"] == "expiration_required"

    expired = await inventory_client.post(
        "/v1/inventory/opening-stock",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "expired-lot"},
        ),
        json={**base_post, "expiration_date": "2020-01-01"},
    )
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "expired_stock_not_available"

    lot_without_expiration = await configure_sku(
        inventory_client,
        inventory_settings,
        sku_command(
            product_code="FOOD",
            sku_code="FOOD-LOT",
            tracking_policy="lot",
            expiration_control=False,
            barcode="480000000097",
        ),
        "configure-food-lot",
    )
    no_expiration_lot = {
        **base_post,
        "sku_id": lot_without_expiration["sku_id"],
        "source_reference": "LOT-NULL-EXPIRY",
        "lot_code": "LOT-NULL",
    }
    first_lot = await inventory_client.post(
        "/v1/inventory/opening-stock",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "lot-null-expiry"},
        ),
        json=no_expiration_lot,
    )
    conflicting_lot = await inventory_client.post(
        "/v1/inventory/opening-stock",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "lot-dated-expiry"},
        ),
        json={
            **no_expiration_lot,
            "source_reference": "LOT-DATED-EXPIRY",
            "expiration_date": "2028-01-01",
        },
    )
    assert first_lot.status_code == 201
    assert conflicting_lot.status_code == 409
    assert conflicting_lot.json()["error"]["code"] == "lot_identity_conflict"

    cross_warehouse_lot = {
        **base_post,
        "source_reference": "CROSS-WAREHOUSE-LOT",
        "lot_code": "LOT-NETWORK",
    }

    async def post_cross_warehouse(
        key: str,
        target_warehouse_id: str,
        location_id: str,
        expiration_date: str,
    ) -> int:
        response = await inventory_client.post(
            "/v1/inventory/opening-stock",
            headers=auth(
                inventory_settings,
                "inventory-all",
                **{"Idempotency-Key": key},
            ),
            json={
                **cross_warehouse_lot,
                "warehouse_id": target_warehouse_id,
                "location_id": location_id,
                "expiration_date": expiration_date,
            },
        )
        return response.status_code

    assert sorted(
        await asyncio.gather(
            post_cross_warehouse(
                "cross-lot-mnl",
                warehouses["MNL-01"],
                available.json()["location_id"],
                "2028-01-01",
            ),
            post_cross_warehouse(
                "cross-lot-ceb",
                warehouses["CEB-01"],
                ceb_available.json()["location_id"],
                "2029-01-01",
            ),
        )
    ) == [201, 409]

    duplicate_barcode = await inventory_client.post(
        "/v1/catalog/skus",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "duplicate-barcode"},
        ),
        json=sku_command(
            product_code="OTHER",
            sku_code="OTHER-SKU",
            barcode="480000000002",
        ),
    )
    assert duplicate_barcode.status_code == 409
    assert duplicate_barcode.json()["error"]["code"] == "active_barcode_exists"

    serial = await configure_sku(
        inventory_client,
        inventory_settings,
        sku_command(
            product_code="DEV",
            sku_code="DEVICE-SERIAL",
            tracking_policy="serial",
            barcode="480000000003",
        ),
        "configure-device",
    )
    serial_post = {
        **base_post,
        "sku_id": serial["sku_id"],
        "quantity": "2.000000",
        "source_reference": "SERIAL-OPEN",
        "lot_code": None,
        "serial_numbers": ["SN-002", "SN-001"],
        "expiration_date": None,
    }
    for invalid_serials in ([""], ["X" * 101]):
        invalid_serial = await inventory_client.post(
            "/v1/inventory/opening-stock",
            headers=auth(
                inventory_settings,
                "inventory-mnl",
                **{"Idempotency-Key": f"invalid-serial-{len(invalid_serials[0])}"},
            ),
            json={**serial_post, "serial_numbers": invalid_serials},
        )
        assert invalid_serial.status_code == 422
    first_serial = await inventory_client.post(
        "/v1/inventory/opening-stock",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "serial-1"},
        ),
        json=serial_post,
    )
    duplicate_serial = await inventory_client.post(
        "/v1/inventory/opening-stock",
        headers=auth(
            inventory_settings,
            "inventory-mnl",
            **{"Idempotency-Key": "serial-2"},
        ),
        json={**serial_post, "source_reference": "SERIAL-OPEN-2"},
    )
    assert first_serial.status_code == 201, first_serial.text
    assert duplicate_serial.status_code == 409
    assert duplicate_serial.json()["error"]["code"] == "duplicate_serial_identity"
    serial_availability = await inventory_client.get(
        "/v1/inventory/availability?query=DEVICE",
        headers=auth(inventory_settings, "inventory-mnl"),
    )
    assert serial_availability.json()["items"][0]["serial_numbers"] == ["SN-001", "SN-002"]
    rebuilt = await inventory_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(inventory_settings, "inventory-mnl"),
    )
    assert rebuilt.status_code == 200
    after_rebuild = await inventory_client.get(
        "/v1/inventory/availability?query=DEVICE",
        headers=auth(inventory_settings, "inventory-mnl"),
    )
    assert after_rebuild.json() == serial_availability.json()


def test_date_assumption_is_current() -> None:
    assert date.today() >= date(2026, 1, 1)


def test_currency_quantum_uses_iso_minor_units() -> None:
    assert currency_quantum("JPY") == 1
    assert currency_quantum("PHP") == Decimal("0.01")
    assert currency_quantum("KWD") == Decimal("0.001")
    assert currency_quantum("CLF") == Decimal("0.0001")


@pytest.mark.asyncio
async def test_concurrent_conversion_period_updates_cannot_create_overlap(
    inventory_client: AsyncClient,
    inventory_settings: Settings,
) -> None:
    await bootstrap_inventory(inventory_client, inventory_settings)
    command = sku_command(
        product_code="SEASONAL",
        sku_code="SEASONAL-CASE",
        barcode="480000000096",
    )
    command["conversions"] = [
        {
            "unit_code": "CASE",
            "base_quantity": "12.000000",
            "effective_from": "2026-01-01",
            "effective_to": "2026-03-31",
        },
        {
            "unit_code": "CASE",
            "base_quantity": "24.000000",
            "effective_from": "2026-10-01",
            "effective_to": "2026-12-31",
        },
    ]
    command["barcodes"] = []
    configured = await configure_sku(
        inventory_client,
        inventory_settings,
        command,
        "configure-seasonal",
    )
    first, second = configured["conversions"]

    async def update_period(
        conversion: dict[str, object],
        key: str,
        effective_from: str,
        effective_to: str,
    ) -> int:
        response = await inventory_client.put(
            (
                f"/v1/catalog/skus/{configured['sku_id']}/unit-conversions/"
                f"{conversion['unit_conversion_id']}"
            ),
            headers=auth(
                inventory_settings,
                "inventory-mnl",
                **{"If-Match": "1", "Idempotency-Key": key},
            ),
            json={
                "base_quantity": conversion["base_quantity"],
                "effective_from": effective_from,
                "effective_to": effective_to,
            },
        )
        return response.status_code

    assert sorted(
        await asyncio.gather(
            update_period(first, "seasonal-first", "2026-01-01", "2026-06-30"),
            update_period(second, "seasonal-second", "2026-05-01", "2026-12-31"),
        )
    ) == [200, 409]
