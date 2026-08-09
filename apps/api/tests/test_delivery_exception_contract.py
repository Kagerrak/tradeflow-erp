from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from test_delivery_confirmation_contract import FakeObjectStorage, dispatched_prepaid_delivery
from test_payment_clearance_contract import auth
from test_tracked_stock_picking_contract import approved_serial_order
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings

PARTITION_FIELDS = (
    "accepted_quantity_base",
    "refused_quantity_base",
    "damaged_quantity_base",
    "short_missing_quantity_base",
    "still_undelivered_quantity_base",
)


@pytest.fixture
def confirmation_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def confirmation_client(
    confirmation_settings: Settings,
) -> AsyncIterator[AsyncClient]:
    app = create_app(confirmation_settings)
    app.state.object_storage = FakeObjectStorage()
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def _delivery_line_id(postgres_url: str, delivery_id: str) -> str:
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        value = await connection.scalar(
            text(
                "SELECT delivery_line_id FROM delivery_lines "
                "WHERE delivery_id = :delivery_id ORDER BY delivery_line_id LIMIT 1"
            ),
            {"delivery_id": delivery_id},
        )
    await engine.dispose()
    assert value is not None
    return str(value)


async def _verified_evidence(
    client: AsyncClient,
    settings: Settings,
    delivery_id: str,
    *,
    kind: str = "photo",
) -> str:
    evidence_id = str(uuid4())
    uploaded = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": kind,
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-10T08:00:00Z",
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    completed = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
        headers=auth(settings, "delivery-mnl"),
    )
    assert completed.status_code == 200, completed.text
    return evidence_id


def _line_partition(
    delivery_line_id: str,
    evidence_id: str,
    *,
    accepted: str = "0.000000",
    refused: str = "0.000000",
    damaged: str = "0.000000",
    short_missing: str = "0.000000",
    still_undelivered: str = "0.000000",
) -> dict[str, object]:
    outcome_quantities = {
        "refused": refused,
        "damaged": damaged,
        "short_missing": short_missing,
        "still_undelivered": still_undelivered,
    }
    return {
        "delivery_line_id": delivery_line_id,
        "accepted_quantity_base": accepted,
        "refused_quantity_base": refused,
        "damaged_quantity_base": damaged,
        "short_missing_quantity_base": short_missing,
        "still_undelivered_quantity_base": still_undelivered,
        "exception_details": {
            outcome: {
                "evidence_ids": [evidence_id],
                "reason": f"{outcome} outcome verified with the responsible custodian",
                "responsible_party_type": "carrier",
            }
            for outcome, quantity in outcome_quantities.items()
            if Decimal(quantity) > 0
        },
    }


def _confirmation_command(
    evidence_id: str,
    line: dict[str, object],
    *,
    confirmation_id: str | None = None,
    expected_version: int = 1,
) -> dict[str, object]:
    return {
        "confirmation_id": confirmation_id or str(uuid4()),
        "expected_delivery_version": expected_version,
        "recipient_name": "Exception Contract Recipient",
        "device_captured_at": "2026-08-10T08:01:00Z",
        "notes": "Issue #12 custody partition contract",
        "evidence_ids": [evidence_id],
        "lines": [line],
        "collection": None,
        "on_account_conversion_id": None,
    }


async def _confirm(
    client: AsyncClient,
    settings: Settings,
    delivery_id: str,
    command: dict[str, object],
    *,
    key: str,
) -> Response:
    return await client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(settings, "delivery-mnl", **{"Idempotency-Key": key}),
        json=command,
    )


async def _custody(postgres_url: str, delivery_id: str) -> dict[str, Decimal]:
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT location.custody, coalesce(sum(availability.on_hand), 0) AS quantity
                    FROM delivery_dispatches delivery
                    JOIN delivery_lines line ON line.delivery_id = delivery.delivery_id
                    JOIN warehouse_stock_locations location
                      ON location.warehouse_id = delivery.warehouse_id
                    LEFT JOIN inventory_availability availability
                      ON availability.location_id = location.location_id
                     AND availability.sku_id = line.sku_id
                    WHERE delivery.delivery_id = :delivery_id
                    GROUP BY location.custody
                    """
                ),
                {"delivery_id": delivery_id},
            )
        ).mappings()
    await engine.dispose()
    return {str(row["custody"]): Decimal(row["quantity"]) for row in rows}


async def _dispatched_serial_delivery(
    client: AsyncClient, settings: Settings, postgres_url: str
) -> tuple[dict[str, object], str]:
    fixture = await approved_serial_order(client, settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    pick_id = str(uuid4())
    picked = await client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "exception-serial-pick"},
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
                            "serial_number": serial,
                            "quantity": "1.000000",
                            "manual_reason": "Explicit delivery identity contract",
                        }
                        for serial in ("SN-001", "SN-002")
                    ],
                }
            ],
        },
    )
    assert picked.status_code == 201, picked.text
    delivery_id = str(uuid4())
    dispatched = await client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
        headers=auth(
            settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "exception-serial-dispatch"},
        ),
        json={
            "delivery_id": delivery_id,
            "expected_fulfillment_version": picked.json()["version"],
            "assigned_to": "delivery-mnl",
            "pick_ids": [pick_id],
        },
    )
    assert dispatched.status_code == 201, dispatched.text
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO document_series(document_series_id,branch_id,document_type,"
                "prefix,next_number) VALUES (:id,:branch_id,'delivery_receipt','DR-MNL',1)"
            ),
            {"id": uuid4(), "branch_id": fixture["branch_id"]},
        )
    await engine.dispose()
    return fixture, delivery_id


@pytest.mark.asyncio
async def test_mixed_partition_posts_only_accepted_quantity(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture, delivery_id = await dispatched_prepaid_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    pod_evidence_id = await _verified_evidence(
        confirmation_client, confirmation_settings, delivery_id, kind="signature"
    )
    exception_evidence_id = await _verified_evidence(
        confirmation_client, confirmation_settings, delivery_id
    )
    delivery_line_id = await _delivery_line_id(postgres_url, delivery_id)
    command = _confirmation_command(
        pod_evidence_id,
        _line_partition(
            delivery_line_id,
            exception_evidence_id,
            accepted="0.400000",
            refused="0.400000",
            damaged="0.400000",
            short_missing="0.400000",
            still_undelivered="0.400000",
        ),
    )
    confirmed = await _confirm(
        confirmation_client,
        confirmation_settings,
        delivery_id,
        command,
        key="mixed-delivery-partition",
    )
    assert confirmed.status_code == 201, confirmed.text

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        partition = (
            (
                await connection.execute(
                    text(
                        "SELECT accepted_quantity_base, refused_quantity_base, "
                        "damaged_quantity_base, short_missing_quantity_base, "
                        "still_undelivered_quantity_base FROM delivery_confirmation_lines "
                        "WHERE confirmation_id = :confirmation_id"
                    ),
                    {"confirmation_id": command["confirmation_id"]},
                )
            )
            .mappings()
            .one()
        )
        outbound = await connection.scalar(
            text(
                "SELECT coalesce(sum(quantity_base), 0) FROM stock_movements "
                "WHERE movement_type = 'delivery_confirmation'"
            )
        )
        receipt_lines = await connection.scalar(
            text(
                "SELECT snapshot -> 'lines' FROM delivery_receipts "
                "WHERE confirmation_id = :confirmation_id"
            ),
            {"confirmation_id": command["confirmation_id"]},
        )
    await engine.dispose()
    assert {field: partition[field] for field in PARTITION_FIELDS} == {
        field: Decimal("0.400000") for field in PARTITION_FIELDS
    }
    assert outbound == Decimal("0.400000")
    assert receipt_lines[0]["accepted_quantity_base"] == "0.400000"
    assert receipt_lines[0]["line_id"] == fixture["line_id"]
    custody = await _custody(postgres_url, delivery_id)
    assert custody["in_transit"] == Decimal("1.200000")
    assert custody["investigation"] == Decimal("0.400000")


@pytest.mark.asyncio
async def test_zero_accepted_partition_preserves_all_custody(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, delivery_id = await dispatched_prepaid_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    evidence_id = await _verified_evidence(
        confirmation_client, confirmation_settings, delivery_id, kind="signature"
    )
    line_id = await _delivery_line_id(postgres_url, delivery_id)
    command = _confirmation_command(
        evidence_id,
        _line_partition(line_id, evidence_id, still_undelivered="2.000000"),
    )
    confirmed = await _confirm(
        confirmation_client,
        confirmation_settings,
        delivery_id,
        command,
        key="zero-accepted-partition",
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["lines"][0]["accepted_quantity_base"] == "0.000000"
    assert (await _custody(postgres_url, delivery_id))["in_transit"] == Decimal("2.000000")

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        outbound = await connection.scalar(
            text("SELECT count(*) FROM stock_movements WHERE movement_type='delivery_confirmation'")
        )
        receipt_lines = await connection.scalar(
            text(
                "SELECT snapshot -> 'lines' FROM delivery_receipts "
                "WHERE confirmation_id=:confirmation_id"
            ),
            {"confirmation_id": command["confirmation_id"]},
        )
        outbox_count = await connection.scalar(
            text(
                "SELECT count(*) FROM outbox_events "
                "WHERE aggregate_id=:confirmation_id OR aggregate_id=:delivery_id"
            ),
            {
                "confirmation_id": command["confirmation_id"],
                "delivery_id": delivery_id,
            },
        )
        coverage = await connection.scalar(
            text(
                """
                SELECT state.covered_amount
                FROM delivery_dispatches delivery
                JOIN fulfillment_order_state state
                  ON state.fulfillment_order_id = delivery.fulfillment_order_id
                WHERE delivery.delivery_id=:delivery_id
                """
            ),
            {"delivery_id": delivery_id},
        )
    await engine.dispose()
    assert outbound == 0
    assert receipt_lines is None
    assert outbox_count == 0
    assert coverage == Decimal("224.000000")


@pytest.mark.asyncio
async def test_zero_accepted_cod_reverses_preapproved_on_account_reserve(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture, delivery_id = await dispatched_prepaid_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
        payment_timing_policy="cash_on_delivery",
    )
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        exposure_before = await connection.scalar(
            text(
                "SELECT approved_uninvoiced FROM customer_credit_exposure "
                "WHERE customer_id=:customer_id"
            ),
            {"customer_id": fixture["customer_id"]},
        )
    await engine.dispose()
    conversion_id = str(uuid4())
    approved = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/cod-on-account-conversions",
        headers=auth(
            confirmation_settings,
            "cod-credit-approver-mnl",
            **{"Idempotency-Key": "approve-zero-accepted-cod"},
        ),
        json={
            "conversion_id": conversion_id,
            "expected_delivery_version": 1,
            "reason": "Customer requested account terms before the delivery attempt",
        },
    )
    assert approved.status_code == 201, approved.text
    signature_id = await _verified_evidence(
        confirmation_client, confirmation_settings, delivery_id, kind="signature"
    )
    exception_photo_id = await _verified_evidence(
        confirmation_client, confirmation_settings, delivery_id
    )
    delivery_line_id = await _delivery_line_id(postgres_url, delivery_id)
    confirmed = await _confirm(
        confirmation_client,
        confirmation_settings,
        delivery_id,
        _confirmation_command(
            signature_id,
            _line_partition(
                delivery_line_id,
                exception_photo_id,
                still_undelivered="2.000000",
            ),
        ),
        key="confirm-zero-accepted-preapproved-cod",
    )
    assert confirmed.status_code == 201, confirmed.text
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT conversion.status, conversion.consumed_amount,
                               exposure.approved_uninvoiced
                        FROM cod_on_account_conversions conversion
                        JOIN delivery_dispatches delivery USING (delivery_id)
                        JOIN customer_credit_exposure exposure
                          ON exposure.customer_id=delivery.customer_id
                        WHERE conversion.conversion_id=:conversion_id
                        """
                    ),
                    {"conversion_id": conversion_id},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert row["status"] == "reversed"
    assert row["consumed_amount"] == Decimal("0.000000")
    assert row["approved_uninvoiced"] == (exposure_before or Decimal("0.000000"))


@pytest.mark.asyncio
async def test_invalid_partition_rolls_back_every_authoritative_write(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, delivery_id = await dispatched_prepaid_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    evidence_id = await _verified_evidence(
        confirmation_client, confirmation_settings, delivery_id, kind="signature"
    )
    line_id = await _delivery_line_id(postgres_url, delivery_id)
    command = _confirmation_command(
        evidence_id,
        _line_partition(line_id, evidence_id, accepted="1.000000", refused="0.999999"),
    )
    rejected = await _confirm(
        confirmation_client,
        confirmation_settings,
        delivery_id,
        command,
        key="under-partition-must-rollback",
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "delivery_partition_conflict"

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        counts = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT
                      (SELECT count(*) FROM delivery_confirmations) confirmations,
                      (SELECT count(*) FROM delivery_receipts) receipts,
                      (SELECT count(*) FROM outbox_events) events,
                      (SELECT count(*) FROM stock_movements
                         WHERE movement_type IN
                           ('delivery_confirmation','delivery_exception')) movements
                    """
                    )
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert dict(counts) == {"confirmations": 0, "receipts": 0, "events": 0, "movements": 0}
    assert (await _custody(postgres_url, delivery_id))["in_transit"] == Decimal("2.000000")


async def _exception_delivery(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
    *,
    refused: str = "0.000000",
    damaged: str = "0.000000",
    short_missing: str = "0.000000",
    still_undelivered: str = "0.000000",
) -> tuple[str, str, dict[str, object], dict[str, object]]:
    _, delivery_id = await dispatched_prepaid_delivery(client, settings, postgres_url)
    pod_evidence_id = await _verified_evidence(client, settings, delivery_id, kind="signature")
    evidence_id = await _verified_evidence(client, settings, delivery_id)
    line_id = await _delivery_line_id(postgres_url, delivery_id)
    line = _line_partition(
        line_id,
        evidence_id,
        refused=refused,
        damaged=damaged,
        short_missing=short_missing,
        still_undelivered=still_undelivered,
    )
    command = _confirmation_command(pod_evidence_id, line)
    confirmed = await _confirm(client, settings, delivery_id, command, key=f"exception-{uuid4()}")
    assert confirmed.status_code == 201, confirmed.text
    return delivery_id, evidence_id, line, confirmed.json()


@pytest.mark.asyncio
async def test_return_to_warehouse_receipt_moves_refused_and_damaged_only_to_quarantine(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    delivery_id, evidence_id, line, confirmed = await _exception_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
        refused="1.000000",
        damaged="1.000000",
    )
    returned = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/return-to-warehouse-receipts",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "return-refused-damaged"},
        ),
        json={
            "return_receipt_id": str(uuid4()),
            "expected_delivery_version": confirmed["version"],
            "received_at": datetime.now(UTC).isoformat(),
            "evidence_ids": [evidence_id],
            "reason": "Refused and damaged goods physically received",
            "lines": [
                {
                    "delivery_line_id": line["delivery_line_id"],
                    "refused_quantity_base": "1.000000",
                    "damaged_quantity_base": "1.000000",
                }
            ],
        },
    )
    assert returned.status_code == 201, returned.text
    custody = await _custody(postgres_url, delivery_id)
    assert custody["available"] == Decimal("0.000000")
    assert custody["in_transit"] == Decimal("0.000000")
    assert custody["quarantine"] == Decimal("2.000000")


@pytest.mark.asyncio
async def test_partial_return_rebuild_preserves_open_in_transit_custody(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    delivery_id, evidence_id, line, confirmed = await _exception_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
        refused="2.000000",
    )
    returned = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/return-to-warehouse-receipts",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "partial-return-refused"},
        ),
        json={
            "return_receipt_id": str(uuid4()),
            "expected_delivery_version": confirmed["version"],
            "received_at": datetime.now(UTC).isoformat(),
            "evidence_ids": [evidence_id],
            "reason": "One refused unit physically received",
            "lines": [
                {
                    "delivery_line_id": line["delivery_line_id"],
                    "refused_quantity_base": "1.000000",
                }
            ],
        },
    )
    assert returned.status_code == 201, returned.text
    rebuilt = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        state = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT state.custody, state.open_quantity_base,
                               state.returned_quantity_base, state.status
                        FROM delivery_exception_state state
                        JOIN delivery_exception_cases exception
                          USING (exception_case_id)
                        JOIN delivery_confirmation_lines line
                          USING (confirmation_line_id)
                        WHERE line.confirmation_id=:confirmation_id
                          AND exception.exception_kind='refused'
                        """
                    ),
                    {"confirmation_id": confirmed["confirmation_id"]},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert dict(state) == {
        "custody": "in_transit",
        "open_quantity_base": Decimal("1.000000"),
        "returned_quantity_base": Decimal("1.000000"),
        "status": "partially_resolved",
    }


@pytest.mark.parametrize(
    (
        "resolution_type",
        "expected_quarantine",
        "expected_valuation_quantity",
        "expected_valuation_value",
        "expected_moving_average",
    ),
    [
        (
            "recovery",
            Decimal("2.000000"),
            Decimal("2.000000"),
            Decimal("0.000000"),
            Decimal("0.000000"),
        ),
        (
            "carrier_claim",
            Decimal("0"),
            Decimal("1.000000"),
            Decimal("10.000000"),
            Decimal("10.000000"),
        ),
        (
            "inventory_adjustment",
            Decimal("0"),
            Decimal("1.000000"),
            Decimal("10.000000"),
            Decimal("10.000000"),
        ),
    ],
)
@pytest.mark.asyncio
async def test_short_investigation_has_three_immutable_resolution_paths(
    resolution_type: str,
    expected_quarantine: Decimal,
    expected_valuation_quantity: Decimal,
    expected_valuation_value: Decimal,
    expected_moving_average: Decimal,
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    delivery_id, evidence_id, _, confirmed = await _exception_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
        short_missing="2.000000",
    )
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        investigation_id = await connection.scalar(
            text(
                """
                SELECT exception.exception_case_id
                FROM delivery_exception_cases exception
                JOIN delivery_confirmation_lines line
                  ON line.confirmation_line_id = exception.confirmation_line_id
                WHERE line.confirmation_id=:confirmation_id
                  AND exception.exception_kind='short_missing'
                """
            ),
            {"confirmation_id": confirmed["confirmation_id"]},
        )
    await engine.dispose()
    assert investigation_id is not None
    assert (await _custody(postgres_url, delivery_id))["investigation"] == Decimal("2.000000")
    if resolution_type != "recovery":
        engine = create_async_engine(postgres_url)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO stock_movements(
                      movement_id, sku_id, warehouse_id, location_id, movement_type,
                      quantity_base, unit_cost, value_delta, base_currency,
                      source_reference, entered_unit, conversion_snapshot,
                      actor_subject, correlation_id, idempotency_key,
                      movement_group_id, movement_leg)
                    SELECT :movement_id, line.sku_id, delivery.warehouse_id, location.location_id,
                      'opening_stock', 1.000000, 10.000000, 10.000000, 'PHP',
                      'LOSS-RESOLUTION-MIXED-COST', 'EA',
                      '{"source":"loss-regression","factor":"1.000000"}'::jsonb,
                      'sales-mnl', 'loss-regression', :idempotency_key,
                      :movement_group_id, 'opening_in'
                    FROM delivery_dispatches delivery
                    JOIN delivery_lines line USING (delivery_id)
                    JOIN warehouse_stock_locations location
                      ON location.warehouse_id=delivery.warehouse_id
                     AND location.custody='available'
                    WHERE delivery.delivery_id=:delivery_id
                    LIMIT 1
                    """
                ),
                {
                    "movement_id": uuid4(),
                    "movement_group_id": uuid4(),
                    "idempotency_key": f"mixed-cost-{resolution_type}",
                    "delivery_id": delivery_id,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE inventory_availability availability
                    SET on_hand=on_hand + 1.000000
                    FROM delivery_dispatches delivery, delivery_lines line,
                         warehouse_stock_locations location
                    WHERE delivery.delivery_id=:delivery_id
                      AND line.delivery_id=delivery.delivery_id
                      AND location.warehouse_id=delivery.warehouse_id
                      AND location.custody='available'
                      AND availability.sku_id=line.sku_id
                      AND availability.warehouse_id=delivery.warehouse_id
                      AND availability.location_id=location.location_id
                      AND availability.identity_key=''
                    """
                ),
                {"delivery_id": delivery_id},
            )
            await connection.execute(
                text(
                    """
                    UPDATE inventory_valuation valuation
                    SET quantity_on_hand=quantity_on_hand + 1.000000,
                        inventory_value=inventory_value + 10.000000,
                        moving_average_unit_cost=3.333333
                    FROM delivery_dispatches delivery, delivery_lines line
                    WHERE delivery.delivery_id=:delivery_id
                      AND line.delivery_id=delivery.delivery_id
                      AND valuation.sku_id=line.sku_id
                      AND valuation.warehouse_id=delivery.warehouse_id
                    """
                ),
                {"delivery_id": delivery_id},
            )
        await engine.dispose()
    missing_evidence = await confirmation_client.post(
        f"/v1/delivery-investigations/{investigation_id}/resolutions",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": f"reject-evidence-free-{resolution_type}"},
        ),
        json={
            "resolution_id": str(uuid4()),
            "expected_investigation_version": 1,
            "resolution_type": resolution_type,
            "reason": "Evidence is intentionally omitted",
        },
    )
    assert missing_evidence.status_code == 422, missing_evidence.text
    resolved = await confirmation_client.post(
        f"/v1/delivery-investigations/{investigation_id}/resolutions",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": f"resolve-{resolution_type}"},
        ),
        json={
            "resolution_id": str(uuid4()),
            "expected_investigation_version": 1,
            "resolution_type": resolution_type,
            "reason": f"Approved {resolution_type} resolution with physical evidence",
            "evidence_ids": [evidence_id],
        },
    )
    assert resolved.status_code == 201, resolved.text
    assert resolved.json()["status"] == "resolved"
    custody = await _custody(postgres_url, delivery_id)
    assert custody["investigation"] == Decimal("0.000000")
    assert custody.get("quarantine", Decimal("0")) == expected_quarantine
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        live_valuation = (
            (
                await connection.execute(
                    text(
                        "SELECT quantity_on_hand, inventory_value, moving_average_unit_cost "
                        "FROM inventory_valuation"
                    )
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert dict(live_valuation) == {
        "quantity_on_hand": expected_valuation_quantity,
        "inventory_value": expected_valuation_value,
        "moving_average_unit_cost": expected_moving_average,
    }
    rebuilt = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        rebuilt_valuation = (
            (
                await connection.execute(
                    text(
                        "SELECT quantity_on_hand, inventory_value, moving_average_unit_cost "
                        "FROM inventory_valuation"
                    )
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert dict(rebuilt_valuation) == dict(live_valuation)


@pytest.mark.asyncio
async def test_still_undelivered_retry_creates_a_new_delivery_before_confirmation(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    source_delivery_id, _, _, confirmed = await _exception_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
        still_undelivered="2.000000",
    )
    retry_delivery_id = str(uuid4())
    unauthorized = await confirmation_client.post(
        f"/v1/deliveries/{source_delivery_id}/retries",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "reject-unauthorized-delivery-retry-assignee"},
        ),
        json={
            "retry_delivery_id": str(uuid4()),
            "expected_delivery_version": confirmed["version"],
            "assigned_to": "warehouse-supervisor-mnl",
            "reason": "This user is not authorized Delivery Staff",
        },
    )
    assert unauthorized.status_code == 422, unauthorized.text
    assert unauthorized.json()["error"]["code"] == "delivery_assignee_not_authorized"
    retried = await confirmation_client.post(
        f"/v1/deliveries/{source_delivery_id}/retries",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "create-delivery-retry"},
        ),
        json={
            "retry_delivery_id": retry_delivery_id,
            "expected_delivery_version": confirmed["version"],
            "assigned_to": "delivery-mnl",
            "reason": "Customer is now available for the retained shipment",
        },
    )
    assert retried.status_code == 201, retried.text
    assert retried.json()["delivery_id"] == retry_delivery_id
    assert retry_delivery_id != source_delivery_id

    overallocated_delivery_id = uuid4()
    overallocated_line_id = uuid4()
    engine = create_async_engine(postgres_url)
    with pytest.raises(DBAPIError, match="Retry allocations exceed still-undelivered custody"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO delivery_dispatches(
                      delivery_id, fulfillment_order_id, sales_order_id,
                      sales_order_revision_id, customer_id, branch_id, warehouse_id,
                      delivery_address_version_id, delivery_address_snapshot,
                      recipient_name_snapshot, payment_timing_policy,
                      evidence_requirements, initial_assignee_subject, dispatched_by,
                      correlation_id, idempotency_key, dispatch_kind, parent_delivery_id)
                    SELECT :retry_delivery_id, fulfillment_order_id, sales_order_id,
                      sales_order_revision_id, customer_id, branch_id, warehouse_id,
                      delivery_address_version_id, delivery_address_snapshot,
                      recipient_name_snapshot, payment_timing_policy,
                      evidence_requirements, 'delivery-mnl', 'warehouse-supervisor-mnl',
                      'db-negative-retry', :idempotency_key, 'retry', delivery_id
                    FROM delivery_dispatches WHERE delivery_id=:source_delivery_id
                    """
                ),
                {
                    "retry_delivery_id": overallocated_delivery_id,
                    "source_delivery_id": source_delivery_id,
                    "idempotency_key": f"db-overallocated-retry-{uuid4()}",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO delivery_state(delivery_id,status,assigned_to,version) "
                    "VALUES (:delivery_id,'dispatched','delivery-mnl',1)"
                ),
                {"delivery_id": overallocated_delivery_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO delivery_lines(
                      delivery_line_id, delivery_id, pick_line_id, line_id, sku_id,
                      quantity_base, movement_group_id, staging_movement_id,
                      transit_movement_id, source_exception_case_id)
                    SELECT :retry_line_id, :retry_delivery_id, source_line.pick_line_id,
                      source_line.line_id, source_line.sku_id, exception.original_quantity_base,
                      :movement_group_id, NULL, NULL, exception.exception_case_id
                    FROM delivery_exception_cases exception
                    JOIN delivery_confirmation_lines confirmation_line
                      USING (confirmation_line_id)
                    JOIN delivery_lines source_line
                      ON source_line.delivery_line_id=confirmation_line.delivery_line_id
                    JOIN delivery_confirmations confirmation USING (confirmation_id)
                    WHERE confirmation.delivery_id=:source_delivery_id
                      AND exception.exception_kind='still_undelivered'
                    """
                ),
                {
                    "retry_line_id": overallocated_line_id,
                    "retry_delivery_id": overallocated_delivery_id,
                    "movement_group_id": uuid4(),
                    "source_delivery_id": source_delivery_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO delivery_retry_allocations(
                      retry_allocation_id, source_exception_case_id,
                      retry_delivery_line_id, quantity_base, allocated_by, reason,
                      correlation_id, idempotency_key)
                    SELECT :allocation_id, source_exception_case_id, :retry_line_id,
                      quantity_base, 'warehouse-supervisor-mnl',
                      'Database must reject duplicate custody allocation',
                      'db-negative-retry', :idempotency_key
                    FROM delivery_retry_allocations
                    WHERE retry_delivery_line_id=:valid_retry_line_id
                    """
                ),
                {
                    "allocation_id": uuid4(),
                    "retry_line_id": overallocated_line_id,
                    "idempotency_key": f"db-overallocated-retry-allocation-{uuid4()}",
                    "valid_retry_line_id": retried.json()["lines"][0]["delivery_line_id"],
                },
            )
    await engine.dispose()

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        retry_coverage = await connection.scalar(
            text(
                """
                SELECT state.covered_amount
                FROM delivery_dispatches retry
                JOIN fulfillment_order_state state
                  ON state.fulfillment_order_id = retry.fulfillment_order_id
                WHERE retry.delivery_id=:retry_delivery_id
                """
            ),
            {"retry_delivery_id": retry_delivery_id},
        )
    await engine.dispose()
    assert retry_coverage == Decimal("224.000000")

    evidence_id = await _verified_evidence(
        confirmation_client, confirmation_settings, retry_delivery_id, kind="signature"
    )
    retry_line_id = await _delivery_line_id(postgres_url, retry_delivery_id)
    retry_command = _confirmation_command(
        evidence_id,
        _line_partition(retry_line_id, evidence_id, accepted="2.000000"),
    )
    accepted = await _confirm(
        confirmation_client,
        confirmation_settings,
        retry_delivery_id,
        retry_command,
        key="accept-retry-delivery",
    )
    assert accepted.status_code == 201, accepted.text


@pytest.mark.asyncio
async def test_identical_concurrent_confirmation_is_one_idempotent_posting(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, delivery_id = await dispatched_prepaid_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    evidence_id = await _verified_evidence(
        confirmation_client, confirmation_settings, delivery_id, kind="signature"
    )
    line_id = await _delivery_line_id(postgres_url, delivery_id)
    command = _confirmation_command(
        evidence_id, _line_partition(line_id, evidence_id, accepted="2.000000")
    )
    first, replay = await asyncio.gather(
        _confirm(
            confirmation_client, confirmation_settings, delivery_id, command, key="same-partition"
        ),
        _confirm(
            confirmation_client, confirmation_settings, delivery_id, command, key="same-partition"
        ),
    )
    assert sorted((first.status_code, replay.status_code)) == [200, 201]
    assert first.json() == replay.json()


@pytest.mark.asyncio
async def test_serial_positions_require_explicit_exact_identity_partitions(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, delivery_id = await _dispatched_serial_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    pod_evidence_id = await _verified_evidence(
        confirmation_client, confirmation_settings, delivery_id, kind="signature"
    )
    photo_evidence_id = await _verified_evidence(
        confirmation_client, confirmation_settings, delivery_id
    )
    delivery_line_id = await _delivery_line_id(postgres_url, delivery_id)
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        allocations = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT allocation.allocation_id, serial.serial_number
                    FROM delivery_line_identity_allocations allocation
                    JOIN pick_identity_assignments assignment
                      ON assignment.pick_identity_assignment_id =
                         allocation.pick_identity_assignment_id
                    JOIN stock_serial_allocations serial
                      ON serial.serial_allocation_id = assignment.serial_allocation_id
                    WHERE allocation.delivery_line_id=:delivery_line_id
                    ORDER BY serial.serial_number
                    """
                    ),
                    {"delivery_line_id": delivery_line_id},
                )
            )
            .mappings()
            .all()
        )
    await engine.dispose()
    assert [row["serial_number"] for row in allocations] == ["SN-001", "SN-002"]

    split_serial_line = _line_partition(
        delivery_line_id,
        photo_evidence_id,
        accepted="1.000000",
        damaged="1.000000",
    )
    split_serial_line["identity_partitions"] = [
        {
            "delivery_line_identity_allocation_id": str(row["allocation_id"]),
            "accepted_quantity_base": "0.500000",
            "refused_quantity_base": "0.000000",
            "damaged_quantity_base": "0.500000",
            "short_missing_quantity_base": "0.000000",
            "still_undelivered_quantity_base": "0.000000",
        }
        for row in allocations
    ]
    split_serial = await _confirm(
        confirmation_client,
        confirmation_settings,
        delivery_id,
        _confirmation_command(pod_evidence_id, split_serial_line),
        key="reject-split-serial-delivery-partition",
    )
    assert split_serial.status_code == 409, split_serial.text
    assert split_serial.json()["error"]["code"] == "delivery_identity_partition_conflict"

    line = _line_partition(
        delivery_line_id,
        photo_evidence_id,
        accepted="1.000000",
        damaged="1.000000",
    )
    line["identity_partitions"] = [
        {
            "delivery_line_identity_allocation_id": str(allocations[0]["allocation_id"]),
            "accepted_quantity_base": "1.000000",
            "refused_quantity_base": "0.000000",
            "damaged_quantity_base": "0.000000",
            "short_missing_quantity_base": "0.000000",
            "still_undelivered_quantity_base": "0.000000",
        },
        {
            "delivery_line_identity_allocation_id": str(allocations[1]["allocation_id"]),
            "accepted_quantity_base": "0.000000",
            "refused_quantity_base": "0.000000",
            "damaged_quantity_base": "1.000000",
            "short_missing_quantity_base": "0.000000",
            "still_undelivered_quantity_base": "0.000000",
        },
    ]
    confirmed = await _confirm(
        confirmation_client,
        confirmation_settings,
        delivery_id,
        _confirmation_command(pod_evidence_id, line),
        key="explicit-serial-delivery-partition",
    )
    assert confirmed.status_code == 201, confirmed.text

    engine = create_async_engine(postgres_url)
    with pytest.raises(
        DBAPIError,
        match="Stock Movement identity allocations do not exactly match tracked quantity",
    ):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO stock_movement_identity_allocations(
                      allocation_id, movement_id,
                      delivery_line_identity_allocation_id, quantity_base)
                    SELECT :allocation_id, line.outbound_movement_id,
                      :damaged_identity_allocation_id, 1.000000
                    FROM delivery_confirmation_lines line
                    WHERE line.confirmation_id=:confirmation_id
                    """
                ),
                {
                    "allocation_id": uuid4(),
                    "damaged_identity_allocation_id": allocations[1]["allocation_id"],
                    "confirmation_id": confirmed.json()["confirmation_id"],
                },
            )
    await engine.dispose()

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        persisted = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT accepted_quantity_base, damaged_quantity_base
                    FROM delivery_confirmation_identity_partitions
                    ORDER BY accepted_quantity_base DESC
                    """
                    )
                )
            )
            .mappings()
            .all()
        )
        transit_serials = await connection.scalar(
            text(
                """
                SELECT coalesce(array_agg(serial_number ORDER BY serial_number), '{}')
                FROM inventory_availability availability
                JOIN warehouse_stock_locations location
                  ON location.location_id = availability.location_id,
                    jsonb_array_elements_text(availability.serial_numbers) serial_number
                WHERE location.custody='in_transit' AND availability.on_hand > 0
                """
            )
        )
    await engine.dispose()
    assert [dict(row) for row in persisted] == [
        {"accepted_quantity_base": Decimal("1.000000"), "damaged_quantity_base": Decimal("0")},
        {"accepted_quantity_base": Decimal("0"), "damaged_quantity_base": Decimal("1.000000")},
    ]
    assert transit_serials == ["SN-002"]

    rebuilt = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        rebuilt_transit_serials = await connection.scalar(
            text(
                """
                SELECT coalesce(array_agg(serial_number ORDER BY serial_number), '{}')
                FROM inventory_availability availability
                JOIN warehouse_stock_locations location
                  ON location.location_id = availability.location_id,
                    jsonb_array_elements_text(availability.serial_numbers) serial_number
                WHERE location.custody='in_transit' AND availability.on_hand > 0
                """
            )
        )
    await engine.dispose()
    assert rebuilt_transit_serials == ["SN-002"]


@pytest.mark.asyncio
async def test_exception_custody_projection_rebuilds_from_immutable_movements(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    delivery_id, _, _, _ = await _exception_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
        short_missing="1.000000",
        still_undelivered="1.000000",
    )
    before = await _custody(postgres_url, delivery_id)
    rebuilt = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert await _custody(postgres_url, delivery_id) == before
