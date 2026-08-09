from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_payment_clearance_contract import (
    approved_prepaid_order,
    auth,
    bootstrap_payment_clearance,
    create_customer,
    create_price_list,
    create_tax_code,
    record_receipt,
)
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def picking_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def picking_client(picking_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(picking_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_released_untracked_fulfillment_can_be_partially_picked_idempotently(
    picking_client: AsyncClient,
    picking_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(picking_client, picking_settings, postgres_url)
    fulfillment_order = fixture["fulfillment_order"]
    fulfillment_order_id = fulfillment_order["fulfillment_order_id"]

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        unreleased_state_count = await connection.scalar(
            text(
                """
                SELECT count(*)
                FROM fulfillment_line_pick_state
                WHERE fulfillment_order_id = :fulfillment_order_id
                """
            ),
            {"fulfillment_order_id": fulfillment_order_id},
        )
    await engine.dispose()
    assert unreleased_state_count == 0

    receipt = await record_receipt(
        picking_client,
        picking_settings,
        fixture,
        payment_method="cash",
        key="pick-cash-receipt",
    )
    assert receipt.status_code == 201, receipt.text
    release = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/pick-release",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "pick-release"},
        ),
        json={"reason": "Funds cleared; release to warehouse"},
    )
    assert release.status_code == 201, release.text

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        released_quantity = await connection.scalar(
            text(
                """
                SELECT released_quantity_base
                FROM fulfillment_line_pick_state
                WHERE fulfillment_order_id = :fulfillment_order_id
                  AND line_id = :line_id
                """
            ),
            {
                "fulfillment_order_id": fulfillment_order_id,
                "line_id": fixture["line_id"],
            },
        )
    await engine.dispose()
    assert released_quantity == Decimal("2.000000")

    pick_id = str(uuid4())
    command = {
        "pick_id": pick_id,
        "expected_fulfillment_version": 3,
        "lines": [
            {
                "line_id": fixture["line_id"],
                "quantity": "1.000000",
                "unit_code": "EA",
                "selections": [],
            }
        ],
    }
    first = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "partial-untracked-pick"},
        ),
        json=command,
    )
    assert first.status_code == 201, first.text
    assert first.json() == {
        "pick_id": pick_id,
        "fulfillment_order_id": fulfillment_order_id,
        "status": "partially_picked",
        "picked_quantity_base": "1.000000",
        "remaining_quantity_base": "1.000000",
        "version": 4,
        "lines": [
            {
                "line_id": fixture["line_id"],
                "sku_id": fixture["sku_id"],
                "quantity_base": "1.000000",
                "conversion_snapshot": {
                    "entered_quantity": "1.000000",
                    "entered_unit": "EA",
                    "base_quantity_per_unit": "1",
                    "base_quantity": "1.000000",
                    "unit_conversion_id": "",
                },
                "source_movement_id": first.json()["lines"][0]["source_movement_id"],
                "staging_movement_id": first.json()["lines"][0]["staging_movement_id"],
                "lot_selections": [],
                "serial_selections": [],
            }
        ],
    }

    replay = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "partial-untracked-pick"},
        ),
        json=command,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()

    availability = await picking_client.get(
        "/v1/inventory/availability",
        headers=auth(picking_settings, "warehouse-mnl"),
        params={"query": "PREPAID-EA"},
    )
    assert availability.status_code == 200, availability.text
    rows = availability.json()["items"]
    available = next(row for row in rows if row["location_code"] == "PAYMENT-AVAILABLE")
    staging = next(row for row in rows if row["location_code"] == "DISPATCH-STAGING")
    assert available["on_hand"] == "1.000000"
    assert staging["on_hand"] == "1.000000"
    assert available["warehouse_on_hand"] == "2.000000"
    assert available["commercial_reserved"] == "1.000000"


@pytest.mark.asyncio
async def test_pick_enforces_idempotency_remaining_quantity_and_completion(
    picking_client: AsyncClient,
    picking_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(
        picking_client,
        picking_settings,
        postgres_url,
    )
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    receipt = await record_receipt(
        picking_client,
        picking_settings,
        fixture,
        payment_method="cash",
        key="pick-limits-receipt",
    )
    assert receipt.status_code == 201, receipt.text
    release = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/pick-release",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "pick-limits-release"},
        ),
        json={"reason": "Release for quantity boundary checks"},
    )
    assert release.status_code == 201, release.text

    first_command = {
        "pick_id": str(uuid4()),
        "expected_fulfillment_version": 3,
        "lines": [
            {
                "line_id": fixture["line_id"],
                "quantity": "1.000000",
                "unit_code": "EA",
                "selections": [],
            }
        ],
    }
    first = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "pick-limits-first"},
        ),
        json=first_command,
    )
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "partially_picked"
    assert first.json()["remaining_quantity_base"] == "1.000000"

    changed_command = {
        **first_command,
        "lines": [{**first_command["lines"][0], "quantity": "0.500000"}],
    }
    changed_replay = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "pick-limits-first"},
        ),
        json=changed_command,
    )
    assert changed_replay.status_code == 409, changed_replay.text
    assert changed_replay.json()["error"]["code"] == "idempotency_conflict"

    over_remaining = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "pick-limits-over"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 4,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.500000",
                    "unit_code": "EA",
                    "selections": [],
                }
            ],
        },
    )
    assert over_remaining.status_code == 409, over_remaining.text
    assert over_remaining.json()["error"]["code"] == "pick_quantity_exceeds_released"

    final_pick = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "pick-limits-final"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 4,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "selections": [],
                }
            ],
        },
    )
    assert final_pick.status_code == 201, final_pick.text
    assert final_pick.json()["status"] == "picked"
    assert final_pick.json()["remaining_quantity_base"] == "0.000000"
    assert final_pick.json()["version"] == 5

    extra_pick = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "pick-limits-extra"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 5,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "selections": [],
                }
            ],
        },
    )
    assert extra_pick.status_code == 409, extra_pick.text
    assert extra_pick.json()["error"]["code"] == "fulfillment_already_picked"


async def approved_tracked_order(
    client: AsyncClient,
    settings: Settings,
    *,
    tracking_policy: str,
    expiration_control: bool,
    stock_entries: list[dict[str, object]],
    key_prefix: str,
    barcodes: list[dict[str, object]] | None = None,
    conversions: list[dict[str, object]] | None = None,
    selling_unit: str = "EA",
    order_quantity: str = "2.000000",
) -> dict[str, object]:
    organization = await bootstrap_payment_clearance(client, settings)
    branch = organization["branches"][0]
    branch_id = branch["branch_id"]
    warehouse_id = branch["warehouses"][0]["warehouse_id"]
    customer = await create_customer(client, settings, branch_id)
    sku_response = await client.post(
        "/v1/catalog/skus",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": f"{key_prefix}-sku"},
        ),
        json={
            "product_code": f"{key_prefix.upper()}-GOODS",
            "product_name": f"{tracking_policy.title()} Goods",
            "sku_code": f"{key_prefix.upper()}-EA",
            "sku_name": f"{tracking_policy.title()} Goods Each",
            "base_stocking_unit": "EA",
            "tracking_policy": tracking_policy,
            "expiration_control": expiration_control,
            "conversions": conversions or [],
            "barcodes": barcodes or [],
        },
    )
    assert sku_response.status_code == 201, sku_response.text
    sku = sku_response.json()
    tax = await create_tax_code(client, settings)
    if selling_unit == "EA":
        price_list = await create_price_list(
            client,
            settings,
            branch_id=branch_id,
            customer_id=customer["customer_id"],
            sku_id=sku["sku_id"],
            tax_code_version_id=tax["tax_code_version_id"],
        )
    else:
        price_list_response = await client.post(
            "/v1/sales/price-list-versions",
            headers=auth(
                settings,
                "sales-mnl",
                **{"Idempotency-Key": f"{key_prefix}-price-list"},
            ),
            json={
                "code": f"{key_prefix.upper()}-PRICE",
                "branch_id": branch_id,
                "customer_id": customer["customer_id"],
                "inclusion_mode": "exclusive",
                "effective_from": "2026-01-01",
                "effective_to": None,
                "items": [
                    {
                        "sku_id": sku["sku_id"],
                        "unit_code": selling_unit,
                        "list_unit_price": "100.000000",
                        "floor_unit_price": "80.000000",
                        "tax_code_version_id": tax["tax_code_version_id"],
                    }
                ],
            },
        )
        assert price_list_response.status_code == 201, price_list_response.text
        price_list = price_list_response.json()
    location_ids: dict[str, str] = {}
    requested_location_codes = {
        str(entry.get("location_code", f"{key_prefix.upper()}-AVAILABLE"))
        for entry in stock_entries
    }
    for location_position, location_code in enumerate(sorted(requested_location_codes), start=1):
        location_response = await client.post(
            "/v1/inventory/locations",
            headers=auth(
                settings,
                "sales-mnl",
                **{"Idempotency-Key": (f"{key_prefix}-location-{location_position}")},
            ),
            json={
                "warehouse_id": warehouse_id,
                "code": location_code,
                "name": f"{tracking_policy.title()} Pick Available {location_position}",
                "custody": "available",
            },
        )
        assert location_response.status_code == 201, location_response.text
        location_ids[location_code] = location_response.json()["location_id"]
    for position, stock_entry in enumerate(stock_entries, start=1):
        key = f"{key_prefix}-opening-{position}"
        location_code = str(stock_entry.get("location_code", f"{key_prefix.upper()}-AVAILABLE"))
        opening = await client.post(
            "/v1/inventory/opening-stock",
            headers=auth(
                settings,
                "warehouse-mnl",
                **{"Idempotency-Key": key},
            ),
            json={
                "sku_id": sku["sku_id"],
                "warehouse_id": warehouse_id,
                "location_id": location_ids[location_code],
                "quantity": stock_entry["quantity"],
                "unit_code": stock_entry.get("unit_code", "EA"),
                "unit_cost": "10.000000",
                "source_reference": key.upper(),
                "lot_code": stock_entry.get("lot_code"),
                "serial_numbers": stock_entry.get("serial_numbers", []),
                "expiration_date": stock_entry.get("expiration_date"),
            },
        )
        assert opening.status_code == 201, opening.text
    sales_order_id = str(uuid4())
    line_id = str(uuid4())
    order = await client.post(
        "/v1/sales/orders",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": f"{key_prefix}-order"},
        ),
        json={
            "sales_order_id": sales_order_id,
            "branch_id": branch_id,
            "customer_id": customer["customer_id"],
            "expected_customer_version": customer["version"],
            "expected_price_list_version_id": price_list["price_list_version_id"],
            "expected_pricing_date": date.today().isoformat(),
            "delivery_address_version_id": customer["addresses"][0]["address_version_id"],
            "payment_timing_policy": None,
            "payment_timing_override_reason": None,
            "order_discount_amount": "0.000000",
            "lines": [
                {
                    "line_id": line_id,
                    "sku_id": sku["sku_id"],
                    "expected_price_list_line_id": price_list["items"][0]["price_list_line_id"],
                    "expected_unit_conversion_id": (
                        sku["conversions"][0]["unit_conversion_id"]
                        if selling_unit != "EA"
                        else None
                    ),
                    "expected_unit_conversion_version": (
                        sku["conversions"][0]["version"] if selling_unit != "EA" else None
                    ),
                    "quantity": order_quantity,
                    "unit_code": selling_unit,
                    "manual_override_unit_price": None,
                    "price_override_reason": None,
                }
            ],
        },
    )
    assert order.status_code == 201, order.text
    approval = await client.post(
        f"/v1/sales/orders/{sales_order_id}/commercial-approval",
        headers=auth(
            settings,
            "sales-mnl",
            **{
                "Idempotency-Key": f"{key_prefix}-approval",
                "If-Match": "1",
            },
        ),
        json={
            "warehouse_id": warehouse_id,
            "exception_reason": None,
            "credit_override_reason": None,
        },
    )
    assert approval.status_code == 201, approval.text
    fulfillment = await client.get(
        "/v1/fulfillment/orders",
        headers=auth(settings, "warehouse-mnl"),
        params={"sales_order_id": sales_order_id},
    )
    assert fulfillment.status_code == 200, fulfillment.text
    fulfillment_order = fulfillment.json()["items"][0]
    fixture = {
        "branch_id": branch_id,
        "customer": customer,
        "customer_id": customer["customer_id"],
        "fulfillment_order": fulfillment_order,
        "line_id": line_id,
        "price_list": price_list,
        "sales_order_id": sales_order_id,
        "sku_id": sku["sku_id"],
        "unit_conversion_id": (
            sku["conversions"][0]["unit_conversion_id"] if sku["conversions"] else ""
        ),
        "warehouse_id": warehouse_id,
    }
    receipt = await record_receipt(
        client,
        settings,
        fixture,
        payment_method="cash",
        key=f"{key_prefix}-payment",
        amount=fulfillment_order["payment_required"],
    )
    assert receipt.status_code == 201, receipt.text
    release = await client.post(
        f"/v1/fulfillment/orders/{fulfillment_order['fulfillment_order_id']}/pick-release",
        headers=auth(
            settings,
            "warehouse-mnl",
            **{"Idempotency-Key": f"{key_prefix}-release"},
        ),
        json={"reason": "Lot order payment cleared"},
    )
    assert release.status_code == 201, release.text
    return fixture


@pytest.mark.asyncio
async def test_least_privilege_picker_can_load_scoped_released_work(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_tracked_order(
        picking_client,
        picking_settings,
        tracking_policy="serial",
        expiration_control=False,
        key_prefix="picker-queue",
        order_quantity="1.000000",
        stock_entries=[{"quantity": "1.000000", "serial_numbers": ["PICKER-QUEUE-001"]}],
    )

    queue = await picking_client.get(
        "/v1/fulfillment/orders",
        headers=auth(picking_settings, "warehouse-picker-mnl"),
    )
    assert queue.status_code == 200, queue.text
    assert fixture["fulfillment_order"]["fulfillment_order_id"] in {
        item["fulfillment_order_id"] for item in queue.json()["items"]
    }
    context = await picking_client.get(
        "/v1/fulfillment/orders/"
        f"{fixture['fulfillment_order']['fulfillment_order_id']}/picking-context",
        headers=auth(picking_settings, "warehouse-picker-mnl"),
    )
    assert context.status_code == 200, context.text
    forbidden_release = await picking_client.post(
        "/v1/fulfillment/orders/"
        f"{fixture['fulfillment_order']['fulfillment_order_id']}/pick-release",
        headers=auth(
            picking_settings,
            "warehouse-picker-mnl",
            **{"Idempotency-Key": "picker-must-not-release"},
        ),
        json={"reason": "A picker must not release work"},
    )
    assert forbidden_release.status_code == 403, forbidden_release.text
    assert forbidden_release.json()["error"]["code"] == "capability_required"


async def approved_lot_order(
    client: AsyncClient,
    settings: Settings,
) -> dict[str, object]:
    return await approved_tracked_order(
        client,
        settings,
        tracking_policy="lot",
        expiration_control=True,
        key_prefix="lot-pick",
        stock_entries=[
            {
                "quantity": "1.000000",
                "lot_code": "LOT-EARLY",
                "expiration_date": "2026-09-30",
            },
            {
                "quantity": "1.000000",
                "lot_code": "LOT-LATE",
                "expiration_date": "2026-12-31",
            },
        ],
    )


async def approved_serial_order(
    client: AsyncClient,
    settings: Settings,
) -> dict[str, object]:
    return await approved_tracked_order(
        client,
        settings,
        tracking_policy="serial",
        expiration_control=False,
        key_prefix="serial-pick",
        stock_entries=[
            {
                "quantity": "2.000000",
                "serial_numbers": ["SN-001", "SN-002"],
            }
        ],
        barcodes=[{"barcode": "SERIAL-EA-BC", "unit_code": None}],
    )


@pytest.mark.asyncio
async def test_expiration_control_automatically_selects_earliest_eligible_lot(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_lot_order(picking_client, picking_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    response = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "pick-earliest-lot"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "selections": [],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["lines"][0]["lot_selections"] == [
        {
            "lot_code": "LOT-EARLY",
            "expiration_date": "2026-09-30",
            "quantity_base": "1.000000",
            "recommended": "true",
        }
    ]


@pytest.mark.asyncio
async def test_later_lot_requires_reason_and_fefo_override_authority(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_lot_order(picking_client, picking_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    command = {
        "pick_id": str(uuid4()),
        "expected_fulfillment_version": 3,
        "lines": [
            {
                "line_id": fixture["line_id"],
                "quantity": "1.000000",
                "unit_code": "EA",
                "selections": [
                    {
                        "lot_code": "LOT-LATE",
                        "quantity": "1.000000",
                        "serial_number": None,
                        "barcode": None,
                        "manual_reason": "Picker confirmed the labeled lot",
                        "fefo_override_reason": None,
                    }
                ],
            }
        ],
    }
    missing_reason = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "late-lot-missing-reason"},
        ),
        json=command,
    )
    assert missing_reason.status_code == 422, missing_reason.text
    assert missing_reason.json()["error"]["code"] == "fefo_override_reason_required"

    command["lines"][0]["selections"][0]["fefo_override_reason"] = (
        "Customer requires the longer remaining shelf life"
    )
    no_authority = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "late-lot-no-authority"},
        ),
        json=command,
    )
    assert no_authority.status_code == 403, no_authority.text
    assert no_authority.json()["error"]["code"] == "capability_required"

    accepted = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "late-lot-authorized"},
        ),
        json=command,
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["lines"][0]["lot_selections"] == [
        {
            "lot_code": "LOT-LATE",
            "expiration_date": "2026-12-31",
            "quantity_base": "1.000000",
            "recommended": "false",
        }
    ]


@pytest.mark.asyncio
async def test_equal_expiry_lot_scan_is_fefo_without_override(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_tracked_order(
        picking_client,
        picking_settings,
        tracking_policy="lot",
        expiration_control=True,
        key_prefix="fefo-tie",
        stock_entries=[
            {
                "quantity": "1.000000",
                "lot_code": "TIE-A",
                "expiration_date": "2026-12-31",
            },
            {
                "quantity": "1.000000",
                "lot_code": "TIE-B",
                "expiration_date": "2026-12-31",
            },
        ],
    )
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    response = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "pick-fefo-tie-b"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "selections": [{"barcode": "TIE-B"}],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["lines"][0]["lot_selections"] == [
        {
            "lot_code": "TIE-B",
            "expiration_date": "2026-12-31",
            "quantity_base": "1.000000",
            "recommended": "true",
        }
    ]


@pytest.mark.asyncio
async def test_expired_lot_is_never_resolved_or_manually_pickable(
    picking_client: AsyncClient,
    picking_settings: Settings,
    postgres_url: str,
) -> None:
    initially_fresh = date.today() + timedelta(days=1)
    expired = date.today() - timedelta(days=1)
    fixture = await approved_tracked_order(
        picking_client,
        picking_settings,
        tracking_policy="lot",
        expiration_control=True,
        key_prefix="expired-lot",
        stock_entries=[
            {
                "quantity": "1.000000",
                "lot_code": "LOT-EXPIRED",
                "expiration_date": initially_fresh.isoformat(),
            },
            {
                "quantity": "1.000000",
                "lot_code": "LOT-FRESH",
                "expiration_date": "2026-12-31",
            },
        ],
    )
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE inventory_availability
                SET expiration_date = :expired
                WHERE sku_id = :sku_id AND lot_code = 'LOT-EXPIRED'
                """
            ),
            {"expired": expired, "sku_id": fixture["sku_id"]},
        )
    await engine.dispose()
    scan = await picking_client.post(
        "/v1/inventory/barcodes/resolve",
        headers=auth(picking_settings, "warehouse-mnl"),
        json={
            "warehouse_id": fixture["warehouse_id"],
            "barcode": "LOT-EXPIRED",
        },
    )
    assert scan.status_code == 404, scan.text
    assert scan.json()["error"]["code"] == "barcode_mapping_not_found"

    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    manual = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "pick-expired-lot"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "selections": [
                        {
                            "lot_code": "LOT-EXPIRED",
                            "quantity": "1.000000",
                            "manual_reason": "Damaged label required manual lookup",
                        }
                    ],
                }
            ],
        },
    )
    assert manual.status_code == 409, manual.text
    assert manual.json()["error"]["code"] == "eligible_pick_stock_insufficient"


@pytest.mark.asyncio
async def test_pick_allocates_released_quantity_across_fefo_lots_and_locations(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_tracked_order(
        picking_client,
        picking_settings,
        tracking_policy="lot",
        expiration_control=True,
        key_prefix="split-lot-pick",
        order_quantity="2.000000",
        stock_entries=[
            {
                "quantity": "1.000000",
                "lot_code": "SPLIT-EARLY",
                "expiration_date": "2026-10-31",
                "location_code": "PICK-FACE-A",
            },
            {
                "quantity": "1.000000",
                "lot_code": "SPLIT-LATE",
                "expiration_date": "2026-12-31",
                "location_code": "PICK-FACE-B",
            },
        ],
    )
    response = await picking_client.post(
        f"/v1/fulfillment/orders/{fixture['fulfillment_order']['fulfillment_order_id']}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "split-lot-fefo-pick"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "selections": [],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["picked_quantity_base"] == "2.000000"
    assert [line["lot_selections"][0]["lot_code"] for line in response.json()["lines"]] == [
        "SPLIT-EARLY",
        "SPLIT-LATE",
    ]
    assert [line["quantity_base"] for line in response.json()["lines"]] == [
        "1.000000",
        "1.000000",
    ]
    assert [line["lot_selections"][0]["recommended"] for line in response.json()["lines"]] == [
        "true",
        "true",
    ]


@pytest.mark.asyncio
async def test_explicit_lot_identity_can_span_multiple_available_locations(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_tracked_order(
        picking_client,
        picking_settings,
        tracking_policy="lot",
        expiration_control=True,
        key_prefix="same-lot-two-bins",
        order_quantity="2.000000",
        stock_entries=[
            {
                "quantity": "1.000000",
                "lot_code": "SHARED-LOT",
                "expiration_date": "2026-12-31",
                "location_code": "SHARED-LOT-BIN-A",
            },
            {
                "quantity": "1.000000",
                "lot_code": "SHARED-LOT",
                "expiration_date": "2026-12-31",
                "location_code": "SHARED-LOT-BIN-B",
            },
        ],
    )
    response = await picking_client.post(
        f"/v1/fulfillment/orders/{fixture['fulfillment_order']['fulfillment_order_id']}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "same-lot-two-bins-pick"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "selections": [{"barcode": "SHARED-LOT", "quantity": "2.000000"}],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert [line["quantity_base"] for line in response.json()["lines"]] == [
        "1.000000",
        "1.000000",
    ]
    assert {line["lot_selections"][0]["lot_code"] for line in response.json()["lines"]} == {
        "SHARED-LOT"
    }
    assert {line["lot_selections"][0]["recommended"] for line in response.json()["lines"]} == {
        "true"
    }


@pytest.mark.asyncio
async def test_pick_assigns_serials_from_multiple_available_rows_and_locations(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_tracked_order(
        picking_client,
        picking_settings,
        tracking_policy="serial",
        expiration_control=False,
        key_prefix="split-serial-pick",
        order_quantity="2.000000",
        stock_entries=[
            {
                "quantity": "1.000000",
                "serial_numbers": ["SPLIT-SN-A"],
                "location_code": "SERIAL-BIN-A",
            },
            {
                "quantity": "1.000000",
                "serial_numbers": ["SPLIT-SN-B"],
                "location_code": "SERIAL-BIN-B",
            },
        ],
    )
    response = await picking_client.post(
        f"/v1/fulfillment/orders/{fixture['fulfillment_order']['fulfillment_order_id']}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "split-serial-row-pick"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "selections": [
                        {"barcode": "SPLIT-SN-A"},
                        {"barcode": "SPLIT-SN-B"},
                    ],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["picked_quantity_base"] == "2.000000"
    assert sorted(line["serial_selections"] for line in response.json()["lines"]) == [
        ["SPLIT-SN-A"],
        ["SPLIT-SN-B"],
    ]


@pytest.mark.asyncio
async def test_serial_pick_requires_exact_unique_available_identities(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_serial_order(picking_client, picking_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    first_command = {
        "pick_id": str(uuid4()),
        "expected_fulfillment_version": 3,
        "lines": [
            {
                "line_id": fixture["line_id"],
                "quantity": "1.000000",
                "unit_code": "EA",
                "selections": [
                    {
                        "serial_number": "SN-001",
                        "quantity": "1.000000",
                        "lot_code": None,
                        "barcode": None,
                        "manual_reason": "Serial label confirmed",
                        "fefo_override_reason": None,
                    }
                ],
            }
        ],
    }
    first = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "pick-serial-001"},
        ),
        json=first_command,
    )
    assert first.status_code == 201, first.text
    assert first.json()["lines"][0]["serial_selections"] == ["SN-001"]

    duplicate_command = {
        **first_command,
        "pick_id": str(uuid4()),
        "expected_fulfillment_version": 4,
    }
    duplicate = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "pick-serial-001-again"},
        ),
        json=duplicate_command,
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "serial_already_picked"


@pytest.mark.asyncio
async def test_scanned_serial_posts_exact_custody_and_capture_evidence(
    picking_client: AsyncClient,
    picking_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_serial_order(picking_client, picking_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    pick_id = str(uuid4())
    response = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{
                "Idempotency-Key": "scan-serial-001",
                "X-Correlation-ID": "11111111-1111-4111-8111-111111111111",
            },
        ),
        json={
            "pick_id": pick_id,
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "selections": [{"barcode": "SN-001"}],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["lines"][0]["serial_selections"] == ["SN-001"]

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        movements = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT movement_leg,quantity_base,value_delta,correlation_id,
                           movement_group_id
                    FROM stock_movements
                    WHERE source_reference LIKE :source_reference
                    ORDER BY movement_leg
                    """
                    ),
                    {"source_reference": f"PICK:{pick_id}:%"},
                )
            )
            .mappings()
            .all()
        )
        captured_barcode = await connection.scalar(
            text(
                """
                SELECT captured_barcode
                FROM pick_identity_assignments pia
                JOIN pick_lines pl ON pl.pick_line_id = pia.pick_line_id
                WHERE pl.pick_id = :pick_id
                """
            ),
            {"pick_id": pick_id},
        )
    await engine.dispose()
    assert [row["movement_leg"] for row in movements] == [
        "pick_available_out",
        "pick_staging_in",
    ]
    assert {str(row["quantity_base"]) for row in movements} == {"1.000000"}
    assert sum(row["value_delta"] for row in movements) == 0
    assert {row["movement_group_id"] for row in movements} == {movements[0]["movement_group_id"]}
    assert {row["correlation_id"] for row in movements} == {"11111111-1111-4111-8111-111111111111"}
    assert captured_barcode == "SN-001"


@pytest.mark.asyncio
async def test_pick_and_context_require_direct_warehouse_scope(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_serial_order(picking_client, picking_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    context = await picking_client.get(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picking-context",
        headers=auth(picking_settings, "warehouse-cross-scope"),
    )
    assert context.status_code == 403, context.text
    assert context.json()["error"]["code"] == "operational_scope_required"

    history = await picking_client.get(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(picking_settings, "warehouse-cross-scope"),
    )
    assert history.status_code == 403, history.text
    assert history.json()["error"]["code"] == "operational_scope_required"

    barcode = await picking_client.post(
        "/v1/inventory/barcodes/resolve",
        headers=auth(picking_settings, "warehouse-cross-scope"),
        json={"warehouse_id": fixture["warehouse_id"], "barcode": "SN-001"},
    )
    assert barcode.status_code == 403, barcode.text
    assert barcode.json()["error"]["code"] == "operational_scope_required"

    pick = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-cross-scope",
            **{"Idempotency-Key": "branch-cross-scope-pick"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "selections": [{"barcode": "SN-001"}],
                }
            ],
        },
    )
    assert pick.status_code == 403, pick.text
    assert pick.json()["error"]["code"] == "operational_scope_required"


@pytest.mark.asyncio
async def test_barcode_resolution_is_scoped_unique_and_identity_aware(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_serial_order(picking_client, picking_settings)
    item = await picking_client.post(
        "/v1/inventory/barcodes/resolve",
        headers=auth(picking_settings, "warehouse-mnl"),
        json={
            "warehouse_id": fixture["warehouse_id"],
            "barcode": " serial-ea-bc ",
        },
    )
    assert item.status_code == 200, item.text
    assert item.json()["sku_id"] == fixture["sku_id"]
    assert item.json()["unit_code"] == "EA"
    assert item.json()["serial_number"] is None

    serial = await picking_client.post(
        "/v1/inventory/barcodes/resolve",
        headers=auth(picking_settings, "warehouse-mnl"),
        json={"warehouse_id": fixture["warehouse_id"], "barcode": "SN-001"},
    )
    assert serial.status_code == 200, serial.text
    assert serial.json()["sku_id"] == fixture["sku_id"]
    assert serial.json()["serial_number"] == "SN-001"

    forbidden = await picking_client.post(
        "/v1/inventory/barcodes/resolve",
        headers=auth(picking_settings, "warehouse-ceb"),
        json={"warehouse_id": fixture["warehouse_id"], "barcode": "SN-001"},
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["error"]["code"] == "operational_scope_required"

    unknown = await picking_client.post(
        "/v1/inventory/barcodes/resolve",
        headers=auth(picking_settings, "warehouse-mnl"),
        json={"warehouse_id": fixture["warehouse_id"], "barcode": "UNKNOWN"},
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["code"] == "barcode_mapping_not_found"


@pytest.mark.asyncio
async def test_barcode_resolution_rejects_inactive_and_ambiguous_matches(
    picking_client: AsyncClient,
    picking_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_tracked_order(
        picking_client,
        picking_settings,
        tracking_policy="serial",
        expiration_control=False,
        key_prefix="barcode-policy",
        stock_entries=[
            {
                "quantity": "2.000000",
                "serial_numbers": ["DUP-ID", "ACTIVE-ID"],
            }
        ],
        barcodes=[
            {"barcode": "DUP-ID", "unit_code": None},
            {"barcode": "INACTIVE-CATALOG", "unit_code": None},
        ],
    )
    ambiguous = await picking_client.post(
        "/v1/inventory/barcodes/resolve",
        headers=auth(picking_settings, "warehouse-mnl"),
        json={"warehouse_id": fixture["warehouse_id"], "barcode": "DUP-ID"},
    )
    assert ambiguous.status_code == 409, ambiguous.text
    assert ambiguous.json()["error"]["code"] == "barcode_mapping_ambiguous"

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE barcode_mappings
                SET is_active = false
                WHERE sku_id = :sku_id AND barcode = 'INACTIVE-CATALOG'
                """
            ),
            {"sku_id": fixture["sku_id"]},
        )
    await engine.dispose()
    inactive = await picking_client.post(
        "/v1/inventory/barcodes/resolve",
        headers=auth(picking_settings, "warehouse-mnl"),
        json={
            "warehouse_id": fixture["warehouse_id"],
            "barcode": "INACTIVE-CATALOG",
        },
    )
    assert inactive.status_code == 422, inactive.text
    assert inactive.json()["error"]["code"] == "barcode_mapping_inactive"


@pytest.mark.asyncio
async def test_pick_reversal_is_immutable_idempotent_and_restores_reservation(
    picking_client: AsyncClient,
    picking_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(picking_client, picking_settings, postgres_url)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    receipt = await record_receipt(
        picking_client,
        picking_settings,
        fixture,
        payment_method="cash",
        key="reversal-payment",
    )
    assert receipt.status_code == 201, receipt.text
    release = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/pick-release",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "reversal-release"},
        ),
        json={"reason": "Release reversal scenario"},
    )
    assert release.status_code == 201, release.text
    pick_id = str(uuid4())
    picked = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "reversal-original-pick"},
        ),
        json={
            "pick_id": pick_id,
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "selections": [],
                }
            ],
        },
    )
    assert picked.status_code == 201, picked.text

    reversal_id = str(uuid4())
    command = {
        "reversal_pick_id": reversal_id,
        "expected_fulfillment_version": 4,
        "reason": "Picker selected the wrong carton",
    }
    reversed_response = await picking_client.post(
        f"/v1/fulfillment/picks/{pick_id}/reversal",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "reverse-original-pick"},
        ),
        json=command,
    )
    assert reversed_response.status_code == 201, reversed_response.text
    assert reversed_response.json()["original_pick_id"] == pick_id
    assert reversed_response.json()["reversal_pick_id"] == reversal_id
    assert reversed_response.json()["status"] == "reversed"
    assert reversed_response.json()["reversed_quantity_base"] == "1.000000"
    assert reversed_response.json()["version"] == 5

    replay = await picking_client.post(
        f"/v1/fulfillment/picks/{pick_id}/reversal",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "reverse-original-pick"},
        ),
        json=command,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == reversed_response.json()

    availability = await picking_client.get(
        "/v1/inventory/availability",
        headers=auth(picking_settings, "warehouse-mnl"),
        params={"query": "PREPAID-EA"},
    )
    assert availability.status_code == 200, availability.text
    rows = availability.json()["items"]
    available = next(row for row in rows if row["location_code"] == "PAYMENT-AVAILABLE")
    staging = next(row for row in rows if row["location_code"] == "DISPATCH-STAGING")
    assert available["on_hand"] == "2.000000"
    assert staging["on_hand"] == "0.000000"
    assert available["commercial_reserved"] == "2.000000"
    assert available["warehouse_on_hand"] == "2.000000"


@pytest.mark.asyncio
async def test_picking_context_and_history_expose_authoritative_progress(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_lot_order(picking_client, picking_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    before = await picking_client.get(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picking-context",
        headers=auth(picking_settings, "warehouse-mnl"),
    )
    assert before.status_code == 200, before.text
    assert before.json()["version"] == 3
    assert before.json()["status"] == "pick_released"
    assert before.json()["lines"][0]["remaining_quantity_base"] == "2.000000"
    assert before.json()["lines"][0]["fefo_candidates"] == [
        {
            "lot_code": "LOT-EARLY",
            "expiration_date": "2026-09-30",
            "available_quantity_base": "1.000000",
            "recommended": True,
        },
        {
            "lot_code": "LOT-LATE",
            "expiration_date": "2026-12-31",
            "available_quantity_base": "1.000000",
            "recommended": False,
        },
    ]

    pick_id = str(uuid4())
    posted = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "context-history-pick"},
        ),
        json={
            "pick_id": pick_id,
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "selections": [],
                }
            ],
        },
    )
    assert posted.status_code == 201, posted.text

    after = await picking_client.get(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picking-context",
        headers=auth(picking_settings, "warehouse-mnl"),
    )
    assert after.status_code == 200, after.text
    assert after.json()["version"] == 4
    assert after.json()["status"] == "partially_picked"
    assert after.json()["lines"][0]["picked_quantity_base"] == "1.000000"
    assert after.json()["lines"][0]["remaining_quantity_base"] == "1.000000"
    assert after.json()["lines"][0]["fefo_candidates"] == [
        {
            "lot_code": "LOT-LATE",
            "expiration_date": "2026-12-31",
            "available_quantity_base": "1.000000",
            "recommended": True,
        }
    ]

    history = await picking_client.get(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(picking_settings, "warehouse-mnl"),
    )
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["pick_id"] == pick_id
    assert history.json()["items"][0]["event_type"] == "posted"
    assert history.json()["items"][0]["quantity_base"] == "1.000000"
    assert history.json()["items"][0]["actor_subject"] == "warehouse-mnl"


@pytest.mark.asyncio
async def test_pick_uses_the_approved_unit_conversion_snapshot(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_tracked_order(
        picking_client,
        picking_settings,
        tracking_policy="untracked",
        expiration_control=False,
        key_prefix="case-pick",
        conversions=[
            {
                "unit_code": "CASE",
                "base_quantity": "12.000000",
                "effective_from": "2026-01-01",
                "effective_to": None,
            }
        ],
        selling_unit="CASE",
        order_quantity="1.500000",
        stock_entries=[{"quantity": "18.000000"}],
    )
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    response = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "pick-half-case"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "0.500000",
                    "unit_code": "CASE",
                    "selections": [],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["picked_quantity_base"] == "6.000000"
    assert response.json()["remaining_quantity_base"] == "12.000000"
    assert response.json()["lines"][0]["conversion_snapshot"] == {
        "entered_quantity": "0.500000",
        "entered_unit": "CASE",
        "base_quantity_per_unit": "12.000000",
        "base_quantity": "6.000000",
        "unit_conversion_id": fixture["unit_conversion_id"],
    }


@pytest.mark.asyncio
async def test_pick_inventory_and_reservation_projections_rebuild_exactly(
    picking_client: AsyncClient,
    picking_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_serial_order(picking_client, picking_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    picked = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "rebuild-serial-pick"},
        ),
        json={
            "pick_id": str(uuid4()),
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "selections": [
                        {
                            "serial_number": "SN-001",
                            "quantity": "1.000000",
                            "lot_code": None,
                            "barcode": None,
                            "manual_reason": "Serial label confirmed",
                            "fefo_override_reason": None,
                        }
                    ],
                }
            ],
        },
    )
    assert picked.status_code == 201, picked.text
    before = await picking_client.get(
        "/v1/inventory/availability",
        headers=auth(picking_settings, "warehouse-mnl"),
        params={"query": "SERIAL-PICK-EA"},
    )
    assert before.status_code == 200, before.text

    async def reservation_snapshot() -> tuple[dict[str, str], dict[str, str]]:
        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            aggregate = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT reserved_quantity_base
                        FROM inventory_reserved_by_sku_warehouse
                        WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id
                        """
                        ),
                        {
                            "sku_id": fixture["sku_id"],
                            "warehouse_id": fixture["warehouse_id"],
                        },
                    )
                )
                .mappings()
                .one()
            )
            commitment = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT reserved_quantity_base,picked_quantity_base,
                               backorder_quantity_base
                        FROM sales_order_line_commitments
                        WHERE sales_order_id = :sales_order_id AND line_id = :line_id
                        """
                        ),
                        {
                            "sales_order_id": fixture["sales_order_id"],
                            "line_id": fixture["line_id"],
                        },
                    )
                )
                .mappings()
                .one()
            )
        await engine.dispose()
        return (
            {key: str(value) for key, value in aggregate.items()},
            {key: str(value) for key, value in commitment.items()},
        )

    reservation_before = await reservation_snapshot()

    inventory_rebuild = await picking_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(picking_settings, "warehouse-supervisor-mnl"),
    )
    assert inventory_rebuild.status_code == 200, inventory_rebuild.text
    reservation_rebuild = await picking_client.post(
        "/v1/sales/projections/rebuild",
        headers=auth(picking_settings, "sales-mnl"),
    )
    assert reservation_rebuild.status_code == 200, reservation_rebuild.text

    after = await picking_client.get(
        "/v1/inventory/availability",
        headers=auth(picking_settings, "warehouse-mnl"),
        params={"query": "SERIAL-PICK-EA"},
    )
    assert after.status_code == 200, after.text
    assert after.json() == before.json()
    assert await reservation_snapshot() == reservation_before


@pytest.mark.asyncio
async def test_concurrent_serial_picks_cannot_stage_the_same_identity_twice(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_serial_order(picking_client, picking_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]

    async def attempt(key: str) -> object:
        return await picking_client.post(
            f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
            headers=auth(
                picking_settings,
                "warehouse-supervisor-mnl",
                **{"Idempotency-Key": key},
            ),
            json={
                "pick_id": str(uuid4()),
                "expected_fulfillment_version": 3,
                "lines": [
                    {
                        "line_id": fixture["line_id"],
                        "quantity": "1.000000",
                        "unit_code": "EA",
                        "selections": [
                            {
                                "serial_number": "SN-001",
                                "quantity": "1.000000",
                                "lot_code": None,
                                "barcode": None,
                                "manual_reason": "Concurrent scan",
                                "fefo_override_reason": None,
                            }
                        ],
                    }
                ],
            },
        )

    results = await asyncio.gather(attempt("serial-race-a"), attempt("serial-race-b"))
    assert sorted(result.status_code for result in results) == [201, 409]
    conflict = next(result for result in results if result.status_code == 409)
    assert conflict.json()["error"]["code"] in {
        "fulfillment_version_conflict",
        "serial_already_picked",
    }

    availability = await picking_client.get(
        "/v1/inventory/availability",
        headers=auth(picking_settings, "warehouse-mnl"),
        params={"query": "SERIAL-PICK-EA"},
    )
    assert availability.status_code == 200, availability.text
    staged_serials = sorted(
        serial_number
        for row in availability.json()["items"]
        if row["location_code"] == "DISPATCH-STAGING"
        for serial_number in row["serial_numbers"]
    )
    assert staged_serials == ["SN-001"]


@pytest.mark.asyncio
async def test_concurrent_fulfillment_orders_cannot_stage_the_same_serial_twice(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_tracked_order(
        picking_client,
        picking_settings,
        tracking_policy="serial",
        expiration_control=False,
        key_prefix="cross-order-race",
        order_quantity="1.000000",
        stock_entries=[
            {
                "quantity": "2.000000",
                "serial_numbers": ["SHARED-001", "SHARED-002"],
            }
        ],
    )
    second_sales_order_id = str(uuid4())
    second_line_id = str(uuid4())
    customer = fixture["customer"]
    price_list = fixture["price_list"]
    second_order = await picking_client.post(
        "/v1/sales/orders",
        headers=auth(
            picking_settings,
            "sales-mnl",
            **{"Idempotency-Key": "cross-order-race-order-2"},
        ),
        json={
            "sales_order_id": second_sales_order_id,
            "branch_id": fixture["branch_id"],
            "customer_id": customer["customer_id"],
            "expected_customer_version": customer["version"],
            "expected_price_list_version_id": price_list["price_list_version_id"],
            "expected_pricing_date": date.today().isoformat(),
            "delivery_address_version_id": customer["addresses"][0]["address_version_id"],
            "payment_timing_policy": None,
            "payment_timing_override_reason": None,
            "order_discount_amount": "0.000000",
            "lines": [
                {
                    "line_id": second_line_id,
                    "sku_id": fixture["sku_id"],
                    "expected_price_list_line_id": price_list["items"][0]["price_list_line_id"],
                    "expected_unit_conversion_id": None,
                    "expected_unit_conversion_version": None,
                    "quantity": "1.000000",
                    "unit_code": "EA",
                    "manual_override_unit_price": None,
                    "price_override_reason": None,
                }
            ],
        },
    )
    assert second_order.status_code == 201, second_order.text
    approval = await picking_client.post(
        f"/v1/sales/orders/{second_sales_order_id}/commercial-approval",
        headers=auth(
            picking_settings,
            "sales-mnl",
            **{
                "Idempotency-Key": "cross-order-race-approval-2",
                "If-Match": "1",
            },
        ),
        json={
            "warehouse_id": fixture["warehouse_id"],
            "exception_reason": None,
            "credit_override_reason": None,
        },
    )
    assert approval.status_code == 201, approval.text
    fulfillment = await picking_client.get(
        "/v1/fulfillment/orders",
        headers=auth(picking_settings, "warehouse-mnl"),
        params={"sales_order_id": second_sales_order_id},
    )
    assert fulfillment.status_code == 200, fulfillment.text
    second_fulfillment = fulfillment.json()["items"][0]
    second_fixture = {
        **fixture,
        "customer_id": customer["customer_id"],
        "sales_order_id": second_sales_order_id,
        "line_id": second_line_id,
        "fulfillment_order": second_fulfillment,
    }
    receipt = await record_receipt(
        picking_client,
        picking_settings,
        second_fixture,
        payment_method="cash",
        key="cross-order-race-payment-2",
        amount=second_fulfillment["payment_required"],
    )
    assert receipt.status_code == 201, receipt.text
    release = await picking_client.post(
        f"/v1/fulfillment/orders/{second_fulfillment['fulfillment_order_id']}/pick-release",
        headers=auth(
            picking_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": "cross-order-race-release-2"},
        ),
        json={"reason": "Release second order for stock-lock race"},
    )
    assert release.status_code == 201, release.text

    async def attempt(fulfillment_order_id: str, line_id: str, key: str) -> object:
        return await picking_client.post(
            f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
            headers=auth(
                picking_settings,
                "warehouse-mnl",
                **{"Idempotency-Key": key},
            ),
            json={
                "pick_id": str(uuid4()),
                "expected_fulfillment_version": 3,
                "lines": [
                    {
                        "line_id": line_id,
                        "quantity": "1.000000",
                        "unit_code": "EA",
                        "selections": [
                            {
                                "barcode": "SHARED-001",
                                "quantity": "1.000000",
                            }
                        ],
                    }
                ],
            },
        )

    first_fulfillment = fixture["fulfillment_order"]
    results = await asyncio.gather(
        attempt(
            first_fulfillment["fulfillment_order_id"],
            fixture["line_id"],
            "cross-order-race-pick-1",
        ),
        attempt(
            second_fulfillment["fulfillment_order_id"],
            second_line_id,
            "cross-order-race-pick-2",
        ),
    )
    assert sorted(result.status_code for result in results) in ([201, 404], [201, 409])
    conflict = next(result for result in results if result.status_code != 201)
    assert conflict.json()["error"]["code"] in {
        "barcode_mapping_not_found",
        "serial_already_picked",
    }

    availability = await picking_client.get(
        "/v1/inventory/availability",
        headers=auth(picking_settings, "warehouse-mnl"),
        params={"query": "CROSS-ORDER-RACE-EA"},
    )
    assert availability.status_code == 200, availability.text
    staged_serials = [
        serial_number
        for row in availability.json()["items"]
        if row["location_code"] == "DISPATCH-STAGING"
        for serial_number in row["serial_numbers"]
    ]
    assert staged_serials == ["SHARED-001"]


@pytest.mark.asyncio
async def test_concurrent_last_lot_unit_is_staged_only_once(
    picking_client: AsyncClient,
    picking_settings: Settings,
) -> None:
    fixture = await approved_tracked_order(
        picking_client,
        picking_settings,
        tracking_policy="lot",
        expiration_control=True,
        key_prefix="last-lot-race",
        stock_entries=[
            {
                "quantity": "1.000000",
                "lot_code": "LAST-EARLY",
                "expiration_date": "2026-10-31",
            },
            {
                "quantity": "1.000000",
                "lot_code": "LAST-LATE",
                "expiration_date": "2026-12-31",
            },
        ],
    )
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]

    async def attempt(key: str) -> object:
        return await picking_client.post(
            f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
            headers=auth(
                picking_settings,
                "warehouse-mnl",
                **{"Idempotency-Key": key},
            ),
            json={
                "pick_id": str(uuid4()),
                "expected_fulfillment_version": 3,
                "lines": [
                    {
                        "line_id": fixture["line_id"],
                        "quantity": "1.000000",
                        "unit_code": "EA",
                        "selections": [],
                    }
                ],
            },
        )

    results = await asyncio.gather(attempt("last-lot-a"), attempt("last-lot-b"))
    assert sorted(result.status_code for result in results) == [201, 409]
    conflict = next(result for result in results if result.status_code == 409)
    assert conflict.json()["error"]["code"] == "fulfillment_version_conflict"

    availability = await picking_client.get(
        "/v1/inventory/availability",
        headers=auth(picking_settings, "warehouse-mnl"),
        params={"query": "LAST-LOT-RACE-EA"},
    )
    assert availability.status_code == 200, availability.text
    staged = [
        row for row in availability.json()["items"] if row["location_code"] == "DISPATCH-STAGING"
    ]
    assert sum(Decimal(row["on_hand"]) for row in staged) == Decimal("1.000000")
