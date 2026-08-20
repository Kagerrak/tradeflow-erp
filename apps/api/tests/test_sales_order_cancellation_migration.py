from __future__ import annotations

import asyncio
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_payment_clearance_contract import approved_prepaid_order
from test_sales_order_cancellation_contract import (
    _cancel,
    _ensure_cancellation_capability,
    _grant_cancellation_authority,
    _order_version,
)
from test_sales_order_cancellation_contract import (
    cancellation_client as cancellation_client,
)
from test_sales_order_cancellation_contract import (
    cancellation_settings as cancellation_settings,
)
from test_sales_order_cancellation_contract import (
    fake_storage as fake_storage,
)
from tradeflow_api.config import Settings


@pytest.mark.asyncio
async def test_sales_order_cancellation_migration_round_trips(postgres_url: str) -> None:
    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)

    try:
        await asyncio.to_thread(command.downgrade, config, "e8b78e1dfcfc")

        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            before = dict(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT version_num,
                                   (SELECT column_name
                                      FROM information_schema.columns
                                     WHERE table_name = 'sales_order_line_commitments'
                                       AND column_name = 'cancelled_quantity_base')
                                     AS has_cancelled_column,
                                   to_regclass('sales_order_cancellations') IS NOT NULL
                                     AS has_cancellations,
                                   to_regclass('sales_order_cancellation_lines') IS NOT NULL
                                     AS has_cancellation_lines,
                                   (SELECT pg_get_constraintdef(oid)
                                      FROM pg_constraint
                                     WHERE conrelid = 'sales_orders'::regclass
                                       AND conname = 'ck_sales_orders_status')
                                     AS status_check
                              FROM alembic_version
                            """
                        )
                    )
                )
                .mappings()
                .one()
            )
        await engine.dispose()

        assert before["version_num"] == "e8b78e1dfcfc"
        assert before["has_cancelled_column"] is None
        assert before["has_cancellations"] is False
        assert before["has_cancellation_lines"] is False
        assert "'draft'" in before["status_check"]
        assert "'approved'" in before["status_check"]
        assert "'held'" in before["status_check"]

        await asyncio.to_thread(command.upgrade, config, "d62caac1e324")

        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            after = dict(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT version_num,
                                   (SELECT column_name
                                      FROM information_schema.columns
                                     WHERE table_name = 'sales_order_line_commitments'
                                       AND column_name = 'cancelled_quantity_base')
                                     AS has_cancelled_column,
                                   to_regclass('sales_order_cancellations') IS NOT NULL
                                     AS has_cancellations,
                                   to_regclass('sales_order_cancellation_lines') IS NOT NULL
                                     AS has_cancellation_lines,
                                   (SELECT EXISTS (
                                      SELECT 1 FROM pg_indexes
                                       WHERE indexname = 'ix_sales_order_cancellations_order'
                                   )) AS has_order_index,
                                   (SELECT EXISTS (
                                      SELECT 1 FROM pg_indexes
                                       WHERE indexname = 'ix_sales_order_cancellation_lines_order'
                                   )) AS has_lines_index,
                                   (SELECT pg_get_constraintdef(oid)
                                      FROM pg_constraint
                                     WHERE conrelid = 'sales_orders'::regclass
                                       AND conname = 'ck_sales_orders_status')
                                     AS status_check
                              FROM alembic_version
                            """
                        )
                    )
                )
                .mappings()
                .one()
            )
        await engine.dispose()

        assert after["version_num"] == "d62caac1e324"
        assert after["has_cancelled_column"] == "cancelled_quantity_base"
        assert after["has_cancellations"] is True
        assert after["has_cancellation_lines"] is True
        assert after["has_order_index"] is True
        assert after["has_lines_index"] is True
        assert "partially_cancelled" in after["status_check"]
        assert "cancelled" in after["status_check"]

        await asyncio.to_thread(command.downgrade, config, "e8b78e1dfcfc")
        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "e8b78e1dfcfc"
            )
        await engine.dispose()
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")


@pytest.mark.asyncio
async def test_populated_cancellation_history_refuses_downgrade(
    cancellation_client: AsyncClient,
    cancellation_settings: Settings,
    postgres_url: str,
) -> None:
    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)

    fixture = await approved_prepaid_order(cancellation_client, cancellation_settings, postgres_url)
    await _ensure_cancellation_capability(postgres_url)
    await _grant_cancellation_authority(
        postgres_url,
        subject="sales-mnl",
        branch_id=cast(str, fixture["branch_id"]),
        warehouse_id=cast(str, fixture["warehouse_id"]),
    )
    order = await _order_version(
        cancellation_client, cancellation_settings, cast(str, fixture["sales_order_id"])
    )
    cancelled = await _cancel(
        cancellation_client,
        cancellation_settings,
        cast(str, fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "3.000000",
            }
        ],
        reason="Populate cancellation history for migration guard.",
        if_match=cast(int, order["metadata_version"]),
        key="migration-populated-cancellation",
    )
    assert cancelled.status_code == 201, cancelled.text

    with pytest.raises(
        Exception,
        match="Cannot downgrade d62caac1e324 while cancellation history exists",
    ):
        await asyncio.to_thread(command.downgrade, config, "e8b78e1dfcfc")

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    await engine.dispose()
    assert version == "d62caac1e324"

    await asyncio.to_thread(command.upgrade, config, "head")
