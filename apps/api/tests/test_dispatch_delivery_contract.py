from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_payment_clearance_contract import (
    approved_prepaid_order,
    auth,
    record_receipt,
)
from test_tracked_stock_picking_contract import approved_serial_order
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def dispatch_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def dispatch_client(dispatch_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(dispatch_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_supervisor_dispatches_a_picked_order_to_assigned_delivery_staff(
    dispatch_client: AsyncClient,
    dispatch_settings: Settings,
    postgres_url: str,
) -> None:
    picking_client = dispatch_client
    picking_settings = dispatch_settings
    fixture = await approved_prepaid_order(picking_client, picking_settings, postgres_url)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    receipt = await record_receipt(
        picking_client,
        picking_settings,
        fixture,
        payment_method="cash",
        key="dispatch-payment",
    )
    assert receipt.status_code == 201, receipt.text
    release = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/pick-release",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "dispatch-release"},
        ),
        json={"reason": "Release paid stock for dispatch"},
    )
    assert release.status_code == 201, release.text
    pick_id = str(uuid4())
    pick = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "dispatch-pick"},
        ),
        json={
            "pick_id": pick_id,
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
    assert pick.status_code == 201, pick.text

    delivery_id = str(uuid4())
    command = {
        "delivery_id": delivery_id,
        "expected_fulfillment_version": 4,
        "assigned_to": "delivery-mnl",
        "pick_ids": [pick_id],
    }
    dispatched = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "dispatch-order"},
        ),
        json=command,
    )
    assert dispatched.status_code == 201, dispatched.text
    assert dispatched.json() == {
        "delivery_id": delivery_id,
        "fulfillment_order_id": fulfillment_order_id,
        "status": "dispatched",
        "assigned_to": "delivery-mnl",
        "payment_timing_policy": "prepaid",
        "version": 1,
        "lines": [
            {
                "line_id": fixture["line_id"],
                "sku_id": fixture["sku_id"],
                "quantity_base": "2.000000",
                "lot_selections": [],
                "serial_numbers": [],
                "staging_movement_ids": [dispatched.json()["lines"][0]["staging_movement_ids"][0]],
                "transit_movement_ids": [dispatched.json()["lines"][0]["transit_movement_ids"][0]],
            }
        ],
    }

    replay = await picking_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "dispatch-order"},
        ),
        json=command,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == dispatched.json()

    availability = await picking_client.get(
        "/v1/inventory/availability",
        headers=auth(picking_settings, "warehouse-supervisor-mnl"),
        params={"query": "PREPAID-EA"},
    )
    assert availability.status_code == 200, availability.text
    staging = next(
        row for row in availability.json()["items"] if row["custody"] == "dispatch_staging"
    )
    transit = next(row for row in availability.json()["items"] if row["custody"] == "in_transit")
    assert staging["on_hand"] == "0.000000"
    assert transit["on_hand"] == "2.000000"
    assert transit["warehouse_on_hand"] == "0.000000"

    assigned = await picking_client.get(
        "/v1/deliveries/assigned",
        headers=auth(picking_settings, "delivery-mnl"),
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.headers["cache-control"] == "private, max-age=0, must-revalidate"
    assert assigned.headers["etag"]
    assert assigned.json() == {
        "items": [
            {
                "delivery_id": delivery_id,
                "fulfillment_order_id": fulfillment_order_id,
                "status": "dispatched",
                "version": 1,
                "assigned_to": "delivery-mnl",
                "recipient_name": "Prepaid Retail Customer",
                "delivery_address": {
                    "address_key": "DELIVERY",
                    "version": 1,
                    "kind": "delivery",
                    "line_1": "100 Payment Street",
                    "line_2": None,
                    "city": "Manila",
                    "region": "NCR",
                    "postal_code": "1000",
                    "country_code": "PH",
                },
                "payment_timing_policy": "prepaid",
                "collection_required": False,
                "collection_amount_due": None,
                "evidence_requirements": ["recipient_name", "signature"],
                "lines": [
                    {
                        "line_id": fixture["line_id"],
                        "sku_id": fixture["sku_id"],
                        "sku_code": "PREPAID-EA",
                        "sku_name": "Prepaid Goods Each",
                        "quantity_base": "2.000000",
                        "lot_selections": [],
                        "serial_numbers": [],
                    }
                ],
            }
        ],
        "total": 1,
    }

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE customer_accounts SET legal_name = 'Renamed after Dispatch' "
                "WHERE customer_id = :customer_id"
            ),
            {"customer_id": fixture["customer_id"]},
        )
    await engine.dispose()
    snapshotted = await picking_client.get(
        f"/v1/deliveries/{delivery_id}",
        headers=auth(picking_settings, "delivery-mnl"),
    )
    assert snapshotted.status_code == 200, snapshotted.text
    assert snapshotted.json()["recipient_name"] == "Prepaid Retail Customer"

    backup = await picking_client.get(
        "/v1/deliveries/assigned",
        headers=auth(picking_settings, "delivery-backup-mnl"),
    )
    assert backup.status_code == 200, backup.text
    assert backup.json() == {"items": [], "total": 0}

    reassigned = await picking_client.post(
        f"/v1/deliveries/{delivery_id}/assignment",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "reassign-delivery"},
        ),
        json={
            "expected_delivery_version": 1,
            "assigned_to": "delivery-backup-mnl",
            "reason": "Primary driver became unavailable",
        },
    )
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json() == {
        "delivery_id": delivery_id,
        "status": "dispatched",
        "assigned_to": "delivery-backup-mnl",
        "version": 2,
    }
    stale_assignment = await picking_client.get(
        f"/v1/deliveries/{delivery_id}",
        headers=auth(picking_settings, "delivery-mnl"),
    )
    assert stale_assignment.status_code == 403, stale_assignment.text
    assert stale_assignment.json()["error"]["code"] == "delivery_assignment_required"
    backup_detail = await picking_client.get(
        f"/v1/deliveries/{delivery_id}",
        headers=auth(picking_settings, "delivery-backup-mnl"),
    )
    assert backup_detail.status_code == 200, backup_detail.text
    assert backup_detail.json()["assigned_to"] == "delivery-backup-mnl"
    assert backup_detail.json()["version"] == 2

    restored_assignment = await picking_client.post(
        f"/v1/deliveries/{delivery_id}/assignment",
        headers=auth(
            picking_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "restore-delivery-assignment"},
        ),
        json={
            "expected_delivery_version": 2,
            "assigned_to": "delivery-mnl",
            "reason": "Primary driver is available again",
        },
    )
    assert restored_assignment.status_code == 200, restored_assignment.text
    assert restored_assignment.json()["version"] == 3

    revoked_scope = await picking_client.put(
        "/v1/organization/users/delivery-mnl",
        headers=auth(
            picking_settings,
            "ops-admin",
            **{
                "If-Match": "1",
                "Idempotency-Key": "revoke-delivery-scope",
            },
        ),
        json={
            "display_name": "Manila Delivery Staff",
            "is_operations_administrator": False,
            "is_active": True,
            "role_template_codes": ["DELIVERY_STAFF"],
            "branch_codes": [],
            "warehouse_codes": [],
            "approval_authorities": [],
        },
    )
    assert revoked_scope.status_code == 200, revoked_scope.text
    stale_cache_refresh = await picking_client.get(
        "/v1/deliveries/assigned",
        headers=auth(picking_settings, "delivery-mnl"),
    )
    assert stale_cache_refresh.status_code == 403, stale_cache_refresh.text
    assert stale_cache_refresh.json()["error"]["code"] == "operational_scope_required"


@pytest.mark.asyncio
async def test_two_partial_dispatches_complete_without_resubmitting_prior_picks(
    dispatch_client: AsyncClient,
    dispatch_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(dispatch_client, dispatch_settings, postgres_url)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    receipt = await record_receipt(
        dispatch_client,
        dispatch_settings,
        fixture,
        payment_method="cash",
        key="partial-dispatch-payment",
    )
    assert receipt.status_code == 201, receipt.text
    release = await dispatch_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/pick-release",
        headers=auth(
            dispatch_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "partial-dispatch-release"},
        ),
        json={"reason": "Stage two separate handoffs"},
    )
    assert release.status_code == 201, release.text
    pick_ids = [str(uuid4()), str(uuid4())]
    for index, pick_id in enumerate(pick_ids):
        pick = await dispatch_client.post(
            f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
            headers=auth(
                dispatch_settings,
                "warehouse-supervisor-mnl",
                **{"Idempotency-Key": f"partial-dispatch-pick-{index}"},
            ),
            json={
                "pick_id": pick_id,
                "expected_fulfillment_version": 3 + index,
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
        assert pick.status_code == 201, pick.text

    for index, pick_id in enumerate(pick_ids):
        dispatched = await dispatch_client.post(
            f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
            headers=auth(
                dispatch_settings,
                "warehouse-supervisor-mnl",
                **{"Idempotency-Key": f"partial-dispatch-{index}"},
            ),
            json={
                "delivery_id": str(uuid4()),
                "expected_fulfillment_version": 5 + index,
                "assigned_to": "delivery-mnl",
                "pick_ids": [pick_id],
            },
        )
        assert dispatched.status_code == 201, dispatched.text
        history = await dispatch_client.get(
            f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
            headers=auth(dispatch_settings, "warehouse-supervisor-mnl"),
        )
        assert history.status_code == 200, history.text
        dispatched_by_id = {item["pick_id"]: item["dispatched"] for item in history.json()["items"]}
        assert dispatched_by_id[pick_id] is True

    context = await dispatch_client.get(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picking-context",
        headers=auth(dispatch_settings, "warehouse-supervisor-mnl"),
    )
    assert context.status_code == 200, context.text
    assert context.json()["status"] == "dispatched"


@pytest.mark.asyncio
async def test_concurrent_serial_dispatch_preserves_identities_and_rebuilds_custody(
    dispatch_client: AsyncClient,
    dispatch_settings: Settings,
) -> None:
    fixture = await approved_serial_order(dispatch_client, dispatch_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    pick_id = str(uuid4())
    picked = await dispatch_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            dispatch_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "serial-dispatch-pick"},
        ),
        json={
            "pick_id": pick_id,
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "selections": [
                        {
                            "serial_number": "SN-001",
                            "quantity": "1.000000",
                            "manual_reason": "Serial label confirmed",
                        },
                        {
                            "serial_number": "SN-002",
                            "quantity": "1.000000",
                            "manual_reason": "Serial label confirmed",
                        },
                    ],
                }
            ],
        },
    )
    assert picked.status_code == 201, picked.text
    delivery_ids = [str(uuid4()), str(uuid4())]

    async def dispatch(delivery_id: str, key: str) -> Response:
        return await dispatch_client.post(
            f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
            headers=auth(
                dispatch_settings,
                "warehouse-supervisor-mnl",
                **{"Idempotency-Key": key},
            ),
            json={
                "delivery_id": delivery_id,
                "expected_fulfillment_version": 4,
                "assigned_to": "delivery-mnl",
                "pick_ids": [pick_id],
            },
        )

    first, second = await asyncio.gather(
        dispatch(delivery_ids[0], "serial-dispatch-a"),
        dispatch(delivery_ids[1], "serial-dispatch-b"),
    )
    responses = [first, second]
    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "fulfillment_version_conflict"
    posted = next(response for response in responses if response.status_code == 201)
    assert posted.json()["lines"][0]["serial_numbers"] == ["SN-001", "SN-002"]

    rebuilt = await dispatch_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(dispatch_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    availability = await dispatch_client.get(
        "/v1/inventory/availability",
        headers=auth(dispatch_settings, "warehouse-supervisor-mnl"),
        params={"query": "SERIAL-PICK-EA"},
    )
    assert availability.status_code == 200, availability.text
    transit_serials = sorted(
        serial_number
        for row in availability.json()["items"]
        if row["custody"] == "in_transit"
        for serial_number in row["serial_numbers"]
    )
    assert transit_serials == ["SN-001", "SN-002"]


@pytest.mark.asyncio
async def test_dispatch_rejects_incomplete_serial_identity_coverage(
    dispatch_client: AsyncClient,
    dispatch_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_serial_order(dispatch_client, dispatch_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    pick_id = str(uuid4())
    picked = await dispatch_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            dispatch_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "identity-guard-pick"},
        ),
        json={
            "pick_id": pick_id,
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "selections": [
                        {
                            "serial_number": "SN-001",
                            "quantity": "1.000000",
                            "manual_reason": "Serial label confirmed",
                        },
                        {
                            "serial_number": "SN-002",
                            "quantity": "1.000000",
                            "manual_reason": "Serial label confirmed",
                        },
                    ],
                }
            ],
        },
    )
    assert picked.status_code == 201, picked.text

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(text("ALTER TABLE pick_lines DISABLE TRIGGER USER"))
        await connection.execute(
            text("UPDATE pick_lines SET quantity_base = 3 WHERE pick_id = :pick_id"),
            {"pick_id": pick_id},
        )
        await connection.execute(text("ALTER TABLE pick_lines ENABLE TRIGGER USER"))
    await engine.dispose()

    dispatched = await dispatch_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
        headers=auth(
            dispatch_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "identity-guard-dispatch"},
        ),
        json={
            "delivery_id": str(uuid4()),
            "expected_fulfillment_version": 4,
            "assigned_to": "delivery-mnl",
            "pick_ids": [pick_id],
        },
    )
    assert dispatched.status_code == 409, dispatched.text
    assert dispatched.json()["error"]["code"] == "dispatch_identity_conflict"
