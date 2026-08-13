from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy import text
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
async def test_delivery_correction_migration_round_trips_from_early_schema(
    postgres_url: str,
) -> None:
    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)

    try:
        await asyncio.to_thread(command.downgrade, config, "0001")
        await asyncio.to_thread(command.upgrade, config, "0015")

        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            at_0015 = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT version_num,
                                   (SELECT character_maximum_length
                                      FROM information_schema.columns
                                     WHERE table_name = 'stock_movements'
                                       AND column_name = 'movement_leg') AS movement_leg_length,
                                   to_regclass('delivery_corrections') IS NOT NULL
                                     AS has_corrections,
                                   to_regprocedure(
                                     'validate_delivery_correction_completeness()'
                                   ) IS NOT NULL AS has_completeness_guard
                              FROM alembic_version
                            """
                        )
                    )
                )
                .mappings()
                .one()
            )
        await engine.dispose()
        assert dict(at_0015) == {
            "version_num": "0015",
            "movement_leg_length": 64,
            "has_corrections": True,
            "has_completeness_guard": True,
        }

        await asyncio.to_thread(command.downgrade, config, "0014")
        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            at_0014 = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT version_num,
                                   (SELECT character_maximum_length
                                      FROM information_schema.columns
                                     WHERE table_name = 'stock_movements'
                                       AND column_name = 'movement_leg') AS movement_leg_length,
                                   to_regclass('delivery_corrections') IS NOT NULL
                                     AS has_corrections
                              FROM alembic_version
                            """
                        )
                    )
                )
                .mappings()
                .one()
            )
        await engine.dispose()
        assert dict(at_0014) == {
            "version_num": "0014",
            "movement_leg_length": 40,
            "has_corrections": False,
        }

        await asyncio.to_thread(command.upgrade, config, "0015")
        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0015"
            )
        await engine.dispose()
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")


@pytest.mark.asyncio
async def test_populated_delivery_correction_history_refuses_downgrade_without_data_loss(
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

    correction_id = str(uuid4())
    requested = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "migration-populated-correction-request"},
        ),
        json={
            "correction_id": correction_id,
            "reason": "Posted immutable history must prevent a destructive downgrade.",
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
            **{"Idempotency-Key": "migration-populated-correction-authorization"},
        ),
        json={"expected_correction_version": 1},
    )
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["status"] == "posted"

    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    with pytest.raises(
        Exception,
        match="Cannot downgrade 0015 while immutable Delivery Correction history exists",
    ):
        await asyncio.to_thread(command.downgrade, config, "0014")

    async with engine.connect() as connection:
        preserved = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT (SELECT version_num FROM alembic_version) AS version_num,
                               correction.correction_id,
                               receipt.number,
                               receipt.snapshot
                          FROM delivery_corrections correction
                          JOIN delivery_receipts receipt
                            ON receipt.delivery_receipt_id =
                               correction.original_delivery_receipt_id
                         WHERE correction.correction_id = :correction_id
                        """
                    ),
                    {"correction_id": correction_id},
                )
            )
            .mappings()
            .one()
        )
    assert preserved["version_num"] == "d53dcaa7ede3"
    assert preserved["correction_id"] == UUID(correction_id)
    assert preserved["number"] == authorized.json()["receipt_effect"]["original_number"]
    assert preserved["snapshot"]["delivery_id"] == confirmation["delivery_id"]

    readable = await confirmation_client.get(
        f"/v1/delivery-corrections/{correction_id}",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert readable.status_code == 200, readable.text
    assert readable.json()["status"] == "posted"
    await engine.dispose()


@pytest.mark.asyncio
async def test_upgrade_preserves_legacy_invoice_rounding_and_correction_reverses_it_exactly(
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

    async with engine.connect() as connection:
        source_invoice_id = await connection.scalar(
            text(
                "SELECT draft_invoice_id FROM draft_invoices "
                "WHERE delivery_confirmation_id = :confirmation_id"
            ),
            {"confirmation_id": confirmation["confirmation_id"]},
        )
    assert source_invoice_id is not None

    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    await asyncio.to_thread(command.downgrade, config, "0014")
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE draft_invoice_lines DISABLE TRIGGER "
                    "trg_draft_invoice_lines_immutable"
                )
            )
            await connection.execute(
                text("ALTER TABLE draft_invoices DISABLE TRIGGER trg_draft_invoices_immutable")
            )
            await connection.execute(
                text(
                    "UPDATE draft_invoice_lines SET line_total = line_total + 0.01 "
                    "WHERE draft_invoice_id = :draft_invoice_id"
                ),
                {"draft_invoice_id": source_invoice_id},
            )
            await connection.execute(
                text(
                    "UPDATE draft_invoices SET grand_total = grand_total + 0.01 "
                    "WHERE draft_invoice_id = :draft_invoice_id"
                ),
                {"draft_invoice_id": source_invoice_id},
            )
            await connection.execute(
                text(
                    "ALTER TABLE draft_invoice_lines ENABLE TRIGGER "
                    "trg_draft_invoice_lines_immutable"
                )
            )
            await connection.execute(
                text("ALTER TABLE draft_invoices ENABLE TRIGGER trg_draft_invoices_immutable")
            )
        await asyncio.to_thread(command.upgrade, config, "head")

        async with engine.connect() as connection:
            legacy = dict(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT invoice.subtotal,invoice.discount_total,invoice.tax_total,
                                   invoice.grand_total,line.subtotal AS line_subtotal,
                                   line.discount_amount,line.tax_amount,line.line_total
                            FROM draft_invoices invoice
                            JOIN draft_invoice_lines line USING (draft_invoice_id)
                            WHERE invoice.draft_invoice_id = :draft_invoice_id
                            """
                        ),
                        {"draft_invoice_id": source_invoice_id},
                    )
                )
                .mappings()
                .one()
            )
            constraint_validation = dict(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT bool_and(NOT convalidated) FILTER (
                                     WHERE conname IN ('ck_draft_invoice_signed_totals',
                                       'ck_draft_invoice_line_signed_values')) AS not_valid,
                                   count(*) FILTER (
                                     WHERE conname IN ('ck_draft_invoice_signed_totals',
                                       'ck_draft_invoice_line_signed_values')) AS guard_count
                            FROM pg_constraint
                            """
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert legacy["grand_total"] == legacy["subtotal"] - legacy["discount_total"] + legacy[
            "tax_total"
        ] + Decimal("0.010000")
        assert legacy["line_total"] == (
            legacy["line_subtotal"]
            - legacy["discount_amount"]
            + legacy["tax_amount"]
            + Decimal("0.010000")
        )
        assert constraint_validation == {"not_valid": True, "guard_count": 2}

        correction_id = str(uuid4())
        requested = await confirmation_client.post(
            f"/v1/delivery-receipts/{receipt_id}/corrections",
            headers=auth(
                confirmation_settings,
                "warehouse-supervisor-mnl",
                **{"Idempotency-Key": "legacy-rounding-correction-request"},
            ),
            json={
                "correction_id": correction_id,
                "reason": "Grandfathered invoice rounding must reverse without mutation.",
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
        assert Decimal(requested.json()["affected_value_base_currency"]) == Decimal("112.010000")
        authorized = await confirmation_client.post(
            f"/v1/delivery-corrections/{correction_id}/authorization",
            headers=auth(
                confirmation_settings,
                "delivery-correction-checker-mnl",
                **{"Idempotency-Key": "legacy-rounding-correction-authorization"},
            ),
            json={"expected_correction_version": 1},
        )
        assert authorized.status_code == 200, authorized.text
        assert await poll_delivery_confirmation_outbox(
            {"database_session_factory": factory, "object_storage": fake_storage}
        ) == {"completed": 1, "failed": 0}

        async with engine.connect() as connection:
            invoices = [
                dict(row)
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT invoice_kind,subtotal,discount_total,tax_total,grand_total
                            FROM draft_invoices WHERE correction_id = :correction_id
                            ORDER BY invoice_kind
                            """
                        ),
                        {"correction_id": correction_id},
                    )
                ).mappings()
            ]
        by_kind = {row["invoice_kind"]: row for row in invoices}
        assert by_kind["reversal"]["grand_total"] == -legacy["grand_total"]
        assert by_kind["reversal"]["subtotal"] == -legacy["subtotal"]
        assert by_kind["replacement"]["grand_total"] == (
            by_kind["replacement"]["subtotal"]
            - by_kind["replacement"]["discount_total"]
            + by_kind["replacement"]["tax_total"]
        )
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
        await engine.dispose()
