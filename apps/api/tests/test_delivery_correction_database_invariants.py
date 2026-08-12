from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_delivery_confirmation_contract import FakeObjectStorage
from test_delivery_confirmation_contract import confirmation_client as confirmation_client
from test_delivery_confirmation_contract import confirmation_settings as confirmation_settings
from test_delivery_confirmation_contract import fake_storage as fake_storage
from test_delivery_correction_contract import _confirm_fully_accepted_delivery
from test_payment_clearance_contract import auth
from tradeflow_api.config import Settings
from tradeflow_worker.worker import poll_delivery_confirmation_outbox


@pytest.mark.asyncio
async def test_database_rejects_correction_movement_and_invoice_economic_corruption(
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
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 1, "failed": 0}

    correction_id = uuid4()
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "database-economic-guard-request"},
        ),
        json={
            "correction_id": str(correction_id),
            "reason": "One accepted unit must be corrected to refused.",
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

    async with engine.connect() as connection:
        source = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT correction_line.correction_line_id,correction_line.sku_id,
                               correction.warehouse_id,correction.base_currency,
                               source_movement.movement_id AS source_movement_id,
                               source_movement.location_id,source_movement.quantity_base,
                               source_movement.unit_cost,source_movement.value_delta,
                               (SELECT movement_id FROM stock_movements
                                 WHERE movement_type = 'opening_stock'
                                   AND sku_id = correction_line.sku_id
                                   AND warehouse_id = correction.warehouse_id
                                 ORDER BY posted_at LIMIT 1) AS unrelated_movement_id
                        FROM delivery_correction_lines correction_line
                        JOIN delivery_corrections correction USING (correction_id)
                        JOIN delivery_confirmation_lines confirmation_line
                          USING (confirmation_line_id)
                        JOIN stock_movements source_movement
                          ON source_movement.movement_id =
                             confirmation_line.outbound_movement_id
                        WHERE correction_line.correction_id = :correction_id
                        """
                    ),
                    {"correction_id": correction_id},
                )
            )
            .mappings()
            .one()
        )

    movement_insert = text(
        """
        INSERT INTO stock_movements(
          movement_id,sku_id,warehouse_id,location_id,movement_type,quantity_base,
          unit_cost,value_delta,base_currency,source_reference,entered_unit,
          conversion_snapshot,actor_subject,correlation_id,idempotency_key,
          movement_group_id,movement_leg,reversal_of_movement_id
        ) VALUES (
          :movement_id,:sku_id,:warehouse_id,:location_id,'delivery_correction',
          :quantity_base,:unit_cost,:value_delta,:base_currency,:source_reference,
          'BASE','{"source":"direct-db-negative","factor":"1.000000"}'::jsonb,
          'delivery-correction-checker-mnl','direct-db-economic-guard',:idempotency_key,
          :movement_group_id,'correction_accepted_reversal_in',:original_movement_id
        )
        """
    )
    effect_insert = text(
        """
        INSERT INTO delivery_correction_movement_effects(
          movement_effect_id,correction_id,correction_line_id,effect_role,outcome,
          movement_id,original_movement_id
        ) VALUES (
          :movement_effect_id,:correction_id,:correction_line_id,'reversal','accepted',
          :movement_id,:original_movement_id
        )
        """
    )

    async def attempt_movement(
        *,
        original_movement_id: UUID,
        unit_cost: Decimal,
        value_delta: Decimal,
        expected_error: str,
    ) -> None:
        movement_id = uuid4()
        values = {
            **source,
            "movement_id": movement_id,
            "movement_effect_id": uuid4(),
            "correction_id": correction_id,
            "quantity_base": source["quantity_base"],
            "unit_cost": unit_cost,
            "value_delta": value_delta,
            "source_reference": f"DELIVERY-CORRECTION:{correction_id}",
            "idempotency_key": str(uuid4()),
            "movement_group_id": uuid4(),
            "original_movement_id": original_movement_id,
        }
        with pytest.raises(DBAPIError, match=expected_error):
            async with engine.begin() as connection:
                await connection.execute(movement_insert, values)
                await connection.execute(effect_insert, values)

    await attempt_movement(
        original_movement_id=source["source_movement_id"],
        unit_cost=source["unit_cost"] + Decimal("1.000000"),
        value_delta=-source["value_delta"],
        expected_error="Correction Movement economics do not belong to its correction line",
    )
    await attempt_movement(
        original_movement_id=source["source_movement_id"],
        unit_cost=source["unit_cost"],
        value_delta=-source["value_delta"] + Decimal("0.010000"),
        expected_error="Correction reversal must exactly negate its immediate source Movement",
    )
    await attempt_movement(
        original_movement_id=source["unrelated_movement_id"],
        unit_cost=source["unit_cost"],
        value_delta=-source["value_delta"],
        expected_error="Correction reversal must exactly negate its immediate source Movement",
    )

    authorized = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "database-economic-guard-authorization"},
        ),
        json={"expected_correction_version": 1},
    )
    assert authorized.status_code == 200, authorized.text
    posted = authorized.json()

    async with engine.connect() as connection:
        invoice_source = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT invoice.*,line.draft_invoice_line_id,line.line_id,line.sku_id,
                               line.accepted_quantity_base,line.unit_price,
                               line.subtotal AS line_subtotal,
                               line.discount_amount,line.tax_amount,line.line_total,
                               line.calculation_snapshot
                        FROM draft_invoices invoice
                        JOIN draft_invoice_lines line USING (draft_invoice_id)
                        WHERE invoice.draft_invoice_id = :draft_invoice_id
                        """
                    ),
                    {
                        "draft_invoice_id": posted["draft_invoice_effect"][
                            "original_draft_invoice_id"
                        ]
                    },
                )
            )
            .mappings()
            .one()
        )

    replacement_invoice_id = UUID(posted["draft_invoice_effect"]["replacement_draft_invoice_id"])
    malformed_subtotal = Decimal("112.000000")
    with pytest.raises(
        DBAPIError,
        match="Replacement Draft Invoice must exactly allocate its corrected source lines",
    ):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO draft_invoices(
                      draft_invoice_id,delivery_confirmation_id,source_event_id,invoice_kind,
                      correction_id,reversal_of_draft_invoice_id,replaces_draft_invoice_id,
                      status,sales_order_id,sales_order_revision_id,customer_id,branch_id,
                      currency,subtotal,discount_total,tax_total,grand_total,source_snapshot
                    ) VALUES (
                      :draft_invoice_id,:delivery_confirmation_id,:source_event_id,'replacement',
                      :correction_id,NULL,:source_invoice_id,'draft',:sales_order_id,
                      :sales_order_revision_id,:customer_id,:branch_id,:currency,
                      :subtotal,0,0,:subtotal,'{}'::jsonb
                    )
                    """
                ),
                {
                    **invoice_source,
                    "draft_invoice_id": replacement_invoice_id,
                    "source_invoice_id": invoice_source["draft_invoice_id"],
                    "source_event_id": posted["outbox_event_id"],
                    "correction_id": correction_id,
                    "subtotal": malformed_subtotal,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO draft_invoice_lines(
                      draft_invoice_line_id,draft_invoice_id,line_id,sku_id,
                      accepted_quantity_base,invoice_kind,unit_price,subtotal,
                      discount_amount,tax_amount,line_total,calculation_snapshot
                    ) VALUES (
                      :draft_invoice_line_id,:draft_invoice_id,:wrong_line_id,:sku_id,
                      1,'replacement',:unit_price,:subtotal,0,0,:subtotal,'{}'::jsonb
                    )
                    """
                ),
                {
                    **invoice_source,
                    "draft_invoice_line_id": uuid4(),
                    "draft_invoice_id": replacement_invoice_id,
                    "wrong_line_id": uuid4(),
                    "subtotal": malformed_subtotal,
                },
            )

    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 1, "failed": 0}

    replacement_receipt_id = posted["receipt_effect"]["replacement_delivery_receipt_id"]
    replacement_receipt = await confirmation_client.get(
        f"/v1/delivery-receipts/{replacement_receipt_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert replacement_receipt.status_code == 200, replacement_receipt.text
    replacement_source = replacement_receipt.json()
    second_correction_id = uuid4()
    second_requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{replacement_receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "database-line-ownership-guard-request"},
        ),
        json={
            "correction_id": str(second_correction_id),
            "reason": "A second proposal provides a distinct immutable line identity.",
            "evidence_ids": [confirmation["evidence_id"]],
            "lines": [
                {
                    "delivery_line_id": replacement_source["confirmation_lines"][0][
                        "delivery_line_id"
                    ],
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
    assert second_requested.status_code == 201, second_requested.text

    async with engine.connect() as connection:
        immediate_prior = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT movement.movement_id AS source_movement_id,
                               movement.location_id,movement.quantity_base,
                               movement.unit_cost,movement.value_delta
                        FROM delivery_correction_movement_effects effect
                        JOIN stock_movements movement USING (movement_id)
                        WHERE effect.correction_id = :correction_id
                          AND effect.effect_role = 'replacement'
                          AND effect.outcome = 'accepted'
                        """
                    ),
                    {"correction_id": correction_id},
                )
            )
            .mappings()
            .one()
        )
    swapped_movement_id = uuid4()
    swapped_values = {
        **source,
        **immediate_prior,
        "movement_id": swapped_movement_id,
        "movement_effect_id": uuid4(),
        "correction_id": second_correction_id,
        # Deliberately use the first correction's physical line for the second header.
        "correction_line_id": source["correction_line_id"],
        "value_delta": -immediate_prior["value_delta"],
        "source_reference": f"DELIVERY-CORRECTION:{second_correction_id}",
        "idempotency_key": str(uuid4()),
        "movement_group_id": uuid4(),
        "original_movement_id": immediate_prior["source_movement_id"],
    }
    with pytest.raises(
        DBAPIError,
        match="Correction Movement economics do not belong to its correction line",
    ):
        async with engine.begin() as connection:
            await connection.execute(movement_insert, swapped_values)
            await connection.execute(effect_insert, swapped_values)
    await engine.dispose()
