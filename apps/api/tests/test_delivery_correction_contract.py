from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_delivery_confirmation_contract import (
    FakeObjectStorage,
    dispatched_prepaid_delivery,
)
from test_delivery_confirmation_contract import (
    confirmation_client as confirmation_client,
)
from test_delivery_confirmation_contract import (
    confirmation_settings as confirmation_settings,
)
from test_delivery_confirmation_contract import (
    fake_storage as fake_storage,
)
from test_payment_clearance_contract import (
    auth,
    bootstrap_payment_clearance,
    create_customer,
    create_price_list,
    create_sku,
    create_tax_code,
    record_receipt,
    seed_available_stock,
)
from test_tracked_stock_picking_contract import (
    approved_serial_order,
    approved_tracked_order,
)
from tradeflow_api.config import Settings
from tradeflow_api.delivery_confirmation_outbox import (
    create_corrected_draft_invoices_for_event,
    render_corrected_delivery_receipt_for_event,
)
from tradeflow_worker.worker import poll_delivery_confirmation_outbox


async def _confirm_fully_accepted_delivery(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
) -> tuple[dict[str, object], dict[str, object]]:
    fixture, delivery_id = await dispatched_prepaid_delivery(
        client,
        settings,
        postgres_url,
        unit_cost="7.500000",
    )
    evidence_id = str(uuid4())
    upload = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T13:00:00Z",
        },
    )
    assert upload.status_code == 201, upload.text
    completed = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
        headers=auth(settings, "delivery-mnl"),
    )
    assert completed.status_code == 200, completed.text
    confirmed = await client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            settings,
            "delivery-mnl",
            **{"Idempotency-Key": "delivery-correction-source-confirmation"},
        ),
        json={
            "confirmation_id": str(uuid4()),
            "expected_delivery_version": 1,
            "recipient_name": "Ana Santos",
            "device_captured_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "accepted_quantity_base": "2.000000",
                }
            ],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    return fixture, {**confirmed.json(), "evidence_id": evidence_id}


async def _confirm_fully_accepted_serial_delivery(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
) -> tuple[dict[str, object], dict[str, object]]:
    fixture = await approved_serial_order(client, settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    pick_id = str(uuid4())
    picked = await client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "serial-correction-source-pick"},
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
                            "serial_number": serial_number,
                            "quantity": "1.000000",
                            "manual_reason": "Serial identity verified for correction contract",
                        }
                        for serial_number in ("SN-001", "SN-002")
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
            **{"Idempotency-Key": "serial-correction-source-dispatch"},
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
    evidence_id = str(uuid4())
    upload = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T13:00:00Z",
        },
    )
    assert upload.status_code == 201, upload.text
    completed = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
        headers=auth(settings, "delivery-mnl"),
    )
    assert completed.status_code == 200, completed.text
    confirmed = await client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            settings,
            "delivery-mnl",
            **{"Idempotency-Key": "serial-correction-source-confirmation"},
        ),
        json={
            "confirmation_id": str(uuid4()),
            "expected_delivery_version": 1,
            "recipient_name": "Serial Correction Recipient",
            "device_captured_at": "2026-08-01T13:01:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "accepted_quantity_base": "2.000000",
                }
            ],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    return fixture, {**confirmed.json(), "evidence_id": evidence_id}


async def _serial_correction_projection(
    postgres_url: str,
    *,
    confirmation_id: str,
    correction_id: str,
    sku_id: str,
    warehouse_id: str,
) -> dict[str, object]:
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        availability = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT location.custody, availability.identity_key,
                               availability.serial_numbers, availability.on_hand,
                               availability.reserved
                        FROM inventory_availability availability
                        JOIN warehouse_stock_locations location USING (location_id)
                        WHERE availability.sku_id = :sku_id
                          AND availability.warehouse_id = :warehouse_id
                          AND availability.on_hand <> 0
                        ORDER BY location.custody, availability.identity_key
                        """
                    ),
                    {"sku_id": sku_id, "warehouse_id": warehouse_id},
                )
            ).mappings()
        ]
        valuation = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT quantity_on_hand, inventory_value, moving_average_unit_cost
                        FROM inventory_valuation
                        WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id
                        """
                    ),
                    {"sku_id": sku_id, "warehouse_id": warehouse_id},
                )
            )
            .mappings()
            .one()
        )
        confirmation = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT confirmation.confirmation_id,
                               line.accepted_quantity_base, line.refused_quantity_base,
                               line.damaged_quantity_base, line.short_missing_quantity_base,
                               line.still_undelivered_quantity_base, line.unit_cost,
                               line.value_delta, line.outbound_movement_id
                        FROM delivery_confirmations confirmation
                        JOIN delivery_confirmation_lines line USING (confirmation_id)
                        WHERE confirmation.confirmation_id = :confirmation_id
                        """
                    ),
                    {"confirmation_id": confirmation_id},
                )
            )
            .mappings()
            .one()
        )
        cases = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT exception.exception_case_id, exception.confirmation_line_id,
                               exception.correction_line_id, exception.exception_kind,
                               exception.original_quantity_base, exception.initial_custody,
                               exception.investigation_out_movement_id,
                               exception.investigation_in_movement_id,
                               state.status, state.custody, state.open_quantity_base,
                               state.returned_quantity_base,
                               state.retry_allocated_quantity_base,
                               state.resolved_quantity_base, state.version
                        FROM delivery_exception_cases exception
                        JOIN delivery_exception_state state USING (exception_case_id)
                        WHERE exception.correction_line_id IN (
                          SELECT correction_line_id FROM delivery_correction_lines
                          WHERE correction_id = :correction_id
                        )
                        ORDER BY exception.exception_kind, exception.exception_case_id
                        """
                    ),
                    {"correction_id": correction_id},
                )
            ).mappings()
        ]
    await engine.dispose()
    return {
        "availability": availability,
        "valuation": valuation,
        "confirmation": confirmation,
        "cases": cases,
    }


async def _sequential_correction_projection(
    postgres_url: str,
    *,
    correction_ids: list[str],
    sku_id: str,
    warehouse_id: str,
) -> dict[str, object]:
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        availability = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT location.custody, availability.identity_key,
                               availability.on_hand, availability.reserved
                        FROM inventory_availability availability
                        JOIN warehouse_stock_locations location USING (location_id)
                        WHERE availability.sku_id = :sku_id
                          AND availability.warehouse_id = :warehouse_id
                          AND availability.on_hand <> 0
                        ORDER BY location.custody, availability.identity_key
                        """
                    ),
                    {"sku_id": sku_id, "warehouse_id": warehouse_id},
                )
            ).mappings()
        ]
        valuation = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT quantity_on_hand,inventory_value,moving_average_unit_cost
                        FROM inventory_valuation
                        WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id
                        """
                    ),
                    {"sku_id": sku_id, "warehouse_id": warehouse_id},
                )
            )
            .mappings()
            .one()
        )
        cases = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT line.correction_id, exception.exception_case_id,
                               exception.exception_kind, exception.original_quantity_base,
                               exception.initial_custody, state.status, state.custody,
                               state.open_quantity_base,state.resolved_quantity_base,state.version,
                               coalesce(
                                 array_agg(event.event_type ORDER BY event.occurred_at), '{}'
                               ) AS event_types
                        FROM delivery_exception_cases exception
                        JOIN delivery_correction_lines line USING (correction_line_id)
                        JOIN delivery_exception_state state USING (exception_case_id)
                        LEFT JOIN delivery_exception_events event USING (exception_case_id)
                        WHERE line.correction_id = ANY(CAST(:correction_ids AS uuid[]))
                        GROUP BY line.correction_id,exception.exception_case_id,state.status,
                                 state.custody,state.open_quantity_base,
                                 state.resolved_quantity_base,state.version
                        ORDER BY line.correction_id,exception.exception_kind
                        """
                    ),
                    {"correction_ids": correction_ids},
                )
            ).mappings()
        ]
        effects = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT effect.effect_role,effect.outcome,effect.movement_id,
                               movement.movement_leg,movement.quantity_base,
                               movement.value_delta,movement.reversal_of_movement_id
                        FROM delivery_correction_movement_effects effect
                        JOIN stock_movements movement USING (movement_id)
                        WHERE effect.correction_id = :correction_id
                        ORDER BY CASE effect.effect_role
                          WHEN 'original' THEN 1 WHEN 'reversal' THEN 2 ELSE 3 END,
                          movement.movement_leg,movement.movement_id
                        """
                    ),
                    {"correction_id": correction_ids[-1]},
                )
            ).mappings()
        ]
    await engine.dispose()
    return {
        "availability": availability,
        "valuation": valuation,
        "cases": cases,
        "effects": effects,
    }


@pytest.mark.asyncio
async def test_distinct_approver_posts_complete_correction_without_editing_issued_receipt(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    fixture, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    original_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    original_outbox_event_id = UUID(confirmation["outbox_event_id"])

    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    processed = await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    )
    assert processed == {"completed": 1, "failed": 0}

    original_response = await confirmation_client.get(
        f"/v1/delivery-receipts/{original_receipt_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert original_response.status_code == 200, original_response.text
    original = original_response.json()
    original_number = original["number"]
    original_snapshot = original["snapshot"]

    async with engine.connect() as connection:
        before = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM stock_movements) AS movements,
                          (SELECT count(*) FROM draft_invoices) AS invoices,
                          (SELECT count(*) FROM delivery_receipts) AS receipts,
                          (SELECT count(*) FROM document_series_number_audit) AS number_audits,
                          (SELECT count(*) FROM outbox_events) AS outbox_events,
                          (SELECT next_number FROM document_series
                           WHERE document_type = 'delivery_receipt') AS next_number
                        """
                    )
                )
            )
            .mappings()
            .one()
        )

    correction_id = str(uuid4())
    proposal = {
        "correction_id": correction_id,
        "reason": "Customer signed for one unit; the second unit was refused at the doorstep.",
        "evidence_ids": [confirmation["evidence_id"]],
        "lines": [
            {
                "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                "accepted_quantity_base": "1.000000",
                "refused_quantity_base": "1.000000",
                "damaged_quantity_base": "0.000000",
                "short_missing_quantity_base": "0.000000",
                "still_undelivered_quantity_base": "0.000000",
                "identity_positions": [],
            }
        ],
    }
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "request-delivery-correction"},
        ),
        json=proposal,
    )
    assert requested.status_code == 201, requested.text
    pending = requested.json()

    receipt_inputs_response = await confirmation_client.get(
        f"/v1/delivery-receipts/{original_receipt_id}",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert receipt_inputs_response.status_code == 200, receipt_inputs_response.text
    receipt_inputs = receipt_inputs_response.json()
    assert receipt_inputs["correction_status"] == "current"
    assert receipt_inputs["correction_id"] == correction_id
    assert receipt_inputs["corrects_delivery_receipt_id"] is None
    assert receipt_inputs["replacement_delivery_receipt_id"] is None
    assert len(receipt_inputs["confirmation_lines"]) == 1
    source_line = receipt_inputs["confirmation_lines"][0]
    assert source_line["delivery_line_id"] == proposal["lines"][0]["delivery_line_id"]
    assert source_line["line_id"] == fixture["line_id"]
    assert source_line["sku_id"] == fixture["sku_id"]
    assert Decimal(source_line["accepted_quantity_base"]) == Decimal("2")
    assert Decimal(source_line["refused_quantity_base"]) == Decimal("0")
    assert Decimal(source_line["damaged_quantity_base"]) == Decimal("0")
    assert Decimal(source_line["short_missing_quantity_base"]) == Decimal("0")
    assert Decimal(source_line["still_undelivered_quantity_base"]) == Decimal("0")
    assert Decimal(source_line["unit_cost"]) == Decimal("7.5")
    assert Decimal(source_line["value_delta"]) == Decimal("-15")
    assert source_line["identity_positions"] == []

    assert pending["correction_id"] == correction_id
    assert pending["original_delivery_receipt_id"] == original_receipt_id
    assert pending["confirmation_id"] == confirmation["confirmation_id"]
    assert pending["status"] == "pending_authorization"
    assert pending["version"] == 1
    assert pending["reason"] == proposal["reason"]
    assert pending["evidence_ids"] == proposal["evidence_ids"]
    assert pending["requested_by"] == "warehouse-supervisor-mnl"
    assert pending["requested_at"]
    assert pending["authorized_by"] is None
    assert pending["authorized_at"] is None
    assert pending["lines"] == proposal["lines"]

    read_pending = await confirmation_client.get(
        f"/v1/delivery-corrections/{correction_id}",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert read_pending.status_code == 200, read_pending.text
    assert read_pending.json() == pending

    async with engine.connect() as connection:
        after_request = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM stock_movements) AS movements,
                          (SELECT count(*) FROM draft_invoices) AS invoices,
                          (SELECT count(*) FROM delivery_receipts) AS receipts,
                          (SELECT count(*) FROM document_series_number_audit) AS number_audits,
                          (SELECT count(*) FROM outbox_events) AS outbox_events,
                          (SELECT next_number FROM document_series
                           WHERE document_type = 'delivery_receipt') AS next_number
                        """
                    )
                )
            )
            .mappings()
            .one()
        )
    assert dict(after_request) == dict(before)

    self_authorization = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "self-authorize-delivery-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert self_authorization.status_code == 403, self_authorization.text
    assert self_authorization.json()["error"]["code"] == "maker_checker_violation"

    authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-delivery-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert authorized.status_code == 200, authorized.text
    posted = authorized.json()
    assert posted["correction_id"] == correction_id
    assert posted["status"] == "posted"
    assert posted["version"] == 2
    assert posted["requested_by"] == "warehouse-supervisor-mnl"
    assert posted["authorized_by"] == "delivery-correction-checker-mnl"
    assert posted["authorized_at"]
    assert posted["lines"] == proposal["lines"]
    assert len(posted["stock_effect"]["reversal_movement_ids"]) == 1
    assert len(posted["stock_effect"]["replacement_movement_ids"]) == 1
    assert posted["draft_invoice_effect"]["original_draft_invoice_id"]
    assert posted["draft_invoice_effect"]["reversal_draft_invoice_id"]
    assert posted["draft_invoice_effect"]["replacement_draft_invoice_id"]
    assert posted["receipt_effect"]["original_delivery_receipt_id"] == original_receipt_id
    replacement_receipt_id = posted["receipt_effect"]["replacement_delivery_receipt_id"]
    assert replacement_receipt_id != original_receipt_id

    corrected_original_response = await confirmation_client.get(
        f"/v1/delivery-receipts/{original_receipt_id}",
        headers=auth(confirmation_settings, "delivery-correction-checker-mnl"),
    )
    assert corrected_original_response.status_code == 200, corrected_original_response.text
    corrected_original = corrected_original_response.json()
    assert corrected_original["number"] == original_number
    assert corrected_original["snapshot"] == original_snapshot
    assert corrected_original["correction_status"] == "corrected"
    assert corrected_original["correction_id"] == correction_id
    assert corrected_original["corrects_delivery_receipt_id"] is None
    assert corrected_original["replacement_delivery_receipt_id"] == replacement_receipt_id

    replacement_response = await confirmation_client.get(
        f"/v1/delivery-receipts/{replacement_receipt_id}",
        headers=auth(confirmation_settings, "delivery-correction-checker-mnl"),
    )
    assert replacement_response.status_code == 200, replacement_response.text
    replacement = replacement_response.json()
    assert replacement["number"] != original_number
    assert replacement["number"].endswith("00000002")
    assert replacement["correction_status"] == "replacement"
    assert replacement["correction_id"] == correction_id
    assert replacement["corrects_delivery_receipt_id"] == original_receipt_id
    assert replacement["replacement_delivery_receipt_id"] is None
    assert Decimal(replacement["snapshot"]["lines"][0]["accepted_quantity_base"]) == Decimal("1")

    async with engine.connect() as connection:
        effects = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM delivery_receipts) AS receipts,
                          (SELECT count(*) FROM document_series_number_audit) AS number_audits,
                          (SELECT next_number FROM document_series
                           WHERE document_type = 'delivery_receipt') AS next_number,
                          (SELECT count(*) FROM stock_movements
                           WHERE movement_id = ANY(CAST(:reversal_ids AS uuid[]))
                              OR movement_id = ANY(CAST(:replacement_ids AS uuid[]))) AS effects,
                          (SELECT count(*) FROM outbox_handler_receipts
                           WHERE outbox_event_id = :source_event_id) AS original_handler_receipts
                        """
                    ),
                    {
                        "replacement_ids": posted["stock_effect"]["replacement_movement_ids"],
                        "reversal_ids": posted["stock_effect"]["reversal_movement_ids"],
                        "source_event_id": original_outbox_event_id,
                    },
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert effects["receipts"] == before["receipts"] + 1
    assert effects["number_audits"] == before["number_audits"] + 1
    assert effects["next_number"] == before["next_number"] + 1
    assert effects["effects"] == 2
    assert effects["original_handler_receipts"] == 2


@pytest.mark.asyncio
async def test_correction_command_and_worker_replays_preserve_one_reconciled_audit_chain(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    original_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    context = {"database_session_factory": factory, "object_storage": fake_storage}
    original_processed = await poll_delivery_confirmation_outbox(context)
    assert original_processed == {"completed": 1, "failed": 0}

    async with engine.connect() as connection:
        original_invoice = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT invoice.*, line.accepted_quantity_base,
                               line.subtotal AS line_subtotal,
                               line.discount_amount AS line_discount,
                               line.tax_amount AS line_tax,
                               line.line_total
                        FROM draft_invoices invoice
                        JOIN draft_invoice_lines line USING (draft_invoice_id)
                        WHERE invoice.invoice_kind = 'original'
                        """
                    )
                )
            )
            .mappings()
            .one()
        )
    assert original_invoice["accepted_quantity_base"] == Decimal("2.000000")
    assert original_invoice["subtotal"] == Decimal("200.000000")
    assert original_invoice["discount_total"] == Decimal("0.000000")
    assert original_invoice["tax_total"] == Decimal("24.000000")
    assert original_invoice["grand_total"] == Decimal("224.000000")

    correction_id = str(uuid4())
    proposal = {
        "correction_id": correction_id,
        "reason": "One of two delivered units was refused and remained in carrier custody.",
        "evidence_ids": [confirmation["evidence_id"]],
        "lines": [
            {
                "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                "accepted_quantity_base": "1.000000",
                "refused_quantity_base": "1.000000",
                "damaged_quantity_base": "0.000000",
                "short_missing_quantity_base": "0.000000",
                "still_undelivered_quantity_base": "0.000000",
                "identity_positions": [],
            }
        ],
    }
    create_headers = auth(
        confirmation_settings,
        "warehouse-supervisor-mnl",
        **{"Idempotency-Key": "replay-correction-request"},
    )
    first_request = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=create_headers,
        json=proposal,
    )
    replayed_request = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=create_headers,
        json=proposal,
    )
    assert first_request.status_code == 201, first_request.text
    assert replayed_request.status_code == 200, replayed_request.text
    assert replayed_request.json() == first_request.json()
    pending = first_request.json()

    authorize_headers = auth(
        confirmation_settings,
        "delivery-correction-checker-mnl",
        **{"Idempotency-Key": "replay-correction-authorization"},
    )
    authorization = {"expected_correction_version": 1}
    first_authorization = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=authorize_headers,
        json=authorization,
    )
    replayed_authorization = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=authorize_headers,
        json=authorization,
    )
    assert first_authorization.status_code == 200, first_authorization.text
    assert replayed_authorization.status_code == 200, replayed_authorization.text
    assert replayed_authorization.json() == first_authorization.json()
    posted = first_authorization.json()
    assert posted["correction_id"] == pending["correction_id"] == correction_id
    assert (
        posted["stock_effect"]["original_movement_ids"]
        == pending["stock_effect"]["original_movement_ids"]
    )
    assert posted["draft_invoice_effect"] == pending["draft_invoice_effect"]
    assert posted["receipt_effect"]["original_delivery_receipt_id"] == original_receipt_id
    assert posted["outbox_event_id"]
    assert posted["receipt_effect"]["replacement_delivery_receipt_id"]

    correction_event_id = posted["outbox_event_id"]
    fake_storage.fail_puts = 1
    failed_processing = await poll_delivery_confirmation_outbox(context)
    assert failed_processing == {"completed": 0, "failed": 1}
    async with engine.begin() as connection:
        failed_effects = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM draft_invoices
                           WHERE correction_id = :correction_id) AS correction_invoices,
                          (SELECT count(*) FROM outbox_handler_receipts
                           WHERE outbox_event_id = :event_id) AS handler_receipts
                        """
                    ),
                    {"correction_id": correction_id, "event_id": correction_event_id},
                )
            )
            .mappings()
            .one()
        )
        await connection.execute(
            text(
                "UPDATE outbox_processing_state SET available_at = now() "
                "WHERE outbox_event_id = :event_id"
            ),
            {"event_id": correction_event_id},
        )
    assert dict(failed_effects) == {"correction_invoices": 0, "handler_receipts": 0}

    retried_processing = await poll_delivery_confirmation_outbox(context)
    async with engine.connect() as connection:
        retry_state = (
            (
                await connection.execute(
                    text(
                        "SELECT status, attempts, last_error FROM outbox_processing_state "
                        "WHERE outbox_event_id = :event_id"
                    ),
                    {"event_id": correction_event_id},
                )
            )
            .mappings()
            .one()
        )
    assert retried_processing == {"completed": 1, "failed": 0}, dict(retry_state)
    rendered_replacement = fake_storage.put_body
    assert rendered_replacement is not None
    rendered_text = rendered_replacement.decode("latin-1")
    repeated_processing = await poll_delivery_confirmation_outbox(context)
    assert repeated_processing == {"completed": 0, "failed": 0}
    assert fake_storage.put_attempts == 3
    assert fake_storage.put_body == rendered_replacement

    current = await confirmation_client.get(
        f"/v1/delivery-corrections/{correction_id}",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert current.status_code == 200, current.text
    completed = current.json()
    assert completed["draft_invoice_effect"]["status"] == "completed"
    assert (
        completed["draft_invoice_effect"] | {"status": "pending"} == posted["draft_invoice_effect"]
    )
    assert completed["receipt_effect"]["replacement_document_status"] == "ready"
    replacement_number = completed["receipt_effect"]["replacement_number"]
    assert replacement_number == "DR-MNL-00000002"

    async with engine.connect() as connection:
        current_original = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT invoice.*, line.accepted_quantity_base,
                               line.subtotal AS line_subtotal,
                               line.discount_amount AS line_discount,
                               line.tax_amount AS line_tax,
                               line.line_total
                        FROM draft_invoices invoice
                        JOIN draft_invoice_lines line USING (draft_invoice_id)
                        WHERE invoice.draft_invoice_id = :invoice_id
                        """
                    ),
                    {"invoice_id": original_invoice["draft_invoice_id"]},
                )
            )
            .mappings()
            .one()
        )
        invoice_chain = list(
            (
                await connection.execute(
                    text(
                        """
                        SELECT invoice.invoice_kind, invoice.draft_invoice_id,
                               invoice.reversal_of_draft_invoice_id,
                               invoice.replaces_draft_invoice_id,
                               invoice.subtotal, invoice.discount_total,
                               invoice.tax_total, invoice.grand_total,
                               line.accepted_quantity_base,
                               line.subtotal AS line_subtotal,
                               line.discount_amount AS line_discount,
                               line.tax_amount AS line_tax,
                               line.line_total
                        FROM draft_invoices invoice
                        JOIN draft_invoice_lines line USING (draft_invoice_id)
                        WHERE invoice.delivery_confirmation_id = :confirmation_id
                        ORDER BY CASE invoice.invoice_kind
                          WHEN 'original' THEN 1 WHEN 'reversal' THEN 2 ELSE 3 END
                        """
                    ),
                    {"confirmation_id": confirmation["confirmation_id"]},
                )
            ).mappings()
        )
        audit = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM delivery_corrections
                           WHERE correction_id = :correction_id) AS corrections,
                          (SELECT count(*) FROM delivery_correction_authorizations
                           WHERE correction_id = :correction_id) AS authorizations,
                          (SELECT count(*) FROM outbox_events
                           WHERE outbox_event_id = :event_id) AS outbox_events,
                          (SELECT count(*) FROM outbox_handler_receipts
                           WHERE outbox_event_id = :event_id) AS handler_receipts,
                          (SELECT count(*) FROM delivery_receipts
                           WHERE correction_id = :correction_id) AS replacement_receipts,
                          (SELECT count(*) FROM delivery_receipt_documents document
                           JOIN delivery_receipts receipt USING (delivery_receipt_id)
                           WHERE receipt.correction_id = :correction_id) AS replacement_documents,
                          to_regclass('customer_ledger_entries') AS customer_ledger_table
                        """
                    ),
                    {"correction_id": correction_id, "event_id": correction_event_id},
                )
            )
            .mappings()
            .one()
        )
        document = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT receipt.number, receipt.corrects_delivery_receipt_id,
                               document.object_key, document.checksum_sha256,
                               document.size_bytes, document.status
                        FROM delivery_receipts receipt
                        JOIN delivery_receipt_documents document USING (delivery_receipt_id)
                        WHERE receipt.correction_id = :correction_id
                        """
                    ),
                    {"correction_id": correction_id},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()

    assert dict(current_original) == dict(original_invoice)
    assert [row["invoice_kind"] for row in invoice_chain] == [
        "original",
        "reversal",
        "replacement",
    ]
    original, reversal, replacement = invoice_chain
    assert (
        str(reversal["draft_invoice_id"])
        == completed["draft_invoice_effect"]["reversal_draft_invoice_id"]
    )
    assert reversal["reversal_of_draft_invoice_id"] == original["draft_invoice_id"]
    assert reversal["replaces_draft_invoice_id"] is None
    assert reversal["accepted_quantity_base"] == Decimal("-2.000000")
    assert reversal["subtotal"] == Decimal("-200.000000")
    assert reversal["discount_total"] == Decimal("0.000000")
    assert reversal["tax_total"] == Decimal("-24.000000")
    assert reversal["grand_total"] == Decimal("-224.000000")
    assert (
        str(replacement["draft_invoice_id"])
        == completed["draft_invoice_effect"]["replacement_draft_invoice_id"]
    )
    assert replacement["reversal_of_draft_invoice_id"] is None
    assert replacement["replaces_draft_invoice_id"] == original["draft_invoice_id"]
    assert replacement["accepted_quantity_base"] == Decimal("1.000000")
    assert replacement["subtotal"] == Decimal("100.000000")
    assert replacement["discount_total"] == Decimal("0.000000")
    assert replacement["tax_total"] == Decimal("12.000000")
    assert replacement["grand_total"] == Decimal("112.000000")
    for amount in ("subtotal", "discount_total", "tax_total", "grand_total"):
        assert sum(row[amount] for row in invoice_chain) == replacement[amount]
    assert dict(audit) == {
        "corrections": 1,
        "authorizations": 1,
        "outbox_events": 1,
        "handler_receipts": 2,
        "replacement_receipts": 1,
        "replacement_documents": 1,
        "customer_ledger_table": None,
    }
    assert document["number"] == replacement_number
    assert str(document["corrects_delivery_receipt_id"]) == original_receipt_id
    assert document["object_key"].endswith(
        f"{completed['receipt_effect']['replacement_delivery_receipt_id']}.pdf"
    )
    assert document["checksum_sha256"] == sha256(rendered_replacement).hexdigest()
    assert document["size_bytes"] == len(rendered_replacement)
    assert document["status"] == "ready"
    assert replacement_number in rendered_text
    assert (
        f"Corrects Delivery Receipt: {confirmation['delivery_receipt']['number']}" in rendered_text
    )


@pytest.mark.asyncio
async def test_serial_correction_preserves_exact_identity_custody_through_rebuild(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    fixture, confirmation = await _confirm_fully_accepted_serial_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    original_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    processed = await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    )
    assert processed == {"completed": 1, "failed": 0}

    receipt = await confirmation_client.get(
        f"/v1/delivery-receipts/{original_receipt_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert receipt.status_code == 200, receipt.text
    source_line = receipt.json()["confirmation_lines"][0]
    positions = sorted(source_line["identity_positions"], key=lambda row: row["serial_number"])
    assert [position["serial_number"] for position in positions] == ["SN-001", "SN-002"]
    assert all(
        Decimal(position["accepted_quantity_base"]) == Decimal("1") for position in positions
    )

    invalid_id = str(uuid4())
    invalid = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "reject-fractional-serial-correction"},
        ),
        json={
            "correction_id": invalid_id,
            "reason": "A serial identity may not be divided between correction outcomes.",
            "evidence_ids": [confirmation["evidence_id"]],
            "lines": [
                {
                    "delivery_line_id": source_line["delivery_line_id"],
                    "accepted_quantity_base": "1.000000",
                    "refused_quantity_base": "0.000000",
                    "damaged_quantity_base": "0.000000",
                    "short_missing_quantity_base": "1.000000",
                    "still_undelivered_quantity_base": "0.000000",
                    "identity_positions": [
                        {
                            "delivery_line_identity_allocation_id": position[
                                "delivery_line_identity_allocation_id"
                            ],
                            "accepted_quantity_base": "0.500000",
                            "refused_quantity_base": "0.000000",
                            "damaged_quantity_base": "0.000000",
                            "short_missing_quantity_base": "0.500000",
                            "still_undelivered_quantity_base": "0.000000",
                        }
                        for position in positions
                    ],
                }
            ],
        },
    )
    assert invalid.status_code == 409, invalid.text
    assert invalid.json()["error"]["code"] == "delivery_correction_identity_conflict"
    async with engine.connect() as connection:
        invalid_artifacts = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM delivery_corrections
                           WHERE correction_id = :correction_id) AS corrections,
                          (SELECT count(*) FROM delivery_correction_lines
                           WHERE correction_id = :correction_id) AS lines,
                          (SELECT count(*) FROM delivery_correction_identity_positions position
                           JOIN delivery_correction_lines line USING (correction_line_id)
                           WHERE line.correction_id = :correction_id) AS identities,
                          (SELECT count(*) FROM stock_movements
                           WHERE source_reference = :source_reference) AS movements,
                          (SELECT count(*) FROM delivery_exception_cases exception
                           JOIN delivery_correction_lines line USING (correction_line_id)
                           WHERE line.correction_id = :correction_id) AS exception_cases,
                          (SELECT count(*) FROM outbox_events
                           WHERE aggregate_id = :correction_id) AS outbox_events
                        """
                    ),
                    {
                        "correction_id": invalid_id,
                        "source_reference": f"DELIVERY-CORRECTION:{invalid_id}",
                    },
                )
            )
            .mappings()
            .one()
        )
    assert dict(invalid_artifacts) == {
        "corrections": 0,
        "lines": 0,
        "identities": 0,
        "movements": 0,
        "exception_cases": 0,
        "outbox_events": 0,
    }

    correction_id = str(uuid4())
    valid_line = {
        "delivery_line_id": source_line["delivery_line_id"],
        "accepted_quantity_base": "1.000000",
        "refused_quantity_base": "0.000000",
        "damaged_quantity_base": "0.000000",
        "short_missing_quantity_base": "1.000000",
        "still_undelivered_quantity_base": "0.000000",
        "identity_positions": [
            {
                "delivery_line_identity_allocation_id": positions[0][
                    "delivery_line_identity_allocation_id"
                ],
                "accepted_quantity_base": "1.000000",
                "refused_quantity_base": "0.000000",
                "damaged_quantity_base": "0.000000",
                "short_missing_quantity_base": "0.000000",
                "still_undelivered_quantity_base": "0.000000",
            },
            {
                "delivery_line_identity_allocation_id": positions[1][
                    "delivery_line_identity_allocation_id"
                ],
                "accepted_quantity_base": "0.000000",
                "refused_quantity_base": "0.000000",
                "damaged_quantity_base": "0.000000",
                "short_missing_quantity_base": "1.000000",
                "still_undelivered_quantity_base": "0.000000",
            },
        ],
    }
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "request-serial-correction"},
        ),
        json={
            "correction_id": correction_id,
            "reason": "SN-002 was reported missing after the original receipt was issued.",
            "evidence_ids": [confirmation["evidence_id"]],
            "lines": [valid_line],
        },
    )
    assert requested.status_code == 201, requested.text
    authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-serial-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert authorized.status_code == 200, authorized.text
    posted = authorized.json()
    assert len(posted["stock_effect"]["original_movement_ids"]) == 1
    assert len(posted["stock_effect"]["reversal_movement_ids"]) == 1
    assert len(posted["stock_effect"]["replacement_movement_ids"]) == 3

    async with engine.connect() as connection:
        movements = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT effect.effect_role, effect.outcome, movement.movement_id,
                               movement.movement_type, movement.movement_leg,
                               movement.quantity_base, movement.unit_cost,
                               movement.value_delta, movement.reversal_of_movement_id,
                               coalesce(array_agg(
                                 serial.serial_number ORDER BY serial.serial_number
                               )
                                 FILTER (WHERE serial.serial_number IS NOT NULL), '{}') AS serials,
                               coalesce(sum(identity.quantity_base), 0) AS identity_quantity
                        FROM delivery_correction_movement_effects effect
                        JOIN stock_movements movement USING (movement_id)
                        LEFT JOIN stock_movement_identity_allocations identity USING (movement_id)
                        LEFT JOIN delivery_line_identity_allocations allocation
                          ON allocation.allocation_id =
                             identity.delivery_line_identity_allocation_id
                        LEFT JOIN pick_identity_assignments assignment
                          ON assignment.pick_identity_assignment_id =
                             allocation.pick_identity_assignment_id
                        LEFT JOIN stock_serial_allocations serial
                          ON serial.serial_allocation_id = assignment.serial_allocation_id
                        WHERE effect.correction_id = :correction_id
                        GROUP BY effect.effect_role, effect.outcome, movement.movement_id,
                                 movement.movement_type, movement.movement_leg,
                                 movement.quantity_base, movement.unit_cost,
                                 movement.value_delta, movement.reversal_of_movement_id
                        ORDER BY CASE effect.effect_role
                          WHEN 'original' THEN 1 WHEN 'reversal' THEN 2 ELSE 3 END,
                          movement.movement_leg
                        """
                    ),
                    {"correction_id": correction_id},
                )
            ).mappings()
        ]
        original_case_count = await connection.scalar(
            text(
                """
                SELECT count(*) FROM delivery_exception_cases exception
                JOIN delivery_confirmation_lines line USING (confirmation_line_id)
                WHERE line.confirmation_id = :confirmation_id
                  AND exception.correction_line_id IS NULL
                """
            ),
            {"confirmation_id": confirmation["confirmation_id"]},
        )
        original_identity_partitions = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT serial.serial_number, partition.accepted_quantity_base,
                               partition.refused_quantity_base,
                               partition.damaged_quantity_base,
                               partition.short_missing_quantity_base,
                               partition.still_undelivered_quantity_base
                        FROM delivery_confirmation_identity_partitions partition
                        JOIN delivery_line_identity_allocations allocation
                          ON allocation.allocation_id =
                             partition.delivery_line_identity_allocation_id
                        JOIN pick_identity_assignments assignment
                          ON assignment.pick_identity_assignment_id =
                             allocation.pick_identity_assignment_id
                        JOIN stock_serial_allocations serial
                          ON serial.serial_allocation_id = assignment.serial_allocation_id
                        WHERE partition.confirmation_line_id IN (
                          SELECT confirmation_line_id FROM delivery_confirmation_lines
                          WHERE confirmation_id = :confirmation_id
                        )
                        ORDER BY serial.serial_number
                        """
                    ),
                    {"confirmation_id": confirmation["confirmation_id"]},
                )
            ).mappings()
        ]
    assert original_case_count == 0
    assert original_identity_partitions == [
        {
            "serial_number": "SN-001",
            "accepted_quantity_base": Decimal("1.000000"),
            "refused_quantity_base": Decimal("0.000000"),
            "damaged_quantity_base": Decimal("0.000000"),
            "short_missing_quantity_base": Decimal("0.000000"),
            "still_undelivered_quantity_base": Decimal("0.000000"),
        },
        {
            "serial_number": "SN-002",
            "accepted_quantity_base": Decimal("1.000000"),
            "refused_quantity_base": Decimal("0.000000"),
            "damaged_quantity_base": Decimal("0.000000"),
            "short_missing_quantity_base": Decimal("0.000000"),
            "still_undelivered_quantity_base": Decimal("0.000000"),
        },
    ]
    assert [
        {
            "effect_role": row["effect_role"],
            "outcome": row["outcome"],
            "movement_type": row["movement_type"],
            "movement_leg": row["movement_leg"],
            "quantity_base": row["quantity_base"],
            "unit_cost": row["unit_cost"],
            "value_delta": row["value_delta"],
            "serials": row["serials"],
            "identity_quantity": row["identity_quantity"],
        }
        for row in movements
    ] == [
        {
            "effect_role": "original",
            "outcome": "accepted",
            "movement_type": "delivery_confirmation",
            "movement_leg": "delivery_outbound",
            "quantity_base": Decimal("2.000000"),
            "unit_cost": Decimal("10.000000"),
            "value_delta": Decimal("-20.000000"),
            "serials": ["SN-001", "SN-002"],
            "identity_quantity": Decimal("2.000000"),
        },
        {
            "effect_role": "reversal",
            "outcome": "accepted",
            "movement_type": "delivery_correction",
            "movement_leg": "correction_accepted_reversal_in",
            "quantity_base": Decimal("2.000000"),
            "unit_cost": Decimal("10.000000"),
            "value_delta": Decimal("20.000000"),
            "serials": ["SN-001", "SN-002"],
            "identity_quantity": Decimal("2.000000"),
        },
        {
            "effect_role": "replacement",
            "outcome": "accepted",
            "movement_type": "delivery_correction",
            "movement_leg": "correction_accepted_replacement_out",
            "quantity_base": Decimal("1.000000"),
            "unit_cost": Decimal("10.000000"),
            "value_delta": Decimal("-10.000000"),
            "serials": ["SN-001"],
            "identity_quantity": Decimal("1.000000"),
        },
        {
            "effect_role": "replacement",
            "outcome": "short_missing",
            "movement_type": "delivery_correction",
            "movement_leg": "correction_exception_replacement_investigation_in",
            "quantity_base": Decimal("1.000000"),
            "unit_cost": Decimal("10.000000"),
            "value_delta": Decimal("10.000000"),
            "serials": ["SN-002"],
            "identity_quantity": Decimal("1.000000"),
        },
        {
            "effect_role": "replacement",
            "outcome": "short_missing",
            "movement_type": "delivery_correction",
            "movement_leg": "correction_exception_replacement_transit_out",
            "quantity_base": Decimal("1.000000"),
            "unit_cost": Decimal("10.000000"),
            "value_delta": Decimal("-10.000000"),
            "serials": ["SN-002"],
            "identity_quantity": Decimal("1.000000"),
        },
    ]

    before_rebuild = await _serial_correction_projection(
        postgres_url,
        confirmation_id=confirmation["confirmation_id"],
        correction_id=correction_id,
        sku_id=fixture["sku_id"],
        warehouse_id=fixture["warehouse_id"],
    )
    assert before_rebuild["availability"] == [
        {
            "custody": "investigation",
            "identity_key": "serial:SN-002",
            "serial_numbers": ["SN-002"],
            "on_hand": Decimal("1.000000"),
            "reserved": Decimal("0.000000"),
        }
    ]
    assert before_rebuild["valuation"] == {
        "quantity_on_hand": Decimal("1.000000"),
        "inventory_value": Decimal("10.000000"),
        "moving_average_unit_cost": Decimal("10.000000"),
    }
    assert before_rebuild["confirmation"]["accepted_quantity_base"] == Decimal("2.000000")
    assert before_rebuild["confirmation"]["short_missing_quantity_base"] == Decimal("0.000000")
    assert len(before_rebuild["cases"]) == 1
    correction_case = before_rebuild["cases"][0]
    assert correction_case["correction_line_id"] is not None
    assert correction_case["exception_kind"] == "short_missing"
    assert correction_case["original_quantity_base"] == Decimal("1.000000")
    assert correction_case["initial_custody"] == "investigation"
    assert correction_case["status"] == "open"
    assert correction_case["custody"] == "investigation"
    assert correction_case["open_quantity_base"] == Decimal("1.000000")
    assert correction_case["resolved_quantity_base"] == Decimal("0.000000")

    rebuilt = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    after_rebuild = await _serial_correction_projection(
        postgres_url,
        confirmation_id=confirmation["confirmation_id"],
        correction_id=correction_id,
        sku_id=fixture["sku_id"],
        warehouse_id=fixture["warehouse_id"],
    )
    assert after_rebuild == before_rebuild


@pytest.mark.asyncio
async def test_authorization_controls_and_late_failure_leave_correction_pending_atomically(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    original_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    processed = await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    )
    assert processed == {"completed": 1, "failed": 0}
    correction_id = str(uuid4())
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "request-authorization-control-correction"},
        ),
        json={
            "correction_id": correction_id,
            "reason": "One of the two units was refused at the customer doorstep.",
            "evidence_ids": [confirmation["evidence_id"]],
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "accepted_quantity_base": "1.000000",
                    "refused_quantity_base": "1.000000",
                    "damaged_quantity_base": "0.000000",
                    "short_missing_quantity_base": "0.000000",
                    "still_undelivered_quantity_base": "0.000000",
                    "identity_positions": [],
                }
            ],
        },
    )
    assert requested.status_code == 201, requested.text
    pending = requested.json()
    assert pending["status"] == "pending_authorization"
    assert pending["version"] == 1

    attempts = [
        ("warehouse-supervisor-mnl", "self", "maker_checker_violation"),
        (
            "delivery-correction-checker-ceb",
            "out-of-scope",
            "operational_scope_required",
        ),
        (
            "delivery-correction-checker-low-mnl",
            "under-authority",
            "approval_authority_required",
        ),
    ]
    for subject, key, expected_code in attempts:
        rejected = await confirmation_client.post(
            f"/v1/delivery-corrections/{correction_id}/authorization",
            headers=auth(
                confirmation_settings,
                subject,
                **{"Idempotency-Key": f"{key}-authorization-control-correction"},
            ),
            json={"expected_correction_version": 1},
        )
        assert rejected.status_code == 403, rejected.text
        assert rejected.json()["error"]["code"] == expected_code
        unchanged = await confirmation_client.get(
            f"/v1/delivery-corrections/{correction_id}",
            headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        )
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json() == pending

    async with engine.connect() as connection:
        before_failure = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM stock_movements) AS movements,
                          (SELECT count(*) FROM delivery_correction_movement_effects)
                            AS movement_effects,
                          (SELECT count(*) FROM draft_invoices) AS draft_invoices,
                          (SELECT count(*) FROM delivery_receipts) AS receipts,
                          (SELECT count(*) FROM delivery_receipt_documents) AS receipt_documents,
                          (SELECT count(*) FROM document_series_number_audit) AS number_audits,
                          (SELECT next_number FROM document_series
                           WHERE document_type = 'delivery_receipt') AS next_number,
                          (SELECT count(*) FROM outbox_events) AS outbox_events,
                          (SELECT count(*) FROM delivery_correction_authorizations)
                            AS authorizations,
                          (SELECT count(*) FROM platform_command_receipts) AS command_receipts
                        """
                    )
                )
            )
            .mappings()
            .one()
        )

    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE FUNCTION fail_late_delivery_correction_authorization()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  RAISE EXCEPTION 'injected late delivery correction authorization failure'
                    USING ERRCODE = 'P0001';
                END
                $$
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TRIGGER fail_late_delivery_correction_authorization
                BEFORE INSERT ON delivery_correction_authorizations
                FOR EACH ROW EXECUTE FUNCTION fail_late_delivery_correction_authorization()
                """
            )
        )
    try:
        with pytest.raises(
            DBAPIError,
            match="injected late delivery correction authorization failure",
        ):
            await confirmation_client.post(
                f"/v1/delivery-corrections/{correction_id}/authorization",
                headers=auth(
                    confirmation_settings,
                    "delivery-correction-checker-mnl",
                    **{"Idempotency-Key": "late-failure-authorization-control-correction"},
                ),
                json={"expected_correction_version": 1},
            )
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DROP TRIGGER IF EXISTS fail_late_delivery_correction_authorization "
                    "ON delivery_correction_authorizations"
                )
            )
            await connection.execute(
                text("DROP FUNCTION IF EXISTS fail_late_delivery_correction_authorization()")
            )

    unchanged = await confirmation_client.get(
        f"/v1/delivery-corrections/{correction_id}",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json() == pending
    async with engine.connect() as connection:
        after_failure = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM stock_movements) AS movements,
                          (SELECT count(*) FROM delivery_correction_movement_effects)
                            AS movement_effects,
                          (SELECT count(*) FROM draft_invoices) AS draft_invoices,
                          (SELECT count(*) FROM delivery_receipts) AS receipts,
                          (SELECT count(*) FROM delivery_receipt_documents) AS receipt_documents,
                          (SELECT count(*) FROM document_series_number_audit) AS number_audits,
                          (SELECT next_number FROM document_series
                           WHERE document_type = 'delivery_receipt') AS next_number,
                          (SELECT count(*) FROM outbox_events) AS outbox_events,
                          (SELECT count(*) FROM delivery_correction_authorizations)
                            AS authorizations,
                          (SELECT count(*) FROM platform_command_receipts) AS command_receipts
                        """
                    )
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert after_failure == before_failure


async def _confirm_delivery_with_short(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
) -> tuple[dict[str, object], dict[str, object], UUID]:
    fixture, delivery_id = await dispatched_prepaid_delivery(
        client,
        settings,
        postgres_url,
        unit_cost="7.500000",
    )
    delivery_detail = await client.get(
        f"/v1/deliveries/{delivery_id}",
        headers=auth(settings, "delivery-mnl"),
    )
    assert delivery_detail.status_code == 200, delivery_detail.text
    delivery_line_id = delivery_detail.json()["lines"][0]["delivery_line_id"]
    evidence_id = str(uuid4())
    upload = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T13:00:00Z",
        },
    )
    assert upload.status_code == 201, upload.text
    photo_evidence_id = str(uuid4())
    upload = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(settings, "delivery-mnl"),
        json={
            "evidence_id": photo_evidence_id,
            "kind": "photo",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T13:00:01Z",
        },
    )
    assert upload.status_code == 201, upload.text
    for completed_id in (evidence_id, photo_evidence_id):
        completed = await client.post(
            f"/v1/deliveries/{delivery_id}/evidence/{completed_id}/complete",
            headers=auth(settings, "delivery-mnl"),
        )
        assert completed.status_code == 200, completed.text
    confirmed = await client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            settings,
            "delivery-mnl",
            **{"Idempotency-Key": "delivery-correction-source-with-short"},
        ),
        json={
            "confirmation_id": str(uuid4()),
            "expected_delivery_version": 1,
            "recipient_name": "Ana Santos",
            "device_captured_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    "delivery_line_id": delivery_line_id,
                    "accepted_quantity_base": "1.000000",
                    "short_missing_quantity_base": "1.000000",
                    "exception_details": {
                        "short_missing": {
                            "reason": "One unit was missing at the customer site.",
                            "evidence_ids": [photo_evidence_id],
                            "responsible_party_type": "carrier",
                        }
                    },
                }
            ],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        case_id = await connection.scalar(
            text(
                """
                SELECT exception.exception_case_id
                FROM delivery_exception_cases exception
                JOIN delivery_confirmation_lines line USING (confirmation_line_id)
                WHERE line.confirmation_id = :confirmation_id
                  AND exception.exception_kind = 'short_missing'
                  AND exception.correction_line_id IS NULL
                """
            ),
            {"confirmation_id": confirmed.json()["confirmation_id"]},
        )
    return fixture, {**confirmed.json(), "evidence_id": evidence_id}, UUID(str(case_id))


async def _confirm_two_line_fully_accepted_delivery(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
) -> tuple[dict[str, object], dict[str, object]]:
    organization = await bootstrap_payment_clearance(client, settings)
    branch = organization["branches"][0]
    branch_id = branch["branch_id"]
    warehouse_id = branch["warehouses"][0]["warehouse_id"]
    customer = await create_customer(client, settings, branch_id)
    sku = await create_sku(client, settings)
    tax = await create_tax_code(client, settings)
    price_list = await create_price_list(
        client,
        settings,
        branch_id=branch_id,
        customer_id=customer["customer_id"],
        sku_id=sku["sku_id"],
        tax_code_version_id=tax["tax_code_version_id"],
    )
    price_list_line_id = price_list["items"][0]["price_list_line_id"]
    sales_order_id = str(uuid4())
    first_line_id = str(uuid4())
    second_line_id = str(uuid4())
    created = await client.post(
        "/v1/sales/orders",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": "two-line-correction-order"},
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
                    "line_id": first_line_id,
                    "sku_id": sku["sku_id"],
                    "expected_price_list_line_id": price_list_line_id,
                    "expected_unit_conversion_id": None,
                    "expected_unit_conversion_version": None,
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "manual_override_unit_price": None,
                    "price_override_reason": None,
                },
                {
                    "line_id": second_line_id,
                    "sku_id": sku["sku_id"],
                    "expected_price_list_line_id": price_list_line_id,
                    "expected_unit_conversion_id": None,
                    "expected_unit_conversion_version": None,
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "manual_override_unit_price": None,
                    "price_override_reason": None,
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["grand_total"] == "448.00"
    await seed_available_stock(
        postgres_url, sku_id=sku["sku_id"], warehouse_id=warehouse_id, quantity="4.000000"
    )
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO document_series(
                  document_series_id, branch_id, document_type, prefix, next_number
                ) VALUES (
                  :series_id, :branch_id, 'delivery_receipt', 'DR-MNL', 1
                )
                """
            ),
            {"branch_id": branch_id, "series_id": uuid4()},
        )
        await connection.execute(
            text(
                """
                INSERT INTO stock_movements(
                  movement_id, sku_id, warehouse_id, location_id, movement_type,
                  quantity_base, unit_cost, value_delta, base_currency,
                  source_reference, entered_unit, conversion_snapshot,
                  actor_subject, correlation_id, idempotency_key,
                  movement_group_id, movement_leg)
                SELECT :movement_id, :sku_id, :warehouse_id, location_id,
                  'opening_stock', 4.000000, :unit_cost,
                  4.000000 * CAST(:unit_cost AS numeric),
                  'PHP', 'TEST-SEED', 'EA',
                  '{"source":"contract-seed","factor":"1.000000"}'::jsonb,
                  'sales-mnl', 'two-line-correction', :idempotency_key,
                  :movement_group_id, 'opening_in'
                FROM warehouse_stock_locations
                WHERE warehouse_id = :warehouse_id AND custody = 'available'
                """
            ),
            {
                "idempotency_key": f"two-line-opening-{uuid4()}",
                "movement_group_id": str(uuid4()),
                "movement_id": str(uuid4()),
                "sku_id": sku["sku_id"],
                "unit_cost": "7.500000",
                "warehouse_id": warehouse_id,
            },
        )
        await connection.execute(
            text(
                """
                UPDATE inventory_valuation
                SET inventory_value = quantity_on_hand * CAST(:unit_cost AS numeric),
                    moving_average_unit_cost = CAST(:unit_cost AS numeric)
                WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id
                """
            ),
            {
                "sku_id": sku["sku_id"],
                "unit_cost": "7.500000",
                "warehouse_id": warehouse_id,
            },
        )
    await engine.dispose()
    approved = await client.post(
        f"/v1/sales/orders/{sales_order_id}/commercial-approval",
        headers=auth(
            settings,
            "sales-mnl",
            **{"Idempotency-Key": "two-line-correction-approval", "If-Match": "1"},
        ),
        json={
            "warehouse_id": warehouse_id,
            "exception_reason": None,
            "credit_override_reason": None,
        },
    )
    assert approved.status_code == 201, approved.text
    fulfillment = await client.get(
        "/v1/fulfillment/orders",
        headers=auth(settings, "warehouse-supervisor-mnl"),
        params={"sales_order_id": sales_order_id},
    )
    assert fulfillment.status_code == 200, fulfillment.text
    fulfillment_orders = fulfillment.json()["items"]
    assert len(fulfillment_orders) == 1
    fulfillment_order_id = fulfillment_orders[0]["fulfillment_order_id"]
    receipt = await record_receipt(
        client,
        settings,
        {
            "branch_id": branch_id,
            "customer_id": customer["customer_id"],
            "sales_order_id": sales_order_id,
        },
        payment_method="cash",
        key="two-line-correction-prepayment",
        amount="448.00",
    )
    assert receipt.status_code == 201, receipt.text
    released = await client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/pick-release",
        headers=auth(
            settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "two-line-correction-release"},
        ),
        json={"reason": "Release two-line correction delivery"},
    )
    assert released.status_code == 201, released.text
    pick_id = str(uuid4())
    picked = await client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "two-line-correction-pick"},
        ),
        json={
            "pick_id": pick_id,
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": first_line_id,
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "selections": [],
                },
                {
                    "line_id": second_line_id,
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "selections": [],
                },
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
            **{"Idempotency-Key": "two-line-correction-dispatch"},
        ),
        json={
            "delivery_id": delivery_id,
            "expected_fulfillment_version": picked.json()["version"],
            "assigned_to": "delivery-mnl",
            "pick_ids": [pick_id],
        },
    )
    assert dispatched.status_code == 201, dispatched.text
    evidence_id = str(uuid4())
    upload = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T13:00:00Z",
        },
    )
    assert upload.status_code == 201, upload.text
    completed = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
        headers=auth(settings, "delivery-mnl"),
    )
    assert completed.status_code == 200, completed.text
    confirmed = await client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            settings,
            "delivery-mnl",
            **{"Idempotency-Key": "two-line-correction-confirmation"},
        ),
        json={
            "confirmation_id": str(uuid4()),
            "expected_delivery_version": 1,
            "recipient_name": "Two Line Recipient",
            "device_captured_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    "line_id": first_line_id,
                    "accepted_quantity_base": "2.000000",
                },
                {
                    "line_id": second_line_id,
                    "accepted_quantity_base": "2.000000",
                },
            ],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    return {
        "branch_id": branch_id,
        "customer_id": customer["customer_id"],
        "first_line_id": first_line_id,
        "second_line_id": second_line_id,
        "sales_order_id": sales_order_id,
        "sku_id": sku["sku_id"],
        "warehouse_id": warehouse_id,
    }, {**confirmed.json(), "evidence_id": evidence_id}


@pytest.mark.asyncio
async def test_authorization_rejects_draft_invoice_posted_since_request(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    original_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 1, "failed": 0}
    correction_id = str(uuid4())
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "request-posted-invoice-correction"},
        ),
        json={
            "correction_id": correction_id,
            "reason": "Customer refused one of the two units.",
            "evidence_ids": [confirmation["evidence_id"]],
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "accepted_quantity_base": "1.000000",
                    "refused_quantity_base": "1.000000",
                    "damaged_quantity_base": "0.000000",
                    "short_missing_quantity_base": "0.000000",
                    "still_undelivered_quantity_base": "0.000000",
                    "identity_positions": [],
                }
            ],
        },
    )
    assert requested.status_code == 201, requested.text
    pending = requested.json()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE draft_invoices DISABLE TRIGGER trg_draft_invoices_immutable")
            )
            await connection.execute(
                text("ALTER TABLE draft_invoices DROP CONSTRAINT ck_draft_invoice_status")
            )
            await connection.execute(
                text(
                    "UPDATE draft_invoices SET status = 'posted' "
                    "WHERE delivery_confirmation_id = :confirmation_id "
                    "AND invoice_kind = 'original'"
                ),
                {"confirmation_id": confirmation["confirmation_id"]},
            )
            await connection.execute(
                text("ALTER TABLE draft_invoices ENABLE TRIGGER trg_draft_invoices_immutable")
            )
        rejected = await confirmation_client.post(
            f"/v1/delivery-corrections/{correction_id}/authorization",
            headers=auth(
                confirmation_settings,
                "delivery-correction-checker-mnl",
                **{"Idempotency-Key": "authorize-posted-invoice-correction"},
            ),
            json={"expected_correction_version": 1},
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "delivery_correction_not_eligible"
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE draft_invoices DISABLE TRIGGER trg_draft_invoices_immutable")
            )
            await connection.execute(
                text(
                    "UPDATE draft_invoices SET status = 'draft' "
                    "WHERE delivery_confirmation_id = :confirmation_id "
                    "AND invoice_kind = 'original'"
                ),
                {"confirmation_id": confirmation["confirmation_id"]},
            )
            await connection.execute(
                text(
                    "ALTER TABLE draft_invoices ADD CONSTRAINT ck_draft_invoice_status "
                    "CHECK (status = 'draft')"
                )
            )
            await connection.execute(
                text("ALTER TABLE draft_invoices ENABLE TRIGGER trg_draft_invoices_immutable")
            )
    unchanged = await confirmation_client.get(
        f"/v1/delivery-corrections/{correction_id}",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json() == pending
    async with engine.connect() as connection:
        counts = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM delivery_correction_authorizations
                           WHERE correction_id = :correction_id) AS authorizations,
                          (SELECT count(*) FROM delivery_correction_movement_effects
                           WHERE correction_id = :correction_id) AS effects,
                          (SELECT count(*) FROM delivery_receipts
                           WHERE correction_id = :correction_id) AS replacement_receipts
                        """
                    ),
                    {"correction_id": correction_id},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert counts == {"authorizations": 0, "effects": 0, "replacement_receipts": 0}


@pytest.mark.asyncio
async def test_authorization_rejects_exception_custody_changed_since_request(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    _, confirmation, case_id = await _confirm_delivery_with_short(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    original_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 1, "failed": 0}
    correction_id = str(uuid4())
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "request-custody-changed-correction"},
        ),
        json={
            "correction_id": correction_id,
            "reason": "Customer later accepted the missing unit.",
            "evidence_ids": [confirmation["evidence_id"]],
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "accepted_quantity_base": "2.000000",
                    "refused_quantity_base": "0.000000",
                    "damaged_quantity_base": "0.000000",
                    "short_missing_quantity_base": "0.000000",
                    "still_undelivered_quantity_base": "0.000000",
                    "identity_positions": [],
                }
            ],
        },
    )
    assert requested.status_code == 201, requested.text
    pending = requested.json()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO delivery_exception_events(
                  exception_event_id, exception_case_id, event_type, quantity_base,
                  source_document_type, source_document_id, from_custody, to_custody,
                  reason, approved_by, actor_subject, correlation_id, idempotency_key
                )
                VALUES (
                  :event_id, :case_id, 'return_received', 1.000000,
                  'manual_test', :case_id, 'investigation', 'in_transit',
                  'Injected custody change after correction request',
                  'warehouse-supervisor-mnl', 'warehouse-supervisor-mnl',
                  'test-correlation', :key
                )
                """
            ),
            {
                "event_id": uuid4(),
                "case_id": case_id,
                "key": str(uuid4()),
            },
        )
    rejected = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-custody-changed-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "delivery_correction_not_eligible"
    unchanged = await confirmation_client.get(
        f"/v1/delivery-corrections/{correction_id}",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json() == pending
    async with engine.connect() as connection:
        counts = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM delivery_correction_authorizations
                           WHERE correction_id = :correction_id) AS authorizations,
                          (SELECT count(*) FROM delivery_correction_movement_effects
                           WHERE correction_id = :correction_id) AS effects,
                          (SELECT count(*) FROM delivery_receipts
                           WHERE correction_id = :correction_id) AS replacement_receipts
                        """
                    ),
                    {"correction_id": correction_id},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert counts == {"authorizations": 0, "effects": 0, "replacement_receipts": 0}


@pytest.mark.asyncio
async def test_zero_accepted_correction_preserves_series_and_posts_reversal_only(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    original_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    context = {"database_session_factory": factory, "object_storage": fake_storage}
    assert await poll_delivery_confirmation_outbox(context) == {"completed": 1, "failed": 0}
    async with engine.connect() as connection:
        original_invoice = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT draft_invoice_id, subtotal, discount_total, tax_total, grand_total
                        FROM draft_invoices
                        WHERE delivery_confirmation_id = :confirmation_id
                          AND invoice_kind = 'original'
                        """
                    ),
                    {"confirmation_id": confirmation["confirmation_id"]},
                )
            )
            .mappings()
            .one()
        )
        series_before = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT series.document_series_id, series.next_number,
                               count(audit.document_series_number_audit_id) AS audit_count
                        FROM document_series series
                        LEFT JOIN document_series_number_audit audit USING (document_series_id)
                        WHERE series.document_type = 'delivery_receipt'
                        GROUP BY series.document_series_id, series.next_number
                        """
                    )
                )
            )
            .mappings()
            .one()
        )

    correction_id = str(uuid4())
    command = {
        "correction_id": correction_id,
        "reason": "Both units were refused; no corrected receipt is issuable.",
        "evidence_ids": [confirmation["evidence_id"]],
        "lines": [
            {
                "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                "accepted_quantity_base": "0.000000",
                "refused_quantity_base": "2.000000",
                "damaged_quantity_base": "0.000000",
                "short_missing_quantity_base": "0.000000",
                "still_undelivered_quantity_base": "0.000000",
                "identity_positions": [],
            }
        ],
    }
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "request-zero-accepted-correction"},
        ),
        json=command,
    )
    assert requested.status_code == 201, requested.text
    authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-zero-accepted-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert authorized.status_code == 200, authorized.text
    posted = authorized.json()
    replay = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-zero-accepted-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == posted
    assert posted["status"] == "posted"
    assert posted["stock_effect"]["status"] == "posted"
    assert len(posted["stock_effect"]["original_movement_ids"]) == 1
    assert len(posted["stock_effect"]["reversal_movement_ids"]) == 1
    assert posted["stock_effect"]["replacement_movement_ids"] == []
    assert posted["draft_invoice_effect"] == {
        "status": "pending",
        "original_draft_invoice_id": str(original_invoice["draft_invoice_id"]),
        "reversal_draft_invoice_id": posted["draft_invoice_effect"]["reversal_draft_invoice_id"],
        "replacement_draft_invoice_id": None,
    }
    assert posted["receipt_effect"]["original_delivery_receipt_id"] == original_receipt_id
    assert posted["receipt_effect"]["replacement_delivery_receipt_id"] is None
    assert posted["receipt_effect"]["replacement_number"] is None
    assert posted["receipt_effect"]["replacement_document_status"] is None

    corrected_receipt = await confirmation_client.get(
        f"/v1/delivery-receipts/{original_receipt_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert corrected_receipt.status_code == 200, corrected_receipt.text
    assert corrected_receipt.json()["correction_status"] == "corrected"
    assert corrected_receipt.json()["correction_id"] == correction_id
    assert corrected_receipt.json()["replacement_delivery_receipt_id"] is None

    assert await poll_delivery_confirmation_outbox(context) == {"completed": 1, "failed": 0}
    assert await poll_delivery_confirmation_outbox(context) == {"completed": 0, "failed": 0}
    current = await confirmation_client.get(
        f"/v1/delivery-corrections/{correction_id}",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert current.status_code == 200, current.text
    completed = current.json()
    assert completed["draft_invoice_effect"] == {
        **posted["draft_invoice_effect"],
        "status": "completed",
    }
    assert completed["receipt_effect"] == posted["receipt_effect"]

    async with engine.connect() as connection:
        invoice_chain = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT invoice.invoice_kind, invoice.draft_invoice_id,
                               invoice.reversal_of_draft_invoice_id,
                               invoice.replaces_draft_invoice_id,
                               invoice.subtotal, invoice.discount_total,
                               invoice.tax_total, invoice.grand_total,
                               count(line.draft_invoice_line_id) AS line_count
                        FROM draft_invoices invoice
                        LEFT JOIN draft_invoice_lines line USING (draft_invoice_id)
                        WHERE invoice.delivery_confirmation_id = :confirmation_id
                        GROUP BY invoice.draft_invoice_id
                        ORDER BY CASE invoice.invoice_kind
                          WHEN 'original' THEN 1 WHEN 'reversal' THEN 2 ELSE 3 END
                        """
                    ),
                    {"confirmation_id": confirmation["confirmation_id"]},
                )
            ).mappings()
        ]
        zero_accepted_artifacts = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM delivery_receipts
                           WHERE correction_id = :correction_id) AS replacement_receipts,
                          (SELECT count(*) FROM delivery_receipt_documents document
                           JOIN delivery_receipts receipt USING (delivery_receipt_id)
                           WHERE receipt.correction_id = :correction_id) AS replacement_documents,
                          (SELECT count(*) FROM outbox_events
                           WHERE aggregate_id = :correction_id) AS outbox_events,
                          (SELECT count(*) FROM delivery_correction_authorizations
                           WHERE correction_id = :correction_id) AS authorizations,
                          series.next_number,
                          count(audit.document_series_number_audit_id) AS audit_count
                        FROM document_series series
                        LEFT JOIN document_series_number_audit audit USING (document_series_id)
                        WHERE series.document_series_id = :series_id
                        GROUP BY series.next_number
                        """
                    ),
                    {
                        "correction_id": correction_id,
                        "series_id": series_before["document_series_id"],
                    },
                )
            )
            .mappings()
            .one()
        )
    assert [row["invoice_kind"] for row in invoice_chain] == ["original", "reversal"]
    assert invoice_chain[1]["draft_invoice_id"] == UUID(
        posted["draft_invoice_effect"]["reversal_draft_invoice_id"]
    )
    assert invoice_chain[1]["reversal_of_draft_invoice_id"] == original_invoice["draft_invoice_id"]
    assert invoice_chain[1]["replaces_draft_invoice_id"] is None
    assert invoice_chain[1]["line_count"] == 1
    for amount_field in ("subtotal", "discount_total", "tax_total", "grand_total"):
        assert invoice_chain[1][amount_field] == -original_invoice[amount_field]
    assert zero_accepted_artifacts == {
        "replacement_receipts": 0,
        "replacement_documents": 0,
        "outbox_events": 1,
        "authorizations": 1,
        "next_number": series_before["next_number"],
        "audit_count": series_before["audit_count"],
    }

    skipped_audit_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO document_series_number_audit(
                  document_series_number_audit_id, document_series_id,
                  series_number, status, reason
                ) VALUES (:audit_id, :series_id, :series_number, 'skipped', :reason)
                """
            ),
            {
                "audit_id": skipped_audit_id,
                "series_id": series_before["document_series_id"],
                "series_number": series_before["next_number"],
                "reason": "Preprinted stock was voided before issue",
            },
        )
        await connection.execute(
            text(
                "UPDATE document_series SET next_number = next_number + 1 "
                "WHERE document_series_id = :series_id"
            ),
            {"series_id": series_before["document_series_id"]},
        )
    with pytest.raises(DBAPIError, match="uq_document_series_number_audit"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO document_series_number_audit(
                      document_series_number_audit_id, document_series_id,
                      series_number, status, delivery_receipt_id
                    ) VALUES (:audit_id, :series_id, :series_number, 'issued', :receipt_id)
                    """
                ),
                {
                    "audit_id": uuid4(),
                    "series_id": series_before["document_series_id"],
                    "series_number": series_before["next_number"],
                    "receipt_id": original_receipt_id,
                },
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE document_series_number_audit SET reason = 'changed' "
                    "WHERE document_series_number_audit_id = :audit_id"
                ),
                {"audit_id": skipped_audit_id},
            )
    async with engine.connect() as connection:
        skipped_audit = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT series_number, status, delivery_receipt_id, reason
                        FROM document_series_number_audit
                        WHERE document_series_number_audit_id = :audit_id
                        """
                    ),
                    {"audit_id": skipped_audit_id},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert skipped_audit == {
        "series_number": series_before["next_number"],
        "status": "skipped",
        "delivery_receipt_id": None,
        "reason": "Preprinted stock was voided before issue",
    }


@pytest.mark.asyncio
async def test_chain_scope_replay_and_database_guards_reject_invalid_correction_history(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    original_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 1, "failed": 0}
    correction_id = str(uuid4())
    command = {
        "correction_id": correction_id,
        "reason": "One unit was refused after the original receipt was signed.",
        "evidence_ids": [confirmation["evidence_id"]],
        "lines": [
            {
                "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                "accepted_quantity_base": "1.000000",
                "refused_quantity_base": "1.000000",
                "damaged_quantity_base": "0.000000",
                "short_missing_quantity_base": "0.000000",
                "still_undelivered_quantity_base": "0.000000",
                "identity_positions": [],
            }
        ],
    }
    request_headers = auth(
        confirmation_settings,
        "warehouse-supervisor-mnl",
        **{"Idempotency-Key": "request-chain-hardening-correction"},
    )
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=request_headers,
        json=command,
    )
    assert requested.status_code == 201, requested.text
    authorization_headers = auth(
        confirmation_settings,
        "delivery-correction-checker-mnl",
        **{"Idempotency-Key": "authorize-chain-hardening-correction"},
    )
    authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=authorization_headers,
        json={"expected_correction_version": 1},
    )
    assert authorized.status_code == 200, authorized.text
    posted = authorized.json()
    replacement_receipt_id = posted["receipt_effect"]["replacement_delivery_receipt_id"]
    assert replacement_receipt_id is not None
    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 1, "failed": 0}

    async with engine.connect() as connection:
        counts_before_chain_attempts = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM delivery_corrections) AS corrections,
                          (SELECT count(*) FROM delivery_correction_lines) AS lines,
                          (SELECT count(*) FROM delivery_correction_evidence) AS evidence,
                          (SELECT count(*) FROM delivery_correction_authorizations) AS approvals,
                          (SELECT count(*) FROM delivery_correction_movement_effects) AS effects,
                          (SELECT count(*) FROM stock_movements) AS movements,
                          (SELECT count(*) FROM draft_invoices) AS invoices,
                          (SELECT count(*) FROM delivery_receipts) AS receipts,
                          (SELECT count(*) FROM document_series_number_audit) AS number_audits,
                          (SELECT count(*) FROM outbox_events) AS outbox_events,
                          (SELECT count(*) FROM platform_command_receipts) AS command_receipts
                        """
                    )
                )
            )
            .mappings()
            .one()
        )
    chained_command = {**command, "correction_id": str(uuid4())}
    original_again = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "reject-noncurrent-original-correction"},
        ),
        json=chained_command,
    )
    assert original_again.status_code == 409, original_again.text
    assert original_again.json()["error"]["code"] == "delivery_correction_chain_conflict"
    async with engine.connect() as connection:
        counts_after_chain_attempts = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM delivery_corrections) AS corrections,
                          (SELECT count(*) FROM delivery_correction_lines) AS lines,
                          (SELECT count(*) FROM delivery_correction_evidence) AS evidence,
                          (SELECT count(*) FROM delivery_correction_authorizations) AS approvals,
                          (SELECT count(*) FROM delivery_correction_movement_effects) AS effects,
                          (SELECT count(*) FROM stock_movements) AS movements,
                          (SELECT count(*) FROM draft_invoices) AS invoices,
                          (SELECT count(*) FROM delivery_receipts) AS receipts,
                          (SELECT count(*) FROM document_series_number_audit) AS number_audits,
                          (SELECT count(*) FROM outbox_events) AS outbox_events,
                          (SELECT count(*) FROM platform_command_receipts) AS command_receipts
                        """
                    )
                )
            )
            .mappings()
            .one()
        )
    assert counts_after_chain_attempts == counts_before_chain_attempts

    branch_id = UUID(posted["branch_id"])
    warehouse_id = UUID(posted["warehouse_id"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM user_branch_scopes WHERE user_subject = 'warehouse-supervisor-mnl'"
                )
            )
            await connection.execute(
                text(
                    "DELETE FROM user_warehouse_scopes "
                    "WHERE user_subject = 'warehouse-supervisor-mnl'"
                )
            )
        denied_request_replay = await confirmation_client.post(
            f"/v1/delivery-receipts/{original_receipt_id}/corrections",
            headers=request_headers,
            json=command,
        )
        assert denied_request_replay.status_code == 403, denied_request_replay.text
        assert denied_request_replay.json()["error"]["code"] == "operational_scope_required"
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO user_branch_scopes(user_subject,branch_id) "
                    "VALUES ('warehouse-supervisor-mnl',:branch_id) ON CONFLICT DO NOTHING"
                ),
                {"branch_id": branch_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO user_warehouse_scopes(user_subject,warehouse_id) "
                    "VALUES ('warehouse-supervisor-mnl',:warehouse_id) ON CONFLICT DO NOTHING"
                ),
                {"warehouse_id": warehouse_id},
            )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM user_branch_scopes "
                    "WHERE user_subject = 'delivery-correction-checker-mnl'"
                )
            )
            await connection.execute(
                text(
                    "DELETE FROM user_warehouse_scopes "
                    "WHERE user_subject = 'delivery-correction-checker-mnl'"
                )
            )
        denied_authorization_replay = await confirmation_client.post(
            f"/v1/delivery-corrections/{correction_id}/authorization",
            headers=authorization_headers,
            json={"expected_correction_version": 1},
        )
        assert denied_authorization_replay.status_code == 403, denied_authorization_replay.text
        assert denied_authorization_replay.json()["error"]["code"] == "operational_scope_required"
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO user_branch_scopes(user_subject,branch_id) "
                    "VALUES ('delivery-correction-checker-mnl',:branch_id) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"branch_id": branch_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO user_warehouse_scopes(user_subject,warehouse_id) "
                    "VALUES ('delivery-correction-checker-mnl',:warehouse_id) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"warehouse_id": warehouse_id},
            )

    correction_insert = text(
        """
        INSERT INTO delivery_corrections(
          correction_id,original_delivery_receipt_id,delivery_id,confirmation_id,
          branch_id,warehouse_id,reason,requested_by,correlation_id,idempotency_key,
          base_currency,affected_inventory_value,affected_draft_invoice_value,
          affected_value_base_currency,original_draft_invoice_id,
          reversal_draft_invoice_id,replacement_draft_invoice_id
        )
        SELECT :new_correction_id,:replacement_receipt_id,
               coalesce(:delivery_id,delivery_id),confirmation_id,branch_id,warehouse_id,
               :reason,requested_by,'direct-db-hardening',:idempotency_key,
               base_currency,:affected_inventory_value,affected_draft_invoice_value,
               affected_value_base_currency,original_draft_invoice_id,
               :reversal_invoice_id,NULL
        FROM delivery_corrections WHERE correction_id = :source_correction_id
        """
    )
    line_insert = text(
        """
        INSERT INTO delivery_correction_lines(
          correction_line_id,correction_id,confirmation_line_id,delivery_line_id,
          line_id,sku_id,accepted_quantity_base,refused_quantity_base,
          damaged_quantity_base,short_missing_quantity_base,
          still_undelivered_quantity_base,unit_cost,value_delta
        )
        SELECT :new_line_id,:new_correction_id,confirmation_line_id,delivery_line_id,
               line_id,sku_id,accepted_quantity_base,refused_quantity_base,
               damaged_quantity_base,short_missing_quantity_base,
               still_undelivered_quantity_base,unit_cost,value_delta
        FROM delivery_correction_lines WHERE correction_id = :source_correction_id
        """
    )

    def direct_values(
        *,
        reason: str,
        delivery_id: UUID | None = None,
        affected_inventory_value: Decimal = Decimal("7.500000"),
    ) -> dict[str, object]:
        return {
            "new_correction_id": uuid4(),
            "replacement_receipt_id": UUID(replacement_receipt_id),
            "delivery_id": delivery_id,
            "reason": reason,
            "idempotency_key": str(uuid4()),
            "reversal_invoice_id": uuid4(),
            "source_correction_id": UUID(correction_id),
            "affected_inventory_value": affected_inventory_value,
        }

    for invalid_reason in ("", "   "):
        values = direct_values(reason=invalid_reason)
        with pytest.raises(DBAPIError, match="ck_delivery_correction_reason"):
            async with engine.begin() as connection:
                await connection.execute(correction_insert, values)

    no_evidence = direct_values(reason="Missing evidence must fail at commit")
    with pytest.raises(
        DBAPIError,
        match="Delivery Correction requires lines and verified source Delivery evidence",
    ):
        async with engine.begin() as connection:
            await connection.execute(correction_insert, no_evidence)
            await connection.execute(
                line_insert,
                {
                    **no_evidence,
                    "new_line_id": uuid4(),
                },
            )
    mismatched_delivery = direct_values(
        reason="A correction may not claim a different Delivery",
        delivery_id=uuid4(),
    )
    with pytest.raises(DBAPIError, match="delivery_dispatches"):
        async with engine.begin() as connection:
            await connection.execute(correction_insert, mismatched_delivery)

    raw_pending = direct_values(
        reason="Valid direct pending correction for DB guard checks",
        affected_inventory_value=Decimal("0.000000"),
    )
    raw_line_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(correction_insert, raw_pending)
        await connection.execute(
            line_insert,
            {**raw_pending, "new_line_id": raw_line_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO delivery_correction_evidence(correction_id,evidence_id)
                SELECT :new_correction_id,evidence_id
                FROM delivery_correction_evidence WHERE correction_id = :source_correction_id
                """
            ),
            raw_pending,
        )
        await connection.execute(
            text(
                "UPDATE delivery_corrections SET sealed_at = now() "
                "WHERE correction_id = :new_correction_id"
            ),
            raw_pending,
        )

    sealed_append_commands = [
        (
            line_insert,
            {**raw_pending, "new_line_id": uuid4()},
        ),
        (
            text(
                """
                INSERT INTO delivery_correction_identity_positions(
                  correction_identity_position_id,correction_line_id,
                  delivery_line_identity_allocation_id,accepted_quantity_base,
                  refused_quantity_base,damaged_quantity_base,
                  short_missing_quantity_base,still_undelivered_quantity_base
                ) VALUES (:position_id,:line_id,:allocation_id,1,0,0,0,0)
                """
            ),
            {
                "position_id": uuid4(),
                "line_id": raw_line_id,
                "allocation_id": uuid4(),
            },
        ),
        (
            text(
                """
                INSERT INTO delivery_correction_evidence(correction_id,evidence_id)
                SELECT :new_correction_id,evidence_id
                FROM delivery_correction_evidence WHERE correction_id = :source_correction_id
                """
            ),
            raw_pending,
        ),
    ]
    for statement, values in sealed_append_commands:
        with pytest.raises(DBAPIError, match="Delivery Correction proposal is sealed"):
            async with engine.begin() as connection:
                await connection.execute(statement, values)

    async with engine.connect() as connection:
        authority_rows = {
            row["user_subject"]: row["approval_authority_id"]
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT user_subject,approval_authority_id FROM approval_authorities
                        WHERE capability_code = 'fulfillment:delivery-correction-authorize'
                          AND user_subject IN (
                            'warehouse-supervisor-mnl',
                            'delivery-correction-checker-mnl',
                            'delivery-correction-checker-low-mnl')
                        """
                    )
                )
            ).mappings()
        }
    invalid_authorizations = [
        (
            "warehouse-supervisor-mnl",
            authority_rows["warehouse-supervisor-mnl"],
        ),
        (
            "delivery-correction-checker-mnl",
            authority_rows["warehouse-supervisor-mnl"],
        ),
        (
            "delivery-correction-checker-low-mnl",
            authority_rows["delivery-correction-checker-low-mnl"],
        ),
    ]
    for authorized_by, authority_id in invalid_authorizations:
        with pytest.raises(
            DBAPIError,
            match="Delivery Correction authorization violates maker-checker authority",
        ):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO delivery_correction_authorizations(
                          correction_id,authorized_by,approval_authority_id,
                          idempotency_key,correlation_id
                        ) VALUES (:correction_id,:authorized_by,:authority_id,:key,'db-hardening')
                        """
                    ),
                    {
                        "correction_id": raw_pending["new_correction_id"],
                        "authorized_by": authorized_by,
                        "authority_id": authority_id,
                        "key": str(uuid4()),
                    },
                )

    async with engine.connect() as connection:
        immutable_before = {
            "evidence": dict(
                (
                    await connection.execute(
                        text(
                            "SELECT * FROM delivery_correction_evidence "
                            "WHERE correction_id = :correction_id"
                        ),
                        {"correction_id": correction_id},
                    )
                )
                .mappings()
                .one()
            ),
            "effect": dict(
                (
                    await connection.execute(
                        text(
                            "SELECT * FROM delivery_correction_movement_effects "
                            "WHERE correction_id = :correction_id "
                            "ORDER BY effect_role LIMIT 1"
                        ),
                        {"correction_id": correction_id},
                    )
                )
                .mappings()
                .one()
            ),
        }
    immutable_commands = [
        (
            "UPDATE delivery_correction_evidence SET evidence_id = evidence_id "
            "WHERE correction_id = :correction_id",
            "evidence",
        ),
        (
            "DELETE FROM delivery_correction_evidence WHERE correction_id = :correction_id",
            "evidence",
        ),
        (
            "UPDATE delivery_correction_movement_effects SET outcome = outcome "
            "WHERE movement_effect_id = :row_id",
            "effect",
        ),
        (
            "DELETE FROM delivery_correction_movement_effects WHERE movement_effect_id = :row_id",
            "effect",
        ),
    ]
    for statement, kind in immutable_commands:
        with pytest.raises(DBAPIError, match="Delivery Correction history is immutable"):
            async with engine.begin() as connection:
                await connection.execute(
                    text(statement),
                    {
                        "correction_id": correction_id,
                        "row_id": immutable_before[kind].get("movement_effect_id"),
                    },
                )
    async with engine.connect() as connection:
        immutable_after = {
            "evidence": dict(
                (
                    await connection.execute(
                        text(
                            "SELECT * FROM delivery_correction_evidence "
                            "WHERE correction_id = :correction_id"
                        ),
                        {"correction_id": correction_id},
                    )
                )
                .mappings()
                .one()
            ),
            "effect": dict(
                (
                    await connection.execute(
                        text(
                            "SELECT * FROM delivery_correction_movement_effects "
                            "WHERE movement_effect_id = :row_id"
                        ),
                        {"row_id": immutable_before["effect"]["movement_effect_id"]},
                    )
                )
                .mappings()
                .one()
            ),
        }
    await engine.dispose()
    assert immutable_after == immutable_before


@pytest.mark.asyncio
async def test_sequential_correction_uses_immediate_prior_receipt_invoice_stock_and_case_state(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    fixture, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    original_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    context = {"database_session_factory": factory, "object_storage": fake_storage}
    assert await poll_delivery_confirmation_outbox(context) == {"completed": 1, "failed": 0}
    source_line_id = confirmation["lines"][0]["delivery_line_id"]
    first_shape = {
        "delivery_line_id": source_line_id,
        "accepted_quantity_base": "1.000000",
        "refused_quantity_base": "0.000000",
        "damaged_quantity_base": "0.000000",
        "short_missing_quantity_base": "1.000000",
        "still_undelivered_quantity_base": "0.000000",
        "identity_positions": [],
    }
    async with engine.connect() as connection:
        correction_count_before_validation = await connection.scalar(
            text("SELECT count(*) FROM delivery_corrections")
        )
    invalid_commands = [
        (
            "   ",
            first_shape,
            {"Idempotency-Key": "reject-whitespace-correction-reason"},
        ),
        (
            "Excess fractional precision must be rejected.",
            {
                **first_shape,
                "accepted_quantity_base": "1.0000001",
                "short_missing_quantity_base": "0.9999999",
            },
            {"Idempotency-Key": "reject-fractional-correction-precision"},
        ),
        (
            "Blank idempotency keys must be rejected.",
            first_shape,
            {"Idempotency-Key": ""},
        ),
        (
            "Oversized idempotency keys must be rejected.",
            first_shape,
            {"Idempotency-Key": "x" * 201},
        ),
    ]
    for reason, line, extra_headers in invalid_commands:
        invalid = await confirmation_client.post(
            f"/v1/delivery-receipts/{original_receipt_id}/corrections",
            headers=auth(
                confirmation_settings,
                "warehouse-supervisor-mnl",
                **extra_headers,
            ),
            json={
                "correction_id": str(uuid4()),
                "reason": reason,
                "evidence_ids": [confirmation["evidence_id"]],
                "lines": [line],
            },
        )
        assert 400 <= invalid.status_code < 500, invalid.text
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT count(*) FROM delivery_corrections"))
            == correction_count_before_validation
        )

    first_correction_id = str(uuid4())
    first_command = {
        "correction_id": first_correction_id,
        "reason": "The second unit was missing after the first receipt was issued.",
        "evidence_ids": [confirmation["evidence_id"]],
        "lines": [first_shape],
    }
    first_requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "request-first-sequential-correction"},
        ),
        json=first_command,
    )
    assert first_requested.status_code == 201, first_requested.text
    first_authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{first_correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-first-sequential-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert first_authorized.status_code == 200, first_authorized.text
    first_posted = first_authorized.json()
    first_replacement_receipt_id = first_posted["receipt_effect"]["replacement_delivery_receipt_id"]
    first_replacement_invoice_id = first_posted["draft_invoice_effect"][
        "replacement_draft_invoice_id"
    ]
    assert first_replacement_receipt_id is not None
    assert first_replacement_invoice_id is not None
    assert len(first_posted["stock_effect"]["replacement_movement_ids"]) == 3
    assert await poll_delivery_confirmation_outbox(context) == {"completed": 1, "failed": 0}

    first_replacement_detail = await confirmation_client.get(
        f"/v1/delivery-receipts/{first_replacement_receipt_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert first_replacement_detail.status_code == 200, first_replacement_detail.text
    source_context = first_replacement_detail.json()
    assert source_context["correction_status"] == "replacement"
    assert source_context["correction_id"] == first_correction_id
    assert source_context["created_by_correction_id"] == first_correction_id
    assert source_context["superseded_by_correction_id"] is None
    assert source_context["corrects_delivery_receipt_id"] == original_receipt_id
    assert source_context["replacement_delivery_receipt_id"] is None
    assert source_context["evidence_ids"] == [confirmation["evidence_id"]]
    assert len(source_context["confirmation_lines"]) == 1
    prior_line = source_context["confirmation_lines"][0]
    assert prior_line["delivery_line_id"] == source_line_id
    assert Decimal(prior_line["accepted_quantity_base"]) == Decimal("1")
    assert Decimal(prior_line["short_missing_quantity_base"]) == Decimal("1")
    assert Decimal(prior_line["damaged_quantity_base"]) == Decimal("0")
    assert Decimal(prior_line["unit_cost"]) == Decimal("7.5")
    assert Decimal(prior_line["value_delta"]) == Decimal("-7.5")
    assert prior_line["identity_positions"] == []

    second_correction_id = str(uuid4())
    second_command = {
        "correction_id": second_correction_id,
        "reason": "The missing unit was found damaged in transit.",
        "evidence_ids": source_context["evidence_ids"],
        "lines": [
            {
                "delivery_line_id": prior_line["delivery_line_id"],
                "accepted_quantity_base": "1.000000",
                "refused_quantity_base": "0.000000",
                "damaged_quantity_base": "1.000000",
                "short_missing_quantity_base": "0.000000",
                "still_undelivered_quantity_base": "0.000000",
                "identity_positions": [],
            }
        ],
    }
    second_requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{first_replacement_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "request-second-sequential-correction"},
        ),
        json=second_command,
    )
    assert second_requested.status_code == 201, second_requested.text
    assert second_requested.json()["lines"] == second_command["lines"]
    second_authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{second_correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-second-sequential-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert second_authorized.status_code == 200, second_authorized.text
    second_posted = second_authorized.json()
    second_replay = await confirmation_client.post(
        f"/v1/delivery-corrections/{second_correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-second-sequential-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert second_replay.status_code == 200, second_replay.text
    assert second_replay.json() == second_posted
    assert set(second_posted["stock_effect"]["original_movement_ids"]) == set(
        first_posted["stock_effect"]["replacement_movement_ids"]
    )
    assert len(second_posted["stock_effect"]["reversal_movement_ids"]) == 3
    assert len(second_posted["stock_effect"]["replacement_movement_ids"]) == 1
    second_replacement_receipt_id = second_posted["receipt_effect"][
        "replacement_delivery_receipt_id"
    ]
    assert second_posted["receipt_effect"]["original_delivery_receipt_id"] == (
        first_replacement_receipt_id
    )
    assert second_replacement_receipt_id is not None
    assert await poll_delivery_confirmation_outbox(context) == {"completed": 1, "failed": 0}
    assert await poll_delivery_confirmation_outbox(context) == {"completed": 0, "failed": 0}

    prior_after = await confirmation_client.get(
        f"/v1/delivery-receipts/{first_replacement_receipt_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert prior_after.status_code == 200, prior_after.text
    assert prior_after.json()["correction_status"] == "corrected"
    assert prior_after.json()["correction_id"] == second_correction_id
    assert prior_after.json()["created_by_correction_id"] == first_correction_id
    assert prior_after.json()["superseded_by_correction_id"] == second_correction_id
    assert prior_after.json()["replacement_delivery_receipt_id"] == second_replacement_receipt_id
    second_receipt = await confirmation_client.get(
        f"/v1/delivery-receipts/{second_replacement_receipt_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert second_receipt.status_code == 200, second_receipt.text
    assert second_receipt.json()["corrects_delivery_receipt_id"] == first_replacement_receipt_id
    assert second_receipt.json()["created_by_correction_id"] == second_correction_id
    assert second_receipt.json()["correction_status"] == "replacement"

    async with engine.connect() as connection:
        second_invoice_links = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          reversal.reversal_of_draft_invoice_id,
                          replacement.replaces_draft_invoice_id,
                          reversal.grand_total AS reversal_total,
                          replacement.grand_total AS replacement_total
                        FROM draft_invoices reversal
                        JOIN draft_invoices replacement
                          ON replacement.correction_id = reversal.correction_id
                         AND replacement.invoice_kind = 'replacement'
                        WHERE reversal.correction_id = :correction_id
                          AND reversal.invoice_kind = 'reversal'
                        """
                    ),
                    {"correction_id": second_correction_id},
                )
            )
            .mappings()
            .one()
        )
    assert second_invoice_links["reversal_of_draft_invoice_id"] == UUID(
        first_replacement_invoice_id
    )
    assert second_invoice_links["replaces_draft_invoice_id"] == UUID(first_replacement_invoice_id)
    assert second_invoice_links["reversal_total"] == Decimal("-112.000000")
    assert second_invoice_links["replacement_total"] == Decimal("112.000000")

    before_rebuild = await _sequential_correction_projection(
        postgres_url,
        correction_ids=[first_correction_id, second_correction_id],
        sku_id=fixture["sku_id"],
        warehouse_id=fixture["warehouse_id"],
    )
    assert before_rebuild["availability"] == [
        {
            "custody": "in_transit",
            "identity_key": "",
            "on_hand": Decimal("1.000000"),
            "reserved": Decimal("0.000000"),
        }
    ]
    assert before_rebuild["valuation"] == {
        "quantity_on_hand": Decimal("1.000000"),
        "inventory_value": Decimal("7.500000"),
        "moving_average_unit_cost": Decimal("7.500000"),
    }
    case_by_kind = {row["exception_kind"]: row for row in before_rebuild["cases"]}
    assert case_by_kind["short_missing"]["correction_id"] == UUID(first_correction_id)
    assert case_by_kind["short_missing"]["status"] == "resolved"
    assert case_by_kind["short_missing"]["open_quantity_base"] == Decimal("0.000000")
    assert case_by_kind["short_missing"]["resolved_quantity_base"] == Decimal("1.000000")
    assert case_by_kind["short_missing"]["event_types"][-1] == "superseded_by_correction"
    assert case_by_kind["damaged"]["correction_id"] == UUID(second_correction_id)
    assert case_by_kind["damaged"]["status"] == "open"
    assert case_by_kind["damaged"]["custody"] == "in_transit"
    assert case_by_kind["damaged"]["open_quantity_base"] == Decimal("1.000000")
    effect_roles = [row["effect_role"] for row in before_rebuild["effects"]]
    assert effect_roles.count("original") == 3
    assert effect_roles.count("reversal") == 3
    assert effect_roles.count("replacement") == 1
    reversed_ids = {
        row["reversal_of_movement_id"]
        for row in before_rebuild["effects"]
        if row["effect_role"] == "reversal"
    }
    assert reversed_ids == {
        UUID(movement_id)
        for movement_id in first_posted["stock_effect"]["replacement_movement_ids"]
    }
    rebuilt = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    after_rebuild = await _sequential_correction_projection(
        postgres_url,
        correction_ids=[first_correction_id, second_correction_id],
        sku_id=fixture["sku_id"],
        warehouse_id=fixture["warehouse_id"],
    )
    await engine.dispose()
    assert after_rebuild == before_rebuild


@pytest.mark.asyncio
async def test_multi_physical_lot_correction_aggregates_case_economics_and_rebuilds_exact_custody(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    fixture = await approved_tracked_order(
        confirmation_client,
        confirmation_settings,
        tracking_policy="lot",
        expiration_control=False,
        key_prefix="correction-case-split",
        conversions=[
            {
                "unit_code": "CASE",
                "base_quantity": "2.000000",
                "effective_from": "2026-01-01",
                "effective_to": None,
            }
        ],
        selling_unit="CASE",
        order_quantity="1.000000",
        selling_unit_price="0.050000",
        selling_unit_floor_price="0.010000",
        opening_unit_cost="0.010000",
        stock_entries=[
            {
                "quantity": "1.000000",
                "lot_code": "CASE-LOT-A",
                "location_code": "CASE-BIN-A",
                "expiration_date": "2027-12-31",
            },
            {
                "quantity": "1.000000",
                "lot_code": "CASE-LOT-B",
                "location_code": "CASE-BIN-B",
                "expiration_date": "2027-12-31",
            },
        ],
    )
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    pick_id = str(uuid4())
    picked = await confirmation_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "correction-case-split-pick"},
        ),
        json={
            "pick_id": pick_id,
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "1.000000",
                    "unit_code": "CASE",
                    "selections": [
                        {
                            "lot_code": lot_code,
                            "quantity": "0.500000",
                            "manual_reason": "Exact physical CASE correction lot",
                        }
                        for lot_code in ("CASE-LOT-A", "CASE-LOT-B")
                    ],
                }
            ],
        },
    )
    assert picked.status_code == 201, picked.text
    assert len(picked.json()["lines"]) == 2
    assert {line["quantity_base"] for line in picked.json()["lines"]} == {"1.000000"}
    assert {
        lot["lot_code"] for line in picked.json()["lines"] for lot in line["lot_selections"]
    } == {"CASE-LOT-A", "CASE-LOT-B"}

    delivery_id = str(uuid4())
    dispatched = await confirmation_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "correction-case-split-dispatch"},
        ),
        json={
            "delivery_id": delivery_id,
            "expected_fulfillment_version": picked.json()["version"],
            "assigned_to": "delivery-mnl",
            "pick_ids": [pick_id],
        },
    )
    assert dispatched.status_code == 201, dispatched.text
    assert len(dispatched.json()["lines"]) == 1
    assert dispatched.json()["lines"][0]["quantity_base"] == "2.000000"

    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO document_series(document_series_id,branch_id,document_type,"
                "prefix,next_number) VALUES (:id,:branch_id,'delivery_receipt','DR-MNL',1)"
            ),
            {"id": uuid4(), "branch_id": fixture["branch_id"]},
        )

    evidence_id = str(uuid4())
    upload = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(confirmation_settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T13:00:00Z",
        },
    )
    assert upload.status_code == 201, upload.text
    completed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert completed.status_code == 200, completed.text
    confirmation_id = str(uuid4())
    confirmed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "correction-case-split-confirmation"},
        ),
        json={
            "confirmation_id": confirmation_id,
            "expected_delivery_version": 1,
            "recipient_name": "Multi-physical CASE Recipient",
            "device_captured_at": "2026-08-01T13:01:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "accepted_quantity_base": "2.000000",
                }
            ],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    original_receipt_id = confirmed.json()["delivery_receipt"]["delivery_receipt_id"]
    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 1, "failed": 0}

    original_receipt = await confirmation_client.get(
        f"/v1/delivery-receipts/{original_receipt_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert original_receipt.status_code == 200, original_receipt.text
    source = original_receipt.json()
    assert len(source["confirmation_lines"]) == 2
    assert len(source["snapshot"]["lines"]) == 1
    assert source["snapshot"]["lines"][0]["accepted_quantity_base"] == "2.000000"
    assert source["snapshot"]["lines"][0]["accepted_quantity_entered"] == "1.000000"
    assert source["snapshot"]["lines"][0]["entered_unit"] == "CASE"
    physical_lines = sorted(
        source["confirmation_lines"],
        key=lambda line: line["identity_positions"][0]["lot_code"],
    )
    assert [
        (
            line["identity_positions"][0]["lot_code"],
            line["accepted_quantity_base"],
            line["unit_cost"],
        )
        for line in physical_lines
    ] == [
        ("CASE-LOT-A", "1.000000", "0.010000"),
        ("CASE-LOT-B", "1.000000", "0.010000"),
    ]

    async with engine.connect() as connection:
        original_invoice = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT invoice.draft_invoice_id,invoice.subtotal,
                               invoice.discount_total,invoice.tax_total,invoice.grand_total,
                               line.accepted_quantity_base,line.subtotal AS line_subtotal,
                               line.discount_amount,line.tax_amount,line.line_total
                        FROM draft_invoices invoice
                        JOIN draft_invoice_lines line USING (draft_invoice_id)
                        WHERE invoice.delivery_confirmation_id = :confirmation_id
                          AND invoice.invoice_kind = 'original'
                        """
                    ),
                    {"confirmation_id": confirmation_id},
                )
            )
            .mappings()
            .one()
        )
    assert original_invoice == {
        "draft_invoice_id": original_invoice["draft_invoice_id"],
        "subtotal": Decimal("0.050000"),
        "discount_total": Decimal("0.000000"),
        "tax_total": Decimal("0.010000"),
        "grand_total": Decimal("0.060000"),
        "accepted_quantity_base": Decimal("2.000000"),
        "line_subtotal": Decimal("0.050000"),
        "discount_amount": Decimal("0.000000"),
        "tax_amount": Decimal("0.010000"),
        "line_total": Decimal("0.060000"),
    }

    correction_id = str(uuid4())
    correction_lines = []
    for position, source_line in enumerate(physical_lines):
        accepted = "1.000000" if position == 0 else "0.000000"
        refused = "0.000000" if position == 0 else "1.000000"
        identity = source_line["identity_positions"][0]
        correction_lines.append(
            {
                "delivery_line_id": source_line["delivery_line_id"],
                "accepted_quantity_base": accepted,
                "refused_quantity_base": refused,
                "damaged_quantity_base": "0.000000",
                "short_missing_quantity_base": "0.000000",
                "still_undelivered_quantity_base": "0.000000",
                "identity_positions": [
                    {
                        "delivery_line_identity_allocation_id": identity[
                            "delivery_line_identity_allocation_id"
                        ],
                        "accepted_quantity_base": accepted,
                        "refused_quantity_base": refused,
                        "damaged_quantity_base": "0.000000",
                        "short_missing_quantity_base": "0.000000",
                        "still_undelivered_quantity_base": "0.000000",
                    }
                ],
            }
        )
    proposal = {
        "correction_id": correction_id,
        "reason": "Only CASE-LOT-A was accepted; CASE-LOT-B was refused.",
        "evidence_ids": [evidence_id],
        "lines": correction_lines,
    }
    request_headers = auth(
        confirmation_settings,
        "warehouse-supervisor-mnl",
        **{"Idempotency-Key": "request-correction-case-split"},
    )
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=request_headers,
        json=proposal,
    )
    assert requested.status_code == 201, requested.text
    pending = requested.json()
    # Worked source economics: replacement subtotal .03 + VAT .01 = .04,
    # so the Draft Invoice delta is .06 - .04 = .02. Scaling .06 directly
    # by one-half would incorrectly produce .03 and disagree with its components.
    assert Decimal(pending["affected_value_base_currency"]) == Decimal("0.020000")
    async with engine.connect() as connection:
        sealed_values = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT affected_inventory_value,affected_draft_invoice_value,
                               affected_value_base_currency
                        FROM delivery_corrections WHERE correction_id = :correction_id
                        """
                    ),
                    {"correction_id": correction_id},
                )
            )
            .mappings()
            .one()
        )
    assert sealed_values == {
        "affected_inventory_value": Decimal("0.010000"),
        "affected_draft_invoice_value": Decimal("0.020000"),
        "affected_value_base_currency": Decimal("0.020000"),
    }
    request_replay = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=request_headers,
        json=proposal,
    )
    assert request_replay.status_code == 200, request_replay.text
    assert request_replay.json() == pending

    authorization_headers = auth(
        confirmation_settings,
        "delivery-correction-checker-mnl",
        **{"Idempotency-Key": "authorize-correction-case-split"},
    )
    authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=authorization_headers,
        json={"expected_correction_version": 1},
    )
    assert authorized.status_code == 200, authorized.text
    posted = authorized.json()
    assert Decimal(posted["affected_value_base_currency"]) == Decimal("0.020000")
    authorization_replay = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=authorization_headers,
        json={"expected_correction_version": 1},
    )
    assert authorization_replay.status_code == 200, authorization_replay.text
    assert authorization_replay.json() == posted
    correction_processed = await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    )
    assert correction_processed == {"completed": 1, "failed": 0}
    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 0, "failed": 0}

    replacement_invoice_id = posted["draft_invoice_effect"]["replacement_draft_invoice_id"]
    replacement_receipt_id = posted["receipt_effect"]["replacement_delivery_receipt_id"]
    assert replacement_invoice_id is not None
    assert replacement_receipt_id is not None
    async with engine.connect() as connection:
        replacement_invoice = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT invoice.subtotal,invoice.discount_total,invoice.tax_total,
                               invoice.grand_total,line.accepted_quantity_base,
                               line.subtotal AS line_subtotal,line.discount_amount,
                               line.tax_amount,line.line_total
                        FROM draft_invoices invoice
                        JOIN draft_invoice_lines line USING (draft_invoice_id)
                        WHERE invoice.draft_invoice_id = :draft_invoice_id
                        """
                    ),
                    {"draft_invoice_id": replacement_invoice_id},
                )
            )
            .mappings()
            .one()
        )
        invoice_counts = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM draft_invoices
                           WHERE delivery_confirmation_id = :confirmation_id) AS invoices,
                          (SELECT count(*) FROM draft_invoice_lines line
                           JOIN draft_invoices invoice USING (draft_invoice_id)
                           WHERE invoice.delivery_confirmation_id = :confirmation_id
                             AND invoice.invoice_kind = 'replacement') AS replacement_lines
                        """
                    ),
                    {"confirmation_id": confirmation_id},
                )
            )
            .mappings()
            .one()
        )
        movement_effects = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT effect.effect_role,effect.outcome,line.delivery_line_id,
                               movement.quantity_base,movement.value_delta,
                               coalesce(array_agg(lot.lot_code ORDER BY lot.lot_code)
                                 FILTER (WHERE lot.lot_code IS NOT NULL),'{}') AS lots
                        FROM delivery_correction_movement_effects effect
                        JOIN delivery_correction_lines line USING (correction_line_id)
                        JOIN stock_movements movement USING (movement_id)
                        LEFT JOIN stock_movement_identity_allocations identity
                          USING (movement_id)
                        LEFT JOIN delivery_line_identity_allocations allocation
                          ON allocation.allocation_id =
                             identity.delivery_line_identity_allocation_id
                        LEFT JOIN pick_identity_assignments assignment
                          ON assignment.pick_identity_assignment_id =
                             allocation.pick_identity_assignment_id
                        LEFT JOIN lot_identities lot
                          ON lot.lot_identity_id = assignment.lot_identity_id
                        WHERE effect.correction_id = :correction_id
                        GROUP BY effect.effect_role,effect.outcome,line.delivery_line_id,
                                 movement.movement_id,movement.quantity_base,
                                 movement.value_delta
                        ORDER BY CASE effect.effect_role
                          WHEN 'original' THEN 1 WHEN 'reversal' THEN 2 ELSE 3 END,
                          lots
                        """
                    ),
                    {"correction_id": correction_id},
                )
            ).mappings()
        ]
    assert replacement_invoice == {
        "subtotal": Decimal("0.030000"),
        "discount_total": Decimal("0.000000"),
        "tax_total": Decimal("0.010000"),
        "grand_total": Decimal("0.040000"),
        "accepted_quantity_base": Decimal("1.000000"),
        "line_subtotal": Decimal("0.030000"),
        "discount_amount": Decimal("0.000000"),
        "tax_amount": Decimal("0.010000"),
        "line_total": Decimal("0.040000"),
    }
    assert replacement_invoice["line_total"] == (
        replacement_invoice["line_subtotal"]
        - replacement_invoice["discount_amount"]
        + replacement_invoice["tax_amount"]
    )
    assert invoice_counts == {"invoices": 3, "replacement_lines": 1}
    assert [
        (row["effect_role"], row["outcome"], row["quantity_base"], row["lots"])
        for row in movement_effects
    ] == [
        ("original", "accepted", Decimal("1.000000"), ["CASE-LOT-A"]),
        ("original", "accepted", Decimal("1.000000"), ["CASE-LOT-B"]),
        ("reversal", "accepted", Decimal("1.000000"), ["CASE-LOT-A"]),
        ("reversal", "accepted", Decimal("1.000000"), ["CASE-LOT-B"]),
        ("replacement", "accepted", Decimal("1.000000"), ["CASE-LOT-A"]),
    ]

    replacement_receipt = await confirmation_client.get(
        f"/v1/delivery-receipts/{replacement_receipt_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert replacement_receipt.status_code == 200, replacement_receipt.text
    replacement_snapshot = replacement_receipt.json()["snapshot"]
    assert len(replacement_snapshot["lines"]) == 1
    assert replacement_snapshot["lines"][0]["accepted_quantity_base"] == "1.000000"
    assert replacement_snapshot["lines"][0]["accepted_quantity_entered"] == "0.500000"
    assert replacement_snapshot["lines"][0]["entered_unit"] == "CASE"
    assert fake_storage.put_body is not None
    rendered = fake_storage.put_body.decode("latin-1")
    assert "0.500000 CASE" in rendered
    assert f"Corrects Delivery Receipt: {source['number']}" in rendered

    async def projection() -> dict[str, object]:
        async with engine.connect() as connection:
            availability = [
                dict(row)
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT location.custody,availability.identity_key,
                                   availability.on_hand,availability.reserved
                            FROM inventory_availability availability
                            JOIN warehouse_stock_locations location USING (location_id)
                            WHERE availability.sku_id = :sku_id
                              AND availability.warehouse_id = :warehouse_id
                              AND availability.on_hand <> 0
                            ORDER BY location.custody,availability.identity_key
                            """
                        ),
                        {
                            "sku_id": fixture["sku_id"],
                            "warehouse_id": fixture["warehouse_id"],
                        },
                    )
                ).mappings()
            ]
            valuation = dict(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT quantity_on_hand,inventory_value,
                                   moving_average_unit_cost
                            FROM inventory_valuation
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
        return {"availability": availability, "valuation": valuation}

    before_rebuild = await projection()
    assert before_rebuild == {
        "availability": [
            {
                "custody": "in_transit",
                "identity_key": "lot:CASE-LOT-B",
                "on_hand": Decimal("1.000000"),
                "reserved": Decimal("0.000000"),
            }
        ],
        "valuation": {
            "quantity_on_hand": Decimal("1.000000"),
            "inventory_value": Decimal("0.010000"),
            "moving_average_unit_cost": Decimal("0.010000"),
        },
    }
    rebuilt = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert await projection() == before_rebuild
    await engine.dispose()


@pytest.mark.asyncio
async def test_sequential_correction_reconstructs_invoice_line_zeroed_then_reaccepted(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    fixture, confirmation = await _confirm_two_line_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    original_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    context = {"database_session_factory": factory, "object_storage": fake_storage}
    assert await poll_delivery_confirmation_outbox(context) == {"completed": 1, "failed": 0}
    assert await poll_delivery_confirmation_outbox(context) == {"completed": 0, "failed": 0}

    async with engine.connect() as connection:
        original_invoice = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT draft_invoice_id, subtotal, discount_total, tax_total, grand_total
                        FROM draft_invoices
                        WHERE delivery_confirmation_id = :confirmation_id
                          AND invoice_kind = 'original'
                        """
                    ),
                    {"confirmation_id": confirmation["confirmation_id"]},
                )
            )
            .mappings()
            .one()
        )
        original_line_totals = {
            row["line_id"]: row
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT line_id, accepted_quantity_base, subtotal,
                               discount_amount, tax_amount, line_total
                        FROM draft_invoice_lines
                        WHERE draft_invoice_id = :draft_invoice_id
                        """
                    ),
                    {"draft_invoice_id": original_invoice["draft_invoice_id"]},
                )
            ).mappings()
        }
    assert original_invoice["grand_total"] == Decimal("448.000000")
    assert len(original_line_totals) == 2

    first_line_id = fixture["first_line_id"]
    second_line_id = fixture["second_line_id"]
    source_line_by_logical = {
        row["line_id"]: row
        for row in confirmation["lines"]
        if row["line_id"] in (first_line_id, second_line_id)
    }
    assert len(source_line_by_logical) == 2
    first_delivery_line_id = source_line_by_logical[first_line_id]["delivery_line_id"]
    second_delivery_line_id = source_line_by_logical[second_line_id]["delivery_line_id"]

    first_correction_id = str(uuid4())
    first_command = {
        "correction_id": first_correction_id,
        "reason": "Second line was refused; zero accepted for that logical line.",
        "evidence_ids": [confirmation["evidence_id"]],
        "lines": [
            {
                "delivery_line_id": first_delivery_line_id,
                "accepted_quantity_base": "2.000000",
                "refused_quantity_base": "0.000000",
                "damaged_quantity_base": "0.000000",
                "short_missing_quantity_base": "0.000000",
                "still_undelivered_quantity_base": "0.000000",
                "identity_positions": [],
            },
            {
                "delivery_line_id": second_delivery_line_id,
                "accepted_quantity_base": "0.000000",
                "refused_quantity_base": "2.000000",
                "damaged_quantity_base": "0.000000",
                "short_missing_quantity_base": "0.000000",
                "still_undelivered_quantity_base": "0.000000",
                "identity_positions": [],
            },
        ],
    }
    first_requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "request-first-zero-sequential-correction"},
        ),
        json=first_command,
    )
    assert first_requested.status_code == 201, first_requested.text
    first_authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{first_correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-first-zero-sequential-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert first_authorized.status_code == 200, first_authorized.text
    first_posted = first_authorized.json()
    first_replacement_receipt_id = first_posted["receipt_effect"]["replacement_delivery_receipt_id"]
    first_replacement_invoice_id = first_posted["draft_invoice_effect"][
        "replacement_draft_invoice_id"
    ]
    assert first_replacement_receipt_id is not None
    assert first_replacement_invoice_id is not None
    assert await poll_delivery_confirmation_outbox(context) == {"completed": 1, "failed": 0}
    assert await poll_delivery_confirmation_outbox(context) == {"completed": 0, "failed": 0}

    async with engine.connect() as connection:
        first_invoice_chain = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT invoice.invoice_kind, invoice.draft_invoice_id,
                               invoice.reversal_of_draft_invoice_id,
                               invoice.replaces_draft_invoice_id,
                               invoice.grand_total,
                               count(line.draft_invoice_line_id) AS line_count
                        FROM draft_invoices invoice
                        LEFT JOIN draft_invoice_lines line USING (draft_invoice_id)
                        WHERE invoice.delivery_confirmation_id = :confirmation_id
                        GROUP BY invoice.draft_invoice_id
                        ORDER BY CASE invoice.invoice_kind
                          WHEN 'original' THEN 1 WHEN 'reversal' THEN 2 ELSE 3 END
                        """
                    ),
                    {"confirmation_id": confirmation["confirmation_id"]},
                )
            ).mappings()
        ]
    assert [row["invoice_kind"] for row in first_invoice_chain] == [
        "original",
        "reversal",
        "replacement",
    ]
    first_replacement = first_invoice_chain[2]
    assert first_replacement["grand_total"] == Decimal("224.000000")
    assert first_replacement["line_count"] == 1

    second_correction_id = str(uuid4())
    second_command = {
        "correction_id": second_correction_id,
        "reason": "Customer agreed to take the previously refused second line.",
        "evidence_ids": [confirmation["evidence_id"]],
        "lines": [
            {
                "delivery_line_id": first_delivery_line_id,
                "accepted_quantity_base": "2.000000",
                "refused_quantity_base": "0.000000",
                "damaged_quantity_base": "0.000000",
                "short_missing_quantity_base": "0.000000",
                "still_undelivered_quantity_base": "0.000000",
                "identity_positions": [],
            },
            {
                "delivery_line_id": second_delivery_line_id,
                "accepted_quantity_base": "2.000000",
                "refused_quantity_base": "0.000000",
                "damaged_quantity_base": "0.000000",
                "short_missing_quantity_base": "0.000000",
                "still_undelivered_quantity_base": "0.000000",
                "identity_positions": [],
            },
        ],
    }
    second_requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{first_replacement_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "request-second-reaccept-sequential-correction"},
        ),
        json=second_command,
    )
    assert second_requested.status_code == 201, second_requested.text
    second_authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{second_correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-second-reaccept-sequential-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert second_authorized.status_code == 200, second_authorized.text
    second_posted = second_authorized.json()
    second_replacement_receipt_id = second_posted["receipt_effect"][
        "replacement_delivery_receipt_id"
    ]
    second_replacement_invoice_id = second_posted["draft_invoice_effect"][
        "replacement_draft_invoice_id"
    ]
    assert second_replacement_receipt_id is not None
    assert second_replacement_invoice_id is not None
    result = await poll_delivery_confirmation_outbox(context)
    if result != {"completed": 1, "failed": 0}:
        async with engine.connect() as connection:
            state = dict(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT status, attempts, last_error
                            FROM outbox_processing_state
                            WHERE outbox_event_id = :event_id
                            """
                        ),
                        {"event_id": second_posted["outbox_event_id"]},
                    )
                )
                .mappings()
                .one()
            )
            print("OUTBOX FAIL", state)
    assert result == {"completed": 1, "failed": 0}
    assert await poll_delivery_confirmation_outbox(context) == {"completed": 0, "failed": 0}

    async with engine.connect() as connection:
        second_invoice_chain = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT invoice.invoice_kind, invoice.draft_invoice_id,
                               invoice.reversal_of_draft_invoice_id,
                               invoice.replaces_draft_invoice_id,
                               invoice.grand_total,
                               count(line.draft_invoice_line_id) AS line_count
                        FROM draft_invoices invoice
                        LEFT JOIN draft_invoice_lines line USING (draft_invoice_id)
                        WHERE invoice.delivery_confirmation_id = :confirmation_id
                        GROUP BY invoice.draft_invoice_id
                        ORDER BY CASE invoice.invoice_kind
                          WHEN 'original' THEN 1 WHEN 'reversal' THEN 2 ELSE 3 END
                        """
                    ),
                    {"confirmation_id": confirmation["confirmation_id"]},
                )
            ).mappings()
        ]
        second_replacement_lines = [
            dict(row)
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT line_id, accepted_quantity_base, subtotal,
                               discount_amount, tax_amount, line_total
                        FROM draft_invoice_lines
                        WHERE draft_invoice_id = :draft_invoice_id
                        ORDER BY line_id
                        """
                    ),
                    {"draft_invoice_id": second_replacement_invoice_id},
                )
            ).mappings()
        ]
        second_receipt_snapshot = dict(
            (
                await connection.execute(
                    text("SELECT snapshot FROM delivery_receipts WHERE delivery_receipt_id = :id"),
                    {"id": second_replacement_receipt_id},
                )
            )
            .mappings()
            .one()
        )["snapshot"]
    kinds = [row["invoice_kind"] for row in second_invoice_chain]
    assert kinds.count("original") == 1
    assert kinds.count("reversal") == 2
    assert kinds.count("replacement") == 2
    second_reversal = next(
        row
        for row in second_invoice_chain
        if row["invoice_kind"] == "reversal" and row["grand_total"] == Decimal("-224.000000")
    )
    second_replacement = next(
        row
        for row in second_invoice_chain
        if row["invoice_kind"] == "replacement" and row["grand_total"] == Decimal("448.000000")
    )
    assert second_reversal["line_count"] == 1
    assert second_replacement["line_count"] == 2
    assert len(second_replacement_lines) == 2
    for line in second_replacement_lines:
        assert line["accepted_quantity_base"] == Decimal("2.000000")
        assert line["line_total"] == Decimal("224.000000")
    assert sum(row["line_total"] for row in second_replacement_lines) == Decimal("448.000000")
    assert len(second_receipt_snapshot["lines"]) == 2
    assert {line["line_id"] for line in second_receipt_snapshot["lines"]} == {
        first_line_id,
        second_line_id,
    }

    second_replay = await confirmation_client.post(
        f"/v1/delivery-corrections/{second_correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-second-reaccept-sequential-correction"},
        ),
        json={"expected_correction_version": 1},
    )
    assert second_replay.status_code == 200, second_replay.text
    assert second_replay.json() == second_posted

    rebuilt = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    async with engine.connect() as connection:
        after_rebuild = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT quantity_on_hand, inventory_value, moving_average_unit_cost
                        FROM inventory_valuation
                        WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id
                        """
                    ),
                    {"sku_id": fixture["sku_id"], "warehouse_id": fixture["warehouse_id"]},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert after_rebuild == {
        "quantity_on_hand": Decimal("0.000000"),
        "inventory_value": Decimal("0.000000"),
        "moving_average_unit_cost": Decimal("7.500000"),
    }


async def _ensure_correction_user(
    postgres_url: str,
    subject: str,
    branch_id: str,
    warehouse_id: str,
) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO users(subject, display_name, is_active)
                VALUES (:subject, :display_name, true)
                ON CONFLICT (subject) DO UPDATE SET is_active = true
                """
            ),
            {"subject": subject, "display_name": subject},
        )
        role_template_id = await connection.scalar(
            text("SELECT role_template_id FROM role_templates WHERE code = 'WAREHOUSE_SUPERVISOR'")
        )
        await connection.execute(
            text(
                """
                INSERT INTO user_role_templates(user_subject, role_template_id)
                VALUES (:subject, :role_template_id)
                ON CONFLICT DO NOTHING
                """
            ),
            {"subject": subject, "role_template_id": role_template_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO user_branch_scopes(user_subject, branch_id)
                VALUES (:subject, :branch_id)
                ON CONFLICT DO NOTHING
                """
            ),
            {"subject": subject, "branch_id": branch_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO user_warehouse_scopes(user_subject, warehouse_id)
                VALUES (:subject, :warehouse_id)
                ON CONFLICT DO NOTHING
                """
            ),
            {"subject": subject, "warehouse_id": warehouse_id},
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_receipt_access_scopes_allow_correction_operators_by_branch_and_warehouse(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    fixture, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    original_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    processed = await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    )
    assert processed == {"completed": 1, "failed": 0}

    requester_response = await confirmation_client.get(
        f"/v1/delivery-receipts/{original_receipt_id}",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert requester_response.status_code == 200, requester_response.text
    assert requester_response.json()["delivery_receipt_id"] == original_receipt_id

    authorizer_response = await confirmation_client.post(
        f"/v1/delivery-receipts/{original_receipt_id}/access",
        headers=auth(confirmation_settings, "delivery-correction-checker-mnl"),
    )
    assert authorizer_response.status_code == 200, authorizer_response.text
    body = authorizer_response.json()
    assert body["access_url"]
    assert body["expires_at"]

    async with engine.connect() as connection:
        wrong_warehouse_id = await connection.scalar(
            text("SELECT warehouse_id FROM warehouses WHERE code = 'CEB-01'")
        )
    assert wrong_warehouse_id is not None
    await _ensure_correction_user(
        postgres_url,
        "correction-requester-wrong-warehouse",
        fixture["branch_id"],
        str(wrong_warehouse_id),
    )
    wrong_warehouse_response = await confirmation_client.get(
        f"/v1/delivery-receipts/{original_receipt_id}",
        headers=auth(confirmation_settings, "correction-requester-wrong-warehouse"),
    )
    assert wrong_warehouse_response.status_code == 403, wrong_warehouse_response.text
    assert wrong_warehouse_response.json()["error"]["code"] in (
        "operational_scope_required",
        "delivery_assignment_required",
    )

    unassigned_response = await confirmation_client.get(
        f"/v1/delivery-receipts/{original_receipt_id}",
        headers=auth(confirmation_settings, "delivery-backup-mnl"),
    )
    assert unassigned_response.status_code == 403, unassigned_response.text
    assert unassigned_response.json()["error"]["code"] == "delivery_assignment_required"

    await engine.dispose()


async def _ensure_warehouse_scoped_authority(
    postgres_url: str,
    subject: str,
    branch_id: str,
    warehouse_id: str,
    maximum_amount: Decimal,
) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO approval_authorities(
                  approval_authority_id, user_subject, capability_code,
                  branch_id, warehouse_id, maximum_amount, maker_checker_required
                ) VALUES (
                  :authority_id, :subject, 'fulfillment:delivery-correction-authorize',
                  :branch_id, :warehouse_id, :maximum_amount, true
                )
                """
            ),
            {
                "authority_id": uuid4(),
                "subject": subject,
                "branch_id": branch_id,
                "warehouse_id": warehouse_id,
                "maximum_amount": maximum_amount,
            },
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_delivery_correction_authorization_matrix(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    """Exercise the authorization boundary for every combination of capability,
    branch/warehouse scope, approval authority grain, and approval limit.
    """
    fixture, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 1, "failed": 0}

    correction_id = str(uuid4())
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "matrix-correction-request"},
        ),
        json={
            "correction_id": correction_id,
            "reason": "Authorization matrix test.",
            "evidence_ids": [confirmation["evidence_id"]],
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "accepted_quantity_base": "1.000000",
                    "refused_quantity_base": "1.000000",
                    "damaged_quantity_base": "0.000000",
                    "short_missing_quantity_base": "0.000000",
                    "still_undelivered_quantity_base": "0.000000",
                    "identity_positions": [],
                }
            ],
        },
    )
    assert requested.status_code == 201, requested.text

    async def authorize_as(user: str, key: str) -> object:
        return await confirmation_client.post(
            f"/v1/delivery-corrections/{correction_id}/authorization",
            headers=auth(
                confirmation_settings,
                user,
                **{"Idempotency-Key": key},
            ),
            json={"expected_correction_version": 1},
        )

    # Requester cannot authorize their own proposal.
    self_auth = await authorize_as("warehouse-supervisor-mnl", "matrix-self-authorization")
    assert self_auth.status_code == 403, self_auth.text
    assert self_auth.json()["error"]["code"] == "maker_checker_violation"

    # Capable user whose approval limit is below the affected value is rejected.
    low_limit = await authorize_as("delivery-correction-checker-low-mnl", "matrix-low-limit")
    assert low_limit.status_code == 403, low_limit.text
    assert low_limit.json()["error"]["code"] == "approval_authority_required"

    # Capable user from a different branch/warehouse is rejected at scope.
    wrong_branch = await authorize_as("delivery-correction-checker-ceb", "matrix-wrong-branch")
    assert wrong_branch.status_code == 403, wrong_branch.text
    assert wrong_branch.json()["error"]["code"] == "operational_scope_required"

    # User without the authorizer capability is rejected at capability.
    no_capability = await authorize_as("delivery-mnl", "matrix-no-capability")
    assert no_capability.status_code == 403, no_capability.text
    assert no_capability.json()["error"]["code"] == "capability_required"

    # Warehouse-scoped authority for the wrong warehouse is rejected.
    await _ensure_correction_user(
        postgres_url,
        "correction-warehouse-wrong-mnl",
        str(fixture["branch_id"]),
        str(fixture["warehouse_id"]),
    )
    async with engine.connect() as connection:
        wrong_warehouse_id = await connection.scalar(
            text("SELECT warehouse_id FROM warehouses WHERE code = 'CEB-01'")
        )
    assert wrong_warehouse_id is not None
    await _ensure_warehouse_scoped_authority(
        postgres_url,
        "correction-warehouse-wrong-mnl",
        str(fixture["branch_id"]),
        str(wrong_warehouse_id),
        Decimal("1000.00"),
    )
    wrong_warehouse_auth = await authorize_as(
        "correction-warehouse-wrong-mnl", "matrix-wrong-warehouse-scope"
    )
    assert wrong_warehouse_auth.status_code == 403, wrong_warehouse_auth.text
    assert wrong_warehouse_auth.json()["error"]["code"] == "approval_authority_required"

    # Warehouse-scoped authority for the matching warehouse succeeds.
    await _ensure_correction_user(
        postgres_url,
        "correction-warehouse-scoped-mnl",
        str(fixture["branch_id"]),
        str(fixture["warehouse_id"]),
    )
    await _ensure_warehouse_scoped_authority(
        postgres_url,
        "correction-warehouse-scoped-mnl",
        str(fixture["branch_id"]),
        str(fixture["warehouse_id"]),
        Decimal("1000.00"),
    )
    scoped_auth = await authorize_as(
        "correction-warehouse-scoped-mnl", "matrix-matching-warehouse-scope"
    )
    assert scoped_auth.status_code == 200, scoped_auth.text
    assert scoped_auth.json()["status"] == "posted"

    await engine.dispose()


@pytest.mark.asyncio
async def test_correction_outbox_handlers_are_idempotent_and_recover_from_transient_failure(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    """Correction outbox handlers deduplicate replays and recover after a storage failure."""
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 1, "failed": 0}

    correction_id = str(uuid4())
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "outbox-correction-request"},
        ),
        json={
            "correction_id": correction_id,
            "reason": "Outbox idempotency test.",
            "evidence_ids": [confirmation["evidence_id"]],
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "accepted_quantity_base": "1.000000",
                    "refused_quantity_base": "1.000000",
                    "damaged_quantity_base": "0.000000",
                    "short_missing_quantity_base": "0.000000",
                    "still_undelivered_quantity_base": "0.000000",
                    "identity_positions": [],
                }
            ],
        },
    )
    assert requested.status_code == 201, requested.text

    authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "outbox-correction-authorize"},
        ),
        json={"expected_correction_version": 1},
    )
    assert authorized.status_code == 200, authorized.text
    outbox_event_id = UUID(authorized.json()["outbox_event_id"])

    # Simulate a transient storage failure on the first poll attempt.
    fake_storage.fail_puts = 1
    first_poll = await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    )
    assert first_poll == {"completed": 0, "failed": 1}

    async with engine.connect() as connection:
        document = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT status
                        FROM delivery_receipt_documents
                        WHERE delivery_receipt_id = :receipt_id
                        """
                    ),
                    {
                        "receipt_id": authorized.json()["receipt_effect"][
                            "replacement_delivery_receipt_id"
                        ]
                    },
                )
            )
            .mappings()
            .one()
        )
        correction_handler_count = await connection.scalar(
            text(
                """
                SELECT count(*)
                FROM outbox_handler_receipts
                WHERE outbox_event_id = :event_id
                  AND handler_name IN (
                    'finance.delivery-correction.v1',
                    'documents.delivery-correction-receipt.v1'
                  )
                """
            ),
            {"event_id": outbox_event_id},
        )
    assert document["status"] == "pending_document"
    # The whole event transaction rolls back on failure, so no partial receipts.
    assert correction_handler_count == 0

    # Replay after fixing the failure should complete exactly once.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE outbox_processing_state SET available_at = now() WHERE status = 'failed'")
        )
    second_poll = await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    )
    assert second_poll == {"completed": 1, "failed": 0}

    async with engine.connect() as connection:
        final_correction_handler_count = await connection.scalar(
            text(
                """
                SELECT count(*)
                FROM outbox_handler_receipts
                WHERE outbox_event_id = :event_id
                  AND handler_name IN (
                    'finance.delivery-correction.v1',
                    'documents.delivery-correction-receipt.v1'
                  )
                """
            ),
            {"event_id": outbox_event_id},
        )
    assert final_correction_handler_count == 2

    async with engine.begin() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        first_invoice_id = await create_corrected_draft_invoices_for_event(session, outbox_event_id)
        replayed_invoice_id = await create_corrected_draft_invoices_for_event(
            session, outbox_event_id
        )
        assert replayed_invoice_id == first_invoice_id

        first_receipt_id = await render_corrected_delivery_receipt_for_event(
            session, outbox_event_id, fake_storage
        )
        replayed_receipt_id = await render_corrected_delivery_receipt_for_event(
            session, outbox_event_id, fake_storage
        )
        assert replayed_receipt_id == first_receipt_id

    # A final poll finds no remaining work.
    final_poll = await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    )
    assert final_poll == {"completed": 0, "failed": 0}

    await engine.dispose()


@pytest.mark.asyncio
async def test_correction_projections_reconcile_after_rebuild(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    """After a posted correction, rebuilding inventory projections from source
    movements reproduces the same availability and valuation snapshot.
    """
    fixture, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 1, "failed": 0}

    correction_id = str(uuid4())
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "reconcile-correction-request"},
        ),
        json={
            "correction_id": correction_id,
            "reason": "Reconcile projections after rebuild.",
            "evidence_ids": [confirmation["evidence_id"]],
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "accepted_quantity_base": "1.000000",
                    "refused_quantity_base": "1.000000",
                    "damaged_quantity_base": "0.000000",
                    "short_missing_quantity_base": "0.000000",
                    "still_undelivered_quantity_base": "0.000000",
                    "identity_positions": [],
                }
            ],
        },
    )
    assert requested.status_code == 201, requested.text

    authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "reconcile-correction-authorize"},
        ),
        json={"expected_correction_version": 1},
    )
    assert authorized.status_code == 200, authorized.text

    async def availability_snapshot() -> list[dict[str, object]]:
        response = await confirmation_client.get(
            "/v1/inventory/availability",
            headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        )
        assert response.status_code == 200, response.text
        return [
            item for item in response.json()["items"] if item["sku_id"] == str(fixture["sku_id"])
        ]

    before = await availability_snapshot()
    assert before

    rebuild = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuild.status_code == 200, rebuild.text

    after = await availability_snapshot()
    assert after == before

    await engine.dispose()
