# ruff: noqa: F401, F811
from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_inventory_transfer_contract import (
    _bootstrap_transfer_environment,
    _create_released_transfer,
    transfer_client,
    transfer_settings,
)
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
async def transfer_env(
    transfer_client: AsyncClient,
    transfer_settings: Settings,
) -> dict[str, object]:
    env = await _bootstrap_transfer_environment(transfer_client, transfer_settings)
    env["transfer_id"] = await _create_released_transfer(
        transfer_client,
        transfer_settings,
        env,
        f"transfer-invariant-fixture-{uuid4()}",
    )
    return env


@pytest.mark.asyncio
async def test_inventory_transfer_history_is_immutable(transfer_env: dict[str, object]) -> None:
    transfer_id = transfer_env["transfer_id"]
    postgres_url = str(transfer_env["settings"].database_url)
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        with pytest.raises(Exception):  # noqa: B017
            await connection.execute(
                text(
                    "UPDATE inventory_transfers SET quantity_base = quantity_base + 1 "
                    "WHERE transfer_id = :transfer_id"
                ),
                {"transfer_id": transfer_id},
            )
        with pytest.raises(Exception):  # noqa: B017
            await connection.execute(
                text("DELETE FROM inventory_transfers WHERE transfer_id = :transfer_id"),
                {"transfer_id": transfer_id},
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_inventory_transfer_received_shape_is_enforced(
    transfer_env: dict[str, object],
) -> None:
    postgres_url = str(transfer_env["settings"].database_url)
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        with pytest.raises(Exception):  # noqa: B017
            await connection.execute(
                text(
                    "INSERT INTO inventory_transfers ("
                    "transfer_id, sku_id, from_warehouse_id, to_warehouse_id, "
                    "from_location_id, to_location_id, quantity_base, unit_cost, "
                    "base_currency, status, reason, source_reference, requested_by, "
                    "requested_at, release_movement_group_id, correlation_id, idempotency_key"
                    ") SELECT :transfer_id, sku_id, from_warehouse_id, to_warehouse_id, "
                    "from_location_id, to_location_id, quantity_base, unit_cost, "
                    "base_currency, 'received', reason, source_reference, requested_by, "
                    "requested_at, release_movement_group_id, correlation_id, :key "
                    "FROM inventory_transfers WHERE transfer_id = :existing_id"
                ),
                {
                    "transfer_id": "12345678-1234-1234-1234-123456789abc",
                    "existing_id": transfer_env["transfer_id"],
                    "key": "shape-test-key",
                },
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_inventory_transfer_status_transition_is_allowed(
    transfer_env: dict[str, object],
) -> None:
    transfer_id = transfer_env["transfer_id"]
    postgres_url = str(transfer_env["settings"].database_url)
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE inventory_transfers SET status = 'received', "
                "version = version + 1, "
                "received_by = requested_by, received_at = now(), "
                "receive_movement_group_id = release_movement_group_id "
                "WHERE transfer_id = :transfer_id"
            ),
            {"transfer_id": transfer_id},
        )
        result = await connection.execute(
            text(
                "SELECT status, version FROM inventory_transfers WHERE transfer_id = :transfer_id"
            ),
            {"transfer_id": transfer_id},
        )
        row = result.mappings().one()
        assert row["status"] == "received"
        assert row["version"] == 2
    await engine.dispose()
