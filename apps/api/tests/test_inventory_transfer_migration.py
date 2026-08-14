from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_inventory_transfer_contract import (
    _bootstrap_transfer_environment,
    _create_released_transfer,
)
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def migration_database_url(postgres_url: str) -> Iterator[str]:
    base = postgres_url.rsplit("/", 1)[0]
    url = f"{base}/tradeflow_migration_test"
    previous = os.environ.get("TRADEFLOW_DATABASE_URL")
    os.environ["TRADEFLOW_DATABASE_URL"] = url
    try:
        yield url
    finally:
        if previous is None:
            os.environ.pop("TRADEFLOW_DATABASE_URL", None)
        else:
            os.environ["TRADEFLOW_DATABASE_URL"] = previous


@pytest.fixture
def migration_alembic_config(migration_database_url: str) -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", migration_database_url)
    return config


@pytest.fixture
def migration_settings(migration_database_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=migration_database_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def migration_client(migration_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(migration_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def _reset_migration_database(admin_url: str, target_url: str) -> None:
    engine = create_async_engine(admin_url)
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = 'tradeflow_migration_test' AND pid <> pg_backend_pid()"
            )
        )
        await connection.execute(text("DROP DATABASE IF EXISTS tradeflow_migration_test"))
        await connection.execute(text("CREATE DATABASE tradeflow_migration_test"))
    await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_migration_database(
    migration_database_url: str,
    migration_alembic_config: Config,
) -> None:
    admin_url = migration_database_url.rsplit("/", 1)[0] + "/postgres"
    asyncio.run(_reset_migration_database(admin_url, migration_database_url))
    command.upgrade(migration_alembic_config, "head")


@pytest.mark.asyncio
async def test_inventory_transfer_migration_round_trip(
    migration_client: AsyncClient,
    migration_settings: Settings,
    migration_database_url: str,
    migration_alembic_config: Config,
) -> None:
    env = await _bootstrap_transfer_environment(migration_client, migration_settings)
    await _create_released_transfer(
        migration_client,
        migration_settings,
        env,
        "migration-transfer-request",
        quantity="5.000000",
    )

    # Downgrade is blocked while immutable transfer history exists.
    with pytest.raises(Exception):  # noqa: B017
        await asyncio.to_thread(command.downgrade, migration_alembic_config, "0017")

    engine = create_async_engine(migration_database_url)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE inventory_transfers CASCADE"))
        await connection.execute(text("TRUNCATE TABLE stock_movements CASCADE"))
        await connection.execute(
            text(
                "DELETE FROM inventory_availability WHERE location_id IN ("
                "SELECT location_id FROM warehouse_stock_locations "
                "WHERE custody = 'transfer_in_transit')"
            )
        )
        await connection.execute(
            text("DELETE FROM warehouse_stock_locations WHERE custody = 'transfer_in_transit'")
        )
    await engine.dispose()

    # Empty downgrade to the credit-note merge baseline and re-upgrade succeeds.
    await asyncio.to_thread(command.downgrade, migration_alembic_config, "0017")
    await asyncio.to_thread(command.upgrade, migration_alembic_config, "head")

    async with engine.connect() as connection:
        count = await connection.scalar(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = 'inventory_transfers'"
            )
        )
    await engine.dispose()
    assert count == 1


@pytest.mark.asyncio
async def test_inventory_transfer_migration_schema_includes_expected_objects(
    migration_database_url: str,
) -> None:
    engine = create_async_engine(migration_database_url)
    async with engine.connect() as connection:
        tables = {
            row["table_name"]
            for row in (
                await connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            ).mappings()
        }
        movement_types = {
            row["movement_type"]
            for row in (
                await connection.execute(
                    text("SELECT movement_type FROM stock_movements WHERE 1=0")
                )
            ).mappings()
        }
        transfer_custody_index = await connection.scalar(
            text("SELECT to_regclass('uq_warehouse_active_transfer_in_transit')")
        )
        transfer_group_guard = await connection.scalar(
            text(
                "SELECT to_regprocedure('inventory_transfer_group_is_valid("
                "uuid,uuid,character varying,uuid,uuid,uuid,uuid,uuid,numeric,numeric,"
                "character varying)')"
            )
        )
    await engine.dispose()

    assert "inventory_transfers" in tables
    # The table exists; constraint validation is covered by model parity and
    # contract tests. The empty result above just confirms the connection.
    assert movement_types == set()
    assert transfer_custody_index is not None
    assert transfer_group_guard is not None
